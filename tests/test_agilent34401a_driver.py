from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from genesis.core.instrument import discovery
from genesis.core.instrument.config_field import ConfigChoice
from genesis.core.instrument.registry import InstrumentRegistry
from genesis.core.transport.base_transport import BaseTransport
from genesis.instruments.agilent34401a.driver import (
    INSTRUMENT_TYPE_KEY,
    Agilent34401AInstrument,
    registerInstruments,
)


class _ScriptedTransport(BaseTransport):
    """Record every command and fail on unexpected or missing query responses."""

    def __init__(self, responses: list[str] | None = None) -> None:
        super().__init__(resourceName="MOCK")
        self.writtenCommands: list[str] = []
        self.responses = list(responses or [])
        self.readCount = 0

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def write(self, command: str) -> None:
        self.writtenCommands.append(command)

    def read(self) -> str:
        self.readCount += 1
        if not self.responses:
            raise AssertionError("Unexpected read without a scripted response")
        return self.responses.pop(0)


class Agilent34401ADriverTests(unittest.TestCase):
    _SIGNAL_BY_FUNCTION = {
        "VOLT:DC": "voltageDcV",
        "VOLT:AC": "voltageAcV",
        "CURR:DC": "currentDcA",
        "CURR:AC": "currentAcA",
        "RES": "resistanceOhm",
        "FRES": "resistance4WireOhm",
        "FREQ": "frequencyHz",
        "PER": "periodSeconds",
        "CONT": "continuityOhm",
        "DIOD": "diodeVoltageV",
    }

    def _instrument(
        self,
        jobConfig: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        responses: list[str] | None = None,
    ) -> tuple[Agilent34401AInstrument, _ScriptedTransport]:
        transport = _ScriptedTransport(responses)
        instrument = Agilent34401AInstrument(
            name="dmm",
            transport=transport,
            metadata=metadata,
            jobConfig=jobConfig,
        )
        return instrument, transport

    def test_defaults_and_schema_are_coherent_and_not_sweepable(self) -> None:
        expectedDefaults = {
            "measurementFunction": "VOLT:DC",
            "rangeAuto": 1,
            "voltageDcRangeV": 1000.0,
            "voltageAcRangeV": 750.0,
            "currentDcRangeA": 3.0,
            "currentAcRangeA": 3.0,
            "resistanceRangeOhm": 100000000.0,
            "frequencyVoltageRangeV": 750.0,
            "nplc": 10.0,
            "autoZero": "ON",
            "inputImpedanceAuto": 0,
            "acBandwidthHz": 20.0,
            "apertureSeconds": 0.1,
            "triggerSource": "IMM",
            "triggerDelayAuto": 1,
            "triggerDelaySeconds": 0.0,
            "sampleCount": 1,
            "displayEnabled": 1,
        }
        self.assertEqual(
            Agilent34401AInstrument.getDefaultJobConfig(), expectedDefaults
        )
        fields = Agilent34401AInstrument.getJobConfigFields()
        self.assertEqual(len(fields), len(expectedDefaults))
        for field in fields:
            with self.subTest(key=field.key):
                self.assertFalse(field.sweepable)
                self.assertTrue(field.helpText)
                if field.fieldType == "enum":
                    self.assertTrue(field.choices)
                    self.assertTrue(
                        all(
                            isinstance(choice, ConfigChoice) for choice in field.choices
                        )
                    )
                    self.assertIn(
                        field.default, [choice.value for choice in field.choices]
                    )
                if isinstance(field.default, (int, float)):
                    self.assertIsNotNone(field.minValue)
                    self.assertIsNotNone(field.maxValue)
                    self.assertGreaterEqual(field.default, field.minValue)
                    self.assertLessEqual(field.default, field.maxValue)

    def test_transport_defaults_and_measurement_keys(self) -> None:
        self.assertEqual(INSTRUMENT_TYPE_KEY, "agilent34401a")
        self.assertEqual(
            Agilent34401AInstrument.getDefaultAddress(), "GPIB0::22::INSTR"
        )
        self.assertEqual(Agilent34401AInstrument.getSupportedTransportKeys(), ["visa"])
        self.assertEqual(Agilent34401AInstrument.getDefaultTransportKey(), "visa")
        self.assertEqual(
            Agilent34401AInstrument.getDefaultTransportSettings()["visaTimeoutMs"],
            120000,
        )
        signals = Agilent34401AInstrument.getAvailableMeasurementSignals()
        self.assertEqual(
            {key for key, _label in signals}, set(self._SIGNAL_BY_FUNCTION.values())
        )
        self.assertEqual(len(signals), len(self._SIGNAL_BY_FUNCTION))

    def test_initialize_uses_one_configure_and_no_reset_or_measurement(self) -> None:
        instrument, transport = self._instrument()
        instrument.initialize()
        self.assertEqual(
            transport.writtenCommands,
            [
                "CONF:VOLT:DC",
                "VOLT:DC:RANG:AUTO ON",
                "VOLT:DC:NPLC 10",
                "ZERO:AUTO ON",
                "INP:IMP:AUTO OFF",
                "TRIG:SOUR IMM",
                "TRIG:DEL:AUTO ON",
                "SAMP:COUN 1",
                "DISP ON",
            ],
        )
        self.assertEqual(transport.readCount, 0)

    def test_each_function_restores_its_dependent_configuration(self) -> None:
        expectedByFunction = {
            "VOLT:DC": [
                "VOLT:DC:RANG:AUTO ON",
                "VOLT:DC:NPLC 100",
                "ZERO:AUTO OFF",
                "INP:IMP:AUTO ON",
            ],
            "VOLT:AC": ["VOLT:AC:RANG:AUTO ON", "DET:BAND 3"],
            "CURR:DC": ["CURR:DC:RANG:AUTO ON", "CURR:DC:NPLC 100", "ZERO:AUTO OFF"],
            "CURR:AC": ["CURR:AC:RANG:AUTO ON", "DET:BAND 3"],
            "RES": ["RES:RANG:AUTO ON", "RES:NPLC 100", "ZERO:AUTO OFF"],
            "FRES": ["FRES:RANG:AUTO ON", "FRES:NPLC 100"],
            "FREQ": ["FREQ:VOLT:RANG:AUTO ON", "FREQ:APER 1"],
            "PER": ["PER:VOLT:RANG:AUTO ON", "PER:APER 1"],
            "CONT": [],
            "DIOD": [],
        }
        for function, dependentCommands in expectedByFunction.items():
            with self.subTest(function=function):
                instrument, transport = self._instrument(
                    {
                        "nplc": 100,
                        "autoZero": "OFF",
                        "inputImpedanceAuto": 1,
                        "acBandwidthHz": 3,
                        "apertureSeconds": 1,
                        "triggerSource": "BUS",
                        "triggerDelayAuto": 0,
                        "triggerDelaySeconds": 0.25,
                        "sampleCount": 2,
                        "displayEnabled": 0,
                    }
                )
                instrument.applyConfigValue("measurementFunction", function)
                self.assertEqual(
                    transport.writtenCommands,
                    [f"CONF:{function}"]
                    + dependentCommands
                    + [
                        "TRIG:SOUR BUS",
                        "TRIG:DEL:AUTO OFF",
                        "TRIG:DEL 0.25",
                        "SAMP:COUN 2",
                        "DISP OFF",
                    ],
                )
                self.assertEqual(instrument.jobConfig["measurementFunction"], function)

    def test_config_keys_map_to_short_scpi_commands(self) -> None:
        cases = [
            ({}, "rangeAuto", 0, ["VOLT:DC:RANG:AUTO OFF", "VOLT:DC:RANG 1000"]),
            ({"rangeAuto": 0}, "rangeAuto", 1, ["VOLT:DC:RANG:AUTO ON"]),
            ({}, "nplc", 0.02, ["VOLT:DC:NPLC 0.02"]),
            ({}, "autoZero", "OFF", ["ZERO:AUTO OFF"]),
            ({}, "inputImpedanceAuto", 1, ["INP:IMP:AUTO ON"]),
            (
                {"measurementFunction": "VOLT:AC"},
                "acBandwidthHz",
                200,
                ["DET:BAND 200"],
            ),
            (
                {"measurementFunction": "FREQ"},
                "apertureSeconds",
                0.01,
                ["FREQ:APER 0.01"],
            ),
            ({"measurementFunction": "PER"}, "apertureSeconds", 1, ["PER:APER 1"]),
            ({}, "triggerSource", "EXT", ["TRIG:SOUR EXT"]),
            ({}, "triggerDelayAuto", 0, ["TRIG:DEL:AUTO OFF", "TRIG:DEL 0"]),
            ({"triggerDelayAuto": 0}, "triggerDelayAuto", 1, ["TRIG:DEL:AUTO ON"]),
            (
                {"triggerDelayAuto": 0},
                "triggerDelaySeconds",
                1.23456789012345,
                ["TRIG:DEL 1.23456789012"],
            ),
            ({}, "sampleCount", 4, ["SAMP:COUN 4"]),
            ({}, "displayEnabled", 0, ["DISP OFF"]),
        ]
        for config, key, value, expected in cases:
            with self.subTest(key=key, value=value, config=config):
                instrument, transport = self._instrument(config)
                instrument.applyConfigValue(key, value)
                self.assertEqual(transport.writtenCommands, expected)
                self.assertEqual(instrument.jobConfig[key], value)

    def test_manual_ranges_have_correct_function_headers_and_limits(self) -> None:
        cases = [
            ("VOLT:DC", "voltageDcRangeV", "VOLT:DC:RANG", [0.1, 1, 10, 100, 1000]),
            ("VOLT:AC", "voltageAcRangeV", "VOLT:AC:RANG", [0.1, 1, 10, 100, 750]),
            ("CURR:DC", "currentDcRangeA", "CURR:DC:RANG", [0.01, 0.1, 1, 3]),
            ("CURR:AC", "currentAcRangeA", "CURR:AC:RANG", [1, 3]),
            (
                "RES",
                "resistanceRangeOhm",
                "RES:RANG",
                [100, 1000, 10000, 100000, 1000000, 10000000, 100000000],
            ),
            (
                "FRES",
                "resistanceRangeOhm",
                "FRES:RANG",
                [100, 1000, 10000, 100000, 1000000, 10000000, 100000000],
            ),
            (
                "FREQ",
                "frequencyVoltageRangeV",
                "FREQ:VOLT:RANG",
                [0.1, 1, 10, 100, 750],
            ),
            ("PER", "frequencyVoltageRangeV", "PER:VOLT:RANG", [0.1, 1, 10, 100, 750]),
        ]
        fields = {
            field.key: field for field in Agilent34401AInstrument.getJobConfigFields()
        }
        for function, key, header, ranges in cases:
            field = fields[key]
            self.assertEqual(field.minValue, min(ranges))
            self.assertEqual(field.maxValue, max(ranges))
            self.assertEqual([choice.value for choice in field.choices], ranges)
            for value in ranges:
                with self.subTest(function=function, value=value):
                    instrument, transport = self._instrument(
                        {"measurementFunction": function, "rangeAuto": 0}
                    )
                    instrument.applyConfigValue(key, value)
                    self.assertEqual(
                        transport.writtenCommands, [f"{header} {value:.12g}"]
                    )

    def test_dormant_settings_are_remembered_without_changing_instrument(self) -> None:
        cases = [
            ({}, "voltageDcRangeV", 10),
            ({"rangeAuto": 0}, "voltageAcRangeV", 10),
            ({}, "currentDcRangeA", 0.1),
            ({}, "currentAcRangeA", 1),
            ({}, "resistanceRangeOhm", 100),
            ({}, "frequencyVoltageRangeV", 1),
            ({"measurementFunction": "VOLT:AC"}, "nplc", 100),
            ({"measurementFunction": "FRES"}, "autoZero", "OFF"),
            ({"measurementFunction": "CURR:DC"}, "inputImpedanceAuto", 1),
            ({}, "acBandwidthHz", 3),
            ({}, "apertureSeconds", 1),
            ({}, "triggerDelaySeconds", 2),
            ({"measurementFunction": "CONT"}, "rangeAuto", 0),
            ({"measurementFunction": "DIOD"}, "rangeAuto", 0),
        ]
        for config, key, value in cases:
            with self.subTest(key=key, config=config):
                instrument, transport = self._instrument(config)
                instrument.applyConfigValue(key, value)
                self.assertEqual(transport.writtenCommands, [])
                self.assertEqual(instrument.jobConfig[key], value)

    def test_manual_range_and_integration_survive_function_round_trip(self) -> None:
        instrument, transport = self._instrument(
            {"rangeAuto": 0, "voltageDcRangeV": 10, "nplc": 100}
        )
        instrument.initialize()
        instrument.applyConfigValue("measurementFunction", "VOLT:AC")
        instrument.applyConfigValue("voltageDcRangeV", 0.1)
        transport.writtenCommands.clear()
        instrument.applyConfigValue("measurementFunction", "VOLT:DC")
        self.assertEqual(
            transport.writtenCommands[:4],
            [
                "CONF:VOLT:DC",
                "VOLT:DC:RANG:AUTO OFF",
                "VOLT:DC:RANG 0.1",
                "VOLT:DC:NPLC 100",
            ],
        )

    def test_nplc_is_discrete_and_numeric_strings_are_normalized(self) -> None:
        fields = {
            field.key: field for field in Agilent34401AInstrument.getJobConfigFields()
        }
        nplc = fields["nplc"]
        self.assertEqual(nplc.fieldType, "enum")
        self.assertEqual(
            [choice.value for choice in nplc.choices], [0.02, 0.2, 1, 10, 100]
        )
        for value in [0.02, 0.2, 1, 10, 100]:
            with self.subTest(value=value):
                instrument, transport = self._instrument()
                instrument.applyConfigValue("nplc", f" {value} ")
                self.assertEqual(
                    transport.writtenCommands, [f"VOLT:DC:NPLC {value:.12g}"]
                )
                self.assertEqual(instrument.jobConfig["nplc"], value)

    def test_numeric_boundaries_and_integer_string_values(self) -> None:
        for key, values, config in [
            ("sampleCount", [1, 50000], {}),
            ("triggerDelaySeconds", [0, 3600], {"triggerDelayAuto": 0}),
        ]:
            for value in values:
                with self.subTest(key=key, value=value):
                    instrument, transport = self._instrument(config)
                    instrument.applyConfigValue(key, str(value))
                    self.assertEqual(instrument.jobConfig[key], value)
                    self.assertEqual(len(transport.writtenCommands), 1)

    def test_invalid_config_is_rejected_before_state_or_transport_changes(self) -> None:
        invalidByKey = {
            "measurementFunction": ["TEMP", "VOLT:DC;*RST"],
            "rangeAuto": [-1, 2, 0.5],
            "voltageDcRangeV": [0, 0.09, 0.5, 1001],
            "voltageAcRangeV": [0, 0.5, 751, 1000],
            "currentDcRangeA": [0, 0.009, 0.5, 3.1],
            "currentAcRangeA": [0.1, 0.99, 2, 3.1],
            "resistanceRangeOhm": [99, 150, 100000001],
            "frequencyVoltageRangeV": [0.09, 0.5, 751],
            "nplc": [0, 0.01, 0.1, 2, 101, float("nan"), float("inf"), "10;*RST"],
            "autoZero": ["ONCE", "AUTO", 2],
            "inputImpedanceAuto": [-1, 2, 0.5],
            "acBandwidthHz": [2, 10, 201],
            "apertureSeconds": [0, 0.02, 1.01],
            "triggerSource": ["INT", "TIMER"],
            "triggerDelayAuto": [-1, 2, 0.5],
            "triggerDelaySeconds": [-0.01, 3600.01, float("nan"), float("inf"), "bad"],
            "sampleCount": [0, 50001, 1.5, float("nan"), float("inf")],
            "displayEnabled": [-1, 2, 0.5],
        }
        for key, values in invalidByKey.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    instrument, transport = self._instrument()
                    before = dict(instrument.jobConfig)
                    with self.assertRaises(ValueError):
                        instrument.applyConfigValue(key, value)
                    self.assertEqual(instrument.jobConfig, before)
                    self.assertEqual(transport.writtenCommands, [])

    def test_initialize_validates_entire_configuration_before_any_write(self) -> None:
        instrument, transport = self._instrument()
        instrument.jobConfig["displayEnabled"] = 2
        with self.assertRaises(ValueError):
            instrument.initialize()
        self.assertEqual(transport.writtenCommands, [])

    def test_bus_sample_memory_limit_is_validated_in_both_update_orders(self) -> None:
        for config, key, value in [
            ({"triggerSource": "BUS", "sampleCount": 512}, "sampleCount", 513),
            ({"sampleCount": 513}, "triggerSource", "BUS"),
        ]:
            with self.subTest(config=config):
                instrument, transport = self._instrument(config)
                before = dict(instrument.jobConfig)
                with self.assertRaises(ValueError):
                    instrument.applyConfigValue(key, value)
                self.assertEqual(instrument.jobConfig, before)
                self.assertEqual(transport.writtenCommands, [])
        instrument, transport = self._instrument({"triggerSource": "BUS"})
        instrument.applyConfigValue("sampleCount", 512)
        self.assertEqual(transport.writtenCommands, ["SAMP:COUN 512"])

    def test_unknown_config_is_ignored_safely(self) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("futureSetting", "arbitrary value")
        self.assertEqual(transport.writtenCommands, [])
        self.assertEqual(instrument.jobConfig["measurementFunction"], "VOLT:DC")

    def test_reads_only_active_function_without_switching_or_reconfiguring(
        self,
    ) -> None:
        requested = list(self._SIGNAL_BY_FUNCTION.values()) + ["unknown"]
        for function, signal in self._SIGNAL_BY_FUNCTION.items():
            with self.subTest(function=function):
                instrument, transport = self._instrument(
                    {"measurementFunction": function}, responses=["  +1.250000E+00\r\n"]
                )
                before = dict(instrument.jobConfig)
                self.assertEqual(
                    instrument.readMeasurements(requested + [signal]), {signal: 1.25}
                )
                self.assertEqual(transport.writtenCommands, ["READ?"])
                self.assertEqual(instrument.jobConfig, before)
                self.assertEqual(transport.readCount, 1)

    def test_unrequested_active_function_does_not_trigger_acquisition(self) -> None:
        for requested in [[], ["unknown"], ["resistanceOhm", "frequencyHz"]]:
            with self.subTest(requested=requested):
                instrument, transport = self._instrument()
                self.assertEqual(instrument.readMeasurements(requested), {})
                self.assertEqual(transport.writtenCommands, [])
                self.assertEqual(transport.readCount, 0)

    def test_multiple_samples_are_averaged_including_optional_trailing_comma(
        self,
    ) -> None:
        for response in [" 1, 2.0, +6E0\r\n", " 1, 2.0, +6E0,\r\n"]:
            with self.subTest(response=response):
                instrument, transport = self._instrument(
                    {"sampleCount": 3}, responses=[response]
                )
                self.assertEqual(
                    instrument.readMeasurements(["voltageDcV"]), {"voltageDcV": 3.0}
                )
                self.assertEqual(transport.writtenCommands, ["READ?"])

    def test_zero_and_negative_readings_are_valid(self) -> None:
        for response, expected in [("0", 0.0), ("-2.5E-3", -0.0025)]:
            with self.subTest(response=response):
                instrument, _transport = self._instrument(responses=[response])
                self.assertEqual(
                    instrument.readMeasurements(["voltageDcV"]),
                    {"voltageDcV": expected},
                )

    def test_malformed_nonfinite_overload_or_incomplete_samples_are_omitted(
        self,
    ) -> None:
        cases = [
            (1, ""),
            (1, " "),
            (1, "bad"),
            (1, ","),
            (1, "NaN"),
            (1, "inf"),
            (1, "-inf"),
            (1, "9.9E37"),
            (1, "-9.9E37"),
            (1, "1E38"),
            (1, "1,2"),
            (1, "1,,"),
            (2, "1"),
            (2, "1,"),
            (2, "1,bad"),
            (2, "1,NaN"),
            (2, "1,9.9E37"),
            (2, "1,,2"),
            (2, "1,2,3"),
        ]
        for count, response in cases:
            with self.subTest(count=count, response=response):
                instrument, transport = self._instrument(
                    {"sampleCount": count}, responses=[response]
                )
                self.assertEqual(instrument.readMeasurements(["voltageDcV"]), {})
                self.assertEqual(transport.writtenCommands, ["READ?"])

    def test_bus_trigger_uses_initialize_trigger_then_fetch(self) -> None:
        instrument, transport = self._instrument(
            {"triggerSource": "BUS", "sampleCount": 2}, responses=["1,3"]
        )
        self.assertEqual(
            instrument.readMeasurements(["voltageDcV"]), {"voltageDcV": 2.0}
        )
        self.assertEqual(transport.writtenCommands, ["INIT", "*TRG", "FETC?"])

    def test_external_trigger_waits_with_read_without_bus_trigger(self) -> None:
        instrument, transport = self._instrument(
            {"triggerSource": "EXT"}, responses=["2"]
        )
        self.assertEqual(
            instrument.readMeasurements(["voltageDcV"]), {"voltageDcV": 2.0}
        )
        self.assertEqual(transport.writtenCommands, ["READ?"])

    def test_transport_failure_is_not_disguised_as_missing_measurement(self) -> None:
        instrument, transport = self._instrument()
        with patch.object(transport, "query", side_effect=TimeoutError("timed out")):
            with self.assertRaises(TimeoutError):
                instrument.readMeasurements(["voltageDcV"])

    def test_safe_state_removes_all_current_injecting_functions(self) -> None:
        for function in ["RES", "FRES", "CONT", "DIOD"]:
            for metadata in [
                None,
                {"safeConfig": {}},
                {"safeConfig": {"measurementFunction": function, "rangeAuto": 0}},
            ]:
                with self.subTest(function=function, metadata=metadata):
                    instrument, transport = self._instrument(
                        {"measurementFunction": function, "rangeAuto": 0},
                        metadata=metadata,
                    )
                    instrument.applySafeState()
                    self.assertEqual(transport.writtenCommands[0], "CONF:VOLT:DC")
                    self.assertEqual(
                        transport.writtenCommands[1], "VOLT:DC:RANG:AUTO ON"
                    )
                    self.assertEqual(
                        instrument.jobConfig["measurementFunction"], "VOLT:DC"
                    )
                    self.assertEqual(instrument.jobConfig["rangeAuto"], 1)
                    self.assertEqual(transport.readCount, 0)
                    self.assertFalse(
                        any(
                            command.startswith(
                                ("INIT", "READ", "FETC", "*TRG", "*RST", "ABOR")
                            )
                            for command in transport.writtenCommands
                        )
                    )

    def test_safe_state_overlays_partial_metadata_on_current_config(self) -> None:
        instrument, transport = self._instrument(
            {
                "measurementFunction": "VOLT:AC",
                "rangeAuto": 0,
                "voltageAcRangeV": 10,
                "acBandwidthHz": 200,
                "sampleCount": 3,
            },
            metadata={"safeConfig": {"displayEnabled": 0}},
        )
        instrument.applySafeState()
        self.assertEqual(
            transport.writtenCommands,
            [
                "CONF:VOLT:AC",
                "VOLT:AC:RANG:AUTO OFF",
                "VOLT:AC:RANG 10",
                "DET:BAND 200",
                "TRIG:SOUR IMM",
                "TRIG:DEL:AUTO ON",
                "SAMP:COUN 3",
                "DISP OFF",
            ],
        )
        self.assertEqual(instrument.jobConfig["displayEnabled"], 0)
        self.assertEqual(instrument.jobConfig["voltageAcRangeV"], 10)

    def test_safe_state_without_metadata_reapplies_noninjecting_job(self) -> None:
        instrument, transport = self._instrument(
            {"measurementFunction": "CURR:AC", "displayEnabled": 0}
        )
        instrument.applySafeState()
        self.assertEqual(transport.writtenCommands[0], "CONF:CURR:AC")
        self.assertEqual(transport.writtenCommands[-1], "DISP OFF")
        self.assertEqual(transport.readCount, 0)

    def test_invalid_safe_config_is_rejected_without_partial_application(self) -> None:
        instrument, transport = self._instrument(metadata={"safeConfig": {"nplc": 3}})
        before = dict(instrument.jobConfig)
        with self.assertRaises(ValueError):
            instrument.applySafeState()
        self.assertEqual(transport.writtenCommands, [])
        self.assertEqual(instrument.jobConfig, before)

    def test_factory_registers_type_and_preserves_constructor_arguments(self) -> None:
        registry = InstrumentRegistry()
        registerInstruments(registry)
        transport = _ScriptedTransport()
        metadata = {"safeConfig": {"displayEnabled": 0}}
        instrument = registry.createInstrument(
            "agilent34401a",
            name="bench dmm",
            transport=transport,
            metadata=metadata,
            jobConfig={"nplc": 100},
        )
        self.assertIsInstance(instrument, Agilent34401AInstrument)
        self.assertIs(
            registry.getInstrumentType("agilent34401a"), Agilent34401AInstrument
        )
        self.assertEqual(instrument.name, "bench dmm")
        self.assertIs(instrument.transport, transport)
        self.assertEqual(instrument.metadata, metadata)
        self.assertEqual(instrument.jobConfig["nplc"], 100)

    def test_discovery_registers_driver_with_and_without_package_scanning(self) -> None:
        registry = InstrumentRegistry()
        discovery.loadBuiltInInstruments(registry)
        self.assertIs(
            registry.getInstrumentType("agilent34401a"), Agilent34401AInstrument
        )
        registry = InstrumentRegistry()
        with patch.object(discovery.pkgutil, "iter_modules", return_value=[]):
            discovery.loadBuiltInInstruments(registry)
        self.assertIs(
            registry.getInstrumentType("agilent34401a"), Agilent34401AInstrument
        )


if __name__ == "__main__":
    unittest.main()
