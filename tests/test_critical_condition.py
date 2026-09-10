from __future__ import annotations

import math
import unittest

from genesis.core.runtime.critical_condition import (
    CriticalCondition,
    CriticalConditionMonitor,
    CriticalRampingConfig,
    merge_critical_measurement_keys,
    validate_critical_ramping_config,
)


class CriticalConditionTests(unittest.TestCase):
    def test_lt_and_gt_boundaries(self) -> None:
        lt = CriticalCondition("dmm", "currentA", "lt", 1.0)
        gt = CriticalCondition("dmm", "currentA", "gt", 1.0)
        self.assertTrue(lt.evaluate(0.999))
        self.assertFalse(lt.evaluate(1.0))
        self.assertFalse(lt.evaluate(1.001))
        self.assertTrue(gt.evaluate(1.001))
        self.assertFalse(gt.evaluate(1.0))
        self.assertFalse(gt.evaluate(0.999))

    def test_nan_and_non_finite_do_not_match(self) -> None:
        cond = CriticalCondition("dmm", "currentA", "gt", 0.0)
        self.assertFalse(cond.evaluate(float("nan")))
        self.assertFalse(cond.evaluate(float("inf")))
        self.assertFalse(cond.evaluate(float("-inf")))

    def test_any_and_all_combine(self) -> None:
        any_cfg = CriticalRampingConfig(
            enabled=True,
            combine="any",
            conditions=[
                CriticalCondition("dmm", "i", "gt", 1.0),
                CriticalCondition("dmm", "v", "lt", 0.0),
            ],
        )
        all_cfg = CriticalRampingConfig(
            enabled=True,
            combine="all",
            conditions=list(any_cfg.conditions),
        )
        sample = {"dmm": {"i": 2.0, "v": 1.0}}
        any_hit = CriticalConditionMonitor(any_cfg).evaluate(sample)
        all_hit = CriticalConditionMonitor(all_cfg).evaluate(sample)
        self.assertIsNotNone(any_hit)
        self.assertIsNone(all_hit)
        both = {"dmm": {"i": 2.0, "v": -1.0}}
        self.assertIsNotNone(CriticalConditionMonitor(all_cfg).evaluate(both))

    def test_consecutive_hits_debounce(self) -> None:
        cfg = CriticalRampingConfig(
            enabled=True,
            consecutive_hits_required=3,
            conditions=[CriticalCondition("dmm", "i", "gt", 1.0)],
        )
        monitor = CriticalConditionMonitor(cfg)
        below = {"dmm": {"i": 0.5}}
        above = {"dmm": {"i": 1.5}}
        self.assertIsNone(monitor.evaluate(above))
        self.assertIsNone(monitor.evaluate(above))
        trigger = monitor.evaluate(above)
        self.assertIsNotNone(trigger)
        self.assertAlmostEqual(trigger.measured_value, 1.5)

        monitor.reset()
        self.assertIsNone(monitor.evaluate(above))
        self.assertIsNone(monitor.evaluate(below))
        self.assertIsNone(monitor.evaluate(above))
        self.assertIsNone(monitor.evaluate(above))
        self.assertIsNotNone(monitor.evaluate(above))

    def test_missing_signal_and_nan_sample_do_not_trigger(self) -> None:
        cfg = CriticalRampingConfig(
            enabled=True,
            conditions=[CriticalCondition("dmm", "i", "gt", 0.0)],
        )
        monitor = CriticalConditionMonitor(cfg)
        self.assertIsNone(monitor.evaluate({}))
        self.assertIsNone(monitor.evaluate({"dmm": {}}))
        self.assertIsNone(monitor.evaluate({"dmm": {"i": math.nan}}))

    def test_disabled_is_noop(self) -> None:
        cfg = CriticalRampingConfig(
            enabled=False,
            conditions=[CriticalCondition("dmm", "i", "gt", 0.0)],
        )
        self.assertIsNone(
            CriticalConditionMonitor(cfg).evaluate({"dmm": {"i": 10.0}})
        )

    def test_from_job_definition_missing_and_aliases(self) -> None:
        self.assertFalse(CriticalRampingConfig.from_job_definition({}).enabled)
        self.assertFalse(CriticalRampingConfig.from_job_definition(None).enabled)
        parsed = CriticalRampingConfig.from_job_definition(
            {
                "criticalRamping": {
                    "enabled": True,
                    "holdSettingsOnTrigger": True,
                    "combine": "and",
                    "consecutiveHitsRequired": 2,
                    "conditions": [
                        {
                            "instrumentId": "dmm",
                            "signalKey": "i",
                            "comparison": ">",
                            "threshold": 1.25,
                        }
                    ],
                }
            }
        )
        self.assertTrue(parsed.enabled)
        self.assertTrue(parsed.hold_settings_on_trigger)
        self.assertEqual(parsed.combine, "all")
        self.assertEqual(parsed.consecutive_hits_required, 2)
        self.assertEqual(parsed.conditions[0].comparison, "gt")

    def test_validate_and_merge_keys(self) -> None:
        disabled = CriticalRampingConfig()
        self.assertIsNone(validate_critical_ramping_config(disabled))
        enabled_empty = CriticalRampingConfig(enabled=True)
        self.assertIsNotNone(validate_critical_ramping_config(enabled_empty))
        cfg = CriticalRampingConfig(
            enabled=True,
            conditions=[CriticalCondition("dmm", "i", "gt", 1.0)],
        )
        self.assertIsNotNone(
            validate_critical_ramping_config(cfg, available_signals=[("smu", "v")])
        )
        self.assertIsNone(
            validate_critical_ramping_config(cfg, available_signals=[("dmm", "i")])
        )
        required: dict[str, set[str]] = {"smu": {"v"}}
        merge_critical_measurement_keys(required, cfg)
        self.assertIn("i", required["dmm"])
        merge_critical_measurement_keys(required, disabled)
        self.assertEqual(required["smu"], {"v"})


if __name__ == "__main__":
    unittest.main()
