from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QDoubleValidator, QValidator, QWheelEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from genesis.ui.no_wheel_combo_box import NoWheelComboBox


class _NoWheelSpinBox(QSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        edit = self.lineEdit()
        if edit is not None:
            edit.setMaxLength(32)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()


class _NoWheelDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._validator = QDoubleValidator(self)
        self._validator.setNotation(QDoubleValidator.Notation.ScientificNotation)
        self.setDecimals(16)
        edit = self.lineEdit()
        if edit is not None:
            edit.setMaxLength(64)

    def wheelEvent(self, event: QWheelEvent) -> None:
        event.ignore()

    def validate(self, text: str, pos: int) -> tuple[QValidator.State, str, int]:
        stripped = text.strip()
        if stripped in {"", "-", "+", ".", "-.", "+."}:
            return (QValidator.State.Intermediate, text, pos)
        self._validator.setRange(
            float(self.minimum()), float(self.maximum()), max(16, int(self.decimals()))
        )
        state, _, _ = self._validator.validate(text, pos)
        return (state, text, pos)

    def valueFromText(self, text: str) -> float:
        try:
            value = float(text.strip())
        except Exception:
            return float(self.value())
        return max(float(self.minimum()), min(float(self.maximum()), value))

    def textFromValue(self, value: float) -> str:
        # Preserve significant precision on focus-out, and use scientific form
        # automatically for very small/large values.
        return f"{float(value):.16g}"


@dataclass(frozen=True, slots=True)
class SweepVariableRef:
    instrumentId: str
    key: str
    label: str = ""


class SweepDefinitionEditor(QWidget):
    changed = Signal()

    def __init__(
        self,
        availableVariables: list[SweepVariableRef],
        mode: str = "1d",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._availableVariables = availableVariables
        self._isSyncingOuterLinearFields = False
        self._isSyncingInnerLinearFields = False
        self._mode = "2d" if mode == "2d" else "1d"
        self.modeCombo = NoWheelComboBox(self)
        self.outerInstrumentCombo = NoWheelComboBox(self)
        self.outerVariableCombo = NoWheelComboBox(self)
        self.outerStartSpin = _NoWheelDoubleSpinBox(self)
        self.outerStopSpin = _NoWheelDoubleSpinBox(self)
        self.outerPointsSpin = _NoWheelSpinBox(self)
        self.outerStepSizeSpin = _NoWheelDoubleSpinBox(self)
        self.outerSpacingCombo = NoWheelComboBox(self)
        self.outerSettleTimeSpin = _NoWheelDoubleSpinBox(self)
        self.outerMaxSlewRateSpin = _NoWheelDoubleSpinBox(self)
        self.outerMaxSlewStepSpin = _NoWheelDoubleSpinBox(self)

        self.innerGroup = QGroupBox("Inner Sweep", self)
        self.innerInstrumentCombo = NoWheelComboBox(self.innerGroup)
        self.innerVariableCombo = NoWheelComboBox(self.innerGroup)
        self.innerStartSpin = _NoWheelDoubleSpinBox(self.innerGroup)
        self.innerStopSpin = _NoWheelDoubleSpinBox(self.innerGroup)
        self.innerPointsSpin = _NoWheelSpinBox(self.innerGroup)
        self.innerStepSizeSpin = _NoWheelDoubleSpinBox(self.innerGroup)
        self.innerSpacingCombo = NoWheelComboBox(self.innerGroup)
        self.innerSettleTimeSpin = _NoWheelDoubleSpinBox(self.innerGroup)
        self.innerMaxSlewRateSpin = _NoWheelDoubleSpinBox(self.innerGroup)
        self.innerMaxSlewStepSpin = _NoWheelDoubleSpinBox(self.innerGroup)

        self._setupUi()
        self.setAvailableVariables(availableVariables)

    def _setupUi(self) -> None:
        rootLayout = QVBoxLayout(self)
        modeGroup = QGroupBox("Sweep Mode", self)
        modeForm = QFormLayout(modeGroup)
        self.modeCombo.addItem("1D Sweep", userData="1d")
        self.modeCombo.addItem("2D Sweep", userData="2d")
        self._restoreData(self.modeCombo, self._mode)
        modeForm.addRow("Mode", self.modeCombo)
        rootLayout.addWidget(modeGroup)

        outerGroup = QGroupBox("Outer Sweep", self)
        outerForm = QFormLayout(outerGroup)
        rootLayout.addWidget(outerGroup)
        rootLayout.addWidget(self.innerGroup)

        self._configureSweepControls(
            self.outerStartSpin,
            self.outerStopSpin,
            self.outerPointsSpin,
            self.outerStepSizeSpin,
            self.outerSettleTimeSpin,
            self.outerMaxSlewRateSpin,
            self.outerMaxSlewStepSpin,
            self.outerSpacingCombo,
        )
        self._configureSweepControls(
            self.innerStartSpin,
            self.innerStopSpin,
            self.innerPointsSpin,
            self.innerStepSizeSpin,
            self.innerSettleTimeSpin,
            self.innerMaxSlewRateSpin,
            self.innerMaxSlewStepSpin,
            self.innerSpacingCombo,
        )

        outerForm.addRow("Instrument", self.outerInstrumentCombo)
        outerForm.addRow("Variable", self.outerVariableCombo)
        outerForm.addRow("Start", self.outerStartSpin)
        outerForm.addRow("Stop", self.outerStopSpin)
        outerForm.addRow("Points", self.outerPointsSpin)
        outerForm.addRow("Step Size", self.outerStepSizeSpin)
        outerForm.addRow("Settle Time (s)", self.outerSettleTimeSpin)
        outerForm.addRow("Maximum Slew Rate (/s)", self.outerMaxSlewRateSpin)
        outerForm.addRow("Maximum Slew Step (/step)", self.outerMaxSlewStepSpin)
        outerForm.addRow("Spacing", self.outerSpacingCombo)

        innerForm = QFormLayout(self.innerGroup)
        innerForm.addRow("Instrument", self.innerInstrumentCombo)
        innerForm.addRow("Variable", self.innerVariableCombo)
        innerForm.addRow("Start", self.innerStartSpin)
        innerForm.addRow("Stop", self.innerStopSpin)
        innerForm.addRow("Points", self.innerPointsSpin)
        innerForm.addRow("Step Size", self.innerStepSizeSpin)
        innerForm.addRow("Settle Time (s)", self.innerSettleTimeSpin)
        innerForm.addRow("Maximum Slew Rate (/s)", self.innerMaxSlewRateSpin)
        innerForm.addRow("Maximum Slew Step (/step)", self.innerMaxSlewStepSpin)
        innerForm.addRow("Spacing", self.innerSpacingCombo)

        self.modeCombo.currentIndexChanged.connect(self._onModeComboChanged)
        self.outerInstrumentCombo.currentIndexChanged.connect(
            self._onOuterInstrumentChanged
        )
        self.outerInstrumentCombo.currentIndexChanged.connect(
            lambda _idx: self.changed.emit()
        )
        self.outerVariableCombo.currentIndexChanged.connect(
            lambda _idx: self.changed.emit()
        )
        self.innerInstrumentCombo.currentIndexChanged.connect(
            self._onInnerInstrumentChanged
        )
        self.innerInstrumentCombo.currentIndexChanged.connect(
            lambda _idx: self.changed.emit()
        )
        self.innerVariableCombo.currentIndexChanged.connect(
            lambda _idx: self.changed.emit()
        )
        self.outerStartSpin.valueChanged.connect(
            lambda _v: self._onLinearBaseChanged("outer")
        )
        self.outerStopSpin.valueChanged.connect(
            lambda _v: self._onLinearBaseChanged("outer")
        )
        self.outerPointsSpin.valueChanged.connect(
            lambda _v: self._onPointsChanged("outer")
        )
        self.outerStepSizeSpin.valueChanged.connect(
            lambda _v: self._onStepSizeChanged("outer")
        )
        self.outerSettleTimeSpin.valueChanged.connect(lambda _v: self.changed.emit())
        self.outerMaxSlewRateSpin.valueChanged.connect(lambda _v: self.changed.emit())
        self.outerMaxSlewStepSpin.valueChanged.connect(lambda _v: self.changed.emit())
        self.outerSpacingCombo.currentIndexChanged.connect(
            lambda _idx: self._onSpacingChanged("outer")
        )

        self.innerStartSpin.valueChanged.connect(
            lambda _v: self._onLinearBaseChanged("inner")
        )
        self.innerStopSpin.valueChanged.connect(
            lambda _v: self._onLinearBaseChanged("inner")
        )
        self.innerPointsSpin.valueChanged.connect(
            lambda _v: self._onPointsChanged("inner")
        )
        self.innerStepSizeSpin.valueChanged.connect(
            lambda _v: self._onStepSizeChanged("inner")
        )
        self.innerSettleTimeSpin.valueChanged.connect(lambda _v: self.changed.emit())
        self.innerMaxSlewRateSpin.valueChanged.connect(lambda _v: self.changed.emit())
        self.innerMaxSlewStepSpin.valueChanged.connect(lambda _v: self.changed.emit())
        self.innerSpacingCombo.currentIndexChanged.connect(
            lambda _idx: self._onSpacingChanged("inner")
        )

        self._syncStepSizeFromPoints("outer")
        self._syncStepSizeFromPoints("inner")
        self._updateLinearControlsEnabled("outer")
        self._updateLinearControlsEnabled("inner")
        self._onModeChanged()

    def _onModeComboChanged(self, _idx: int) -> None:
        self._mode = str(self.modeCombo.currentData() or "1d")
        self._onModeChanged()

    def _configureSweepControls(
        self,
        startSpin: _NoWheelDoubleSpinBox,
        stopSpin: _NoWheelDoubleSpinBox,
        pointsSpin: _NoWheelSpinBox,
        stepSizeSpin: _NoWheelDoubleSpinBox,
        settleTimeSpin: _NoWheelDoubleSpinBox,
        maxSlewRateSpin: _NoWheelDoubleSpinBox,
        maxSlewStepSpin: _NoWheelDoubleSpinBox,
        spacingCombo: QComboBox,
    ) -> None:
        startSpin.setDecimals(16)
        stopSpin.setDecimals(16)
        settleTimeSpin.setDecimals(16)
        startSpin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        stopSpin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        pointsSpin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        stepSizeSpin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        settleTimeSpin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        startSpin.setRange(-1e12, 1e12)
        stopSpin.setRange(-1e12, 1e12)
        settleTimeSpin.setRange(0.0, 1e6)
        settleTimeSpin.setSingleStep(0.01)
        settleTimeSpin.setValue(0.0)
        maxSlewRateSpin.setDecimals(16)
        maxSlewRateSpin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        maxSlewRateSpin.setRange(0.0, 1e12)
        maxSlewRateSpin.setSingleStep(0.1)
        maxSlewRateSpin.setValue(0.0)
        maxSlewStepSpin.setDecimals(16)
        maxSlewStepSpin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        maxSlewStepSpin.setRange(1e-12, 1e12)
        maxSlewStepSpin.setSingleStep(0.001)
        maxSlewStepSpin.setValue(0.01)
        pointsSpin.setRange(2, 100000)
        pointsSpin.setValue(101)
        stepSizeSpin.setDecimals(16)
        stepSizeSpin.setRange(1e-12, 1e12)
        stepSizeSpin.setSingleStep(0.001)
        stepSizeSpin.setValue(0.01)
        spacingCombo.addItem("Linear", userData="linear")
        spacingCombo.addItem("Log", userData="log")

    def setAvailableVariables(self, availableVariables: list[SweepVariableRef]) -> None:
        self._availableVariables = availableVariables
        currentOuterInst = self.outerInstrumentCombo.currentData()
        currentOuterVar = self.outerVariableCombo.currentData()
        currentInnerInst = self.innerInstrumentCombo.currentData()
        currentInnerVar = self.innerVariableCombo.currentData()

        byInstrument: dict[str, list[str]] = {}
        for ref in self._availableVariables:
            byInstrument.setdefault(ref.instrumentId, [])
            if ref.key not in byInstrument[ref.instrumentId]:
                byInstrument[ref.instrumentId].append(ref.key)
        for instId in byInstrument:
            byInstrument[instId].sort()

        self.outerInstrumentCombo.clear()
        self.innerInstrumentCombo.clear()
        for instId in sorted(byInstrument.keys()):
            label = "Time" if instId == "__time__" else instId
            self.outerInstrumentCombo.addItem(label, userData=instId)
            self.innerInstrumentCombo.addItem(label, userData=instId)

        self._restoreData(self.outerInstrumentCombo, currentOuterInst)
        self._restoreData(self.innerInstrumentCombo, currentInnerInst)
        self._rebuildVariableCombo(
            byInstrument,
            str(self.outerInstrumentCombo.currentData() or ""),
            self.outerVariableCombo,
        )
        self._rebuildVariableCombo(
            byInstrument,
            str(self.innerInstrumentCombo.currentData() or ""),
            self.innerVariableCombo,
        )
        self._restoreData(self.outerVariableCombo, currentOuterVar)
        self._restoreData(self.innerVariableCombo, currentInnerVar)

    def _restoreData(self, combo: QComboBox, data: Any) -> None:
        if data is None:
            return
        for i in range(combo.count()):
            if combo.itemData(i) == data:
                combo.setCurrentIndex(i)
                return

    def _rebuildVariableCombo(
        self, byInstrument: dict[str, list[str]], instrumentId: str, combo: QComboBox
    ) -> None:
        current = combo.currentData()
        combo.clear()
        for key in byInstrument.get(instrumentId, []):
            label = next(
                (
                    ref.label
                    for ref in self._availableVariables
                    if ref.instrumentId == instrumentId and ref.key == key and ref.label
                ),
                key,
            )
            combo.addItem(label, userData=key)
        self._restoreData(combo, current)

    def _onOuterInstrumentChanged(self) -> None:
        byInstrument: dict[str, list[str]] = {}
        for ref in self._availableVariables:
            byInstrument.setdefault(ref.instrumentId, [])
            if ref.key not in byInstrument[ref.instrumentId]:
                byInstrument[ref.instrumentId].append(ref.key)
        for instId in byInstrument:
            byInstrument[instId].sort()
        self._rebuildVariableCombo(
            byInstrument,
            str(self.outerInstrumentCombo.currentData() or ""),
            self.outerVariableCombo,
        )

    def _onInnerInstrumentChanged(self) -> None:
        byInstrument: dict[str, list[str]] = {}
        for ref in self._availableVariables:
            byInstrument.setdefault(ref.instrumentId, [])
            if ref.key not in byInstrument[ref.instrumentId]:
                byInstrument[ref.instrumentId].append(ref.key)
        for instId in byInstrument:
            byInstrument[instId].sort()
        self._rebuildVariableCombo(
            byInstrument,
            str(self.innerInstrumentCombo.currentData() or ""),
            self.innerVariableCombo,
        )

    def _onLinearBaseChanged(self, which: str) -> None:
        self._syncStepSizeFromPoints(which)
        self.changed.emit()

    def _onPointsChanged(self, which: str) -> None:
        self._syncStepSizeFromPoints(which)
        self.changed.emit()

    def _onStepSizeChanged(self, which: str) -> None:
        self._syncPointsFromStepSize(which)
        self.changed.emit()

    def _onSpacingChanged(self, which: str) -> None:
        self._updateLinearControlsEnabled(which)
        if self._isLinear(which):
            self._syncStepSizeFromPoints(which)
        self.changed.emit()

    def _isLinear(self, which: str) -> bool:
        spacingCombo = (
            self.outerSpacingCombo if which == "outer" else self.innerSpacingCombo
        )
        return str(spacingCombo.currentData() or "linear") == "linear"

    def _syncStepSizeFromPoints(self, which: str) -> None:
        if self._isSyncing(which) or not self._isLinear(which):
            return
        startSpin, stopSpin, pointsSpin, stepSizeSpin = self._controlsForSync(which)
        points = max(2, int(pointsSpin.value()))
        span = float(stopSpin.value()) - float(startSpin.value())
        step = abs(span / (points - 1)) if points > 1 else 0.0
        if step <= 0.0:
            step = 1e-6
        self._setSyncing(which, True)
        stepSizeSpin.setValue(step)
        self._setSyncing(which, False)

    def _syncPointsFromStepSize(self, which: str) -> None:
        if self._isSyncing(which) or not self._isLinear(which):
            return
        startSpin, stopSpin, pointsSpin, stepSizeSpin = self._controlsForSync(which)
        step = abs(float(stepSizeSpin.value()))
        if step <= 0.0:
            return
        span = abs(float(stopSpin.value()) - float(startSpin.value()))
        points = int(round(span / step)) + 1
        points = max(2, min(points, pointsSpin.maximum()))
        self._setSyncing(which, True)
        pointsSpin.setValue(points)
        self._setSyncing(which, False)

    def _updateLinearControlsEnabled(self, which: str) -> None:
        isLinear = self._isLinear(which)
        if which == "outer":
            self.outerStepSizeSpin.setEnabled(isLinear)
        else:
            self.innerStepSizeSpin.setEnabled(isLinear)

    def _controlsForSync(self, which: str) -> tuple[
        _NoWheelDoubleSpinBox,
        _NoWheelDoubleSpinBox,
        _NoWheelSpinBox,
        _NoWheelDoubleSpinBox,
    ]:
        if which == "outer":
            return (
                self.outerStartSpin,
                self.outerStopSpin,
                self.outerPointsSpin,
                self.outerStepSizeSpin,
            )
        return (
            self.innerStartSpin,
            self.innerStopSpin,
            self.innerPointsSpin,
            self.innerStepSizeSpin,
        )

    def _isSyncing(self, which: str) -> bool:
        return (
            self._isSyncingOuterLinearFields
            if which == "outer"
            else self._isSyncingInnerLinearFields
        )

    def _setSyncing(self, which: str, value: bool) -> None:
        if which == "outer":
            self._isSyncingOuterLinearFields = value
        else:
            self._isSyncingInnerLinearFields = value

    def _onModeChanged(self) -> None:
        is2d = self._mode == "2d"
        self.innerGroup.setEnabled(is2d)
        self.innerGroup.setVisible(is2d)
        self.changed.emit()

    def _axisDefinition(self, which: str) -> dict[str, Any]:
        if which == "outer":
            inst = str(self.outerInstrumentCombo.currentData() or "")
            key = str(self.outerVariableCombo.currentData() or "")
            start = float(self.outerStartSpin.value())
            stop = float(self.outerStopSpin.value())
            points = int(self.outerPointsSpin.value())
            stepSize = float(self.outerStepSizeSpin.value())
            settle = float(self.outerSettleTimeSpin.value())
            maxSlewRate = float(self.outerMaxSlewRateSpin.value())
            maxSlewStep = float(self.outerMaxSlewStepSpin.value())
            spacing = str(self.outerSpacingCombo.currentData() or "linear")
        else:
            inst = str(self.innerInstrumentCombo.currentData() or "")
            key = str(self.innerVariableCombo.currentData() or "")
            start = float(self.innerStartSpin.value())
            stop = float(self.innerStopSpin.value())
            points = int(self.innerPointsSpin.value())
            stepSize = float(self.innerStepSizeSpin.value())
            settle = float(self.innerSettleTimeSpin.value())
            maxSlewRate = float(self.innerMaxSlewRateSpin.value())
            maxSlewStep = float(self.innerMaxSlewStepSpin.value())
            spacing = str(self.innerSpacingCombo.currentData() or "linear")
        return {
            "instrumentId": inst,
            "key": key,
            "start": start,
            "stop": stop,
            "points": points,
            "stepSize": abs(stepSize),
            "settleTimeSeconds": settle,
            "maxSlewRate": max(0.0, maxSlewRate),
            "maxSlewStep": max(1e-12, abs(maxSlewStep)),
            "spacing": spacing,
        }

    def toDefinition(self) -> dict[str, Any]:
        definition: dict[str, Any] = {
            "mode": self._mode,
            "outer": self._axisDefinition("outer"),
        }
        if self._mode == "2d":
            definition["inner"] = self._axisDefinition("inner")
        return definition

    def setDefinition(self, definition: dict[str, Any]) -> None:
        # Backward compatibility: old single-sweep shape.
        if "outer" not in definition:
            definition = {"mode": "1d", "outer": dict(definition)}
        self._mode = "2d" if str(definition.get("mode", "1d")) == "2d" else "1d"
        self._restoreData(self.modeCombo, self._mode)
        outer = definition.get("outer", {})
        inner = definition.get("inner", {})
        if isinstance(outer, dict):
            self._setAxisDefinition("outer", outer)
        if isinstance(inner, dict):
            self._setAxisDefinition("inner", inner)
        self._onModeChanged()

    def _setAxisDefinition(self, which: str, definition: dict[str, Any]) -> None:
        if which == "outer":
            instCombo = self.outerInstrumentCombo
            varCombo = self.outerVariableCombo
            onChanged = self._onOuterInstrumentChanged
            startSpin = self.outerStartSpin
            stopSpin = self.outerStopSpin
            pointsSpin = self.outerPointsSpin
            stepSizeSpin = self.outerStepSizeSpin
            settleSpin = self.outerSettleTimeSpin
            maxSlewRateSpin = self.outerMaxSlewRateSpin
            maxSlewStepSpin = self.outerMaxSlewStepSpin
            spacingCombo = self.outerSpacingCombo
        else:
            instCombo = self.innerInstrumentCombo
            varCombo = self.innerVariableCombo
            onChanged = self._onInnerInstrumentChanged
            startSpin = self.innerStartSpin
            stopSpin = self.innerStopSpin
            pointsSpin = self.innerPointsSpin
            stepSizeSpin = self.innerStepSizeSpin
            settleSpin = self.innerSettleTimeSpin
            maxSlewRateSpin = self.innerMaxSlewRateSpin
            maxSlewStepSpin = self.innerMaxSlewStepSpin
            spacingCombo = self.innerSpacingCombo

        self._restoreData(instCombo, definition.get("instrumentId"))
        onChanged()
        self._restoreData(varCombo, definition.get("key"))
        if "start" in definition:
            startSpin.setValue(float(definition["start"]))
        if "stop" in definition:
            stopSpin.setValue(float(definition["stop"]))
        if "points" in definition:
            pointsSpin.setValue(int(definition["points"]))
        if "stepSize" in definition:
            stepSizeSpin.setValue(float(definition["stepSize"]))
        if "settleTimeSeconds" in definition:
            settleSpin.setValue(float(definition["settleTimeSeconds"]))
        if "maxSlewRate" in definition:
            maxSlewRateSpin.setValue(max(0.0, float(definition["maxSlewRate"])))
        if "maxSlewStep" in definition:
            maxSlewStepSpin.setValue(max(1e-12, float(definition["maxSlewStep"])))
        else:
            # Backward-compat: old jobs use stepSize as slew increment.
            maxSlewStepSpin.setValue(
                max(1e-12, float(definition.get("stepSize", 0.01)))
            )
        self._restoreData(spacingCombo, definition.get("spacing"))
        self._updateLinearControlsEnabled(which)
        if "stepSize" not in definition:
            self._syncStepSizeFromPoints(which)
