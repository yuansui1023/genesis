from __future__ import annotations

import math
from typing import Any

from genesis.core.instrument.base_instrument import BaseInstrument
from genesis.core.instrument.config_field import ConfigChoice, ConfigFieldDefinition
from genesis.core.instrument.registry import InstrumentRegistry
from genesis.core.transport.base_transport import BaseTransport

INSTRUMENT_TYPE_KEY = "agilent34401a"


class Agilent34401AInstrument(BaseInstrument):
    """Agilent/HP 34401A 6½ digit DMM, short-form SCPI over VISA.

    Reference: Keysight 34401-90004, Edition 10 (August 2014), printed
    pp. 17-18, 51-61, 73-81, 101, 105-123, 130-133 and 159-160:
    https://www.keysight.com.cn/cn/zh/assets/9018-01063/user-manuals/9018-01063.pdf

    Measurement semantics (a): only the requested signal for measurementFunction
    is returned. Other keys are silently ignored, without I/O. Reads never change
    function or configuration. IMM/EXT use READ?; BUS uses INIT, *TRG, FETC?
    (at most 512 buffered samples). EXT requires an external trigger. A complete
    batch becomes one arithmetic mean; malformed, incomplete, nonfinite or
    overload (9.9e37) batches are omitted. Optional trailing commas are accepted.

    CONF:<function> presets related settings, so initialize and explicit function
    changes replay the active configuration in a fixed order. No *RST, *CLS,
    self-test, calibration or asynchronous work occurs. Inactive fields are
    validated and retained for a subsequent explicit function change. All config
    fields are non-sweepable: this instrument has no physical output setpoint.

    The 120000 ms VISA timeout is an engineering budget: at 50 Hz, 100 NPLC
    takes 2 s per integration; budget 3 integrations (including autozero and
    overhead) plus 1 s settling per sample, for 10 samples = 70 s, with 50 s
    margin. This is not a bound for every allowed configuration. Larger batches,
    long manual delays, slow AC filters or external-trigger waits can require a
    larger per-job transportSettings.visaTimeoutMs. Delay applies to EACH sample.

    Safe state overlays metadata['safeConfig'] on jobConfig, falling back to
    jobConfig. RES/FRES/CONT/DIOD inject current and are replaced with autoranged
    DCV before any commands are sent. This removes intentional excitation at the
    voltage terminals; it does not disconnect leads or the current-input shunt.
    After a timed-out acquisition the manual requires a VISA device clear before
    configuration can be accepted. BaseTransport has no clear hook. Reopening
    VISA with visaClearOnOpen enabled attempts a clear, but the transport ignores
    clear failures. Confirm recovery (or use a supported controller device clear)
    before reinitializing. This method cannot promise to interrupt a pending
    external trigger or acquisition.
    """

    displayName = "Agilent/HP 34401A 6½ Digit Multimeter"

    _SIGNALS: dict[str, tuple[str, str]] = {
        "VOLT:DC": ("voltageDcV", "DC voltage (V)"),
        "VOLT:AC": ("voltageAcV", "AC voltage (V RMS)"),
        "CURR:DC": ("currentDcA", "DC current (A)"),
        "CURR:AC": ("currentAcA", "AC current (A RMS)"),
        "RES": ("resistanceOhm", "2-wire resistance (ohm)"),
        "FRES": ("resistance4WireOhm", "4-wire resistance (ohm)"),
        "FREQ": ("frequencyHz", "Frequency (Hz)"),
        "PER": ("periodSeconds", "Period (s)"),
        "CONT": ("continuityOhm", "Continuity (ohm)"),
        "DIOD": ("diodeVoltageV", "Diode voltage (V)"),
    }
    _RANGE_KEY_BY_FUNCTION: dict[str, str] = {
        "VOLT:DC": "voltageDcRangeV",
        "VOLT:AC": "voltageAcRangeV",
        "CURR:DC": "currentDcRangeA",
        "CURR:AC": "currentAcRangeA",
        "RES": "resistanceRangeOhm",
        "FRES": "resistanceRangeOhm",
        "FREQ": "frequencyVoltageRangeV",
        "PER": "frequencyVoltageRangeV",
    }
    _RANGES: dict[str, tuple[str, tuple[float, ...], str]] = {
        "voltageDcRangeV": (
            "DC voltage range (V)",
            (0.1, 1.0, 10.0, 100.0, 1000.0),
            "VOLT:DC:RANG",
        ),
        "voltageAcRangeV": (
            "AC voltage range (V RMS)",
            (0.1, 1.0, 10.0, 100.0, 750.0),
            "VOLT:AC:RANG",
        ),
        "currentDcRangeA": (
            "DC current range (A)",
            (0.01, 0.1, 1.0, 3.0),
            "CURR:DC:RANG",
        ),
        "currentAcRangeA": (
            "AC current range (A RMS)",
            (1.0, 3.0),
            "CURR:AC:RANG",
        ),
        "resistanceRangeOhm": (
            "2/4-wire resistance range (ohm)",
            (100.0, 1000.0, 1e4, 1e5, 1e6, 1e7, 1e8),
            "RES:RANG / FRES:RANG",
        ),
        "frequencyVoltageRangeV": (
            "Frequency/period input range (V RMS)",
            (0.1, 1.0, 10.0, 100.0, 750.0),
            "FREQ:VOLT:RANG / PER:VOLT:RANG",
        ),
    }
    _NPLC_FUNCTIONS = frozenset({"VOLT:DC", "CURR:DC", "RES", "FRES"})
    _AUTOZERO_FUNCTIONS = frozenset({"VOLT:DC", "CURR:DC", "RES"})
    _EXCITATION_FUNCTIONS = frozenset({"RES", "FRES", "CONT", "DIOD"})
    _APPLY_ORDER = (
        "rangeAuto",
        "nplc",
        "acBandwidthHz",
        "apertureSeconds",
        "autoZero",
        "inputImpedanceAuto",
        "triggerSource",
        "triggerDelayAuto",
        "sampleCount",
        "displayEnabled",
    )

    @classmethod
    def getSupportedTransportKeys(cls) -> list[str]:
        return ["visa"]

    @classmethod
    def getDefaultAddress(cls) -> str:
        # Factory GPIB address: manual pp. 91, 101.
        return "GPIB0::22::INSTR"

    @classmethod
    def getDefaultTransportSettings(cls) -> dict[str, Any]:
        return {"visaTimeoutMs": 120000}

    @classmethod
    def getAvailableMeasurementSignals(cls) -> list[tuple[str, str]]:
        return list(cls._SIGNALS.values())

    @classmethod
    def getJobConfigFields(cls) -> list[ConfigFieldDefinition]:
        fields = [
            ConfigFieldDefinition(
                key="measurementFunction",
                label="Measurement function",
                fieldType="enum",
                default="VOLT:DC",
                choices=[
                    ConfigChoice(key, label) for key, (_, label) in cls._SIGNALS.items()
                ],
                sweepable=False,
                helpText=(
                    "CONF:<function>; factory DCV. Reapplies dependent settings. "
                    "Only this function's signal is read. RES/FRES/CONT/DIOD inject "
                    "current; safe state substitutes DCV. CONT uses fixed 1 kohm, "
                    "DIOD fixed 1 V/1 mA; both fixed 5½ digits. FREQ/PER: 3 Hz "
                    "to 300 kHz input. DC ratio is not exposed."
                ),
            ),
            ConfigFieldDefinition(
                key="rangeAuto",
                label="Automatic range",
                fieldType="enum",
                default=1,
                minValue=0,
                maxValue=1,
                choices=[ConfigChoice(1, "On"), ConfigChoice(0, "Off")],
                sweepable=False,
                helpText=(
                    "<function>:RANG:AUTO ON|OFF; factory ON. FREQ/PER use "
                    "<function>:VOLT:RANG:AUTO (input volts, not Hz/s). OFF also "
                    "applies that function's manual range. Ignored for CONT/DIOD."
                ),
            ),
        ]
        for key, (label, ranges, command) in cls._RANGES.items():
            fields.append(
                ConfigFieldDefinition(
                    key=key,
                    label=label,
                    fieldType="enum",
                    default=ranges[-1],
                    minValue=ranges[0],
                    maxValue=ranges[-1],
                    choices=[ConfigChoice(value, f"{value:g}") for value in ranges],
                    sweepable=False,
                    helpText=(
                        f"{command} <range>; nominal hardware ranges only. Used "
                        "only for the matching function with rangeAuto OFF. "
                        "Factory ranging is automatic; the dormant manual default "
                        "is the highest range to reduce overload on disabling auto. "
                        "This fallback is a driver choice, not a factory fixed range."
                    ),
                )
            )
        fields.extend(
            [
                ConfigFieldDefinition(
                    key="nplc",
                    label="DC/resistance integration (NPLC)",
                    fieldType="enum",
                    default=10.0,
                    minValue=0.02,
                    maxValue=100.0,
                    choices=[
                        ConfigChoice(n, f"{n:g} PLC")
                        for n in (0.02, 0.2, 1.0, 10.0, 100.0)
                    ],
                    sweepable=False,
                    helpText=(
                        "VOLT:DC:NPLC / CURR:DC:NPLC / RES:NPLC / FRES:NPLC. "
                        "Only 0.02, 0.2, 1, 10, 100 PLC; factory integration 10. "
                        "Remote NPLC 10/100 selects 6½ digits; factory display is "
                        "5½ slow at 10 PLC. Inactive for AC/FREQ/PER/CONT/DIOD. "
                        "Default VISA timeout 120 s budgets 10 samples at 100 PLC "
                        "and 50 Hz with autozero/settling margin; increase "
                        "transportSettings.visaTimeoutMs for longer batches/delays."
                    ),
                ),
                ConfigFieldDefinition(
                    key="autoZero",
                    label="Automatic zero",
                    fieldType="enum",
                    default="ON",
                    choices=[ConfigChoice("ON", "On"), ConfigChoice("OFF", "Off")],
                    sweepable=False,
                    helpText=(
                        "ZERO:AUTO ON|OFF; factory ON. Applies to DCV/DCI/RES; "
                        "FRES forces ON in hardware, other functions ignore it. "
                        "OFF still zeros on entering wait-for-trigger. ONCE is "
                        "excluded because it immediately initiates a conversion."
                    ),
                ),
                ConfigFieldDefinition(
                    key="inputImpedanceAuto",
                    label="Automatic DCV input impedance",
                    fieldType="enum",
                    default=0,
                    minValue=0,
                    maxValue=1,
                    choices=[
                        ConfigChoice(0, "Fixed 10 Mohm"),
                        ConfigChoice(1, "Automatic high impedance"),
                    ],
                    sweepable=False,
                    helpText=(
                        "INP:IMP:AUTO OFF|ON; factory OFF (10 Mohm). DCV only. "
                        "ON gives >10 Gohm on 0.1/1/10 V ranges; higher ranges "
                        "remain 10 Mohm. Reapplied after CONF."
                    ),
                ),
                ConfigFieldDefinition(
                    key="acBandwidthHz",
                    label="AC filter minimum frequency (Hz)",
                    fieldType="enum",
                    default=20,
                    minValue=3,
                    maxValue=200,
                    choices=[
                        ConfigChoice(3, "3 Hz (slow)"),
                        ConfigChoice(20, "20 Hz (medium)"),
                        ConfigChoice(200, "200 Hz (fast)"),
                    ],
                    sweepable=False,
                    helpText=(
                        "DET:BAND 3|20|200; factory 20 Hz. ACV/ACI only; AC "
                        "measurement resolution is fixed at 6½ digits. Automatic "
                        "trigger delays are 7/1/0.6 s respectively."
                    ),
                ),
                ConfigFieldDefinition(
                    key="apertureSeconds",
                    label="Frequency/period gate time (s)",
                    fieldType="enum",
                    default=0.1,
                    minValue=0.01,
                    maxValue=1.0,
                    choices=[
                        ConfigChoice(0.01, "0.01 s (4½ digits)"),
                        ConfigChoice(0.1, "0.1 s (5½ digits)"),
                        ConfigChoice(1.0, "1 s (6½ digits)"),
                    ],
                    sweepable=False,
                    helpText="FREQ:APER / PER:APER 0.01|0.1|1; factory 0.1 s. FREQ/PER only.",
                ),
                ConfigFieldDefinition(
                    key="triggerSource",
                    label="Trigger source",
                    fieldType="enum",
                    default="IMM",
                    choices=[
                        ConfigChoice("IMM", "Immediate"),
                        ConfigChoice("EXT", "External"),
                        ConfigChoice("BUS", "Software bus trigger"),
                    ],
                    sweepable=False,
                    helpText=(
                        "TRIG:SOUR IMM|EXT|BUS; factory remote source IMM. "
                        "EXT waits for a rear-panel trigger after READ?. BUS uses "
                        "INIT, *TRG, FETC? and requires sampleCount <= 512. "
                        "CONF presets TRIG:COUN 1; each read uses one trigger."
                    ),
                ),
                ConfigFieldDefinition(
                    key="triggerDelayAuto",
                    label="Automatic trigger delay",
                    fieldType="enum",
                    default=1,
                    minValue=0,
                    maxValue=1,
                    choices=[ConfigChoice(1, "On"), ConfigChoice(0, "Off")],
                    sweepable=False,
                    helpText=(
                        "TRIG:DEL:AUTO ON|OFF; factory ON. Automatic delay "
                        "depends on function/range/integration/AC filter. OFF "
                        "also applies triggerDelaySeconds."
                    ),
                ),
                ConfigFieldDefinition(
                    key="triggerDelaySeconds",
                    label="Manual trigger delay (s)",
                    fieldType="float",
                    default=0.0,
                    minValue=0.0,
                    maxValue=3600.0,
                    stepValue=0.001,
                    sweepable=False,
                    helpText=(
                        "TRIG:DEL <seconds>; 0..3600 s before EACH sample. "
                        "Only sent when triggerDelayAuto OFF (TRIG:DEL disables "
                        "AUTO). Factory delay is automatic; dormant manual zero "
                        "is a driver choice to avoid an unexpected long wait. "
                        "Increase VISA timeout to cover the full batch delay."
                    ),
                ),
                ConfigFieldDefinition(
                    key="sampleCount",
                    label="Samples per reading",
                    fieldType="int",
                    default=1,
                    minValue=1,
                    maxValue=50000,
                    stepValue=1,
                    sweepable=False,
                    helpText=(
                        "SAMP:COUN <count>; factory 1, hardware 1..50000. BUS "
                        "is limited to 512 by INIT memory. IMM/EXT use READ? "
                        "streaming. Returns the batch mean only if every sample "
                        "is valid. Larger counts may require a longer VISA timeout."
                    ),
                ),
                ConfigFieldDefinition(
                    key="displayEnabled",
                    label="Front-panel display",
                    fieldType="enum",
                    default=1,
                    minValue=0,
                    maxValue=1,
                    choices=[ConfigChoice(1, "On"), ConfigChoice(0, "Off")],
                    sweepable=False,
                    helpText="DISP ON|OFF; factory ON. OFF can improve measurement throughput.",
                ),
            ]
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
        self.jobConfig: dict[str, Any] = self.getDefaultJobConfig()
        self.jobConfig.update(jobConfig or {})
        self._fields = {field.key: field for field in self.getJobConfigFields()}

    def initialize(self) -> None:
        # Validate the complete target before CONF can change the instrument.
        self.jobConfig = self._validatedConfig(self.jobConfig)
        self._configureFunction()

    def applySafeState(self) -> None:
        target = dict(self.jobConfig)
        target.update(self.metadata.get("safeConfig") or {})
        function = str(target["measurementFunction"]).strip().upper()
        if function in self._EXCITATION_FUNCTIONS:
            target.update(measurementFunction="VOLT:DC", rangeAuto=1)
        self.jobConfig = self._validatedConfig(target)
        self._configureFunction()

    def applyConfigValue(self, key: str, value: float | int | str) -> None:
        if key not in self._fields:
            return
        target = dict(self.jobConfig)
        target[key] = value
        # Unlike arbitrary SCPI strings, enum values and numbers are validated
        # before persistence so rejected inputs leave state and transport alone.
        self.jobConfig = self._validatedConfig(target)
        if key == "measurementFunction":
            self._configureFunction()
        else:
            self._applyConfiguredValue(key)

    def _validatedConfig(self, config: dict[str, Any]) -> dict[str, Any]:
        result = dict(config)
        for key, field in self._fields.items():
            value = config[key]
            if field.choices and isinstance(field.choices[0].value, str):
                normalized = str(value).strip().upper()
                if normalized not in {choice.value for choice in field.choices}:
                    raise ValueError(f"Unsupported {key}: {value!r}")
                result[key] = normalized
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"Invalid {key}: {value!r}") from exc
            if (
                not math.isfinite(numeric)
                or (field.minValue is not None and numeric < field.minValue)
                or (field.maxValue is not None and numeric > field.maxValue)
            ):
                raise ValueError(f"Out of range {key}: {value!r}")
            if field.choices:
                matches = [
                    choice.value for choice in field.choices if numeric == choice.value
                ]
                if not matches:
                    raise ValueError(f"Unsupported {key}: {value!r}")
                result[key] = matches[0]
            elif field.fieldType == "int":
                if not numeric.is_integer():
                    raise ValueError(f"{key} must be an integer: {value!r}")
                result[key] = int(numeric)
            else:
                result[key] = numeric
        if result["triggerSource"] == "BUS" and result["sampleCount"] > 512:
            raise ValueError("BUS triggering requires sampleCount <= 512 (INIT memory)")
        return result

    def _configureFunction(self) -> None:
        function = str(self.jobConfig["measurementFunction"])
        self.transport.write(f"CONF:{function}")
        # CONF also presets TRIG:COUN 1 and CALC OFF. Do not use MEAS? here:
        # it would acquire before the configured settings have been restored.
        for key in self._APPLY_ORDER:
            self._applyConfiguredValue(key)

    def _applyConfiguredValue(self, key: str) -> None:
        function = str(self.jobConfig["measurementFunction"])
        value = self.jobConfig[key]
        rangeKey = self._RANGE_KEY_BY_FUNCTION.get(function)
        rangePrefix = f"{function}:VOLT" if function in {"FREQ", "PER"} else function
        if key == "rangeAuto":
            if rangeKey is not None:
                self.transport.write(
                    f"{rangePrefix}:RANG:AUTO {'ON' if value else 'OFF'}"
                )
                if not value:
                    self._applyConfiguredValue(rangeKey)
        elif key in self._RANGES:
            if key == rangeKey and not self.jobConfig["rangeAuto"]:
                self.transport.write(f"{rangePrefix}:RANG {self._fmtFloat(value)}")
        elif key == "nplc":
            if function in self._NPLC_FUNCTIONS:
                self.transport.write(f"{function}:NPLC {self._fmtFloat(value)}")
        elif key == "autoZero":
            if function in self._AUTOZERO_FUNCTIONS:
                self.transport.write(f"ZERO:AUTO {value}")
        elif key == "inputImpedanceAuto":
            if function == "VOLT:DC":
                self.transport.write(f"INP:IMP:AUTO {'ON' if value else 'OFF'}")
        elif key == "acBandwidthHz":
            if function in {"VOLT:AC", "CURR:AC"}:
                self.transport.write(f"DET:BAND {self._fmtFloat(value)}")
        elif key == "apertureSeconds":
            if function in {"FREQ", "PER"}:
                self.transport.write(f"{function}:APER {self._fmtFloat(value)}")
        elif key == "triggerSource":
            self.transport.write(f"TRIG:SOUR {value}")
        elif key == "triggerDelayAuto":
            self.transport.write(f"TRIG:DEL:AUTO {'ON' if value else 'OFF'}")
            if not value:
                self._applyConfiguredValue("triggerDelaySeconds")
        elif key == "triggerDelaySeconds":
            if not self.jobConfig["triggerDelayAuto"]:
                self.transport.write(f"TRIG:DEL {self._fmtFloat(value)}")
        elif key == "sampleCount":
            self.transport.write(f"SAMP:COUN {self._fmtFloat(value)}")
        elif key == "displayEnabled":
            self.transport.write(f"DISP {'ON' if value else 'OFF'}")

    def readMeasurements(self, signalKeys: list[str]) -> dict[str, float]:
        function = str(self.jobConfig["measurementFunction"])
        signalKey = self._SIGNALS[function][0]
        if signalKey not in signalKeys:
            return {}
        if self.jobConfig["triggerSource"] == "BUS":
            self.transport.write("INIT")
            self.transport.write("*TRG")
            response = self.transport.query("FETC?")
        else:
            response = self.transport.query("READ?")
        parts = response.strip().split(",")
        if len(parts) > 1 and not parts[-1].strip():
            parts.pop()
        if len(parts) != self.jobConfig["sampleCount"]:
            return {}
        try:
            samples = [float(part.strip()) for part in parts]
        except ValueError:
            return {}
        if any(
            not math.isfinite(sample) or abs(sample) >= 9.9e37 for sample in samples
        ):
            return {}
        return {signalKey: math.fsum(samples) / len(samples)}

    def _fmtFloat(self, value: float | int | str) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(value)
        return f"{float(value):.12g}"


def _agilent34401aFactory(
    name: str,
    transport: BaseTransport,
    metadata: dict[str, Any] | None = None,
    jobConfig: dict[str, Any] | None = None,
) -> BaseInstrument:
    return Agilent34401AInstrument(
        name=name,
        transport=transport,
        metadata=metadata,
        jobConfig=jobConfig,
    )


def registerInstruments(registry: InstrumentRegistry) -> None:
    registry.registerInstrument(
        key=INSTRUMENT_TYPE_KEY,
        instrumentType=Agilent34401AInstrument,
        factory=_agilent34401aFactory,
    )
