"""HP 4145B Semiconductor Parameter Analyzer, User Mode over VISA.

Command authority is the HP 4145B Operation Manual, part 04145-90310,
printed April 1989 (serial prefix 2830J- and above). Printed pages below
are the manual's page numbers, not the PDF viewer index:

- Table 1-1 sheets 1-4, pp. 1-5 to 1-8: source ranges, compliance, VS/VM.
- pp. 3-22 to 3-25: SMU/VS/VM behavior, Table 3-6, Figure 3-10, Figure 3-13.
- pp. 3-26 to 3-27: Figure 3-14 numeric format.
- p. 3-32: SHORT/MEDIUM/LONG integration and auto-calibration.
- pp. 3-101 to 3-103: factory HP-IB address 17, comma delimiter, EOI on;
  terminator is ``;``, CR, or LF; numerics are at most 12 characters with a
  two-digit exponent and no unit suffix.
- Figure 3-52, pp. 3-109 to 3-111: ``US``, ``DV``/``DI``, ``DS``, ``TI``/``TV``,
  ``IT1``-``IT3``, ``BC``, ``CA0``/``CA1``, ``ID``.
- Figure 3-53, pp. 3-116 to 3-117: User Mode reading layout and status codes.
- Figure 3-55, pp. 3-118 to 3-119: status byte, including the fixture-lid bit.
- Figure 3-57, p. 3-123: Sample Program 2 (``TI1`` then ENTER, no ``DO``).

One Genesis instrument is one 4145B and one VISA session. SMU1-SMU4, VS1,
VS2, VM1, and VM2 are channels of that instance. Genesis sweeps points in
software; VAR1/VAR2, System Mode page setup, disk, and HP-GL are not used.
COM mode is not exposed: the manual defines it as a real common connection
(0 V, 105 mA), not as an output disconnect.

``MainWindow`` initialization does not call ``initialize``. It applies safe
or job keys through ``applyConfigValue``, then ``finalizeInitialization``.
The first bus command is therefore ``US`` from whichever path runs first.
``US`` is not repeated until a transport error clears the prepared flag.
``CA1`` is never sent: in User Mode it performs calibration immediately, and
a mode change forces auto-calibration off (p. 3-111). ``BC`` clears the
HP-IB data buffer only. A VISA device clear restores initial control
settings and leaves User Mode (pp. 3-102, 3-116), so ``visaClearOnOpen`` is
false and this driver never device-clears.

While a channel is off, level, range, compliance, and mode updates stay in
``jobConfig`` and do not send a full ``DV``/``DI`` (that would enable the
output). Enabling, or changing the live source while enabled, sends one
complete ``DV``/``DI`` including range, level, and compliance. A mode change
while enabled first sends the channel-only off command, then the new full
command. Software slew stays enabled: each step is its own full command.
Initialize applies swept levels before the output switch, so that in-memory
ramp does not source; the later output-on command uses the final level.
Once the output is already on, sweep slew steps are real ``DV``/``DI`` writes.
VS has no off command, so a VS slew always sends ``DS``.

Readings are spot measurements. Only status ``N`` with a finite value,
matching channel letter and V/I character, is returned. ``C``, ``T``, ``L``,
``V``, and ``X`` raise ``Hp4145BMeasurementStatusError`` and are not returned
as numbers. Malformed, mismatched, empty, and non-finite replies raise
``Hp4145BMeasurementError``. There is no automatic retrigger. Voltage-source
channels return current (``TI``); current-source channels return voltage
(``TV``), matching p. 1-5. VM1/VM2 use ``TV5``/``TV6`` (p. 3-110). Letters
A-D are SMU1-SMU4 and E/F are VM1/VM2 (Figure 3-53).

``AcquisitionWorker._sampleAllInstruments`` catches read exceptions, logs
them, and continues the sweep with no sample from this instrument. A status
error therefore does not by itself call ``applySafeState``. Setpoint
rejections are likewise logged as ``set failed`` and that sweep point
continues. Critical-ramping hold does not call ``applySafeState``; reads only
send ``BC`` plus one trigger, so they do not clear a held output. Hold is not
a substitute for SMU compliance or the fixture interlock.

In User Mode the fixture lid must be closed, or the shorting connector fitted,
or the instrument will not measure (p. 3-110 and status-byte bit 8 with bit 2,
p. 3-119). This driver does not bypass that interlock. A blocked read holds
the session lock until the VISA timeout, so Abort's ``applySafeState`` can be
delayed by up to ``visaTimeoutMs``. The default 10000 ms is an engineering
margin for one spot reading (LONG integration is 16 medium periods, about
320 ms at 50 Hz), not a guarantee of immediate hardware shutdown.
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

from genesis.core.instrument.base_instrument import BaseInstrument
from genesis.core.instrument.config_field import ConfigChoice, ConfigFieldDefinition
from genesis.core.instrument.registry import InstrumentRegistry
from genesis.core.transport.base_transport import BaseTransport

INSTRUMENT_TYPE_KEY = "hp4145b"

_PROGRAM_NUMBER = re.compile(r"^(?:0|-?\d+(?:\.\d+)?E[+-]\d{2})$")
_NUMERIC_TEXT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_READING = re.compile(
    r"^([NLVXCT])\s*([A-F])\s*([VI])\s*"
    r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d{1,2})?)$"
)
_MAX_PROGRAM_CHARS = 12
_MIN_CURRENT_COMPLIANCE_A = Decimal("5E-11")
_SMU_COUNT = 4
_STATUS_TEXT = {
    "N": "normal",
    "L": "sampling interval too short",
    "V": "A/D converter saturation",
    "X": "oscillation",
    "C": "this channel compliance",
    "T": "another channel compliance",
}
_SMU_LETTER = {1: "A", 2: "B", 3: "C", 4: "D"}
_VM_LETTER = {1: "E", 2: "F"}


class Hp4145BConfigError(ValueError):
    """The requested configuration violates the 4145B manual limits."""


class Hp4145BMeasurementError(RuntimeError):
    """A User Mode reading was empty, malformed, mismatched, or non-finite."""


class Hp4145BMeasurementStatusError(Hp4145BMeasurementError):
    """The instrument returned a non-normal data-status character."""

    def __init__(self, status: str, raw: str, value: float | None, detail: str) -> None:
        self.status = status
        self.raw = raw
        self.value = value
        super().__init__(detail)


class Hp4145BSafeStateError(RuntimeError):
    """One or more safe-state channel commands failed."""

    def __init__(self, detail: str, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class _VoltageRange:
    code: int
    maximum_v: Decimal
    resolution_v: Decimal
    max_current_a: Decimal


@dataclass(frozen=True, slots=True)
class _CurrentRange:
    code: int
    maximum_a: Decimal
    resolution_a: Decimal


# Table 3-6 and Table 1-1. Codes are the User Mode DV/DI range arguments.
_VOLTAGE_RANGES = (
    _VoltageRange(1, Decimal("20"), Decimal("0.001"), Decimal("0.1")),
    _VoltageRange(2, Decimal("40"), Decimal("0.002"), Decimal("0.05")),
    _VoltageRange(3, Decimal("100"), Decimal("0.005"), Decimal("0.02")),
)
_CURRENT_RANGES = (
    _CurrentRange(1, Decimal("1E-9"), Decimal("1E-12")),
    _CurrentRange(2, Decimal("1E-8"), Decimal("1E-11")),
    _CurrentRange(3, Decimal("1E-7"), Decimal("1E-10")),
    _CurrentRange(4, Decimal("1E-6"), Decimal("1E-9")),
    _CurrentRange(5, Decimal("1E-5"), Decimal("1E-8")),
    _CurrentRange(6, Decimal("1E-4"), Decimal("1E-7")),
    _CurrentRange(7, Decimal("1E-3"), Decimal("1E-6")),
    _CurrentRange(8, Decimal("1E-2"), Decimal("1E-5")),
    _CurrentRange(9, Decimal("0.1"), Decimal("1E-4")),
)
_VS_RESOLUTION_V = Decimal("0.001")
_VS_LIMIT_V = Decimal("20")


def _formatProgramNumber(value: Decimal) -> str:
    """Format a quantized value within the 12-character, 2-digit-exponent rule."""

    if value == 0:
        text = "0"
    else:
        sign = "-" if value < 0 else ""
        magnitude = abs(value)
        exponent = magnitude.adjusted()
        if abs(exponent) > 99:
            raise Hp4145BConfigError(f"Exponent exceeds two digits: {value}")
        mantissa = magnitude.scaleb(-exponent)
        mantissa_text = format(mantissa, "f").rstrip("0").rstrip(".")
        text = f"{sign}{mantissa_text}E{exponent:+03d}"
    if len(text) > _MAX_PROGRAM_CHARS or _PROGRAM_NUMBER.fullmatch(text) is None:
        raise Hp4145BConfigError(f"Value {value} does not fit the HP-IB numeric format")
    return text


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    steps = (value / quantum).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    return (steps * quantum).quantize(quantum)


def _parseDecimal(value: Any, key: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise Hp4145BConfigError(f"Invalid {key}: {value!r}")
    if isinstance(value, str):
        text = value.strip()
        if _NUMERIC_TEXT.fullmatch(text) is None:
            raise Hp4145BConfigError(f"Invalid {key}: {value!r}")
        try:
            parsed = Decimal(text)
        except Exception as exc:
            raise Hp4145BConfigError(f"Invalid {key}: {value!r}") from exc
    elif isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise Hp4145BConfigError(f"Invalid {key}: {value!r}")
        parsed = Decimal(str(number))
    else:
        raise Hp4145BConfigError(f"Invalid {key}: {value!r}")
    if not parsed.is_finite():
        raise Hp4145BConfigError(f"Invalid {key}: {value!r}")
    return parsed


def _voltageLimitForCurrent(current_a: Decimal) -> Decimal:
    """Voltage-compliance ceiling from Table 1-1 / Table 3-6 subranges."""

    magnitude = abs(current_a)
    if magnitude <= Decimal("0.02"):
        return Decimal("100")
    if magnitude <= Decimal("0.05"):
        return Decimal("40")
    if magnitude <= Decimal("0.1"):
        return Decimal("20")
    raise Hp4145BConfigError(f"Current {current_a} A exceeds 100 mA")


def _fitVoltage(raw: Decimal, range_index: int) -> tuple[Decimal, int, Decimal]:
    """Return quantized volts, DV range code, and that range's max current."""

    if range_index == 0:
        ceiling = _VOLTAGE_RANGES[-1]
        if abs(raw) > ceiling.maximum_v + (ceiling.resolution_v / 2):
            raise Hp4145BConfigError(f"Voltage {raw} V exceeds ±100 V")
        selected: _VoltageRange | None = None
        quantized = raw
        for spec in _VOLTAGE_RANGES:
            if abs(raw) <= spec.maximum_v + (spec.resolution_v / 2):
                candidate = _quantize(raw, spec.resolution_v)
                if abs(candidate) <= spec.maximum_v:
                    selected = spec
                    quantized = candidate
                    break
        if selected is None:
            raise Hp4145BConfigError(f"Voltage {raw} V exceeds ±100 V")
        for spec in _VOLTAGE_RANGES:
            if abs(quantized) <= spec.maximum_v:
                requantized = _quantize(quantized, spec.resolution_v)
                if abs(requantized) <= spec.maximum_v:
                    return requantized, 0, spec.max_current_a
        raise Hp4145BConfigError(f"Voltage {raw} V exceeds ±100 V")

    spec = next((item for item in _VOLTAGE_RANGES if item.code == range_index), None)
    if spec is None:
        raise Hp4145BConfigError(f"Unsupported voltage range index: {range_index}")
    if abs(raw) > spec.maximum_v + (spec.resolution_v / 2):
        raise Hp4145BConfigError(
            f"Voltage {raw} V exceeds the ±{spec.maximum_v} V range"
        )
    quantized = _quantize(raw, spec.resolution_v)
    if abs(quantized) > spec.maximum_v:
        raise Hp4145BConfigError(
            f"Quantized voltage {quantized} V exceeds the ±{spec.maximum_v} V range"
        )
    return quantized, spec.code, spec.max_current_a


def _fitCurrent(raw: Decimal, range_index: int) -> tuple[Decimal, int]:
    """Return quantized amperes and the DI range code to send."""

    sign = Decimal("-1") if raw < 0 else Decimal("1")
    magnitude = abs(raw)
    if range_index == 0:
        ceiling = _CURRENT_RANGES[-1]
        if magnitude > ceiling.maximum_a + (ceiling.resolution_a / 2):
            raise Hp4145BConfigError(f"Current {raw} A exceeds ±100 mA")
        quantized = magnitude
        found = False
        for spec in _CURRENT_RANGES:
            if magnitude <= spec.maximum_a + (spec.resolution_a / 2):
                candidate = _quantize(magnitude, spec.resolution_a)
                if candidate <= spec.maximum_a:
                    quantized = candidate
                    found = True
                    break
        if not found:
            raise Hp4145BConfigError(f"Current {raw} A exceeds ±100 mA")
        for spec in _CURRENT_RANGES:
            if quantized <= spec.maximum_a:
                requantized = _quantize(quantized, spec.resolution_a)
                if requantized <= spec.maximum_a:
                    return sign * requantized, 0
        raise Hp4145BConfigError(f"Current {raw} A exceeds ±100 mA")

    spec = next((item for item in _CURRENT_RANGES if item.code == range_index), None)
    if spec is None:
        raise Hp4145BConfigError(f"Unsupported current range index: {range_index}")
    if magnitude > spec.maximum_a + (spec.resolution_a / 2):
        raise Hp4145BConfigError(
            f"Current {raw} A exceeds the ±{spec.maximum_a} A range"
        )
    quantized = _quantize(magnitude, spec.resolution_a)
    if quantized > spec.maximum_a:
        raise Hp4145BConfigError(
            f"Quantized current {quantized} A exceeds the ±{spec.maximum_a} A range"
        )
    return sign * quantized, spec.code


def _fitCurrentCompliance(raw: Decimal, max_current_a: Decimal) -> Decimal:
    # Reject anything below the manual minimum before rounding. Rounding a
    # smaller request up to 50 pA would allow more current than requested.
    if raw < _MIN_CURRENT_COMPLIANCE_A:
        raise Hp4145BConfigError(
            f"Current compliance must be at least {_MIN_CURRENT_COMPLIANCE_A} A"
        )
    quantum = _CURRENT_RANGES[-1].resolution_a
    for spec in _CURRENT_RANGES:
        if raw <= spec.maximum_a + (spec.resolution_a / 2):
            quantum = spec.resolution_a
            break
    if raw > max_current_a + (quantum / 2):
        raise Hp4145BConfigError(
            f"Current compliance {raw} A exceeds {max_current_a} A for this voltage range"
        )
    quantized = _quantize(raw, quantum)
    if quantized < _MIN_CURRENT_COMPLIANCE_A or quantized > max_current_a:
        raise Hp4145BConfigError(
            f"Current compliance {quantized} A is outside {_MIN_CURRENT_COMPLIANCE_A} A "
            f"to {max_current_a} A"
        )
    return quantized


def _fitVoltageCompliance(raw: Decimal, allowed_max_v: Decimal) -> Decimal:
    if raw < 0:
        raise Hp4145BConfigError("Voltage compliance cannot be negative")
    quantum = _VOLTAGE_RANGES[0].resolution_v
    probe = raw if raw <= allowed_max_v else allowed_max_v
    for spec in _VOLTAGE_RANGES:
        if probe <= spec.maximum_v:
            quantum = spec.resolution_v
            break
    if raw > allowed_max_v + (quantum / 2):
        raise Hp4145BConfigError(
            f"Voltage compliance {raw} V exceeds {allowed_max_v} V for this current"
        )
    quantized = _quantize(raw, quantum) if raw != 0 else Decimal("0")
    if quantized < 0 or quantized > allowed_max_v:
        raise Hp4145BConfigError(
            f"Quantized voltage compliance {quantized} V exceeds {allowed_max_v} V"
        )
    return quantized


def _fitVs(raw: Decimal) -> Decimal:
    if abs(raw) > _VS_LIMIT_V + (_VS_RESOLUTION_V / 2):
        raise Hp4145BConfigError(f"VS voltage {raw} V exceeds ±20 V")
    quantized = _quantize(raw, _VS_RESOLUTION_V)
    if abs(quantized) > _VS_LIMIT_V:
        raise Hp4145BConfigError(f"Quantized VS voltage {quantized} V exceeds ±20 V")
    return quantized


def _storedFloat(value: Decimal) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise Hp4145BConfigError(f"Non-finite quantized value: {value}")
    return number


@dataclass(frozen=True, slots=True)
class _Signal:
    key: str
    label: str
    kind: str
    index: int

    @property
    def letter(self) -> str:
        if self.kind == "vm":
            return _VM_LETTER[self.index]
        return _SMU_LETTER[self.index]

    @property
    def type_char(self) -> str:
        return "I" if self.kind == "smu-i" else "V"

    @property
    def trigger(self) -> str:
        if self.kind == "vm":
            return f"TV{4 + self.index}"
        prefix = "TI" if self.kind == "smu-i" else "TV"
        return f"{prefix}{self.index}"


def _signals() -> tuple[_Signal, ...]:
    rows: list[_Signal] = []
    for index in range(1, _SMU_COUNT + 1):
        rows.append(
            _Signal(f"smu{index}VoltageV", f"SMU{index} voltage (V)", "smu-v", index)
        )
        rows.append(
            _Signal(f"smu{index}CurrentA", f"SMU{index} current (A)", "smu-i", index)
        )
    rows.append(_Signal("vm1VoltageV", "VM1 voltage (V)", "vm", 1))
    rows.append(_Signal("vm2VoltageV", "VM2 voltage (V)", "vm", 2))
    return tuple(rows)


_SIGNALS = _signals()
_SIGNALS_BY_KEY = {signal.key: signal for signal in _SIGNALS}


class Hp4145BInstrument(BaseInstrument):
    """One HP 4145B in User Mode. See the module docstring for the contract."""

    displayName = "HP 4145B Semiconductor Parameter Analyzer"

    @classmethod
    def getSupportedTransportKeys(cls) -> list[str]:
        return ["visa"]

    @classmethod
    def getDefaultTransportKey(cls) -> str:
        return "visa"

    @classmethod
    def getDefaultAddress(cls) -> str:
        # Figure 3-51 and Sample Program 2: factory address 17.
        return "GPIB0::17::INSTR"

    @classmethod
    def getDefaultTransportSettings(cls) -> dict[str, Any]:
        # LF is an accepted program terminator (p. 3-103). User Mode data ends
        # with CR/LF (Figure 3-53). EOI is the factory switch setting. Device
        # clear is left off because it exits User Mode.
        return {
            "visaTimeoutMs": 10000,
            "writeTermination": "\n",
            "readTermination": "\n",
            "visaSendEndOnWrite": True,
            "visaClearOnOpen": False,
        }

    @classmethod
    def getAvailableMeasurementSignals(cls) -> list[tuple[str, str]]:
        return [(signal.key, signal.label) for signal in _SIGNALS]

    @classmethod
    def getJobConfigFields(cls) -> list[ConfigFieldDefinition]:
        fields: list[ConfigFieldDefinition] = []
        for index in range(1, _SMU_COUNT + 1):
            fields.extend(_smuFields(index))
        fields.append(
            ConfigFieldDefinition(
                key="vs1VoltageLevelV",
                label="VS1 voltage (V)",
                fieldType="float",
                default=0.0,
                minValue=-20.0,
                maxValue=20.0,
                stepValue=0.001,
                sweepable=True,
                helpText=(
                    "DS1,<voltage>. ±20 V, 1 mV resolution, 10 mA hardware limit "
                    "(Table 1-1, p. 1-7). There is no VS output-off command; 0 V "
                    "is not an open circuit. 0 V is a driver-chosen conservative "
                    "default, not a documented factory User Mode preset."
                ),
            )
        )
        fields.append(
            ConfigFieldDefinition(
                key="vs2VoltageLevelV",
                label="VS2 voltage (V)",
                fieldType="float",
                default=0.0,
                minValue=-20.0,
                maxValue=20.0,
                stepValue=0.001,
                sweepable=True,
                helpText=(
                    "DS2,<voltage>. Same limits as VS1. 0 V is a driver-chosen "
                    "conservative default, not a documented factory preset."
                ),
            )
        )
        fields.append(
            ConfigFieldDefinition(
                key="integrationTimeIndex",
                label="Integration time",
                fieldType="enum",
                default=2,
                minValue=1,
                maxValue=3,
                choices=[
                    ConfigChoice(1, "SHORT (IT1)"),
                    ConfigChoice(2, "MEDIUM (IT2)"),
                    ConfigChoice(3, "LONG (IT3)"),
                ],
                sweepable=False,
                helpText=(
                    "IT1 SHORT (no digital integration), IT2 MEDIUM (16 samples "
                    "in one line cycle), IT3 LONG (16 times MEDIUM). p. 3-32 and "
                    "p. 3-111. MEDIUM is a driver-chosen default; the manual does "
                    "not publish a remote factory preset. Not a continuous NPLC."
                ),
            )
        )
        return fields

    def __init__(
        self,
        name: str,
        transport: BaseTransport,
        metadata: dict[str, Any] | None = None,
        jobConfig: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(name=name, transport=transport, metadata=metadata)
        self.jobConfig = dict(self.getDefaultJobConfig())
        self.jobConfig.update(jobConfig or {})
        self._userModeReady = False
        self._lock = threading.Lock()

    def initialize(self) -> None:
        """Enter User Mode once and command the configured output state.

        The main window does not call this. ``applyConfigValue`` prepares
        User Mode on its own. No self-test, device clear, or calibration.
        """

        with self._lock:
            normalized = self._normalizeConfig(self.jobConfig)
            self._ensurePrepared(normalized)
            for index in range(1, _SMU_COUNT + 1):
                self._write(self._smuCommand(normalized, index))
            self._write(self._vsCommand(normalized, 1))
            self._write(self._vsCommand(normalized, 2))
            self.jobConfig = normalized

    def finalizeInitialization(self, should_stop: Any = None) -> bool:
        """Enter User Mode if no config write has done so yet. Does not source."""

        if should_stop is not None and should_stop():
            return False
        with self._lock:
            self._ensurePrepared(self._normalizeConfig(self.jobConfig))
        return True

    def applyConfigValue(self, key: str, value: float | int | str) -> None:
        if key not in {field.key for field in self.getJobConfigFields()}:
            return
        with self._lock:
            proposed = dict(self.jobConfig)
            proposed[key] = value
            normalized = self._normalizeConfig(proposed)
            self._ensurePrepared(self.jobConfig)
            self._emit(key, normalized, self.jobConfig)
            self.jobConfig = normalized

    def readMeasurements(self, signalKeys: list[str]) -> dict[str, float]:
        requested: list[_Signal] = []
        seen: set[str] = set()
        for key in signalKeys:
            if key in seen or key not in _SIGNALS_BY_KEY:
                continue
            seen.add(key)
            requested.append(_SIGNALS_BY_KEY[key])
        with self._lock:
            measurable = [
                signal
                for signal in requested
                if self._isMeasurable(signal, self.jobConfig)
            ]
            if not measurable:
                return {}
            self._ensurePrepared(self.jobConfig)
            values: dict[str, float] = {}
            for signal in measurable:
                # Each spot is BC + trigger + read. Channels are not simultaneous.
                self._write("BC")
                self._write(signal.trigger)
                raw = self._read()
                values[signal.key] = _parseReading(
                    raw, signal.letter, signal.type_char, signal.key
                )
            return values

    def applySafeState(self) -> None:
        """Turn SMUs off and set both VS outputs to 0 V unless safeConfig says otherwise.

        An explicit ``safeConfig`` entry is the only way to hold a bias.
        Missing output switches are forced off, and missing VS voltages are
        forced to 0 V, even when ``jobConfig`` still enables them. Empty
        ``safeConfig`` uses that shutdown rather than the live job outputs.
        If an explicit bias violates the output envelope, it is not applied;
        safe state falls back to the same shutdown. A failed channel does not
        skip the remaining channels; ``Hp4145BSafeStateError`` is raised after
        those attempts. VS 0 V is a programmed voltage, not a disconnect.
        """

        with self._lock:
            targets = self._safeTargets()
            errors: list[str] = []
            for index in range(1, _SMU_COUNT + 1):
                try:
                    self._ensurePrepared(self.jobConfig)
                    if targets is None:
                        self._forceSmuOff(index)
                    else:
                        self._commandSafeSmu(index, targets)
                except Exception as exc:
                    errors.append(f"SMU{index}: {exc}")
            for index in (1, 2):
                try:
                    self._ensurePrepared(self.jobConfig)
                    if targets is None:
                        self._write(f"DS{index},0")
                        self.jobConfig[f"vs{index}VoltageLevelV"] = 0.0
                    else:
                        self._write(self._vsCommand(targets, index))
                        self.jobConfig[f"vs{index}VoltageLevelV"] = targets[
                            f"vs{index}VoltageLevelV"
                        ]
                except Exception as exc:
                    errors.append(f"VS{index}: {exc}")
            if errors:
                raise Hp4145BSafeStateError(
                    "HP 4145B safe state incomplete: " + "; ".join(errors),
                    errors,
                )

    def _safeTargets(self) -> dict[str, Any] | None:
        """Return a validated hold/shutdown config, or None to force outputs off.

        None means the caller must send channel-only off commands and ``DS,0``
        without revalidating a stored source level. That covers an empty safe
        config and an explicit bias the envelope rejects.
        """

        raw_safe = self.metadata.get("safeConfig", {})
        safe = dict(raw_safe) if isinstance(raw_safe, dict) else {}
        if not safe:
            return None
        merged = dict(self.jobConfig)
        explicit = {str(key) for key in safe}
        merged.update(safe)
        for index in range(1, _SMU_COUNT + 1):
            output_key = f"smu{index}OutputEnabled"
            if output_key not in explicit:
                merged[output_key] = 0
        for index in (1, 2):
            vs_key = f"vs{index}VoltageLevelV"
            if vs_key not in explicit:
                merged[vs_key] = 0.0
        try:
            return self._normalizeConfig(merged)
        except Hp4145BConfigError:
            return None

    def _forceSmuOff(self, index: int) -> None:
        mode = str(self.jobConfig.get(f"smu{index}ForceMode", "VOLT"))
        if mode not in {"VOLT", "CURR"}:
            mode = "VOLT"
        self._write(_offCommand(index, mode))
        self.jobConfig[f"smu{index}OutputEnabled"] = 0

    def _commandSafeSmu(self, index: int, targets: dict[str, Any]) -> None:
        old_mode = str(self.jobConfig[f"smu{index}ForceMode"])
        new_mode = str(targets[f"smu{index}ForceMode"])
        was_enabled = int(self.jobConfig[f"smu{index}OutputEnabled"]) == 1
        now_enabled = int(targets[f"smu{index}OutputEnabled"]) == 1
        if now_enabled and was_enabled and old_mode != new_mode:
            self._write(_offCommand(index, old_mode))
            self._write(self._onCommand(index, targets))
        elif now_enabled:
            self._write(self._onCommand(index, targets))
        else:
            self._write(_offCommand(index, old_mode if was_enabled else new_mode))
        for suffix in _SMU_SUFFIXES:
            key = f"smu{index}{suffix}"
            self.jobConfig[key] = targets[key]

    def _ensurePrepared(self, config: dict[str, Any]) -> None:
        if self._userModeReady:
            return
        self._write("US")
        self._write(f"IT{int(config['integrationTimeIndex'])}")
        self._userModeReady = True

    def _emit(self, key: str, new: dict[str, Any], old: dict[str, Any]) -> None:
        if key == "integrationTimeIndex":
            self._write(f"IT{int(new[key])}")
            return
        if key == "vs1VoltageLevelV":
            self._write(self._vsCommand(new, 1))
            return
        if key == "vs2VoltageLevelV":
            self._write(self._vsCommand(new, 2))
            return
        index = _smuIndex(key)
        if index is None:
            return
        old_mode = str(old[f"smu{index}ForceMode"])
        new_mode = str(new[f"smu{index}ForceMode"])
        was_enabled = int(old[f"smu{index}OutputEnabled"]) == 1
        now_enabled = int(new[f"smu{index}OutputEnabled"]) == 1
        output_key = f"smu{index}OutputEnabled"
        if now_enabled and was_enabled and old_mode != new_mode:
            self._write(_offCommand(index, old_mode))
            try:
                self._write(self._onCommand(index, new))
            except Exception:
                try:
                    self._write(self._onCommand(index, old))
                except Exception:
                    pass
                raise
            return
        if now_enabled and (
            not was_enabled
            or key == output_key
            or _affectsLiveSource(index, key, new_mode)
        ):
            self._write(self._onCommand(index, new))
            return
        if not now_enabled and (was_enabled or key == output_key):
            self._write(_offCommand(index, old_mode if was_enabled else new_mode))

    def _normalizeConfig(self, config: dict[str, Any]) -> dict[str, Any]:
        fields = {field.key: field for field in self.getJobConfigFields()}
        normalized: dict[str, Any] = {}
        decimals: dict[str, Decimal] = {}
        for key, field in fields.items():
            if key not in config:
                raise Hp4145BConfigError(f"Missing {key}")
            raw = config[key]
            if field.choices and isinstance(field.choices[0].value, str):
                text = str(raw).strip()
                allowed = {str(choice.value) for choice in field.choices}
                if text not in allowed:
                    raise Hp4145BConfigError(f"Unsupported {key}: {raw!r}")
                normalized[key] = text
                continue
            if field.choices:
                parsed = _parseDecimal(raw, key)
                match = next(
                    (
                        choice.value
                        for choice in field.choices
                        if parsed == Decimal(str(choice.value))
                    ),
                    None,
                )
                if match is None:
                    raise Hp4145BConfigError(f"Unsupported {key}: {raw!r}")
                normalized[key] = match
                continue
            decimals[key] = _parseDecimal(raw, key)

        for index in range(1, _SMU_COUNT + 1):
            voltage, _vcode, max_current = _fitVoltage(
                decimals[f"smu{index}ForceVoltageLevelV"],
                int(normalized[f"smu{index}VoltageRangeIndex"]),
            )
            current, _icode = _fitCurrent(
                decimals[f"smu{index}ForceCurrentLevelA"],
                int(normalized[f"smu{index}CurrentRangeIndex"]),
            )
            compliance = _fitCurrentCompliance(
                decimals[f"smu{index}CurrentComplianceA"], max_current
            )
            voltage_limit = _voltageLimitForCurrent(current)
            voltage_compliance = _fitVoltageCompliance(
                decimals[f"smu{index}VoltageComplianceV"], voltage_limit
            )
            normalized[f"smu{index}ForceVoltageLevelV"] = _storedFloat(voltage)
            normalized[f"smu{index}ForceCurrentLevelA"] = _storedFloat(current)
            normalized[f"smu{index}CurrentComplianceA"] = _storedFloat(compliance)
            normalized[f"smu{index}VoltageComplianceV"] = _storedFloat(
                voltage_compliance
            )
        normalized["vs1VoltageLevelV"] = _storedFloat(
            _fitVs(decimals["vs1VoltageLevelV"])
        )
        normalized["vs2VoltageLevelV"] = _storedFloat(
            _fitVs(decimals["vs2VoltageLevelV"])
        )
        return normalized

    def _isMeasurable(self, signal: _Signal, config: dict[str, Any]) -> bool:
        if signal.kind == "vm":
            return True
        mode = str(config[f"smu{signal.index}ForceMode"])
        if signal.kind == "smu-i":
            return mode == "VOLT"
        return mode == "CURR"

    def _smuCommand(self, config: dict[str, Any], index: int) -> str:
        if int(config[f"smu{index}OutputEnabled"]) == 1:
            return self._onCommand(index, config)
        return _offCommand(index, str(config[f"smu{index}ForceMode"]))

    def _onCommand(self, index: int, config: dict[str, Any]) -> str:
        mode = str(config[f"smu{index}ForceMode"])
        if mode == "VOLT":
            return (
                f"DV{index},{int(config[f'smu{index}VoltageRangeIndex'])},"
                f"{_formatProgramNumber(_decimal(config[f'smu{index}ForceVoltageLevelV']))},"
                f"{_formatProgramNumber(_decimal(config[f'smu{index}CurrentComplianceA']))}"
            )
        return (
            f"DI{index},{int(config[f'smu{index}CurrentRangeIndex'])},"
            f"{_formatProgramNumber(_decimal(config[f'smu{index}ForceCurrentLevelA']))},"
            f"{_formatProgramNumber(_decimal(config[f'smu{index}VoltageComplianceV']))}"
        )

    def _vsCommand(self, config: dict[str, Any], index: int) -> str:
        return (
            f"DS{index},"
            f"{_formatProgramNumber(_decimal(config[f'vs{index}VoltageLevelV']))}"
        )

    def _write(self, command: str) -> None:
        try:
            self.transport.write(command)
        except Exception:
            self._userModeReady = False
            raise

    def _read(self) -> str:
        try:
            return self.transport.read()
        except Exception:
            self._userModeReady = False
            raise


_SMU_SUFFIXES = (
    "ForceMode",
    "ForceVoltageLevelV",
    "ForceCurrentLevelA",
    "VoltageRangeIndex",
    "CurrentRangeIndex",
    "CurrentComplianceA",
    "VoltageComplianceV",
    "OutputEnabled",
)


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


def _smuIndex(key: str) -> int | None:
    for index in range(1, _SMU_COUNT + 1):
        if key.startswith(f"smu{index}"):
            return index
    return None


def _offCommand(index: int, mode: str) -> str:
    return f"DV{index}" if mode == "VOLT" else f"DI{index}"


def _affectsLiveSource(index: int, key: str, mode: str) -> bool:
    if mode == "VOLT":
        return key in {
            f"smu{index}ForceVoltageLevelV",
            f"smu{index}VoltageRangeIndex",
            f"smu{index}CurrentComplianceA",
        }
    return key in {
        f"smu{index}ForceCurrentLevelA",
        f"smu{index}CurrentRangeIndex",
        f"smu{index}VoltageComplianceV",
    }


def _smuFields(index: int) -> list[ConfigFieldDefinition]:
    prefix = f"smu{index}"
    # Defaults below are driver-chosen conservative lab values. The operation
    # manual does not publish these as the factory User Mode preset.
    return [
        ConfigFieldDefinition(
            key=f"{prefix}ForceMode",
            label=f"SMU{index} source mode",
            fieldType="enum",
            default="VOLT",
            choices=[
                ConfigChoice("VOLT", "Voltage source"),
                ConfigChoice("CURR", "Current source"),
            ],
            sweepable=False,
            helpText=(
                f"DV{index} voltage source or DI{index} current source "
                "(Figure 3-52, p. 3-109). COM is not available. Changing mode "
                "while the output is on sends the channel-only off command "
                "first. Inactive mode setpoints are kept and are not applied."
            ),
        ),
        ConfigFieldDefinition(
            key=f"{prefix}ForceVoltageLevelV",
            label=f"SMU{index} voltage (V)",
            fieldType="float",
            default=0.0,
            minValue=-100.0,
            maxValue=100.0,
            stepValue=0.001,
            sweepable=True,
            helpText=(
                f"DV{index} output value, used only in VOLT mode. Outer bounds "
                "are ±100 V. Table 3-6 resolution is 1/2/5 mV on the 20/40/100 V "
                "ranges. AUTO uses the lowest range that contains the quantized "
                "value. The rectangular bound is not the output envelope: 20 V "
                "allows 100 mA, 40 V allows 50 mA, and 100 V allows 20 mA. "
                "0 V is a driver-chosen default, not a factory preset. A full "
                "DV is sent only while this channel's output is enabled."
            ),
        ),
        ConfigFieldDefinition(
            key=f"{prefix}ForceCurrentLevelA",
            label=f"SMU{index} current (A)",
            fieldType="float",
            default=0.0,
            minValue=-0.1,
            maxValue=0.1,
            stepValue=1e-12,
            sweepable=True,
            helpText=(
                f"DI{index} output value, used only in CURR mode. Outer bounds "
                "are ±100 mA. Source ranges are 1 nA through 100 mA (codes 1-9); "
                "the 1000 pA measurement range is not a source code. Resolution "
                "follows Table 3-6. Voltage compliance is limited to 100/40/20 V "
                "for ≤20 mA, >20-50 mA, and >50-100 mA. 0 A is a driver-chosen "
                "default. A full DI is sent only while this output is enabled."
            ),
        ),
        ConfigFieldDefinition(
            key=f"{prefix}VoltageRangeIndex",
            label=f"SMU{index} voltage range",
            fieldType="enum",
            default=0,
            minValue=0,
            maxValue=3,
            choices=[
                ConfigChoice(0, "AUTO"),
                ConfigChoice(1, "20 V"),
                ConfigChoice(2, "40 V"),
                ConfigChoice(3, "100 V"),
            ],
            sweepable=False,
            helpText=(
                f"DV{index} range code: 0 AUTO, 1 ±20 V, 2 ±40 V, 3 ±100 V "
                "(p. 3-109). AUTO is the driver default. A fixed range must "
                "contain the voltage, and its current limit applies even when "
                "the voltage would fit a lower range."
            ),
        ),
        ConfigFieldDefinition(
            key=f"{prefix}CurrentRangeIndex",
            label=f"SMU{index} current range",
            fieldType="enum",
            default=0,
            minValue=0,
            maxValue=9,
            choices=[
                ConfigChoice(0, "AUTO"),
                ConfigChoice(1, "1 nA"),
                ConfigChoice(2, "10 nA"),
                ConfigChoice(3, "100 nA"),
                ConfigChoice(4, "1 µA"),
                ConfigChoice(5, "10 µA"),
                ConfigChoice(6, "100 µA"),
                ConfigChoice(7, "1 mA"),
                ConfigChoice(8, "10 mA"),
                ConfigChoice(9, "100 mA"),
            ],
            sweepable=False,
            helpText=(
                f"DI{index} range code 0 AUTO and 1-9 for 1 nA, 10 nA, 100 nA, "
                "1 µA, 10 µA, 100 µA, 1 mA, 10 mA, 100 mA (p. 3-109). The 100 mA "
                "code has 20/50/100 mA subranges (Table 3-6). AUTO is the driver "
                "default."
            ),
        ),
        ConfigFieldDefinition(
            key=f"{prefix}CurrentComplianceA",
            label=f"SMU{index} current compliance (A)",
            fieldType="float",
            default=1e-6,
            minValue=5e-11,
            maxValue=0.1,
            stepValue=1e-12,
            sweepable=False,
            helpText=(
                f"DV{index} compliance argument. Table 1-1 (p. 1-6): 50 pA "
                "minimum through the voltage range's maximum current. Negative "
                "and zero are rejected. 1 µA is a driver-chosen conservative "
                "default, not a factory preset. Sent only with an enabled "
                "voltage source."
            ),
        ),
        ConfigFieldDefinition(
            key=f"{prefix}VoltageComplianceV",
            label=f"SMU{index} voltage compliance (V)",
            fieldType="float",
            default=1.0,
            minValue=0.0,
            maxValue=100.0,
            stepValue=0.001,
            sweepable=False,
            helpText=(
                f"DI{index} compliance argument. 0 V is allowed (p. 1-6). The "
                "ceiling follows the current subrange: 100 V at ≤20 mA, 40 V "
                "above 20 mA through 50 mA, and 20 V above 50 mA. 1 V is a "
                "driver-chosen conservative default, not a factory preset."
            ),
        ),
        ConfigFieldDefinition(
            key=f"{prefix}OutputEnabled",
            label=f"SMU{index} output",
            fieldType="enum",
            default=0,
            minValue=0,
            maxValue=1,
            choices=[ConfigChoice(0, "Off"), ConfigChoice(1, "On")],
            sweepable=False,
            helpText=(
                f"Off sends DV{index} or DI{index} with only the channel number "
                "(NOT USE, p. 3-109). On sends the full source command. Off is "
                "the driver-chosen safe default. Settings changed while off do "
                "not enable the output."
            ),
        ),
    ]


def _parseReading(raw: str, letter: str, type_char: str, signal_key: str) -> float:
    text = raw.strip()
    match = _READING.fullmatch(text)
    if match is None:
        raise Hp4145BMeasurementError(
            f"Malformed HP 4145B reading for {signal_key}: {raw!r}"
        )
    status, got_letter, got_type, number_text = match.groups()
    if got_letter != letter or got_type != type_char:
        raise Hp4145BMeasurementError(
            f"HP 4145B reading for {signal_key} was {got_letter}{got_type}, "
            f"expected {letter}{type_char}: {raw!r}"
        )
    try:
        value = float(number_text)
    except ValueError as exc:
        raise Hp4145BMeasurementError(
            f"Malformed HP 4145B reading for {signal_key}: {raw!r}"
        ) from exc
    if status != "N":
        meaning = _STATUS_TEXT.get(status, status)
        parsed: float | None = value if math.isfinite(value) else None
        raise Hp4145BMeasurementStatusError(
            status,
            raw,
            parsed,
            f"HP 4145B {signal_key} status {status} ({meaning}): {raw!r}",
        )
    if not math.isfinite(value):
        raise Hp4145BMeasurementError(
            f"Non-finite HP 4145B reading for {signal_key}: {raw!r}"
        )
    return value


def _hp4145bFactory(
    name: str,
    transport: BaseTransport,
    metadata: dict[str, Any] | None = None,
    jobConfig: dict[str, Any] | None = None,
) -> BaseInstrument:
    return Hp4145BInstrument(
        name=name,
        transport=transport,
        metadata=metadata,
        jobConfig=jobConfig,
    )


def registerInstruments(registry: InstrumentRegistry) -> None:
    registry.registerInstrument(
        key=INSTRUMENT_TYPE_KEY,
        instrumentType=Hp4145BInstrument,
        factory=_hp4145bFactory,
    )
