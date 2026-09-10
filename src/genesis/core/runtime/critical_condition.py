from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

Comparison = Literal["lt", "gt"]
CombineMode = Literal["any", "all"]

_COMPARISON_ALIASES: dict[str, Comparison] = {
    "lt": "lt",
    "<": "lt",
    "less": "lt",
    "gt": "gt",
    ">": "gt",
    "greater": "gt",
}
_COMBINE_ALIASES: dict[str, CombineMode] = {
    "any": "any",
    "or": "any",
    "all": "all",
    "and": "all",
}


def _as_finite_float(value: Any) -> float | None:
    try:
        measured = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(measured):
        return None
    return measured


def _parse_comparison(raw: Any) -> Comparison | None:
    key = str(raw or "").strip().lower()
    return _COMPARISON_ALIASES.get(key)


def _parse_combine(raw: Any) -> CombineMode:
    key = str(raw or "").strip().lower()
    return _COMBINE_ALIASES.get(key, "any")


@dataclass(frozen=True, slots=True)
class CriticalCondition:
    instrument_id: str
    signal_key: str
    comparison: Comparison
    threshold: float

    def evaluate(self, measured: float) -> bool:
        if not math.isfinite(measured):
            return False
        if self.comparison == "lt":
            return measured < self.threshold
        return measured > self.threshold

    def comparison_symbol(self) -> str:
        return "<" if self.comparison == "lt" else ">"

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrumentId": self.instrument_id,
            "signalKey": self.signal_key,
            "comparison": self.comparison,
            "threshold": self.threshold,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "CriticalCondition | None":
        if not isinstance(raw, Mapping):
            return None
        instrument_id = str(raw.get("instrumentId", "")).strip()
        signal_key = str(raw.get("signalKey", "")).strip()
        comparison = _parse_comparison(raw.get("comparison"))
        threshold = _as_finite_float(raw.get("threshold"))
        if not instrument_id or not signal_key or comparison is None or threshold is None:
            return None
        return cls(
            instrument_id=instrument_id,
            signal_key=signal_key,
            comparison=comparison,
            threshold=threshold,
        )


@dataclass(frozen=True, slots=True)
class CriticalMatch:
    instrument_id: str
    signal_key: str
    comparison: Comparison
    threshold: float
    measured_value: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrumentId": self.instrument_id,
            "signalKey": self.signal_key,
            "comparison": self.comparison,
            "threshold": self.threshold,
            "measuredValue": self.measured_value,
        }


@dataclass(frozen=True, slots=True)
class CriticalTrigger:
    instrument_id: str
    signal_key: str
    comparison: Comparison
    threshold: float
    measured_value: float
    combine: CombineMode
    hold_settings_on_trigger: bool
    matched_conditions: tuple[CriticalMatch, ...] = ()

    def reason_text(self) -> str:
        symbol = "<" if self.comparison == "lt" else ">"
        return (
            "Critical ramping triggered: "
            f"{self.instrument_id}:{self.signal_key} = {self.measured_value:g} "
            f"{symbol} {self.threshold:g}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrumentId": self.instrument_id,
            "signalKey": self.signal_key,
            "comparison": self.comparison,
            "threshold": self.threshold,
            "measuredValue": self.measured_value,
            "combine": self.combine,
            "holdSettingsOnTrigger": self.hold_settings_on_trigger,
            "reason": self.reason_text(),
            "matchedConditions": [match.to_dict() for match in self.matched_conditions],
        }


@dataclass(slots=True)
class CriticalRampingConfig:
    enabled: bool = False
    hold_settings_on_trigger: bool = False
    combine: CombineMode = "any"
    consecutive_hits_required: int = 1
    conditions: list[CriticalCondition] = field(default_factory=list)

    def referenced_signals(self) -> list[tuple[str, str]]:
        seen: set[tuple[str, str]] = set()
        refs: list[tuple[str, str]] = []
        for condition in self.conditions:
            pair = (condition.instrument_id, condition.signal_key)
            if pair in seen:
                continue
            seen.add(pair)
            refs.append(pair)
        return refs

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "holdSettingsOnTrigger": bool(self.hold_settings_on_trigger),
            "combine": self.combine,
            "consecutiveHitsRequired": max(1, int(self.consecutive_hits_required)),
            "conditions": [condition.to_dict() for condition in self.conditions],
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "CriticalRampingConfig":
        if not isinstance(raw, Mapping):
            return cls()
        conditions: list[CriticalCondition] = []
        for item in list(raw.get("conditions", []) or []):
            parsed = CriticalCondition.from_dict(item if isinstance(item, Mapping) else None)
            if parsed is not None:
                conditions.append(parsed)
        try:
            consecutive = int(raw.get("consecutiveHitsRequired", 1) or 1)
        except (TypeError, ValueError):
            consecutive = 1
        return cls(
            enabled=bool(raw.get("enabled", False)),
            hold_settings_on_trigger=bool(raw.get("holdSettingsOnTrigger", False)),
            combine=_parse_combine(raw.get("combine", "any")),
            consecutive_hits_required=max(1, consecutive),
            conditions=conditions,
        )

    @classmethod
    def from_job_definition(cls, job: Mapping[str, Any] | None) -> "CriticalRampingConfig":
        if not isinstance(job, Mapping):
            return cls()
        raw = job.get("criticalRamping")
        if not isinstance(raw, Mapping):
            return cls()
        return cls.from_mapping(raw)


def validate_critical_ramping_config(
    config: CriticalRampingConfig,
    available_signals: Sequence[tuple[str, str]] | None = None,
) -> str | None:
    if not config.enabled:
        return None
    if not config.conditions:
        return "Critical ramping is enabled but no conditions are defined."
    available: set[tuple[str, str]] | None = None
    if available_signals is not None:
        available = {(str(inst_id), str(key)) for inst_id, key in available_signals}
    for condition in config.conditions:
        if not condition.instrument_id or not condition.signal_key:
            return "Critical ramping condition is missing an instrument or signal."
        if available is not None and (
            condition.instrument_id,
            condition.signal_key,
        ) not in available:
            return (
                "Critical ramping references unknown signal "
                f"{condition.instrument_id}:{condition.signal_key}."
            )
    return None


def merge_critical_measurement_keys(
    required_keys_by_instrument_id: dict[str, set[str]],
    config: CriticalRampingConfig,
) -> None:
    if not config.enabled:
        return
    for instrument_id, signal_key in config.referenced_signals():
        required_keys_by_instrument_id.setdefault(instrument_id, set()).add(signal_key)


class CriticalConditionMonitor:
    """Stateful consecutive-hit evaluator for critical ramping."""

    def __init__(self, config: CriticalRampingConfig | None = None) -> None:
        self.config = config or CriticalRampingConfig()
        self._consecutive_hits = 0
        self.trigger: CriticalTrigger | None = None

    def reset(self) -> None:
        self._consecutive_hits = 0
        self.trigger = None

    def evaluate(
        self,
        values_by_instrument_id: Mapping[str, Mapping[str, float]] | None,
    ) -> CriticalTrigger | None:
        if self.trigger is not None:
            return self.trigger
        if not self.config.enabled or not self.config.conditions:
            return None

        samples = values_by_instrument_id or {}
        matches: list[CriticalMatch] = []
        satisfied_flags: list[bool] = []
        for condition in self.config.conditions:
            instrument_values = samples.get(condition.instrument_id) or {}
            measured = _as_finite_float(instrument_values.get(condition.signal_key))
            if measured is None:
                satisfied_flags.append(False)
                continue
            hit = condition.evaluate(measured)
            satisfied_flags.append(hit)
            if hit:
                matches.append(
                    CriticalMatch(
                        instrument_id=condition.instrument_id,
                        signal_key=condition.signal_key,
                        comparison=condition.comparison,
                        threshold=condition.threshold,
                        measured_value=measured,
                    )
                )

        if self.config.combine == "all":
            matched = bool(satisfied_flags) and all(satisfied_flags)
        else:
            matched = any(satisfied_flags)

        if not matched or not matches:
            self._consecutive_hits = 0
            return None

        self._consecutive_hits += 1
        if self._consecutive_hits < max(1, int(self.config.consecutive_hits_required)):
            return None

        first = matches[0]
        self.trigger = CriticalTrigger(
            instrument_id=first.instrument_id,
            signal_key=first.signal_key,
            comparison=first.comparison,
            threshold=first.threshold,
            measured_value=first.measured_value,
            combine=self.config.combine,
            hold_settings_on_trigger=self.config.hold_settings_on_trigger,
            matched_conditions=tuple(matches),
        )
        return self.trigger
