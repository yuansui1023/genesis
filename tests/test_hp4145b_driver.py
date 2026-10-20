"""HP 4145B driver tests. No hardware and no VISA session."""

from __future__ import annotations

import math
import threading
import unittest
from decimal import Decimal
from typing import Any

from genesis.core.instrument.config_field import ConfigChoice
from genesis.core.instrument.discovery import loadBuiltInInstruments
from genesis.core.instrument.registry import InstrumentRegistry
from genesis.core.runtime.setpoint_safety import (
    SetpointSafetyController,
    build_value_bounds_by_instrument,
)
from genesis.core.transport.base_transport import BaseTransport
from genesis.instruments.hp4145b.driver import (
    INSTRUMENT_TYPE_KEY,
    Hp4145BConfigError,
    Hp4145BInstrument,
    Hp4145BMeasurementError,
    Hp4145BMeasurementStatusError,
    Hp4145BSafeStateError,
    _formatProgramNumber,
    registerInstruments,
)


class _ScriptedTransport(BaseTransport):
    def __init__(
        self,
        responses: list[str] | None = None,
        fail_commands: set[str] | None = None,
    ) -> None:
        super().__init__(resourceName="MOCK")
        self.writtenCommands: list[str] = []
        self._responses = list(responses or [])
        self.fail_commands = set(fail_commands or [])
        self.fail_reads = 0

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def write(self, command: str) -> None:
        self.writtenCommands.append(command)
        if command in self.fail_commands:
            raise RuntimeError(f"write failed: {command}")

    def read(self) -> str:
        if self.fail_reads:
            self.fail_reads -= 1
            raise TimeoutError("visa timeout")
        if not self._responses:
            raise AssertionError("unexpected read")
        return self._responses.pop(0)


class _BlockingReadTransport(_ScriptedTransport):
    def __init__(self) -> None:
        super().__init__(responses=["NAI+1.0000E-03\r\n"])
        self.entered = threading.Event()
        self.release = threading.Event()

    def read(self) -> str:
        self.entered.set()
        if not self.release.wait(2):
            raise TimeoutError("blocked read was not released")
        return super().read()


class Hp4145BDriverTests(unittest.TestCase):
    def _instrument(
        self,
        jobConfig: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        responses: list[str] | None = None,
        fail_commands: set[str] | None = None,
    ) -> tuple[Hp4145BInstrument, _ScriptedTransport]:
        transport = _ScriptedTransport(responses, fail_commands)
        instrument = Hp4145BInstrument(
            name="spa",
            transport=transport,
            metadata=metadata,
            jobConfig=jobConfig,
        )
        return instrument, transport

    def test_registration_discovery_and_transport_defaults(self) -> None:
        registry = InstrumentRegistry()
        registerInstruments(registry)
        self.assertIs(
            registry.getInstrumentType(INSTRUMENT_TYPE_KEY), Hp4145BInstrument
        )
        discovered = InstrumentRegistry()
        loadBuiltInInstruments(discovered)
        self.assertIn("hp4145b", discovered.listInstruments())
        self.assertEqual(Hp4145BInstrument.getDefaultAddress(), "GPIB0::17::INSTR")
        self.assertEqual(Hp4145BInstrument.getSupportedTransportKeys(), ["visa"])
        self.assertEqual(Hp4145BInstrument.getDefaultTransportKey(), "visa")
        self.assertEqual(
            Hp4145BInstrument.getDefaultTransportSettings(),
            {
                "visaTimeoutMs": 10000,
                "writeTermination": "\n",
                "readTermination": "\n",
                "visaSendEndOnWrite": True,
                "visaClearOnOpen": False,
            },
        )
        created = registry.createInstrument(
            INSTRUMENT_TYPE_KEY, name="spa", transport=_ScriptedTransport()
        )
        self.assertIsInstance(created, Hp4145BInstrument)

    def test_field_metadata_defaults_and_sweepable_setpoints(self) -> None:
        fields = Hp4145BInstrument.getJobConfigFields()
        by_key = {field.key: field for field in fields}
        self.assertEqual(len(fields), len(by_key))
        sweepable = {field.key for field in fields if field.sweepable}
        self.assertEqual(
            sweepable,
            {
                "smu1ForceVoltageLevelV",
                "smu2ForceVoltageLevelV",
                "smu3ForceVoltageLevelV",
                "smu4ForceVoltageLevelV",
                "smu1ForceCurrentLevelA",
                "smu2ForceCurrentLevelA",
                "smu3ForceCurrentLevelA",
                "smu4ForceCurrentLevelA",
                "vs1VoltageLevelV",
                "vs2VoltageLevelV",
            },
        )
        for index in range(1, 5):
            self.assertEqual(by_key[f"smu{index}ForceMode"].default, "VOLT")
            self.assertEqual(by_key[f"smu{index}OutputEnabled"].default, 0)
            self.assertEqual(by_key[f"smu{index}VoltageRangeIndex"].default, 0)
            self.assertEqual(by_key[f"smu{index}CurrentRangeIndex"].default, 0)
            self.assertEqual(by_key[f"smu{index}CurrentComplianceA"].default, 1e-6)
            self.assertEqual(by_key[f"smu{index}VoltageComplianceV"].default, 1.0)
            self.assertIn("driver-chosen", by_key[f"smu{index}OutputEnabled"].helpText)
        self.assertEqual(by_key["integrationTimeIndex"].default, 2)
        self.assertEqual(
            [choice.value for choice in by_key["smu1VoltageRangeIndex"].choices or []],
            [0, 1, 2, 3],
        )
        self.assertEqual(
            [choice.value for choice in by_key["smu1CurrentRangeIndex"].choices or []],
            list(range(10)),
        )
        for field in fields:
            self.assertTrue(field.helpText)
            if field.fieldType == "enum":
                self.assertTrue(field.choices)
                self.assertTrue(
                    all(isinstance(choice, ConfigChoice) for choice in field.choices)
                )
                self.assertIn(field.default, [choice.value for choice in field.choices])
            if isinstance(field.default, (int, float)) and not isinstance(
                field.default, bool
            ):
                self.assertIsNotNone(field.minValue)
                self.assertIsNotNone(field.maxValue)
                self.assertGreaterEqual(field.default, field.minValue)
                self.assertLessEqual(field.default, field.maxValue)
        signals = Hp4145BInstrument.getAvailableMeasurementSignals()
        self.assertEqual(
            [key for key, _label in signals],
            [
                "smu1VoltageV",
                "smu1CurrentA",
                "smu2VoltageV",
                "smu2CurrentA",
                "smu3VoltageV",
                "smu3CurrentA",
                "smu4VoltageV",
                "smu4CurrentA",
                "vm1VoltageV",
                "vm2VoltageV",
            ],
        )
        self.assertEqual(Hp4145BInstrument.getAvailablePlotSignals(), signals)
        instrument = Hp4145BInstrument("spa", _ScriptedTransport())
        self.assertTrue(instrument.shouldUseRuntimeSlew("smu1ForceVoltageLevelV"))
        self.assertTrue(instrument.shouldUseRuntimeSlew("smu4ForceCurrentLevelA"))
        self.assertTrue(instrument.shouldUseRuntimeSlew("vs2VoltageLevelV"))

    def test_initialize_enters_user_mode_and_commands_outputs_off(self) -> None:
        instrument, transport = self._instrument()
        instrument.initialize()
        self.assertEqual(
            transport.writtenCommands,
            ["US", "IT2", "DV1", "DV2", "DV3", "DV4", "DS1,0", "DS2,0"],
        )
        self.assertNotIn("CA1", transport.writtenCommands)
        self.assertNotIn("DO", transport.writtenCommands)

    def test_per_key_path_enters_user_mode_once_and_leaves_output_off(self) -> None:
        instrument, transport = self._instrument()
        for key, value in Hp4145BInstrument.getDefaultJobConfig().items():
            instrument.applyConfigValue(key, value)
        instrument.finalizeInitialization()
        self.assertEqual(transport.writtenCommands.count("US"), 1)
        self.assertEqual(
            transport.writtenCommands,
            ["US", "IT2", "DV1", "DV2", "DV3", "DV4", "DS1,0", "DS2,0", "IT2"],
        )
        self.assertFalse(
            any(command.startswith("DV1,") for command in transport.writtenCommands)
        )

    def test_level_before_enable_does_not_source_and_enable_sends_full_command(
        self,
    ) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("smu1ForceVoltageLevelV", 2)
        self.assertEqual(transport.writtenCommands, ["US", "IT2"])
        instrument.applyConfigValue("smu1CurrentComplianceA", 2e-6)
        instrument.applyConfigValue("smu1VoltageRangeIndex", 1)
        instrument.applyConfigValue("smu2ForceVoltageLevelV", -1.5)
        self.assertEqual(transport.writtenCommands, ["US", "IT2"])
        instrument.applyConfigValue("smu1OutputEnabled", 1)
        self.assertEqual(transport.writtenCommands[-1], "DV1,1,2E+00,2E-06")
        instrument.applyConfigValue("smu1ForceVoltageLevelV", 2)
        self.assertEqual(transport.writtenCommands[-1], "DV1,1,2E+00,2E-06")
        self.assertEqual(transport.writtenCommands.count("US"), 1)

    def test_range_codes_and_channel_commands(self) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("smu1OutputEnabled", 1)
        for code in (0, 1, 2, 3):
            instrument.applyConfigValue("smu1VoltageRangeIndex", code)
            self.assertTrue(transport.writtenCommands[-1].startswith(f"DV1,{code},"))
        instrument.applyConfigValue("smu4ForceMode", "CURR")
        instrument.applyConfigValue("smu4OutputEnabled", 1)
        for code in range(10):
            instrument.applyConfigValue("smu4CurrentRangeIndex", code)
            self.assertTrue(transport.writtenCommands[-1].startswith(f"DI4,{code},"))
        instrument.applyConfigValue("vs1VoltageLevelV", -1.25)
        instrument.applyConfigValue("vs2VoltageLevelV", 20)
        self.assertEqual(transport.writtenCommands[-2], "DS1,-1.25E+00")
        self.assertEqual(transport.writtenCommands[-1], "DS2,2E+01")
        self.assertTrue(
            all(
                len(part) <= 12 for part in transport.writtenCommands[-1].split(",")[1:]
            )
        )

    def test_di_and_vm_trigger_map_and_mode_switch_is_local(self) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("smu1OutputEnabled", 1)
        instrument.applyConfigValue("smu2OutputEnabled", 1)
        transport.writtenCommands.clear()
        instrument.applyConfigValue("smu2ForceVoltageLevelV", 3)
        self.assertEqual(transport.writtenCommands, ["DV2,0,3E+00,1E-06"])
        transport.writtenCommands.clear()
        instrument.applyConfigValue("smu1ForceMode", "CURR")
        self.assertEqual(transport.writtenCommands, ["DV1", "DI1,0,0,1E+00"])
        transport.writtenCommands.clear()
        instrument.applyConfigValue("smu1ForceCurrentLevelA", -1.5e-6)
        self.assertEqual(transport.writtenCommands, ["DI1,0,-1.5E-06,1E+00"])
        self.assertNotIn("US", transport.writtenCommands)

    def test_closed_channel_mode_and_setpoint_changes_do_not_source(self) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("smu3ForceMode", "CURR")
        instrument.applyConfigValue("smu3ForceCurrentLevelA", 1e-9)
        instrument.applyConfigValue("smu3CurrentRangeIndex", 4)
        instrument.applyConfigValue("smu3VoltageComplianceV", 0)
        self.assertEqual(transport.writtenCommands, ["US", "IT2"])
        instrument.applyConfigValue("smu3OutputEnabled", 1)
        self.assertEqual(transport.writtenCommands[-1], "DI3,4,1E-09,0")

    def test_envelope_boundaries_and_rejected_values_do_not_write(self) -> None:
        accepted = [
            ("smu1ForceVoltageLevelV", 100, "smu1CurrentComplianceA", 0.02),
            ("smu1ForceVoltageLevelV", 40, "smu1CurrentComplianceA", 0.05),
            ("smu1ForceVoltageLevelV", 20, "smu1CurrentComplianceA", 0.1),
            ("smu1ForceVoltageLevelV", 30, "smu1CurrentComplianceA", 0.05),
            ("smu1ForceVoltageLevelV", 50, "smu1CurrentComplianceA", 0.02),
            ("smu1CurrentComplianceA", 5e-11, "smu1ForceVoltageLevelV", 0),
        ]
        for first_key, first_value, second_key, second_value in accepted:
            with self.subTest(first=first_value, second=second_value):
                instrument, transport = self._instrument()
                instrument.applyConfigValue(first_key, first_value)
                instrument.applyConfigValue(second_key, second_value)
                self.assertGreaterEqual(len(transport.writtenCommands), 2)

        rejected_after_voltage = [
            (100, 0.0201),
            (40, 0.0501),
            (20, 0.1001),
            (30, 0.051),
            (50, 0.03),
        ]
        for voltage, compliance in rejected_after_voltage:
            with self.subTest(voltage=voltage, compliance=compliance):
                instrument, transport = self._instrument()
                instrument.applyConfigValue("smu1ForceVoltageLevelV", voltage)
                before = list(transport.writtenCommands)
                stored = instrument.jobConfig["smu1CurrentComplianceA"]
                with self.assertRaises(Hp4145BConfigError):
                    instrument.applyConfigValue("smu1CurrentComplianceA", compliance)
                self.assertEqual(transport.writtenCommands, before)
                self.assertEqual(instrument.jobConfig["smu1CurrentComplianceA"], stored)

        instrument, transport = self._instrument()
        instrument.applyConfigValue("smu1VoltageRangeIndex", 1)
        with self.assertRaises(Hp4145BConfigError):
            instrument.applyConfigValue("smu1ForceVoltageLevelV", 21)
        instrument.applyConfigValue("smu1VoltageRangeIndex", 3)
        with self.assertRaises(Hp4145BConfigError):
            instrument.applyConfigValue("smu1CurrentComplianceA", 0.05)
        instrument.applyConfigValue("smu1ForceMode", "CURR")
        instrument.applyConfigValue("smu1ForceCurrentLevelA", 0.1)
        instrument.applyConfigValue("smu1VoltageComplianceV", 20)
        with self.assertRaises(Hp4145BConfigError):
            instrument.applyConfigValue("smu1VoltageComplianceV", 20.004)
        instrument.applyConfigValue("smu1ForceCurrentLevelA", 0.05)
        instrument.applyConfigValue("smu1VoltageComplianceV", 40)
        instrument.applyConfigValue("smu1ForceCurrentLevelA", 0.02)
        instrument.applyConfigValue("smu1VoltageComplianceV", 100)
        with self.assertRaises(Hp4145BConfigError):
            instrument.applyConfigValue("smu1ForceCurrentLevelA", 0.0201)
        self.assertEqual(instrument.jobConfig["smu1ForceCurrentLevelA"], 0.02)

        for illegal in (0, -1e-9, 4.9e-11, math.nan, math.inf, "1;BC", "1,2"):
            with self.subTest(illegal=illegal):
                fresh, fresh_transport = self._instrument()
                with self.assertRaises(Hp4145BConfigError):
                    fresh.applyConfigValue("smu1CurrentComplianceA", illegal)
                self.assertEqual(fresh_transport.writtenCommands, [])
                self.assertEqual(fresh.jobConfig["smu1CurrentComplianceA"], 1e-6)
        instrument, transport = self._instrument()
        with self.assertRaises(Hp4145BConfigError):
            instrument.applyConfigValue("smu1ForceMode", "COM")
        with self.assertRaises(Hp4145BConfigError):
            instrument.applyConfigValue("smu1VoltageRangeIndex", 4)
        with self.assertRaises(Hp4145BConfigError):
            instrument.applyConfigValue("smu1CurrentRangeIndex", 10)
        with self.assertRaises(Hp4145BConfigError):
            instrument.applyConfigValue("smu1ForceVoltageLevelV", math.nan)
        self.assertEqual(transport.writtenCommands, [])
        self.assertLessEqual(len(_formatProgramNumber(Decimal("-9.9995E+01"))), 12)
        self.assertLessEqual(len(_formatProgramNumber(Decimal("5E-11"))), 12)

    def test_fixed_range_uses_that_range_current_limit(self) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("smu1VoltageRangeIndex", 3)
        instrument.applyConfigValue("smu1ForceVoltageLevelV", 1)
        instrument.applyConfigValue("smu1CurrentComplianceA", 0.02)
        instrument.applyConfigValue("smu1OutputEnabled", 1)
        self.assertEqual(transport.writtenCommands[-1], "DV1,3,1E+00,2E-02")

    def test_readings_parse_status_and_do_not_reconfigure(self) -> None:
        instrument, transport = self._instrument(
            responses=["NAI+1.2345E-03\r\n", "N A I +2.0000E-06\r\n"]
        )
        self.assertEqual(
            instrument.readMeasurements(["smu1CurrentA", "missing", "smu1CurrentA"]),
            {"smu1CurrentA": 1.2345e-3},
        )
        self.assertEqual(transport.writtenCommands, ["US", "IT2", "BC", "TI1"])
        self.assertEqual(
            instrument.readMeasurements(["smu1CurrentA"]),
            {"smu1CurrentA": 2e-6},
        )
        self.assertEqual(transport.writtenCommands.count("US"), 1)
        self.assertNotIn("DO", transport.writtenCommands)
        self.assertFalse(
            any(command.startswith("*") for command in transport.writtenCommands)
        )
        self.assertFalse(
            any(command.startswith(":") for command in transport.writtenCommands)
        )

        instrument.applyConfigValue("smu2ForceMode", "CURR")
        transport._responses.extend(
            ["NBV-2.0000E+01\r\n", "NFV-5.0000E-01\r\n", "NEV+1.0000E+00\r\n"]
        )
        transport.writtenCommands.clear()
        self.assertEqual(
            instrument.readMeasurements(
                ["smu2VoltageV", "smu2CurrentA", "vm2VoltageV", "vm1VoltageV"]
            ),
            {
                "smu2VoltageV": -20.0,
                "vm2VoltageV": -0.5,
                "vm1VoltageV": 1.0,
            },
        )
        self.assertEqual(
            transport.writtenCommands,
            ["BC", "TV2", "BC", "TV6", "BC", "TV5"],
        )

    def test_abnormal_and_malformed_readings_raise_without_retry(self) -> None:
        cases = [
            ("CAI+1.0000E-06\r\n", Hp4145BMeasurementStatusError, "C"),
            ("TAI+1.0000E-06\r\n", Hp4145BMeasurementStatusError, "T"),
            ("LAI+1.0000E-06\r\n", Hp4145BMeasurementStatusError, "L"),
            ("VAI+1.0000E-06\r\n", Hp4145BMeasurementStatusError, "V"),
            ("XAI+1.0000E-06\r\n", Hp4145BMeasurementStatusError, "X"),
            ("NBI+1.0000E-06\r\n", Hp4145BMeasurementError, None),
            ("NAV+1.0000E-06\r\n", Hp4145BMeasurementError, None),
            ("\r\n", Hp4145BMeasurementError, None),
            ("1.0000E-06\r\n", Hp4145BMeasurementError, None),
            ("NAI+nan\r\n", Hp4145BMeasurementError, None),
            ("NAI+inf\r\n", Hp4145BMeasurementError, None),
        ]
        for raw, error_type, status in cases:
            with self.subTest(raw=raw):
                instrument, transport = self._instrument(responses=[raw, raw])
                with self.assertRaises(error_type) as caught:
                    instrument.readMeasurements(["smu1CurrentA"])
                if status is not None:
                    self.assertEqual(caught.exception.status, status)
                self.assertEqual(transport.writtenCommands, ["US", "IT2", "BC", "TI1"])
                self.assertEqual(transport._responses, [raw])

    def test_timeout_is_not_zero_and_next_command_reenters_user_mode(self) -> None:
        instrument, transport = self._instrument(responses=["NAI+1.0000E-03\r\n"])
        transport.fail_reads = 1
        with self.assertRaises(TimeoutError):
            instrument.readMeasurements(["smu1CurrentA"])
        self.assertEqual(
            instrument.readMeasurements(["smu1CurrentA"]), {"smu1CurrentA": 1e-3}
        )
        self.assertEqual(transport.writtenCommands.count("US"), 2)
        self.assertEqual(transport.writtenCommands.count("TI1"), 2)

    def test_voltage_request_in_voltage_mode_does_no_io(self) -> None:
        instrument, transport = self._instrument()
        self.assertEqual(instrument.readMeasurements(["smu1VoltageV"]), {})
        self.assertEqual(transport.writtenCommands, [])

    def test_safe_state_shutdown_partial_hold_and_failed_channel(self) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("smu1OutputEnabled", 1)
        instrument.applyConfigValue("smu2OutputEnabled", 1)
        transport.writtenCommands.clear()
        instrument.applySafeState()
        self.assertEqual(
            transport.writtenCommands,
            ["DV1", "DV2", "DV3", "DV4", "DS1,0", "DS2,0"],
        )
        self.assertEqual(instrument.jobConfig["smu1OutputEnabled"], 0)
        self.assertEqual(instrument.jobConfig["smu2OutputEnabled"], 0)

        held, held_transport = self._instrument(
            metadata={
                "safeConfig": {
                    "smu1OutputEnabled": 1,
                    "smu1ForceVoltageLevelV": 1.0,
                    "vs1VoltageLevelV": 5,
                }
            }
        )
        held.applyConfigValue("smu2OutputEnabled", 1)
        held_transport.writtenCommands.clear()
        held.applySafeState()
        self.assertIn("DV1,0,1E+00,1E-06", held_transport.writtenCommands)
        self.assertIn("DV2", held_transport.writtenCommands)
        self.assertNotIn("DV2,0,0,1E-06", held_transport.writtenCommands)
        self.assertEqual(held_transport.writtenCommands[-2:], ["DS1,5E+00", "DS2,0"])
        self.assertEqual(held.jobConfig["smu1OutputEnabled"], 1)
        self.assertEqual(held.jobConfig["smu2OutputEnabled"], 0)

        illegal, illegal_transport = self._instrument(
            metadata={
                "safeConfig": {
                    "smu1OutputEnabled": 1,
                    "smu1ForceVoltageLevelV": 80,
                    "smu1CurrentComplianceA": 0.05,
                }
            }
        )
        illegal.applyConfigValue("smu1OutputEnabled", 1)
        illegal_transport.writtenCommands.clear()
        illegal.applySafeState()
        self.assertFalse(
            any(
                command.startswith("DV1,")
                for command in illegal_transport.writtenCommands
            )
        )
        self.assertIn("DV1", illegal_transport.writtenCommands)
        self.assertEqual(illegal.jobConfig["smu1OutputEnabled"], 0)

        failing, failing_transport = self._instrument(fail_commands={"DV2"})
        failing.applyConfigValue("smu2OutputEnabled", 1)
        failing_transport.writtenCommands.clear()
        with self.assertRaises(Hp4145BSafeStateError) as caught:
            failing.applySafeState()
        self.assertTrue(any("SMU2" in item for item in caught.exception.errors))
        self.assertIn("DV1", failing_transport.writtenCommands)
        self.assertIn("DV3", failing_transport.writtenCommands)
        self.assertIn("DV4", failing_transport.writtenCommands)
        self.assertIn("DS1,0", failing_transport.writtenCommands)
        self.assertIn("DS2,0", failing_transport.writtenCommands)
        self.assertEqual(failing.jobConfig["smu2OutputEnabled"], 1)
        self.assertEqual(failing.jobConfig["smu3OutputEnabled"], 0)

    def test_software_slew_sends_full_commands_for_enabled_channels(self) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("smu1OutputEnabled", 1)
        instrument.applyConfigValue("smu2OutputEnabled", 1)
        transport.writtenCommands.clear()
        bounds = build_value_bounds_by_instrument({"spa": Hp4145BInstrument})
        safety = SetpointSafetyController(bounds)
        safety.seed_last_value("spa", "smu1ForceVoltageLevelV", 0.0)
        completed = safety.apply_slew_limited(
            instrument_id="spa",
            instrument=instrument,
            key="smu1ForceVoltageLevelV",
            target_value=0.05,
            max_slew_rate=1.0,
            max_slew_step=0.01,
        )
        self.assertTrue(completed)
        self.assertEqual(
            transport.writtenCommands,
            [
                "DV1,0,1E-02,1E-06",
                "DV1,0,2E-02,1E-06",
                "DV1,0,3E-02,1E-06",
                "DV1,0,4E-02,1E-06",
                "DV1,0,5E-02,1E-06",
            ],
        )

    def test_safe_state_waits_until_a_blocked_read_finishes(self) -> None:
        transport = _BlockingReadTransport()
        instrument = Hp4145BInstrument("spa", transport)
        errors: list[BaseException] = []

        def _read() -> None:
            try:
                instrument.readMeasurements(["smu1CurrentA"])
            except BaseException as exc:  # pragma: no cover - test harness
                errors.append(exc)

        reader = threading.Thread(target=_read)
        reader.start()
        self.assertTrue(transport.entered.wait(2))
        safe = threading.Thread(target=instrument.applySafeState)
        safe.start()
        self.assertNotIn("DS1,0", transport.writtenCommands)
        transport.release.set()
        reader.join(2)
        safe.join(2)
        self.assertFalse(reader.is_alive())
        self.assertFalse(safe.is_alive())
        self.assertEqual(errors, [])
        self.assertIn("DS1,0", transport.writtenCommands)
        self.assertEqual(transport.writtenCommands[0], "US")


if __name__ == "__main__":
    unittest.main()
