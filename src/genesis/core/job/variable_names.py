"""Display nicknames for stable instrument variable references."""

from __future__ import annotations

import re
from typing import Any

_DEFAULT_NAMES: dict[str, str] = {
    "forceVoltageLevelV": "V_set",
    "forceCurrentLevelA": "I_set",
    "forceVoltageV": "V_source",
    "forceCurrentA": "I_source",
    "senseVoltageV": "V_meas",
    "senseCurrentA": "I_meas",
    "senseResistanceOhm": "R_meas",
    "sourceVoltageSettingV": "V_set_readback",
    "sourceCurrentSettingA": "I_set_readback",
    "voltageDcV": "V_DC",
    "voltageAcV": "V_AC",
    "currentDcA": "I_DC",
    "currentAcA": "I_AC",
    "resistanceOhm": "R_2wire",
    "resistance4WireOhm": "R_4wire",
    "continuityOhm": "Continuity",
    "diodeVoltageV": "V_diode",
    "frequencyHz": "Frequency",
    "periodSeconds": "Period",
    "x": "X",
    "y": "Y",
    "r": "Magnitude",
    "theta": "Phase",
    "referenceFrequencyHz": "Ref_frequency",
    "referenceOutputAmplitudeVrms": "Ref_amplitude",
    "referencePhaseDeg": "Ref_phase",
    "magnetFieldT": "B_meas",
    "targetFieldT": "B_set",
    "magnetCurrentA": "Magnet_current",
    "magnetVoltageV": "Magnet_voltage",
    "targetStep": "Position_set",
    "currentStep": "Position",
    "vzV": "Vz_set",
    "time": "Time",
    "forceMode": "Source_mode",
    "measurementFunction": "Function",
    "channel": "Channel",
    "rangeAuto": "Auto_range",
    "voltageRangeAuto": "V_auto_range",
    "currentRangeAuto": "I_auto_range",
    "voltageDcRangeV": "V_DC_range",
    "voltageAcRangeV": "V_AC_range",
    "currentDcRangeA": "I_DC_range",
    "currentAcRangeA": "I_AC_range",
    "voltageSenseRangeV": "V_range",
    "currentSenseRangeA": "I_range",
    "voltageRangeIndex": "V_range",
    "currentRangeIndex": "I_range",
    "resistanceRangeOhm": "R_range",
    "frequencyVoltageRangeV": "Frequency_input_range",
    "currentComplianceA": "I_limit",
    "voltageComplianceV": "V_limit",
    "currentLimitA": "I_limit",
    "voltageLimitV": "V_limit",
    "outputEnabled": "Output_on",
    "outputUnstable": "Output_unstable",
    "communicationError": "Command_error",
    "nplc": "NPLC",
    "voltageNplc": "V_NPLC",
    "currentNplc": "I_NPLC",
    "autoZero": "Auto_zero",
    "inputImpedanceAuto": "High_impedance",
    "acBandwidthHz": "AC_bandwidth",
    "apertureSeconds": "Gate_time",
    "triggerSource": "Trigger_source",
    "triggerDelayAuto": "Auto_delay",
    "triggerDelaySeconds": "Trigger_delay",
    "sampleCount": "Sample_count",
    "displayEnabled": "Display_on",
}


def defaultVariableNickname(key: str, label: str = "") -> str:
    known = _DEFAULT_NAMES.get(key)
    if known:
        return known
    channel = re.fullmatch(r"(smu[1-4]|vs[12]|vm[12])(.+)", key)
    if channel:
        prefix, suffix = channel.groups()
        suffixKey = suffix[0].lower() + suffix[1:]
        channelNames = {
            "voltageV": "V_meas",
            "currentA": "I_meas",
            "voltageLevelV": "V_set",
        }
        name = channelNames.get(suffixKey) or defaultVariableNickname(suffixKey)
        return f"{prefix.upper()}_{name}"
    # Driver labels supply meanings for new drivers without instrument-specific UI.
    text = re.sub(r"\([^)]*\)", "", label).strip() if label else ""
    if not text:
        text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    name = re.sub(r"[^\w]+", "_", text, flags=re.UNICODE).strip("_")
    if not name or not name.isidentifier():
        name = f"var_{name}" if name else "Variable"
    return name


def makeVariableNicknames(
    variables: list[tuple[str, str]], saved: dict[str, Any] | None = None
) -> dict[str, str]:
    result: dict[str, str] = {}
    keys = {key for key, _label in variables}
    used: set[str] = set()
    # Reserve saved choices first so regenerating defaults cannot steal a nickname.
    for key, _label in variables:
        nickname = (saved or {}).get(key)
        if (
            isinstance(nickname, str)
            and nickname.isidentifier()
            and nickname.casefold() not in used
            and (nickname not in keys or nickname == key)
        ):
            result[key] = nickname
            used.add(nickname.casefold())
    for key, label in variables:
        if key in result:
            continue
        base = defaultVariableNickname(key, label)
        nickname = base
        number = 2
        while nickname.casefold() in used or (nickname in keys and nickname != key):
            nickname = f"{base}_{number}"
            number += 1
        result[key] = nickname
        used.add(nickname.casefold())
    return result


def replaceVariableReferences(text: str, replacements: dict[str, str]) -> str:
    if not replacements:
        return text
    alternatives = "|".join(
        re.escape(key) for key in sorted(replacements, key=len, reverse=True)
    )
    pattern = re.compile(r"(?<![\w.:])(?:" + alternatives + r")(?![\w.])")
    return pattern.sub(lambda match: replacements[match.group()], text)


def displayExpression(expr: str, nicknames: dict[tuple[str, str], str]) -> str:
    return replaceVariableReferences(
        expr,
        {f"{inst}:{key}": f"{inst}:{name}" for (inst, key), name in nicknames.items()},
    )


def canonicalExpression(expr: str, nicknames: dict[tuple[str, str], str]) -> str:
    return replaceVariableReferences(
        expr,
        {f"{inst}:{name}": f"{inst}:{key}" for (inst, key), name in nicknames.items()},
    )


def jobVariableNicknames(definition: dict[str, Any]) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for instrument in definition.get("instruments", []):
        if not isinstance(instrument, dict):
            continue
        instId = str(instrument.get("id", ""))
        config = instrument.get("config", {})
        keys = list(config) if isinstance(config, dict) else []
        keys.extend(instrument.get("measurements", []))
        for key in keys:
            result[(instId, str(key))] = defaultVariableNickname(str(key))
        names = instrument.get("variableNicknames", {})
        if not isinstance(names, dict):
            continue
        for key, nickname in names.items():
            if isinstance(nickname, str) and nickname.strip():
                result[(instId, str(key))] = nickname.strip()
    # Old jobs get defaults at display time as well as when opened in the builder.
    for variable in definition.get("plotVariables", []):
        if isinstance(variable, dict):
            pair = (str(variable.get("instrumentId", "")), str(variable.get("key", "")))
            result.setdefault(pair, defaultVariableNickname(pair[1]))
    # Include unlisted legacy expression dependencies without changing evaluation.
    from genesis.core.runtime.expression_eval import ExpressionError, compileExpression

    for plot in definition.get("plots", []):
        if not isinstance(plot, dict):
            continue
        axes = [plot.get(key) for key in ("x", "y", "heatmapY")]
        axes.extend(plot.get("ySeries", []))
        for axis in axes:
            if not isinstance(axis, dict):
                continue
            if axis.get("type") == "var":
                pair = (str(axis.get("instrumentId", "")), str(axis.get("key", "")))
                result.setdefault(pair, defaultVariableNickname(pair[1]))
            elif axis.get("type") == "expr":
                try:
                    refs = compileExpression(str(axis.get("expr", ""))).dependencies
                except ExpressionError:
                    continue
                for pair in refs:
                    result.setdefault(pair, defaultVariableNickname(pair[1]))
    result.setdefault(("__time__", "time"), "Time")
    return result


def variableDisplayLabel(
    instrumentId: str, key: str, nicknames: dict[tuple[str, str], str]
) -> str:
    if (instrumentId, key) == ("__time__", "time"):
        return "Time (s)"
    return f"{instrumentId}:{nicknames.get((instrumentId, key), defaultVariableNickname(key))}"


def axisDisplayLabel(axis: Any, nicknames: dict[tuple[str, str], str]) -> str:
    if not isinstance(axis, dict):
        return "?"
    if axis.get("type") == "time":
        return "Time (s)"
    if axis.get("type") == "var":
        return variableDisplayLabel(
            str(axis.get("instrumentId", "")), str(axis.get("key", "")), nicknames
        )
    if axis.get("type") == "expr":
        return (
            displayExpression(str(axis.get("expr", "")).strip(), nicknames)
            or "Expression"
        )
    return "?"
