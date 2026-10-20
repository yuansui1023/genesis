"""YOKOGAWA 7651 Programmable DC Source, native GP-IB commands over VISA.

Command authority is IM 7651-01E, printed pages (not the PDF viewer index):

- 3-7 to 3-11: functions, ranges, and limiter resolution.
- 4-10 to 4-12: source/sink, limiter, and hardware trip.
- 6-1 to 6-7: GP-IB, 50-character messages, terminators, OD/OS/OC formats,
  and device clear.
- 6-21 to 6-26: F, R, S, SA, O, E, RC, and RU.
- 6-31 to 6-34: LV, LA, OS, OD, OC, DL, and H.
- 8-1: actual maximum output and resolution.
- A-1: power-on values. The GP-IB address is retained and has no published
  factory number.
- A-2 error 11: voltage limit in voltage mode, or current limit in current
  mode, is rejected.

The 7651 is a source. ``OD`` returns the programmed setting, not a measured
terminal voltage or load current. ``outputEnabled`` from ``OC`` is required
before that setting can be read as present at the terminals. Output OFF does
not mean the setting is zero, and zero does not mean the output is off.
``O0`` is not documented as a physical open.

``S``, ``SA``, ``O``, ``F``, and ``R`` are stored until ``E`` (or GET). This
driver puts ``E`` in the same program message as every source or output
change and updates its executed-state cache only after that write returns.
``LV`` and ``LA`` are not in the trigger list, so they are sent without ``E``.
``LA`` is sent only in voltage mode and ``LV`` only in current mode.

``RC``, calibration, and device clear are not used. Device clear restores the
power-on panel and leaves the interface inoperative for a few seconds
(p. 6-3). ``visaClearOnOpen`` is therefore false.

The current limiter does not operate on the 10 mV or 100 mV ranges because of
the output divider (p. 4-11). ``SA`` can select those ranges. The hardware
trip (about 130 mA or ±35 V, and about ±0.6 V on the mV ranges) is not a
programmable limiter and is not Genesis critical ramping.

``MainWindow`` does not call ``initialize``. The first ``applyConfigValue``
prepares the session once (``RU0``, ``H1``, ``DL0``) and, when no executed
state is known, turns the output off before programming it. Later sweep steps
send only the source command plus ``E``. A failed write or a truncated ``OS``
drops the executed-state cache. ``readMeasurements`` never turns the output
on or off. ``AcquisitionWorker`` logs read errors and continues, so an
overload or status error does not itself enter safe state.
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

from genesis.core.instrument.base_instrument import BaseInstrument
from genesis.core.instrument.config_field import ConfigChoice, ConfigFieldDefinition
from genesis.core.instrument.registry import InstrumentRegistry
from genesis.core.transport.base_transport import BaseTransport

INSTRUMENT_TYPE_KEY = "yokogawa7651"

_MAX_COMMAND_CHARS = 50
_OS_MAX_LINES = 8
_NUMERIC_TEXT = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_SOURCE_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:E[+-]?\d+)?")
_OD_READING = re.compile(
    r"^(?P<status>[NE])DC(?P<kind>[VA])\s*"
    r"(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:E[+-]?\d+)?)$"
)
_OC_READING = re.compile(r"^STS1\s*=\s*(?P<code>\d+)$")

# OC bits are numbered from 1 in the manual. Bit 1 is the least significant bit.
_OC_OUTPUT_ON = 0x10
_OC_OUTPUT_UNSTABLE = 0x08
_OC_COMMAND_ERROR = 0x04

_VOLTAGE_RANGES: dict[int, tuple[Decimal, Decimal]] = {
    2: (Decimal("0.012"), Decimal("1E-7")),
    3: (Decimal("0.12"), Decimal("1E-6")),
    4: (Decimal("1.2"), Decimal("1E-5")),
    5: (Decimal("12"), Decimal("1E-4")),
    6: (Decimal("32"), Decimal("1E-3")),
}
_CURRENT_RANGES: dict[int, tuple[Decimal, Decimal]] = {
    4: (Decimal("0.0012"), Decimal("1E-8")),
    5: (Decimal("0.012"), Decimal("1E-7")),
    6: (Decimal("0.12"), Decimal("1E-6")),
}
_SETTING_KEYS = (
    "sourceVoltageSettingV",
    "sourceCurrentSettingA",
)
_STATUS_KEYS = ("outputEnabled", "outputUnstable", "communicationError")


class Yokogawa7651ConfigError(ValueError):
    """The requested 7651 configuration is not a legal manual setting."""


class Yokogawa7651ResponseError(RuntimeError):
    """A 7651 reply was missing, malformed, or left the session unsynchronized."""


class Yokogawa7651OverloadError(Yokogawa7651ResponseError):
    """OD returned the overload header. The number is not a normal setting."""

    def __init__(self, raw: str, value: float | None) -> None:
        self.raw = raw
        self.value = value
        super().__init__(f"YOKOGAWA 7651 overload setting response: {raw!r}")


class Yokogawa7651SafeStateError(RuntimeError):
    """Safe state could not be confirmed."""

    def __init__(self, detail: str, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class _Executed:
    mode: str
    voltage_range: int
    current_range: int
    voltage_auto: int
    current_auto: int
    voltage: Decimal
    current: Decimal
    voltage_limit: int
    current_limit_ma: int
    output_on: bool

    def as_job(self) -> dict[str, Any]:
        return {
            "forceMode": self.mode,
            "forceVoltageLevelV": float(self.voltage),
            "forceCurrentLevelA": float(self.current),
            "voltageRangeIndex": self.voltage_range,
            "currentRangeIndex": self.current_range,
            "voltageRangeAuto": self.voltage_auto,
            "currentRangeAuto": self.current_auto,
            "voltageLimitV": self.voltage_limit,
            "currentLimitA": float(self.current_limit_ma) / 1000.0,
            "outputEnabled": 1 if self.output_on else 0,
        }


def format_source_number(value: Decimal) -> str:
    """Format a source value with a complete uppercase exponent.

    The trailing execute command is not part of this number. ``S+1E-2E`` is the
    value ``+1E-2`` followed by ``E``.
    """

    if value == 0:
        return "+0E+0"
    sign = "+" if value > 0 else "-"
    magnitude = abs(value)
    exponent = magnitude.adjusted()
    if abs(exponent) > 99:
        raise Yokogawa7651ConfigError(f"Exponent exceeds two digits: {value}")
    mantissa = magnitude.scaleb(-exponent)
    mantissa_text = format(mantissa, "f").rstrip("0").rstrip(".")
    text = f"{sign}{mantissa_text}E{exponent:+d}"
    if _SOURCE_NUMBER.fullmatch(text) is None:
        raise Yokogawa7651ConfigError(f"Cannot format 7651 value {value}")
    return text


def _source_execute(prefix: str, value: Decimal) -> str:
    number = format_source_number(value)
    command = f"{prefix}{number}E"
    if (
        not command.endswith("E")
        or _SOURCE_NUMBER.fullmatch(command[len(prefix) : -1]) is None
    ):
        raise Yokogawa7651ConfigError(f"Source command is missing execute: {command}")
    return command


def _parse_decimal(value: Any, key: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise Yokogawa7651ConfigError(f"Invalid {key}: {value!r}")
    if isinstance(value, str):
        text = value.strip()
        if _NUMERIC_TEXT.fullmatch(text) is None:
            raise Yokogawa7651ConfigError(f"Invalid {key}: {value!r}")
        try:
            parsed = Decimal(text)
        except Exception as exc:
            raise Yokogawa7651ConfigError(f"Invalid {key}: {value!r}") from exc
    elif isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise Yokogawa7651ConfigError(f"Invalid {key}: {value!r}")
        parsed = Decimal(str(number))
    else:
        raise Yokogawa7651ConfigError(f"Invalid {key}: {value!r}")
    if not parsed.is_finite():
        raise Yokogawa7651ConfigError(f"Invalid {key}: {value!r}")
    return parsed


def _quantize(value: Decimal, quantum: Decimal) -> Decimal:
    steps = (value / quantum).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    return (steps * quantum).quantize(quantum)


def _fit_fixed(raw: Decimal, maximum: Decimal, quantum: Decimal, label: str) -> Decimal:
    if abs(raw) > maximum + (quantum / 2):
        raise Yokogawa7651ConfigError(f"{label} {raw} exceeds ±{maximum}")
    quantized = _quantize(raw, quantum)
    if abs(quantized) > maximum:
        raise Yokogawa7651ConfigError(
            f"Quantized {label} {quantized} exceeds ±{maximum}"
        )
    return quantized


def _fit_auto(
    raw: Decimal, ranges: dict[int, tuple[Decimal, Decimal]], label: str
) -> Decimal:
    ordered = sorted(ranges.items(), key=lambda item: item[1][0])
    for _code, (maximum, quantum) in ordered:
        if abs(raw) <= maximum + (quantum / 2):
            quantized = _quantize(raw, quantum)
            if abs(quantized) <= maximum:
                return quantized
    raise Yokogawa7651ConfigError(f"{label} {raw} exceeds the largest 7651 range")


def _fit_voltage_limit(raw: Decimal) -> int:
    volts = raw.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    if abs(raw - volts) > Decimal("1E-9") or volts < 1 or volts > 30:
        raise Yokogawa7651ConfigError(
            f"Voltage limit must be an integer from 1 to 30 V: {raw}"
        )
    return int(volts)


def _fit_current_limit_ma(raw: Decimal) -> int:
    milli = (raw * Decimal("1000")).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    if abs(raw - (milli / Decimal("1000"))) > Decimal("1E-9"):
        raise Yokogawa7651ConfigError(
            f"Current limit must be an integer number of milliamps: {raw} A"
        )
    if milli < 5 or milli > 120:
        raise Yokogawa7651ConfigError(
            f"Current limit must be from 5 to 120 mA: {raw} A"
        )
    return int(milli)


def _choice(raw: Any, key: str, allowed: set[Any]) -> Any:
    if isinstance(raw, bool):
        raise Yokogawa7651ConfigError(f"Unsupported {key}: {raw!r}")
    if isinstance(raw, str):
        text = raw.strip()
        if text in allowed:
            return text
        raise Yokogawa7651ConfigError(f"Unsupported {key}: {raw!r}")
    try:
        parsed = _parse_decimal(raw, key)
    except Yokogawa7651ConfigError:
        raise Yokogawa7651ConfigError(f"Unsupported {key}: {raw!r}") from None
    for item in allowed:
        if not isinstance(item, str) and parsed == Decimal(str(item)):
            return item
    raise Yokogawa7651ConfigError(f"Unsupported {key}: {raw!r}")


class Yokogawa7651Instrument(BaseInstrument):
    """One 7651 in manual source mode. See the module docstring."""

    displayName = "YOKOGAWA 7651 Programmable DC Source"

    @classmethod
    def getSupportedTransportKeys(cls) -> list[str]:
        return ["visa"]

    @classmethod
    def getDefaultTransportKey(cls) -> str:
        return "visa"

    @classmethod
    def getDefaultAddress(cls) -> str:
        # A-1: the address is retained across power-off and has no published
        # factory number. "Adr 01" on p. 6-2 is the setup display, and
        # "Adr 03" is an example. Callers must set the real VISA resource,
        # for example GPIB0::1::INSTR.
        return ""

    @classmethod
    def getDefaultTransportSettings(cls) -> dict[str, Any]:
        # LF is an accepted program terminator (p. 6-4). DL0 makes GP-IB
        # replies end in CR/LF/EOI. 10000 ms is an engineering margin for one
        # setting or a five-line OS reply, not a hardware specification.
        return {
            "visaTimeoutMs": 10000,
            "writeTermination": "\n",
            "readTermination": "\n",
            "visaSendEndOnWrite": True,
            "visaClearOnOpen": False,
        }

    @classmethod
    def getAvailableMeasurementSignals(cls) -> list[tuple[str, str]]:
        return [
            (
                "sourceVoltageSettingV",
                "Instrument-reported voltage setting (V)",
            ),
            (
                "sourceCurrentSettingA",
                "Instrument-reported current setting (A)",
            ),
            ("outputEnabled", "Instrument-reported output on (1) or off (0)"),
            ("outputUnstable", "Instrument-reported output not stable (1)"),
            (
                "communicationError",
                "Instrument-reported previous command error (1)",
            ),
        ]

    @classmethod
    def getJobConfigFields(cls) -> list[ConfigFieldDefinition]:
        return [
            ConfigFieldDefinition(
                key="forceMode",
                label="Source mode",
                fieldType="enum",
                default="VOLT",
                choices=[
                    ConfigChoice("VOLT", "Voltage source (F1)"),
                    ConfigChoice("CURR", "Current source (F5)"),
                ],
                sweepable=False,
                helpText=(
                    "F1 voltage source or F5 current source (p. 6-22). The "
                    "inactive source value is stored and is not programmed. "
                    "Changing function while the output is on turns the output "
                    "off, programs the new function, then restores the output "
                    "if it should remain on. Initial function in Appendix A-1 "
                    "is the 1 V range."
                ),
            ),
            ConfigFieldDefinition(
                key="forceVoltageLevelV",
                label="Voltage setting (V)",
                fieldType="float",
                default=0.0,
                minValue=-32.0,
                maxValue=32.0,
                stepValue=1e-7,
                sweepable=True,
                helpText=(
                    "S<volts>E on a fixed voltage range, or SA<volts>E when "
                    "voltage auto-range is on. Executed only in VOLT mode. "
                    "Fixed-range maxima are ±0.012/0.12/1.2/12/32 V. A value "
                    "of 0 is the Appendix A-1 output data. This is a source "
                    "setting, not a measured terminal voltage."
                ),
            ),
            ConfigFieldDefinition(
                key="forceCurrentLevelA",
                label="Current setting (A)",
                fieldType="float",
                default=0.0,
                minValue=-0.12,
                maxValue=0.12,
                stepValue=1e-8,
                sweepable=True,
                helpText=(
                    "S<amps>E on a fixed current range, or SA<amps>E when "
                    "current auto-range is on. Executed only in CURR mode. "
                    "Fixed-range maxima are ±0.0012/0.012/0.12 A. Zero is the "
                    "driver default for the inactive current setting."
                ),
            ),
            ConfigFieldDefinition(
                key="voltageRangeIndex",
                label="Fixed voltage range",
                fieldType="enum",
                default=4,
                minValue=2,
                maxValue=6,
                choices=[
                    ConfigChoice(2, "10 mV (R2)"),
                    ConfigChoice(3, "100 mV (R3)"),
                    ConfigChoice(4, "1 V (R4)"),
                    ConfigChoice(5, "10 V (R5)"),
                    ConfigChoice(6, "30 V (R6)"),
                ],
                sweepable=False,
                helpText=(
                    "R2 to R6 while voltage auto-range is off. There is no R0. "
                    "R4 is the Appendix A-1 initial range. R2 and R3 do not "
                    "operate the current limiter (p. 4-11). Ignored while "
                    "voltage auto-range is on."
                ),
            ),
            ConfigFieldDefinition(
                key="currentRangeIndex",
                label="Fixed current range",
                fieldType="enum",
                default=4,
                minValue=4,
                maxValue=6,
                choices=[
                    ConfigChoice(4, "1 mA (R4)"),
                    ConfigChoice(5, "10 mA (R5)"),
                    ConfigChoice(6, "100 mA (R6)"),
                ],
                sweepable=False,
                helpText=(
                    "R4, R5, or R6 while current auto-range is off. R4 is a "
                    "driver-chosen default; Appendix A-1 lists the initial "
                    "function as 1 V and does not name a current range. "
                    "Ignored while current auto-range is on."
                ),
            ),
            ConfigFieldDefinition(
                key="voltageRangeAuto",
                label="Voltage auto-range",
                fieldType="enum",
                default=0,
                minValue=0,
                maxValue=1,
                choices=[
                    ConfigChoice(0, "Fixed range (S)"),
                    ConfigChoice(1, "Auto-range (SA)"),
                ],
                sweepable=False,
                helpText=(
                    "Off sends S on voltageRangeIndex. On sends SA and lets "
                    "the instrument select the range, which can be 10 mV or "
                    "100 mV where the programmed current limit does not "
                    "operate. Off is the driver default."
                ),
            ),
            ConfigFieldDefinition(
                key="currentRangeAuto",
                label="Current auto-range",
                fieldType="enum",
                default=0,
                minValue=0,
                maxValue=1,
                choices=[
                    ConfigChoice(0, "Fixed range (S)"),
                    ConfigChoice(1, "Auto-range (SA)"),
                ],
                sweepable=False,
                helpText=(
                    "Off sends S on currentRangeIndex. On sends SA. Off is "
                    "the driver default."
                ),
            ),
            ConfigFieldDefinition(
                key="voltageLimitV",
                label="Voltage limit (V)",
                fieldType="enum",
                default=1,
                minValue=1,
                maxValue=30,
                stepValue=1,
                choices=[ConfigChoice(value, f"{value} V") for value in range(1, 31)],
                sweepable=False,
                helpText=(
                    "LV<integer volts>, sent only in current-source mode. "
                    "1 to 30 V in 1 V steps (p. 6-31). Sending LV in voltage "
                    "mode is error 11. Appendix A-1 initial value is 30 V; "
                    "1 V is a driver-chosen conservative default. The limiter "
                    "is not the hardware trip and is not critical ramping."
                ),
            ),
            ConfigFieldDefinition(
                key="currentLimitA",
                label="Current limit (A)",
                fieldType="float",
                default=0.005,
                minValue=0.005,
                maxValue=0.12,
                stepValue=0.001,
                sweepable=False,
                helpText=(
                    "LA<integer milliamps>, sent only in voltage-source mode. "
                    "0.005 A is LA5 and 0.12 A is LA120. Values must be exact "
                    "1 mA steps from 5 to 120 mA; LA0.005 is illegal. "
                    "Appendix A-1 initial value is 120 mA; 5 mA is a "
                    "driver-chosen conservative default. It does not operate "
                    "on the 10 mV or 100 mV ranges and cannot be set in µA or nA."
                ),
            ),
            ConfigFieldDefinition(
                key="outputEnabled",
                label="Output",
                fieldType="enum",
                default=0,
                minValue=0,
                maxValue=1,
                choices=[ConfigChoice(0, "Off"), ConfigChoice(1, "On")],
                sweepable=False,
                helpText=(
                    "O0E or O1E. The output change is not executed by O0 or O1 "
                    "alone. Off is the Appendix A-1 initial state. Off is not "
                    "an open circuit, and a source setting of zero is not off. "
                    "Changing a setting while off does not send O1."
                ),
            ),
        ]

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
        self._confirmed: _Executed | None = None
        self._session_ready = False
        self._needs_new_session = False
        self._lock = threading.Lock()

    def initialize(self) -> None:
        """Stop a running program, force output off, then apply the job.

        No RC, calibration, or device clear. The main window does not call
        this; ``applyConfigValue`` performs the same preparation on demand.
        """

        with self._lock:
            self._require_aligned()
            _job, executed = self._normalize(self.jobConfig)
            self._establish(executed)
            self.jobConfig = executed.as_job()

    def finalizeInitialization(self, should_stop: Any = None) -> bool:
        """Prepare the GP-IB session if no config write has done so.

        Does not change the source or the output. Stability after a sweep
        step is the job settle time; this driver does not poll OC, and an OD
        value equal to the target does not prove the terminals have settled.
        """

        if should_stop is not None and should_stop():
            return False
        with self._lock:
            self._require_aligned()
            self._ensure_session()
        return True

    def applyConfigValue(self, key: str, value: float | int | str) -> None:
        if key not in {field.key for field in self.getJobConfigFields()}:
            return
        with self._lock:
            self._require_aligned()
            proposed = dict(self.jobConfig)
            proposed[key] = value
            _job, executed = self._normalize(proposed)
            if self._confirmed is None:
                self._establish(executed)
            elif _structure_changed(self._confirmed, executed):
                self._reconfigure(executed)
            elif key == _active_source_key(executed.mode):
                self._ensure_session()
                self._write(_source_only(executed))
                self._confirmed = executed
            elif key == _active_limit_key(executed.mode):
                self._ensure_session()
                self._write(_limiter_command(executed))
                self._confirmed = executed
            elif key == "outputEnabled":
                self._ensure_session()
                self._write("O1E" if executed.output_on else "O0E")
                self._confirmed = executed
            self.jobConfig = executed.as_job()

    def readMeasurements(self, signalKeys: list[str]) -> dict[str, float]:
        requested: list[str] = []
        seen: set[str] = set()
        for key in signalKeys:
            if key in seen:
                continue
            seen.add(key)
            requested.append(key)
        mode = str(self.jobConfig.get("forceMode", "VOLT"))
        want_voltage = "sourceVoltageSettingV" in requested and mode == "VOLT"
        want_current = "sourceCurrentSettingA" in requested and mode == "CURR"
        status_keys = [key for key in requested if key in _STATUS_KEYS]
        if not want_voltage and not want_current and not status_keys:
            return {}
        with self._lock:
            self._require_aligned()
            self._ensure_session()
            values: dict[str, float] = {}
            if want_voltage or want_current:
                self._write("OD")
                raw = self._read()
                kind = "V" if want_voltage else "A"
                signal = (
                    "sourceVoltageSettingV" if want_voltage else "sourceCurrentSettingA"
                )
                values[signal] = _parse_od(raw, kind, signal)
            if status_keys:
                self._write("OC")
                flags = _parse_oc(self._read())
                for key in status_keys:
                    values[key] = flags[key]
            return values

    def readPanelSettings(self) -> list[str]:
        """Read the five-line OS reply through the END line.

        A missing END marks the session unsynchronized. Later source and
        measurement calls fail until the instrument object is replaced;
        leftover lines are not drained and device clear is not used.
        """

        with self._lock:
            self._require_aligned()
            self._ensure_session()
            self._write("OS")
            lines: list[str] = []
            try:
                for _line_index in range(_OS_MAX_LINES):
                    line = self._read().strip()
                    lines.append(line)
                    if line == "END":
                        return lines
            except Exception as exc:
                self._mark_unsynchronized()
                raise Yokogawa7651ResponseError(
                    f"OS response ended before END: {lines!r}"
                ) from exc
            self._mark_unsynchronized()
            raise Yokogawa7651ResponseError(
                "OS response did not end with END within "
                f"{_OS_MAX_LINES} lines: {lines!r}"
            )

    def applySafeState(self) -> None:
        """Turn the output off unless safeConfig explicitly keeps a valid bias.

        An empty safe config does not honor ``jobConfig.outputEnabled``. An
        invalid explicit bias is not applied; ``RU0`` and ``O0E`` are still
        attempted, and any failure is reported after those attempts. ``O0``
        without ``E`` is not used. The output is not turned back on after a
        failure, and the source is not forced to zero as a substitute for off.
        """

        with self._lock:
            target, invalid = self._safe_target()
            errors: list[str] = []
            if target is None:
                self._shutdown(errors)
                if invalid is not None:
                    errors.append(str(invalid))
                if errors:
                    raise Yokogawa7651SafeStateError(
                        "YOKOGAWA 7651 safe state incomplete: " + "; ".join(errors),
                        errors,
                    )
                return
            try:
                self._establish(target)
                self.jobConfig = target.as_job()
            except Exception as exc:
                errors.append(str(exc))
                self._shutdown(errors)
                raise Yokogawa7651SafeStateError(
                    "YOKOGAWA 7651 safe state incomplete: " + "; ".join(errors),
                    errors,
                ) from exc

    def _safe_target(self) -> tuple[_Executed | None, Exception | None]:
        raw_safe = self.metadata.get("safeConfig", {})
        safe = dict(raw_safe) if isinstance(raw_safe, dict) else {}
        if not safe:
            return None, None
        merged = dict(self.jobConfig)
        explicit = {str(key) for key in safe}
        merged.update(safe)
        if "outputEnabled" not in explicit:
            merged["outputEnabled"] = 0
        try:
            _job, executed = self._normalize(merged)
        except Yokogawa7651ConfigError as exc:
            return None, exc
        return executed, None

    def _shutdown(self, errors: list[str]) -> None:
        for command in ("RU0", "O0E"):
            try:
                self._write(command)
            except Exception as exc:
                errors.append(f"{command}: {exc}")
                continue
            if command == "O0E":
                self.jobConfig["outputEnabled"] = 0
                if self._confirmed is not None:
                    self._confirmed = replace(self._confirmed, output_on=False)

    def _establish(self, executed: _Executed) -> None:
        self._ensure_session()
        self._write("O0E")
        self._write(_function_range_source(executed))
        self._write(_limiter_command(executed))
        if executed.output_on:
            self._write("O1E")
        self._confirmed = executed

    def _reconfigure(self, executed: _Executed) -> None:
        self._ensure_session()
        if self._confirmed is not None and self._confirmed.output_on:
            self._write("O0E")
        self._write(_function_range_source(executed))
        self._write(_limiter_command(executed))
        if executed.output_on:
            self._write("O1E")
        self._confirmed = executed

    def _ensure_session(self) -> None:
        if self._session_ready:
            return
        self._write("RU0")
        self._write("H1")
        self._write("DL0")
        self._session_ready = True

    def _normalize(self, config: dict[str, Any]) -> tuple[dict[str, Any], _Executed]:
        mode = str(
            _choice(config.get("forceMode", "VOLT"), "forceMode", {"VOLT", "CURR"})
        )
        voltage_range = int(
            _choice(
                config.get("voltageRangeIndex", 4),
                "voltageRangeIndex",
                set(_VOLTAGE_RANGES),
            )
        )
        current_range = int(
            _choice(
                config.get("currentRangeIndex", 4),
                "currentRangeIndex",
                set(_CURRENT_RANGES),
            )
        )
        voltage_auto = int(
            _choice(config.get("voltageRangeAuto", 0), "voltageRangeAuto", {0, 1})
        )
        current_auto = int(
            _choice(config.get("currentRangeAuto", 0), "currentRangeAuto", {0, 1})
        )
        output = int(_choice(config.get("outputEnabled", 0), "outputEnabled", {0, 1}))
        voltage = _fit_level(
            _parse_decimal(config.get("forceVoltageLevelV", 0), "forceVoltageLevelV"),
            _VOLTAGE_RANGES,
            voltage_range,
            bool(voltage_auto),
            "Voltage",
        )
        current = _fit_level(
            _parse_decimal(config.get("forceCurrentLevelA", 0), "forceCurrentLevelA"),
            _CURRENT_RANGES,
            current_range,
            bool(current_auto),
            "Current",
        )
        voltage_limit = _fit_voltage_limit(
            _parse_decimal(config.get("voltageLimitV", 1), "voltageLimitV")
        )
        current_limit_ma = _fit_current_limit_ma(
            _parse_decimal(
                config.get("currentLimitA", Decimal("0.005")), "currentLimitA"
            )
        )
        executed = _Executed(
            mode=mode,
            voltage_range=voltage_range,
            current_range=current_range,
            voltage_auto=voltage_auto,
            current_auto=current_auto,
            voltage=voltage,
            current=current,
            voltage_limit=voltage_limit,
            current_limit_ma=current_limit_ma,
            output_on=bool(output),
        )
        return executed.as_job(), executed

    def _write(self, command: str) -> None:
        if len(command) > _MAX_COMMAND_CHARS:
            raise Yokogawa7651ConfigError(
                f"7651 command exceeds {_MAX_COMMAND_CHARS} characters: {command!r}"
            )
        try:
            self.transport.write(command)
        except Exception:
            self._confirmed = None
            self._session_ready = False
            raise

    def _read(self) -> str:
        try:
            return self.transport.read()
        except Exception:
            self._confirmed = None
            self._session_ready = False
            raise

    def _require_aligned(self) -> None:
        if self._needs_new_session:
            raise Yokogawa7651ResponseError(
                "YOKOGAWA 7651 response stream is unsynchronized. "
                "Close and reopen the VISA session. Device clear is not used."
            )

    def _mark_unsynchronized(self) -> None:
        self._needs_new_session = True
        self._confirmed = None
        self._session_ready = False


def _fit_level(
    raw: Decimal,
    ranges: dict[int, tuple[Decimal, Decimal]],
    range_index: int,
    auto: bool,
    label: str,
) -> Decimal:
    if auto:
        return _fit_auto(raw, ranges, label)
    maximum, quantum = ranges[range_index]
    return _fit_fixed(raw, maximum, quantum, label)


def _active_source_key(mode: str) -> str:
    return "forceVoltageLevelV" if mode == "VOLT" else "forceCurrentLevelA"


def _active_limit_key(mode: str) -> str:
    return "currentLimitA" if mode == "VOLT" else "voltageLimitV"


def _structure_changed(old: _Executed, new: _Executed) -> bool:
    if old.mode != new.mode:
        return True
    if new.mode == "VOLT":
        if old.voltage_auto != new.voltage_auto:
            return True
        return not new.voltage_auto and old.voltage_range != new.voltage_range
    if old.current_auto != new.current_auto:
        return True
    return not new.current_auto and old.current_range != new.current_range


def _active_value(executed: _Executed) -> Decimal:
    return executed.voltage if executed.mode == "VOLT" else executed.current


def _active_auto(executed: _Executed) -> bool:
    if executed.mode == "VOLT":
        return bool(executed.voltage_auto)
    return bool(executed.current_auto)


def _source_only(executed: _Executed) -> str:
    prefix = "SA" if _active_auto(executed) else "S"
    return _source_execute(prefix, _active_value(executed))


def _function_range_source(executed: _Executed) -> str:
    function = "F1" if executed.mode == "VOLT" else "F5"
    number = format_source_number(_active_value(executed))
    if _active_auto(executed):
        command = f"{function}SA{number}E"
    else:
        code = (
            executed.voltage_range
            if executed.mode == "VOLT"
            else executed.current_range
        )
        command = f"{function}R{code}S{number}E"
    if len(command) > _MAX_COMMAND_CHARS or not command.endswith("E"):
        raise Yokogawa7651ConfigError(f"Invalid 7651 source transaction: {command}")
    return command


def _limiter_command(executed: _Executed) -> str:
    if executed.mode == "VOLT":
        return f"LA{executed.current_limit_ma}"
    return f"LV{executed.voltage_limit}"


def _parse_od(raw: str, kind: str, signal_key: str) -> float:
    text = raw.strip()
    match = _OD_READING.fullmatch(text)
    if match is None:
        raise Yokogawa7651ResponseError(
            f"Malformed YOKOGAWA 7651 setting for {signal_key}: {raw!r}"
        )
    status = match.group("status")
    got_kind = match.group("kind")
    try:
        value = float(match.group("number"))
    except ValueError as exc:
        raise Yokogawa7651ResponseError(
            f"Malformed YOKOGAWA 7651 setting for {signal_key}: {raw!r}"
        ) from exc
    if got_kind != kind:
        raise Yokogawa7651ResponseError(
            f"YOKOGAWA 7651 setting for {signal_key} was {got_kind}, expected {kind}: {raw!r}"
        )
    if status == "E":
        parsed = value if math.isfinite(value) else None
        raise Yokogawa7651OverloadError(raw, parsed)
    if not math.isfinite(value):
        raise Yokogawa7651ResponseError(
            f"Non-finite YOKOGAWA 7651 setting for {signal_key}: {raw!r}"
        )
    return value


def _parse_oc(raw: str) -> dict[str, float]:
    text = raw.strip()
    match = _OC_READING.fullmatch(text)
    if match is None:
        raise Yokogawa7651ResponseError(f"Malformed YOKOGAWA 7651 status: {raw!r}")
    code = int(match.group("code"))
    if code > 255:
        raise Yokogawa7651ResponseError(
            f"YOKOGAWA 7651 status is outside 0..255: {raw!r}"
        )
    return {
        "outputEnabled": 1.0 if code & _OC_OUTPUT_ON else 0.0,
        "outputUnstable": 1.0 if code & _OC_OUTPUT_UNSTABLE else 0.0,
        "communicationError": 1.0 if code & _OC_COMMAND_ERROR else 0.0,
    }


def _yokogawa7651Factory(
    name: str,
    transport: BaseTransport,
    metadata: dict[str, Any] | None = None,
    jobConfig: dict[str, Any] | None = None,
) -> BaseInstrument:
    return Yokogawa7651Instrument(
        name=name,
        transport=transport,
        metadata=metadata,
        jobConfig=jobConfig,
    )


def registerInstruments(registry: InstrumentRegistry) -> None:
    registry.registerInstrument(
        key=INSTRUMENT_TYPE_KEY,
        instrumentType=Yokogawa7651Instrument,
        factory=_yokogawa7651Factory,
    )
