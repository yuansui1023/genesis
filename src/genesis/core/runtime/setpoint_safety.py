from __future__ import annotations

import time
from dataclasses import dataclass
from numbers import Number
from typing import Any, Callable, Mapping

import numpy as np

from genesis.core.instrument.base_instrument import BaseInstrument


@dataclass(frozen=True, slots=True)
class ValueBounds:
    min_value: float | None
    max_value: float | None

    def clamp(self, value: float) -> float:
        bounded = float(value)
        if self.min_value is not None:
            bounded = max(float(self.min_value), bounded)
        if self.max_value is not None:
            bounded = min(float(self.max_value), bounded)
        return bounded


def build_value_bounds_by_instrument(
    instrument_types_by_id: Mapping[str, type[BaseInstrument]],
) -> dict[str, dict[str, ValueBounds]]:
    bounds_by_inst: dict[str, dict[str, ValueBounds]] = {}
    for instrument_id, instrument_type in instrument_types_by_id.items():
        by_key: dict[str, ValueBounds] = {}
        for field in instrument_type.getJobConfigFields():
            if field.fieldType not in {"float", "int"}:
                continue
            if field.minValue is None and field.maxValue is None:
                continue
            by_key[str(field.key)] = ValueBounds(
                min_value=(None if field.minValue is None else float(field.minValue)),
                max_value=(None if field.maxValue is None else float(field.maxValue)),
            )
        bounds_by_inst[str(instrument_id)] = by_key
    return bounds_by_inst


class SetpointSafetyController:
    """Centralized bounded/slew-limited setpoint application."""

    def __init__(
        self,
        bounds_by_instrument: Mapping[str, Mapping[str, ValueBounds]] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._bounds_by_instrument: dict[str, dict[str, ValueBounds]] = {
            str(inst_id): {str(key): bounds for key, bounds in by_key.items()}
            for inst_id, by_key in (bounds_by_instrument or {}).items()
        }
        self._last_numeric_value_by_ref: dict[tuple[str, str], float] = {}
        self._sleep = sleep_fn or time.sleep

    def seed_last_value(self, instrument_id: str, key: str, value: float) -> None:
        self._last_numeric_value_by_ref[(str(instrument_id), str(key))] = float(value)

    def seed_last_values(
        self, values_by_instrument: Mapping[str, Mapping[str, float]]
    ) -> None:
        for inst_id, by_key in values_by_instrument.items():
            for key, value in by_key.items():
                self.seed_last_value(str(inst_id), str(key), float(value))

    def apply_bounded_immediate(
        self,
        instrument_id: str,
        instrument: BaseInstrument,
        key: str,
        value: float | int | str,
    ) -> float | int | str:
        bounded = self._clamp_value(str(instrument_id), str(key), value)
        instrument.applyConfigValue(str(key), bounded)
        if isinstance(bounded, Number) and not isinstance(bounded, bool):
            self.seed_last_value(str(instrument_id), str(key), float(bounded))
        return bounded

    def apply_slew_limited(
        self,
        instrument_id: str,
        instrument: BaseInstrument,
        key: str,
        target_value: float,
        max_slew_rate: float,
        max_slew_step: float,
        should_stop: Callable[[], bool] | None = None,
        on_applied: Callable[[float], None] | None = None,
    ) -> bool:
        inst_id = str(instrument_id)
        key_str = str(key)
        target = float(self._clamp_value(inst_id, key_str, float(target_value)))
        if not instrument.shouldUseRuntimeSlew(key_str):
            instrument.applyConfigValue(key_str, target)
            self.seed_last_value(inst_id, key_str, target)
            if on_applied is not None:
                on_applied(target)
            return True

        current = self._last_numeric_value_by_ref.get((inst_id, key_str), None)

        if current is None:
            instrument.applyConfigValue(key_str, target)
            self.seed_last_value(inst_id, key_str, target)
            if on_applied is not None:
                on_applied(target)
            return True

        current = float(self._clamp_value(inst_id, key_str, float(current)))
        delta = target - current
        max_rate = float(max_slew_rate)
        max_step = abs(float(max_slew_step))
        if abs(delta) <= 0.0 or max_rate <= 0.0:
            instrument.applyConfigValue(key_str, target)
            self.seed_last_value(inst_id, key_str, target)
            if on_applied is not None:
                on_applied(target)
            return True

        # Explicit policy:
        # - if requested move is <= max slew step: one immediate command
        # - if requested move is > max slew step: multi-step ramp
        if max_step <= 0.0 or abs(delta) <= max_step:
            instrument.applyConfigValue(key_str, target)
            self.seed_last_value(inst_id, key_str, target)
            if on_applied is not None:
                on_applied(target)
            return True
        steps = max(1, int(np.ceil(abs(delta) / max_step)))
        sleep_per_step = max_step / max_rate
        for idx in range(1, steps + 1):
            if should_stop is not None and should_stop():
                return False
            fraction = idx / steps
            interpolated = current + delta * fraction
            bounded_step = float(self._clamp_value(inst_id, key_str, interpolated))
            instrument.applyConfigValue(key_str, bounded_step)
            self.seed_last_value(inst_id, key_str, bounded_step)
            if on_applied is not None:
                on_applied(bounded_step)
            if idx < steps and sleep_per_step > 0.0:
                if should_stop is None:
                    self._sleep(sleep_per_step)
                else:
                    remaining = sleep_per_step
                    while remaining > 0.0:
                        if should_stop():
                            return False
                        chunk = min(0.05, remaining)
                        self._sleep(chunk)
                        remaining -= chunk
        return True

    def _clamp_value(
        self, instrument_id: str, key: str, value: float | int | str
    ) -> float | int | str:
        if isinstance(value, bool):
            return value
        if not isinstance(value, Number):
            return value
        bounds = self._bounds_by_instrument.get(str(instrument_id), {}).get(str(key))
        if bounds is None:
            return value
        return bounds.clamp(float(value))
