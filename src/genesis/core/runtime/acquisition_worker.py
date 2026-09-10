from __future__ import annotations

import time
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from genesis.core.instrument.base_instrument import BaseInstrument
from genesis.core.runtime.critical_condition import (
    CriticalConditionMonitor,
    CriticalRampingConfig,
)
from genesis.core.runtime.setpoint_safety import SetpointSafetyController, ValueBounds


class AcquisitionSample(dict):
    pass


class AcquisitionWorker(QObject):
    sampleEmitted = Signal(float, dict)  # timestamp, valuesByInstrumentId
    statusMessage = Signal(str)
    sweepProgress = Signal(int, int, int, int, str)
    rampProgress = Signal(float, str)  # 0..1, label
    sweepCompleted = Signal()
    criticalTriggered = Signal(dict)

    def __init__(
        self,
        instrumentsById: dict[str, BaseInstrument],
        measurementKeysByInstrumentId: dict[str, list[str]],
        sweeps: list[dict[str, Any]] | None = None,
        intervalSeconds: float = 0.2,
        initialSweepValuesByInstrumentId: dict[str, dict[str, float]] | None = None,
        boundsByInstrumentId: dict[str, dict[str, ValueBounds]] | None = None,
        criticalRamping: CriticalRampingConfig | dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.instrumentsById = instrumentsById
        self.measurementKeysByInstrumentId = measurementKeysByInstrumentId
        self.sweeps = sweeps or []
        self.intervalSeconds = intervalSeconds
        self._shouldStop = False
        self._activeSweepValuesByInstrumentId: dict[str, dict[str, float]] = {
            str(instId): {str(k): float(v) for k, v in values.items()}
            for instId, values in (initialSweepValuesByInstrumentId or {}).items()
            if isinstance(values, dict)
        }
        self._safety = SetpointSafetyController(boundsByInstrumentId)
        self._safety.seed_last_values(self._activeSweepValuesByInstrumentId)
        if isinstance(criticalRamping, CriticalRampingConfig):
            critical_config = criticalRamping
        elif isinstance(criticalRamping, dict):
            critical_config = CriticalRampingConfig.from_mapping(criticalRamping)
        else:
            critical_config = CriticalRampingConfig()
        self._criticalMonitor = CriticalConditionMonitor(critical_config)

    def requestStop(self) -> None:
        self._shouldStop = True

    def activeSweepValuesSnapshot(self) -> dict[str, dict[str, float]]:
        return {
            str(instId): {str(key): float(value) for key, value in values.items()}
            for instId, values in self._activeSweepValuesByInstrumentId.items()
        }

    def criticalTriggerSnapshot(self) -> dict[str, Any] | None:
        trigger = self._criticalMonitor.trigger
        if trigger is None:
            return None
        return trigger.to_dict()

    def run(self) -> None:
        self.statusMessage.emit("Data acquisition started.")
        completedSweep = False
        if self.sweeps:
            completedSweep = self._runConfiguredSweep()
        else:
            while not self._shouldStop:
                self._sampleAllInstruments(time.time())
                if self._shouldStop:
                    break
                if self.intervalSeconds > 0.0:
                    time.sleep(self.intervalSeconds)
        if completedSweep and not self._shouldStop:
            self.sweepCompleted.emit()
        self.statusMessage.emit("Data acquisition loop ended.")

    def _runConfiguredSweep(self) -> bool:
        configured = self._normalizedSweeps()
        if len(configured) == 1:
            return self._runSingleSweep(configured[0])
        if len(configured) == 2:
            return self._runNestedSweep2d(configured[0], configured[1])
        self.statusMessage.emit(
            f"Skipping sweep: nested depth {len(configured)} is unsupported."
        )
        return False

    def _normalizedSweeps(self) -> list[dict[str, Any]]:
        configured: list[dict[str, Any]] = []
        for rawSweep in self.sweeps:
            if not isinstance(rawSweep, dict):
                continue
            # Backward-compat: support nested 2D sweep objects:
            # {"mode":"2d","outer":{...},"inner":{...}}
            if isinstance(rawSweep.get("outer"), dict):
                outer = dict(rawSweep.get("outer", {}))
                if outer:
                    configured.append(outer)
                if str(rawSweep.get("mode", "1d")) == "2d" and isinstance(
                    rawSweep.get("inner"), dict
                ):
                    inner = dict(rawSweep.get("inner", {}))
                    if inner:
                        configured.append(inner)
                continue
            configured.append(rawSweep)
        return configured

    def _runSingleSweep(
        self,
        sweep: dict[str, Any],
    ) -> bool:
        instrumentId = str(sweep.get("instrumentId", ""))
        key = str(sweep.get("key", ""))
        if not instrumentId or not key:
            return False
        isTimeSweep = instrumentId == "__time__" and key == "time"
        instrument = self.instrumentsById.get(instrumentId)
        if not isTimeSweep and instrument is None:
            self.statusMessage.emit(
                f"Skipping sweep {instrumentId}:{key} (instrument not found)."
            )
            return False

        values = self._buildSweepValues(sweep)
        settle = float(sweep.get("settleTimeSeconds", 0.0))
        label = f"{instrumentId}:{key}"
        self.statusMessage.emit(
            f"Running sweep {instrumentId}:{key} ({len(values)} points)."
        )
        self.sweepProgress.emit(0, 1, 0, len(values), label)

        sweepStart = time.time()
        pointIndex = 0
        for pointIndex, point in enumerate(values, start=1):
            if self._shouldStop:
                break
            if isTimeSweep:
                target = sweepStart + float(point)
                self._sleepUntilOrStop(target)
            else:
                try:
                    currentValue = float(
                        self._activeSweepValuesByInstrumentId.get(instrumentId, {}).get(
                            key, point
                        )
                    )
                    targetValue = float(point)
                    rampSteps = self._estimateSlewStepCount(
                        current=currentValue,
                        target=targetValue,
                        maxSlewRate=float(sweep.get("maxSlewRate", 0.0)),
                        maxSlewStep=float(
                            sweep.get("maxSlewStep", sweep.get("stepSize", 0.0))
                        ),
                    )
                    rampApplied = 0

                    def _on_applied(value: float) -> None:
                        nonlocal rampApplied
                        rampApplied += 1
                        self._activeSweepValuesByInstrumentId.setdefault(
                            instrumentId, {}
                        )[key] = float(value)
                        frac = (
                            (pointIndex - 1) + (rampApplied / max(1, rampSteps))
                        ) / max(1, len(values))
                        self.rampProgress.emit(float(min(max(frac, 0.0), 1.0)), label)
                        self._sampleForCriticalDuringRamp()

                    slewCompleted = self._safety.apply_slew_limited(
                        instrument_id=instrumentId,
                        instrument=instrument,
                        key=key,
                        target_value=targetValue,
                        max_slew_rate=float(sweep.get("maxSlewRate", 0.0)),
                        max_slew_step=float(
                            sweep.get("maxSlewStep", sweep.get("stepSize", 0.0))
                        ),
                        should_stop=lambda: self._shouldStop,
                        on_applied=_on_applied,
                    )
                    if not slewCompleted:
                        break
                    if self._shouldStop:
                        break
                    if instrument is not None:

                        def _on_settle_progress(measured: float) -> None:
                            self._activeSweepValuesByInstrumentId.setdefault(
                                instrumentId, {}
                            )[key] = float(measured)
                            frac = pointIndex / max(1, len(values))
                            self.rampProgress.emit(
                                float(min(max(frac, 0.0), 1.0)),
                                f"{label} (settling)",
                            )
                            self._sampleForCriticalDuringRamp()

                        settled = instrument.waitForSetpoint(
                            key=key,
                            target_value=targetValue,
                            should_stop=lambda: self._shouldStop,
                            on_progress=_on_settle_progress,
                        )
                        if not settled:
                            break
                        if self._shouldStop:
                            break
                except Exception as exc:
                    self.statusMessage.emit(f"{instrumentId}:{key} set failed: {exc}")
                    continue

            self._activeSweepValuesByInstrumentId.setdefault(instrumentId, {})[
                key
            ] = point
            stepTimestamp = time.time()
            # For time sweeps, point spacing itself defines the sample interval.
            # Do not add extra settle delay on top of that schedule.
            if not isTimeSweep:
                self._sleepWithStop(settle)
            if self._shouldStop:
                break
            self._sampleAllInstruments(stepTimestamp)
            if self._shouldStop:
                break
            self.sweepProgress.emit(1, 1, pointIndex, len(values), label)
        return (
            (not self._shouldStop) and (len(values) > 0) and (pointIndex == len(values))
        )

    def _runNestedSweep2d(
        self,
        outerSweep: dict[str, Any],
        innerSweep: dict[str, Any],
    ) -> bool:
        outerInstId = str(outerSweep.get("instrumentId", ""))
        outerKey = str(outerSweep.get("key", ""))
        innerInstId = str(innerSweep.get("instrumentId", ""))
        innerKey = str(innerSweep.get("key", ""))
        if not outerInstId or not outerKey or not innerInstId or not innerKey:
            return False

        outerIsTime = outerInstId == "__time__" and outerKey == "time"
        innerIsTime = innerInstId == "__time__" and innerKey == "time"
        outerInstrument = self.instrumentsById.get(outerInstId)
        innerInstrument = self.instrumentsById.get(innerInstId)
        if not outerIsTime and outerInstrument is None:
            self.statusMessage.emit(
                f"Skipping 2D sweep {outerInstId}:{outerKey} (instrument not found)."
            )
            return False
        if not innerIsTime and innerInstrument is None:
            self.statusMessage.emit(
                f"Skipping 2D sweep {innerInstId}:{innerKey} (instrument not found)."
            )
            return False

        outerValues = self._buildSweepValues(outerSweep)
        innerValues = self._buildSweepValues(innerSweep)
        outerSettle = float(outerSweep.get("settleTimeSeconds", 0.0))
        innerSettle = float(innerSweep.get("settleTimeSeconds", 0.0))
        totalPoints = len(outerValues) * len(innerValues)
        label = f"2d:{outerInstId}:{outerKey}|{innerInstId}:{innerKey}"
        self.statusMessage.emit(
            f"Running 2D sweep {outerInstId}:{outerKey} x {innerInstId}:{innerKey} ({totalPoints} points)."
        )
        self.sweepProgress.emit(0, 1, 0, totalPoints, label)

        sweepStart = time.time()
        pointIndex = 0
        for outerValue in outerValues:
            if self._shouldStop:
                break
            outerTimeValue: float | None = None
            if outerIsTime:
                outerTarget = sweepStart + float(outerValue)
                self._sleepUntilOrStop(outerTarget)
                outerTimeValue = float(outerValue)
            else:
                try:
                    currentValue = float(
                        self._activeSweepValuesByInstrumentId.get(outerInstId, {}).get(
                            outerKey, outerValue
                        )
                    )
                    targetValue = float(outerValue)
                    outerRampSteps = self._estimateSlewStepCount(
                        current=currentValue,
                        target=targetValue,
                        maxSlewRate=float(outerSweep.get("maxSlewRate", 0.0)),
                        maxSlewStep=float(
                            outerSweep.get(
                                "maxSlewStep", outerSweep.get("stepSize", 0.0)
                            )
                        ),
                    )
                    outerRampApplied = 0

                    def _on_outer_applied(value: float) -> None:
                        nonlocal outerRampApplied
                        outerRampApplied += 1
                        self._activeSweepValuesByInstrumentId.setdefault(
                            outerInstId, {}
                        )[outerKey] = float(value)
                        frac = (
                            pointIndex + (outerRampApplied / max(1, outerRampSteps))
                        ) / max(1, totalPoints)
                        self.rampProgress.emit(float(min(max(frac, 0.0), 1.0)), label)
                        self._sampleForCriticalDuringRamp()

                    slewCompleted = self._safety.apply_slew_limited(
                        instrument_id=outerInstId,
                        instrument=outerInstrument,
                        key=outerKey,
                        target_value=targetValue,
                        max_slew_rate=float(outerSweep.get("maxSlewRate", 0.0)),
                        max_slew_step=float(
                            outerSweep.get(
                                "maxSlewStep", outerSweep.get("stepSize", 0.0)
                            )
                        ),
                        should_stop=lambda: self._shouldStop,
                        on_applied=_on_outer_applied,
                    )
                    if not slewCompleted:
                        break
                    if self._shouldStop:
                        break
                    if outerInstrument is not None:

                        def _on_outer_settle(measured: float) -> None:
                            self._activeSweepValuesByInstrumentId.setdefault(
                                outerInstId, {}
                            )[outerKey] = float(measured)
                            frac = pointIndex / max(1, totalPoints)
                            self.rampProgress.emit(
                                float(min(max(frac, 0.0), 1.0)),
                                f"{label} (settling outer)",
                            )
                            self._sampleForCriticalDuringRamp()

                        settled = outerInstrument.waitForSetpoint(
                            key=outerKey,
                            target_value=targetValue,
                            should_stop=lambda: self._shouldStop,
                            on_progress=_on_outer_settle,
                        )
                        if not settled:
                            break
                        if self._shouldStop:
                            break
                except Exception as exc:
                    self.statusMessage.emit(
                        f"{outerInstId}:{outerKey} set failed: {exc}"
                    )
                    continue
            self._activeSweepValuesByInstrumentId.setdefault(outerInstId, {})[
                outerKey
            ] = float(outerValue)
            # For time sweeps, point spacing itself defines timing.
            if not outerIsTime:
                self._sleepWithStop(outerSettle)
            if self._shouldStop:
                break

            innerSweepStart = time.time()
            for innerValue in innerValues:
                if self._shouldStop:
                    break
                if innerIsTime:
                    innerTarget = innerSweepStart + float(innerValue)
                    self._sleepUntilOrStop(innerTarget)
                else:
                    try:
                        currentValue = float(
                            self._activeSweepValuesByInstrumentId.get(
                                innerInstId, {}
                            ).get(innerKey, innerValue)
                        )
                        targetValue = float(innerValue)
                        innerRampSteps = self._estimateSlewStepCount(
                            current=currentValue,
                            target=targetValue,
                            maxSlewRate=float(innerSweep.get("maxSlewRate", 0.0)),
                            maxSlewStep=float(
                                innerSweep.get(
                                    "maxSlewStep", innerSweep.get("stepSize", 0.0)
                                )
                            ),
                        )
                        innerRampApplied = 0

                        def _on_inner_applied(value: float) -> None:
                            nonlocal innerRampApplied
                            innerRampApplied += 1
                            self._activeSweepValuesByInstrumentId.setdefault(
                                innerInstId, {}
                            )[innerKey] = float(value)
                            frac = (
                                pointIndex + (innerRampApplied / max(1, innerRampSteps))
                            ) / max(1, totalPoints)
                            self.rampProgress.emit(
                                float(min(max(frac, 0.0), 1.0)), label
                            )
                            self._sampleForCriticalDuringRamp()

                        slewCompleted = self._safety.apply_slew_limited(
                            instrument_id=innerInstId,
                            instrument=innerInstrument,
                            key=innerKey,
                            target_value=targetValue,
                            max_slew_rate=float(innerSweep.get("maxSlewRate", 0.0)),
                            max_slew_step=float(
                                innerSweep.get(
                                    "maxSlewStep", innerSweep.get("stepSize", 0.0)
                                )
                            ),
                            should_stop=lambda: self._shouldStop,
                            on_applied=_on_inner_applied,
                        )
                        if not slewCompleted:
                            break
                        if self._shouldStop:
                            break
                        if innerInstrument is not None:

                            def _on_inner_settle(measured: float) -> None:
                                self._activeSweepValuesByInstrumentId.setdefault(
                                    innerInstId, {}
                                )[innerKey] = float(measured)
                                frac = pointIndex / max(1, totalPoints)
                                self.rampProgress.emit(
                                    float(min(max(frac, 0.0), 1.0)),
                                    f"{label} (settling inner)",
                                )
                                self._sampleForCriticalDuringRamp()

                            settled = innerInstrument.waitForSetpoint(
                                key=innerKey,
                                target_value=targetValue,
                                should_stop=lambda: self._shouldStop,
                                on_progress=_on_inner_settle,
                            )
                            if not settled:
                                break
                            if self._shouldStop:
                                break
                    except Exception as exc:
                        self.statusMessage.emit(
                            f"{innerInstId}:{innerKey} set failed: {exc}"
                        )
                        continue

                self._activeSweepValuesByInstrumentId.setdefault(innerInstId, {})[
                    innerKey
                ] = float(innerValue)
                stepTimestamp = time.time()
                # For time sweeps, point spacing itself defines timing.
                if not innerIsTime:
                    self._sleepWithStop(innerSettle)
                if self._shouldStop:
                    break
                if outerIsTime and outerTimeValue is not None:
                    # Keep the outer swept time coordinate fixed while scanning inner.
                    self._activeSweepValuesByInstrumentId.setdefault("__time__", {})[
                        "time"
                    ] = float(outerTimeValue)
                self._sampleAllInstruments(stepTimestamp)
                if self._shouldStop:
                    break
                pointIndex += 1
                self.sweepProgress.emit(1, 1, pointIndex, totalPoints, label)
        return (not self._shouldStop) and (pointIndex == totalPoints)

    def _estimateSlewStepCount(
        self,
        current: float,
        target: float,
        maxSlewRate: float,
        maxSlewStep: float,
    ) -> int:
        delta = abs(float(target) - float(current))
        maxRate = float(maxSlewRate)
        maxStep = abs(float(maxSlewStep))
        if delta <= 0.0 or maxRate <= 0.0 or maxStep <= 0.0 or delta <= maxStep:
            return 1
        return max(1, int(np.ceil(delta / maxStep)))

    def _sampleAllInstruments(self, timestamp: float) -> None:
        valuesByInstrumentId: dict[str, dict[str, float]] = {}
        for instrumentId, instrument in self.instrumentsById.items():
            keys = self.measurementKeysByInstrumentId.get(instrumentId, [])
            values: dict[str, float] = {}
            if keys:
                try:
                    values = instrument.readMeasurements(keys)
                except Exception as exc:
                    self.statusMessage.emit(f"{instrumentId}: read failed: {exc}")
                    values = {}

            # Include active sweep value(s) so these can be plotted/logged.
            activeSweep = self._activeSweepValuesByInstrumentId.get(instrumentId, {})
            for key, val in activeSweep.items():
                values[key] = float(val)
            valuesByInstrumentId[instrumentId] = values
        activeTimeSweep = self._activeSweepValuesByInstrumentId.get("__time__", {})
        if "time" in activeTimeSweep:
            valuesByInstrumentId["__time__"] = {"time": float(activeTimeSweep["time"])}
        self.sampleEmitted.emit(float(timestamp), valuesByInstrumentId)
        self._evaluateCritical(valuesByInstrumentId)

    def _sampleForCriticalDuringRamp(self) -> None:
        if not self._criticalMonitor.config.enabled or self._shouldStop:
            return
        self._sampleAllInstruments(time.time())

    def _evaluateCritical(
        self, valuesByInstrumentId: dict[str, dict[str, float]]
    ) -> bool:
        if self._criticalMonitor.trigger is not None:
            self._shouldStop = True
            return True
        trigger = self._criticalMonitor.evaluate(valuesByInstrumentId)
        if trigger is None:
            return False
        self._shouldStop = True
        self.statusMessage.emit(trigger.reason_text())
        self.criticalTriggered.emit(trigger.to_dict())
        return True

    def _buildSweepValues(self, sweep: dict[str, Any]) -> np.ndarray:
        start = float(sweep.get("start", 0.0))
        stop = float(sweep.get("stop", 0.0))
        points = max(2, int(sweep.get("points", 2)))
        spacing = str(sweep.get("spacing", "linear"))

        if points == 1:
            return np.asarray([start], dtype=np.float64)

        if spacing == "log" and start > 0 and stop > 0:
            return np.logspace(
                np.log10(start), np.log10(stop), points, dtype=np.float64
            )

        return np.linspace(start, stop, points, dtype=np.float64)

    def _sleepWithStop(self, seconds: float) -> None:
        if seconds <= 0:
            return
        end = time.time() + seconds
        while not self._shouldStop and time.time() < end:
            time.sleep(min(0.05, end - time.time()))

    def _sleepUntilOrStop(self, targetTime: float) -> None:
        while not self._shouldStop and time.time() < targetTime:
            time.sleep(min(0.05, targetTime - time.time()))


def startAcquisitionThread(
    worker: AcquisitionWorker,
) -> tuple[QThread, AcquisitionWorker]:
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    return thread, worker
