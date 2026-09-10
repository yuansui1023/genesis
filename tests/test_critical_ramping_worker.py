from __future__ import annotations

import unittest
from typing import Any

from genesis.core.instrument.base_instrument import BaseInstrument
from genesis.core.runtime.acquisition_worker import AcquisitionWorker
from genesis.core.runtime.critical_condition import (
    CriticalCondition,
    CriticalRampingConfig,
)
from genesis.core.transport.base_transport import BaseTransport


class _NoopTransport(BaseTransport):
    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def write(self, command: str) -> None:
        return None

    def read(self) -> str:
        return "0"


class _RampProbeInstrument(BaseInstrument):
    def __init__(self, name: str = "dev") -> None:
        super().__init__(name=name, transport=_NoopTransport("noop"))
        self.level = 0.0
        self.safeCalls = 0
        self.reads = 0

    def initialize(self) -> None:
        return None

    def applySafeState(self) -> None:
        self.safeCalls += 1

    def applyConfigValue(self, key: str, value: float | int | str) -> None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self.level = float(value)

    def readMeasurements(self, signalKeys: list[str]) -> dict[str, float]:
        self.reads += 1
        return {str(key): float(self.level) for key in signalKeys}


class _SettlingInstrument(_RampProbeInstrument):
    def waitForSetpoint(
        self,
        key: str,
        target_value: float,
        should_stop=None,
        on_progress=None,
    ) -> bool:
        if on_progress is not None:
            on_progress(float(self.level))
        if should_stop is not None and should_stop():
            return False
        return True


def _level_sweep(
    start: float = 0.0,
    stop: float = 1.0,
    points: int = 5,
    max_slew_rate: float = 0.0,
    max_slew_step: float = 1.0,
) -> dict[str, Any]:
    return {
        "instrumentId": "dev",
        "key": "level",
        "start": start,
        "stop": stop,
        "points": points,
        "settleTimeSeconds": 0.0,
        "spacing": "linear",
        "stepSize": abs(stop - start) / max(1, points - 1),
        "maxSlewRate": max_slew_rate,
        "maxSlewStep": max_slew_step,
    }


def _gt_config(threshold: float, hold: bool = False) -> CriticalRampingConfig:
    return CriticalRampingConfig(
        enabled=True,
        hold_settings_on_trigger=hold,
        conditions=[CriticalCondition("dev", "sense", "gt", threshold)],
    )


class CriticalRampingWorkerTests(unittest.TestCase):
    def _worker(
        self,
        instrument: _RampProbeInstrument,
        sweeps: list[dict[str, Any]] | None,
        config: CriticalRampingConfig | None,
        interval_seconds: float = 0.0,
    ) -> AcquisitionWorker:
        return AcquisitionWorker(
            instrumentsById={"dev": instrument},
            measurementKeysByInstrumentId={"dev": ["sense"]},
            sweeps=sweeps or [],
            intervalSeconds=interval_seconds,
            initialSweepValuesByInstrumentId={"dev": {"level": 0.0}},
            boundsByInstrumentId={},
            criticalRamping=config,
        )

    def test_1d_sweep_stops_when_measurement_crosses_threshold(self) -> None:
        instrument = _RampProbeInstrument()
        worker = self._worker(
            instrument,
            [_level_sweep(start=0.0, stop=1.0, points=5)],
            _gt_config(0.4),
        )
        completed = worker._runConfiguredSweep()
        self.assertFalse(completed)
        trigger = worker.criticalTriggerSnapshot()
        self.assertIsNotNone(trigger)
        self.assertGreater(float(trigger["measuredValue"]), 0.4)
        self.assertLess(instrument.level, 1.0)

    def test_2d_sweep_stops_on_inner_axis(self) -> None:
        instrument = _RampProbeInstrument()
        sweeps = [
            _level_sweep(start=0.0, stop=0.0, points=2),
            _level_sweep(start=0.0, stop=1.0, points=5),
        ]
        sweeps[0]["instrumentId"] = "dev"
        worker = self._worker(instrument, sweeps, _gt_config(0.35))
        completed = worker._runConfiguredSweep()
        self.assertFalse(completed)
        self.assertIsNotNone(worker.criticalTriggerSnapshot())

    def test_time_sweep_path_evaluates_samples(self) -> None:
        instrument = _RampProbeInstrument()
        instrument.level = 2.0
        worker = self._worker(
            instrument,
            [
                {
                    "instrumentId": "__time__",
                    "key": "time",
                    "start": 0.0,
                    "stop": 0.0,
                    "points": 3,
                    "settleTimeSeconds": 0.0,
                    "spacing": "linear",
                    "stepSize": 0.0,
                    "maxSlewRate": 0.0,
                    "maxSlewStep": 1.0,
                }
            ],
            _gt_config(1.0),
        )
        completed = worker._runConfiguredSweep()
        self.assertFalse(completed)
        self.assertIsNotNone(worker.criticalTriggerSnapshot())

    def test_continuous_acquisition_stops(self) -> None:
        instrument = _RampProbeInstrument()
        instrument.level = 5.0
        worker = self._worker(instrument, [], _gt_config(1.0))
        worker.run()
        self.assertIsNotNone(worker.criticalTriggerSnapshot())
        self.assertGreaterEqual(instrument.reads, 1)

    def test_disabled_config_completes_full_sweep(self) -> None:
        instrument = _RampProbeInstrument()
        worker = self._worker(
            instrument,
            [_level_sweep(start=0.0, stop=1.0, points=5)],
            CriticalRampingConfig(
                enabled=False,
                conditions=[CriticalCondition("dev", "sense", "gt", 0.1)],
            ),
        )
        completed = worker._runConfiguredSweep()
        self.assertTrue(completed)
        self.assertIsNone(worker.criticalTriggerSnapshot())
        self.assertAlmostEqual(instrument.level, 1.0)

    def test_hold_flag_is_copied_into_trigger_payload(self) -> None:
        instrument = _RampProbeInstrument()
        worker = self._worker(
            instrument,
            [_level_sweep(start=0.0, stop=1.0, points=5)],
            _gt_config(0.2, hold=True),
        )
        worker._runConfiguredSweep()
        trigger = worker.criticalTriggerSnapshot()
        self.assertIsNotNone(trigger)
        self.assertTrue(trigger["holdSettingsOnTrigger"])
        self.assertEqual(instrument.safeCalls, 0)

    def test_mid_ramp_sampling_can_trigger_before_next_point(self) -> None:
        instrument = _SettlingInstrument()
        worker = self._worker(
            instrument,
            [
                _level_sweep(
                    start=0.0,
                    stop=1.0,
                    points=2,
                    max_slew_rate=10.0,
                    max_slew_step=0.2,
                )
            ],
            _gt_config(0.25),
        )
        completed = worker._runSingleSweep(worker.sweeps[0])
        self.assertFalse(completed)
        trigger = worker.criticalTriggerSnapshot()
        self.assertIsNotNone(trigger)
        self.assertLess(float(trigger["measuredValue"]), 1.0)


if __name__ == "__main__":
    unittest.main()
