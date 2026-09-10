from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from genesis.app.main_window import MainWindow
from genesis.core.job.model import JobModel
from genesis.ui.job_builder.critical_condition_editor import (
    CriticalConditionEditor,
    MeasurementSignalRef,
)


class CriticalRampingUiTests(unittest.TestCase):
    _app: QApplication | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_editor_round_trip_and_disabled_legacy_payload(self) -> None:
        editor = CriticalConditionEditor(
            availableSignals=[
                MeasurementSignalRef("dmm", "currentA", "Current"),
                MeasurementSignalRef("dmm", "voltageV", "Voltage"),
            ]
        )
        payload = {
            "enabled": True,
            "holdSettingsOnTrigger": True,
            "combine": "all",
            "consecutiveHitsRequired": 3,
            "conditions": [
                {
                    "instrumentId": "dmm",
                    "signalKey": "currentA",
                    "comparison": "gt",
                    "threshold": 1.5,
                }
            ],
        }
        editor.setDefinition(payload)
        restored = editor.toDefinition()
        self.assertEqual(restored["enabled"], True)
        self.assertEqual(restored["holdSettingsOnTrigger"], True)
        self.assertEqual(restored["combine"], "all")
        self.assertEqual(restored["consecutiveHitsRequired"], 3)
        self.assertEqual(restored["conditions"][0]["signalKey"], "currentA")
        self.assertAlmostEqual(float(restored["conditions"][0]["threshold"]), 1.5)

        editor.setDefinition(None)
        disabled = editor.toDefinition()
        self.assertFalse(disabled["enabled"])
        self.assertEqual(disabled["conditions"], [])
        self.assertIsNone(editor.validateDefinition())

    def test_hold_keeps_start_disabled_and_does_not_reinitialize(self) -> None:
        window = MainWindow()
        window.currentJob = JobModel(jobId="job-1", rawDefinition={"jobId": "job-1"})
        window._initializedJobId = "job-1"
        window._initializedInstrumentsById = {"dev": object()}
        window._onCriticalTriggered(
            {
                "reason": "Critical ramping triggered: dev:i = 2 > 1",
                "holdSettingsOnTrigger": True,
                "instrumentId": "dev",
                "signalKey": "i",
                "measuredValue": 2.0,
                "comparison": "gt",
                "threshold": 1.0,
            }
        )
        self.assertTrue(window._criticalHoldActive)
        self.assertIsNone(window._taskThread)
        self.assertFalse(window.holdStateLabel.isHidden())
        self.assertIn("SETTINGS HELD", window.holdStateLabel.text())
        window._refreshControlStates()
        self.assertFalse(window.startRunButton.isEnabled())
        self.assertTrue(window.stopRunButton.isEnabled())
        window._onStartClicked()
        self.assertIn("held", window.statusLabel.text().lower())

    def test_non_hold_trigger_without_instruments_does_not_hold(self) -> None:
        window = MainWindow()
        window.currentJob = JobModel(jobId="job-1", rawDefinition={"jobId": "job-1"})
        window._onCriticalTriggered(
            {
                "reason": "Critical ramping triggered: dev:i = 2 > 1",
                "holdSettingsOnTrigger": False,
            }
        )
        self.assertFalse(window._criticalHoldActive)
        self.assertFalse(window.holdStateLabel.isVisible())
        self.assertIn("ended", window.statusLabel.text().lower())


if __name__ == "__main__":
    unittest.main()
