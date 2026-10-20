"""YOKOGAWA 7651 driver tests. No hardware and no VISA session."""

from __future__ import annotations

import math
import unittest
from typing import Any

from genesis.core.instrument.config_field import ConfigChoice
from genesis.core.instrument.discovery import loadBuiltInInstruments
from genesis.core.instrument.registry import InstrumentRegistry
from genesis.core.runtime.setpoint_safety import (
    SetpointSafetyController,
    build_value_bounds_by_instrument,
)
from genesis.core.transport.base_transport import BaseTransport
from genesis.instruments.yokogawa7651.driver import (
    INSTRUMENT_TYPE_KEY,
    Yokogawa7651ConfigError,
    Yokogawa7651Instrument,
    Yokogawa7651OverloadError,
    Yokogawa7651ResponseError,
    Yokogawa7651SafeStateError,
    format_source_number,
    registerInstruments,
)
from decimal import Decimal


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
        self.fail_nth: dict[str, int] = {}
        self._seen: dict[str, int] = {}

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def write(self, command: str) -> None:
        self.writtenCommands.append(command)
        self._seen[command] = self._seen.get(command, 0) + 1
        if command in self.fail_commands or self._seen[command] == self.fail_nth.get(
            command
        ):
            raise RuntimeError(f"write failed: {command}")

    def read(self) -> str:
        if not self._responses:
            raise TimeoutError("visa timeout")
        return self._responses.pop(0)


class Yokogawa7651DriverTests(unittest.TestCase):
    def _instrument(
        self,
        jobConfig: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        responses: list[str] | None = None,
        fail_commands: set[str] | None = None,
    ) -> tuple[Yokogawa7651Instrument, _ScriptedTransport]:
        transport = _ScriptedTransport(responses, fail_commands)
        instrument = Yokogawa7651Instrument(
            name="source",
            transport=transport,
            metadata=metadata,
            jobConfig=jobConfig,
        )
        return instrument, transport

    def test_registration_defaults_and_transport(self) -> None:
        registry = InstrumentRegistry()
        registerInstruments(registry)
        self.assertIs(
            registry.getInstrumentType(INSTRUMENT_TYPE_KEY), Yokogawa7651Instrument
        )
        discovered = InstrumentRegistry()
        loadBuiltInInstruments(discovered)
        self.assertIn("yokogawa7651", discovered.listInstruments())
        self.assertEqual(Yokogawa7651Instrument.getDefaultAddress(), "")
        self.assertEqual(Yokogawa7651Instrument.getSupportedTransportKeys(), ["visa"])
        self.assertEqual(Yokogawa7651Instrument.getDefaultTransportKey(), "visa")
        self.assertEqual(
            Yokogawa7651Instrument.getDefaultTransportSettings(),
            {
                "visaTimeoutMs": 10000,
                "writeTermination": "\n",
                "readTermination": "\n",
                "visaSendEndOnWrite": True,
                "visaClearOnOpen": False,
            },
        )
        fields = {
            field.key: field for field in Yokogawa7651Instrument.getJobConfigFields()
        }
        self.assertEqual(fields["forceMode"].default, "VOLT")
        self.assertEqual(fields["forceVoltageLevelV"].default, 0.0)
        self.assertEqual(fields["forceCurrentLevelA"].default, 0.0)
        self.assertEqual(fields["voltageRangeIndex"].default, 4)
        self.assertEqual(fields["currentRangeIndex"].default, 4)
        self.assertEqual(fields["voltageRangeAuto"].default, 0)
        self.assertEqual(fields["currentRangeAuto"].default, 0)
        self.assertEqual(fields["voltageLimitV"].default, 1)
        self.assertEqual(fields["currentLimitA"].default, 0.005)
        self.assertEqual(fields["outputEnabled"].default, 0)
        self.assertEqual(
            [choice.value for choice in fields["voltageRangeIndex"].choices or []],
            [2, 3, 4, 5, 6],
        )
        self.assertEqual(
            [choice.value for choice in fields["currentRangeIndex"].choices or []],
            [4, 5, 6],
        )
        sweepable = {field.key for field in fields.values() if field.sweepable}
        self.assertEqual(sweepable, {"forceVoltageLevelV", "forceCurrentLevelA"})
        for field in fields.values():
            self.assertTrue(field.helpText)
            if field.fieldType == "enum":
                self.assertIsInstance(field.choices, list)
                self.assertTrue(
                    all(
                        isinstance(choice, ConfigChoice)
                        for choice in field.choices or []
                    )
                )
                self.assertIn(
                    field.default, [choice.value for choice in field.choices or []]
                )
        signals = dict(Yokogawa7651Instrument.getAvailableMeasurementSignals())
        self.assertIn("setting", signals["sourceVoltageSettingV"].lower())
        self.assertEqual(
            Yokogawa7651Instrument.getAvailablePlotSignals(),
            Yokogawa7651Instrument.getAvailableMeasurementSignals(),
        )
        created = registry.createInstrument(
            INSTRUMENT_TYPE_KEY, name="source", transport=_ScriptedTransport()
        )
        self.assertIsInstance(created, Yokogawa7651Instrument)
        self.assertTrue(created.shouldUseRuntimeSlew("forceVoltageLevelV"))
        self.assertTrue(created.shouldUseRuntimeSlew("forceCurrentLevelA"))

    def test_initialize_executes_output_off_before_the_source(self) -> None:
        instrument, transport = self._instrument()
        instrument.initialize()
        self.assertEqual(
            transport.writtenCommands,
            ["RU0", "H1", "DL0", "O0E", "F1R4S+0E+0E", "LA5"],
        )
        self.assertNotIn("RC", transport.writtenCommands)
        self.assertNotIn("O1E", transport.writtenCommands)
        self.assertNotIn("LV1", transport.writtenCommands)
        instrument.finalizeInitialization()
        self.assertEqual(transport.writtenCommands.count("RU0"), 1)

    def test_per_key_path_prepares_once_and_does_not_enable_output(self) -> None:
        instrument, transport = self._instrument()
        for key, value in Yokogawa7651Instrument.getDefaultJobConfig().items():
            instrument.applyConfigValue(key, value)
        instrument.finalizeInitialization()
        self.assertEqual(transport.writtenCommands[0], "RU0")
        self.assertEqual(transport.writtenCommands.count("RU0"), 1)
        self.assertNotIn("RC", transport.writtenCommands)
        self.assertNotIn("O1E", transport.writtenCommands)
        self.assertNotIn("O0", transport.writtenCommands)
        self.assertNotIn("O1", transport.writtenCommands)
        self.assertIn("O0E", transport.writtenCommands)
        self.assertIn("F1R4S+0E+0E", transport.writtenCommands)
        self.assertIn("LA5", transport.writtenCommands)
        self.assertFalse(
            any(command.startswith("LV") for command in transport.writtenCommands)
        )
        self.assertFalse(
            any(command.startswith("F5") for command in transport.writtenCommands)
        )
        for command in transport.writtenCommands:
            if command.startswith(("S", "SA", "O0", "O1")):
                self.assertTrue(command.endswith("E"), command)

    def test_source_and_output_commands_include_execute(self) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("forceVoltageLevelV", 1)
        self.assertIn("F1R4S+1E+0E", transport.writtenCommands)
        self.assertNotIn("F1R4S+1E+0", transport.writtenCommands)
        transport.writtenCommands.clear()
        instrument.applyConfigValue("forceVoltageLevelV", 1)
        self.assertEqual(transport.writtenCommands, ["S+1E+0E"])
        instrument.applyConfigValue("outputEnabled", 1)
        self.assertEqual(transport.writtenCommands[-1], "O1E")
        instrument.applyConfigValue("voltageRangeAuto", 1)
        self.assertEqual(
            transport.writtenCommands[-4:],
            ["O0E", "F1SA+1E+0E", "LA5", "O1E"],
        )

    def test_failed_execute_does_not_keep_the_new_setting(self) -> None:
        instrument, transport = self._instrument(fail_commands={"F1R4S+1E+0E"})
        with self.assertRaises(RuntimeError):
            instrument.applyConfigValue("forceVoltageLevelV", 1)
        self.assertEqual(instrument.jobConfig["forceVoltageLevelV"], 0.0)
        self.assertIn("O0E", transport.writtenCommands)
        self.assertIn("F1R4S+1E+0E", transport.writtenCommands)
        transport.fail_commands.clear()
        instrument.applyConfigValue("forceVoltageLevelV", 1)
        retry = transport.writtenCommands[
            transport.writtenCommands.index("F1R4S+1E+0E") + 1 :
        ]
        self.assertIn("O0E", retry)
        self.assertIn("F1R4S+1E+0E", retry)
        self.assertEqual(instrument.jobConfig["forceVoltageLevelV"], 1.0)

    def test_ranges_limits_and_rejected_values(self) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("outputEnabled", 0)
        for code in (2, 3, 4, 5, 6):
            instrument.applyConfigValue("voltageRangeIndex", code)
            self.assertTrue(
                any(
                    command.startswith(f"F1R{code}S")
                    for command in transport.writtenCommands
                )
            )
        before_current = len(transport.writtenCommands)
        instrument.applyConfigValue("forceMode", "CURR")
        current_commands = transport.writtenCommands[before_current:]
        self.assertTrue(
            any(command.startswith("F5R4S") for command in current_commands)
        )
        self.assertIn("LV1", current_commands)
        self.assertFalse(any(command.startswith("LA") for command in current_commands))
        for code in (4, 5, 6):
            instrument.applyConfigValue("currentRangeIndex", code)
            self.assertTrue(
                any(
                    command.startswith(f"F5R{code}S")
                    for command in transport.writtenCommands
                )
            )

        accepted = [
            ("VOLT", 4, 0, "forceVoltageLevelV", 1.2),
            ("VOLT", 2, 0, "forceVoltageLevelV", 0.012),
            ("VOLT", 6, 0, "forceVoltageLevelV", 32),
            ("VOLT", 6, 0, "forceVoltageLevelV", -32),
            ("CURR", 4, 0, "forceCurrentLevelA", 0.0012),
            ("CURR", 6, 0, "forceCurrentLevelA", -0.12),
            ("VOLT", 4, 1, "forceVoltageLevelV", 20),
        ]
        for mode, range_index, auto, key, value in accepted:
            with self.subTest(mode=mode, value=value):
                fresh, _transport = self._instrument()
                fresh.applyConfigValue("forceMode", mode)
                if mode == "VOLT":
                    fresh.applyConfigValue("voltageRangeIndex", range_index)
                    fresh.applyConfigValue("voltageRangeAuto", auto)
                else:
                    fresh.applyConfigValue("currentRangeIndex", range_index)
                    fresh.applyConfigValue("currentRangeAuto", auto)
                fresh.applyConfigValue(key, value)

        rejected = [
            ("forceVoltageLevelV", 1.20001, {}),
            ("forceVoltageLevelV", math.nan, {}),
            ("forceVoltageLevelV", math.inf, {}),
            ("forceVoltageLevelV", "1;O1E", {}),
            ("voltageRangeIndex", 1, {}),
            ("currentRangeIndex", 2, {}),
            ("forceMode", "COM", {}),
            ("currentLimitA", 0, {}),
            ("currentLimitA", 0.004, {}),
            ("currentLimitA", 0.0055, {}),
            ("currentLimitA", 0.121, {}),
            ("voltageLimitV", 0, {}),
            ("voltageLimitV", 1.5, {}),
            ("voltageLimitV", 31, {}),
        ]
        for key, value, _extra in rejected:
            with self.subTest(key=key, value=value):
                fresh, fresh_transport = self._instrument()
                with self.assertRaises(Yokogawa7651ConfigError):
                    fresh.applyConfigValue(key, value)
                self.assertEqual(fresh_transport.writtenCommands, [])

        instrument, transport = self._instrument()
        instrument.applyConfigValue("currentLimitA", 0.12)
        self.assertIn("LA120", transport.writtenCommands)
        self.assertNotIn("LA0.12", transport.writtenCommands)
        self.assertNotIn("LA0.005", transport.writtenCommands)
        instrument.applyConfigValue("currentLimitA", 0.005)
        self.assertIn("LA5", transport.writtenCommands)
        transport.writtenCommands.clear()
        instrument.applyConfigValue("voltageLimitV", 30)
        self.assertEqual(transport.writtenCommands, [])
        instrument.applyConfigValue("forceMode", "CURR")
        self.assertIn("LV30", transport.writtenCommands)
        self.assertFalse(
            any(command.startswith("LA") for command in transport.writtenCommands)
        )

    def test_closed_output_edits_do_not_enable_and_mode_switch_is_ordered(self) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("forceVoltageLevelV", 0.5)
        instrument.applyConfigValue("currentLimitA", 0.01)
        instrument.applyConfigValue("voltageRangeIndex", 5)
        self.assertNotIn("O1E", transport.writtenCommands)
        self.assertIn("LA10", transport.writtenCommands)
        transport.writtenCommands.clear()
        instrument.applyConfigValue("outputEnabled", 1)
        self.assertEqual(transport.writtenCommands, ["O1E"])
        transport.writtenCommands.clear()
        instrument.applyConfigValue("forceMode", "CURR")
        self.assertEqual(
            transport.writtenCommands,
            ["O0E", "F5R4S+0E+0E", "LV1", "O1E"],
        )

    def test_setting_readback_and_status_bits(self) -> None:
        instrument, transport = self._instrument(
            responses=[
                "NDCV+1.20000E+0\r\n",
                "STS1=16\r\n",
                "NDCV +0000.99E+3\r\n",
            ]
        )
        self.assertEqual(instrument.readMeasurements(["sourceCurrentSettingA"]), {})
        self.assertEqual(transport.writtenCommands, [])
        self.assertEqual(
            instrument.readMeasurements(
                ["sourceVoltageSettingV", "sourceVoltageSettingV", "outputEnabled"]
            ),
            {"sourceVoltageSettingV": 1.2, "outputEnabled": 1.0},
        )
        self.assertEqual(
            transport.writtenCommands,
            ["RU0", "H1", "DL0", "OD", "OC"],
        )
        self.assertEqual(
            instrument.readMeasurements(["sourceVoltageSettingV"]),
            {"sourceVoltageSettingV": 990.0},
        )
        self.assertEqual(transport.writtenCommands.count("RU0"), 1)
        self.assertNotIn("O0E", transport.writtenCommands)
        self.assertNotIn("O1E", transport.writtenCommands)

        for raw, enabled, unstable, error in (
            ("STS1=0", 0.0, 0.0, 0.0),
            ("STS1=16", 1.0, 0.0, 0.0),
            ("STS1=24", 1.0, 1.0, 0.0),
            ("STS1=21", 1.0, 0.0, 1.0),
            ("STS1=32", 0.0, 0.0, 0.0),
            ("STS1=8", 0.0, 1.0, 0.0),
            ("STS1=4", 0.0, 0.0, 1.0),
        ):
            with self.subTest(raw=raw):
                reader, reader_transport = self._instrument(responses=[raw])
                self.assertEqual(
                    reader.readMeasurements(
                        ["outputEnabled", "outputUnstable", "communicationError"]
                    ),
                    {
                        "outputEnabled": enabled,
                        "outputUnstable": unstable,
                        "communicationError": error,
                    },
                )
                self.assertEqual(reader_transport.writtenCommands.count("OC"), 1)

    def test_overload_malformed_and_timeout_readings(self) -> None:
        cases = [
            ("EDCV+1.20000E+1\r\n", Yokogawa7651OverloadError),
            ("NDCA+1.00000E-3\r\n", Yokogawa7651ResponseError),
            ("\r\n", Yokogawa7651ResponseError),
            ("+1.20000E+0\r\n", Yokogawa7651ResponseError),
            ("NDCV+nan\r\n", Yokogawa7651ResponseError),
            ("NDCVinf\r\n", Yokogawa7651ResponseError),
        ]
        for raw, error_type in cases:
            with self.subTest(raw=raw):
                instrument, transport = self._instrument(responses=[raw])
                with self.assertRaises(error_type):
                    instrument.readMeasurements(["sourceVoltageSettingV"])
                self.assertEqual(transport.writtenCommands[-1], "OD")
                self.assertNotIn("O1E", transport.writtenCommands)

        instrument, transport = self._instrument()
        with self.assertRaises(TimeoutError):
            instrument.readMeasurements(["outputEnabled"])
        self.assertEqual(instrument.jobConfig["outputEnabled"], 0)
        transport._responses.append("STS1=16")
        self.assertEqual(
            instrument.readMeasurements(["outputEnabled"]),
            {"outputEnabled": 1.0},
        )

    def test_panel_settings_consume_end_and_truncated_os_blocks_later_reads(
        self,
    ) -> None:
        instrument, transport = self._instrument(
            responses=[
                "7651 1.00",
                "F1R4+0",
                "0.1,0.0,RPT",
                "30,120",
                "END",
                "NDCV+0E+0\r\n",
            ]
        )
        self.assertEqual(
            instrument.readPanelSettings(),
            ["7651 1.00", "F1R4+0", "0.1,0.0,RPT", "30,120", "END"],
        )
        self.assertEqual(
            instrument.readMeasurements(["sourceVoltageSettingV"]),
            {"sourceVoltageSettingV": 0.0},
        )
        self.assertEqual(transport.writtenCommands[-1], "OD")

        truncated, truncated_transport = self._instrument(
            responses=["7651 1.00", "F1R4+0", "partial"]
        )
        with self.assertRaises(Yokogawa7651ResponseError):
            truncated.readPanelSettings()
        truncated_transport._responses.append("NDCV+1E+0")
        with self.assertRaises(Yokogawa7651ResponseError):
            truncated.readMeasurements(["sourceVoltageSettingV"])
        self.assertEqual(truncated_transport._responses, ["NDCV+1E+0"])
        truncated.applySafeState()
        self.assertIn("O0E", truncated_transport.writtenCommands)

    def test_safe_state_shutdown_hold_invalid_bias_and_write_failure(self) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("outputEnabled", 1)
        instrument.applyConfigValue("forceVoltageLevelV", 1)
        transport.writtenCommands.clear()
        instrument.applySafeState()
        self.assertEqual(transport.writtenCommands, ["RU0", "O0E"])
        self.assertEqual(instrument.jobConfig["outputEnabled"], 0)
        self.assertEqual(instrument.jobConfig["forceVoltageLevelV"], 1.0)
        self.assertNotIn("O1E", transport.writtenCommands)

        held, held_transport = self._instrument(
            metadata={
                "safeConfig": {
                    "outputEnabled": 1,
                    "forceVoltageLevelV": 0.25,
                }
            }
        )
        held.applySafeState()
        self.assertIn("O0E", held_transport.writtenCommands)
        self.assertIn("F1R4S+2.5E-1E", held_transport.writtenCommands)
        self.assertIn("LA5", held_transport.writtenCommands)
        self.assertEqual(held_transport.writtenCommands[-1], "O1E")
        self.assertEqual(held.jobConfig["outputEnabled"], 1)
        self.assertEqual(held.jobConfig["forceVoltageLevelV"], 0.25)

        partial, partial_transport = self._instrument(
            metadata={"safeConfig": {"forceVoltageLevelV": 0.5}}
        )
        partial.applyConfigValue("outputEnabled", 1)
        partial_transport.writtenCommands.clear()
        partial.applySafeState()
        self.assertIn("O0E", partial_transport.writtenCommands)
        self.assertNotIn("O1E", partial_transport.writtenCommands)
        self.assertEqual(partial.jobConfig["outputEnabled"], 0)
        self.assertEqual(partial.jobConfig["forceVoltageLevelV"], 0.5)

        illegal, illegal_transport = self._instrument(
            metadata={
                "safeConfig": {
                    "outputEnabled": 1,
                    "forceVoltageLevelV": 20,
                    "voltageRangeAuto": 0,
                    "voltageRangeIndex": 4,
                }
            }
        )
        illegal.applyConfigValue("outputEnabled", 1)
        illegal_transport.writtenCommands.clear()
        with self.assertRaises(Yokogawa7651SafeStateError) as caught:
            illegal.applySafeState()
        self.assertIn("O0E", illegal_transport.writtenCommands)
        self.assertNotIn("O1E", illegal_transport.writtenCommands)
        self.assertEqual(illegal.jobConfig["outputEnabled"], 0)
        self.assertTrue(caught.exception.errors)

        failing, failing_transport = self._instrument()
        failing_transport.fail_nth["O0E"] = 2
        failing.applyConfigValue("outputEnabled", 1)
        failing_transport.writtenCommands.clear()
        with self.assertRaises(Yokogawa7651SafeStateError) as caught:
            failing.applySafeState()
        self.assertIn("RU0", failing_transport.writtenCommands)
        self.assertIn("O0E", failing_transport.writtenCommands)
        self.assertNotIn("O1E", failing_transport.writtenCommands)
        self.assertEqual(failing.jobConfig["outputEnabled"], 1)
        self.assertTrue(any("O0E" in item for item in caught.exception.errors))

    def test_software_slew_sends_one_executed_source_command_per_step(self) -> None:
        instrument, transport = self._instrument()
        instrument.applyConfigValue("outputEnabled", 1)
        transport.writtenCommands.clear()
        bounds = build_value_bounds_by_instrument({"source": Yokogawa7651Instrument})
        safety = SetpointSafetyController(bounds)
        safety.seed_last_value("source", "forceVoltageLevelV", 0.0)
        completed = safety.apply_slew_limited(
            instrument_id="source",
            instrument=instrument,
            key="forceVoltageLevelV",
            target_value=0.05,
            max_slew_rate=1.0,
            max_slew_step=0.01,
        )
        self.assertTrue(completed)
        self.assertEqual(
            transport.writtenCommands,
            ["S+1E-2E", "S+2E-2E", "S+3E-2E", "S+4E-2E", "S+5E-2E"],
        )
        self.assertEqual(transport.writtenCommands.count("RU0"), 0)
        self.assertLessEqual(len(format_source_number(Decimal("-32"))), 20)

    def test_current_mode_read_uses_one_od_and_no_voltage_command(self) -> None:
        instrument, transport = self._instrument(responses=["NDCA-1.50000E-3\r\n"])
        instrument.applyConfigValue("forceMode", "CURR")
        transport.writtenCommands.clear()
        self.assertEqual(
            instrument.readMeasurements(
                ["sourceVoltageSettingV", "sourceCurrentSettingA"]
            ),
            {"sourceCurrentSettingA": -0.0015},
        )
        self.assertEqual(transport.writtenCommands, ["OD"])
        self.assertNotIn("F1", "".join(transport.writtenCommands))


if __name__ == "__main__":
    unittest.main()
