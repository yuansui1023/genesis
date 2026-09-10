from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from genesis.core.runtime.critical_condition import (
    CriticalRampingConfig,
    validate_critical_ramping_config,
)
from genesis.ui.job_builder.sweep_definition_editor import (
    _NoWheelDoubleSpinBox,
    _NoWheelSpinBox,
)
from genesis.ui.no_wheel_combo_box import NoWheelComboBox


@dataclass(frozen=True, slots=True)
class MeasurementSignalRef:
    instrumentId: str
    key: str
    label: str = ""


class _ConditionRow(QWidget):
    changed = Signal()
    removeRequested = Signal(object)

    def __init__(
        self,
        availableSignals: list[MeasurementSignalRef],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._availableSignals = availableSignals
        self.instrumentCombo = NoWheelComboBox(self)
        self.signalCombo = NoWheelComboBox(self)
        self.comparisonCombo = NoWheelComboBox(self)
        self.thresholdSpin = _NoWheelDoubleSpinBox(self)
        self.removeButton = QPushButton("Remove", self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.instrumentCombo, 2)
        layout.addWidget(self.signalCombo, 2)
        layout.addWidget(self.comparisonCombo, 1)
        layout.addWidget(self.thresholdSpin, 2)
        layout.addWidget(self.removeButton)

        self.comparisonCombo.addItem("greater than (>)", userData="gt")
        self.comparisonCombo.addItem("less than (<)", userData="lt")
        self.comparisonCombo.addItem("abs greater than (|x| >)", userData="abs_gt")
        self.comparisonCombo.addItem("abs less than (|x| <)", userData="abs_lt")
        self.thresholdSpin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self.thresholdSpin.setDecimals(16)
        self.thresholdSpin.setRange(-1e12, 1e12)
        self.thresholdSpin.setValue(0.0)

        self.instrumentCombo.currentIndexChanged.connect(self._onInstrumentChanged)
        self.signalCombo.currentIndexChanged.connect(lambda _idx: self.changed.emit())
        self.comparisonCombo.currentIndexChanged.connect(
            lambda _idx: self.changed.emit()
        )
        self.thresholdSpin.valueChanged.connect(lambda _v: self.changed.emit())
        self.removeButton.clicked.connect(lambda: self.removeRequested.emit(self))
        self._rebuildInstrumentCombo()

    def setAvailableSignals(self, availableSignals: list[MeasurementSignalRef]) -> None:
        current = self.toDefinition()
        self._availableSignals = availableSignals
        self._rebuildInstrumentCombo()
        self.setDefinition(current)

    def _instrumentIds(self) -> list[str]:
        seen: set[str] = set()
        ids: list[str] = []
        for ref in self._availableSignals:
            if ref.instrumentId in seen or not ref.instrumentId:
                continue
            seen.add(ref.instrumentId)
            ids.append(ref.instrumentId)
        return ids

    def _signalsForInstrument(self, instrumentId: str) -> list[MeasurementSignalRef]:
        return [
            ref for ref in self._availableSignals if ref.instrumentId == instrumentId
        ]

    def _rebuildInstrumentCombo(self) -> None:
        self.instrumentCombo.blockSignals(True)
        self.instrumentCombo.clear()
        for instrumentId in self._instrumentIds():
            self.instrumentCombo.addItem(instrumentId, userData=instrumentId)
        self.instrumentCombo.blockSignals(False)
        self._rebuildSignalCombo()

    def _rebuildSignalCombo(self) -> None:
        instrumentId = str(self.instrumentCombo.currentData() or "")
        self.signalCombo.blockSignals(True)
        self.signalCombo.clear()
        for ref in self._signalsForInstrument(instrumentId):
            label = ref.label.strip() or ref.key
            self.signalCombo.addItem(f"{ref.key} ({label})", userData=ref.key)
        self.signalCombo.blockSignals(False)

    def _onInstrumentChanged(self) -> None:
        self._rebuildSignalCombo()
        self.changed.emit()

    def _restoreData(self, combo: NoWheelComboBox, value: Any) -> None:
        target = str(value or "")
        idx = combo.findData(target)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def toDefinition(self) -> dict[str, Any]:
        return {
            "instrumentId": str(self.instrumentCombo.currentData() or ""),
            "signalKey": str(self.signalCombo.currentData() or ""),
            "comparison": str(self.comparisonCombo.currentData() or "gt"),
            "threshold": float(self.thresholdSpin.value()),
        }

    def setDefinition(self, definition: dict[str, Any] | None) -> None:
        payload = definition if isinstance(definition, dict) else {}
        self._restoreData(self.instrumentCombo, payload.get("instrumentId"))
        self._rebuildSignalCombo()
        self._restoreData(self.signalCombo, payload.get("signalKey"))
        self._restoreData(self.comparisonCombo, payload.get("comparison", "gt"))
        if "threshold" in payload:
            try:
                self.thresholdSpin.setValue(float(payload["threshold"]))
            except (TypeError, ValueError):
                pass


class CriticalConditionEditor(QWidget):
    changed = Signal()

    def __init__(
        self,
        availableSignals: list[MeasurementSignalRef] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._availableSignals = availableSignals or []
        self._rows: list[_ConditionRow] = []

        self.enabledCheck = QCheckBox("Enable critical ramping", self)
        self.holdCheck = QCheckBox("Hold all instrument settings when triggered", self)
        self.combineCombo = NoWheelComboBox(self)
        self.consecutiveSpin = _NoWheelSpinBox(self)
        self.addConditionButton = QPushButton("Add Condition", self)
        self.warningLabel = QLabel(self)
        self._conditionsContainer = QWidget(self)
        self._conditionsLayout = QVBoxLayout(self._conditionsContainer)
        self._conditionsLayout.setContentsMargins(0, 0, 0, 0)
        self._conditionsLayout.setSpacing(6)

        self._setupUi()
        self._syncEnabledState()

    def _setupUi(self) -> None:
        root = QVBoxLayout(self)
        group = QGroupBox("Critical Ramping", self)
        groupLayout = QVBoxLayout(group)

        form = QFormLayout()
        form.addRow(self.enabledCheck)
        form.addRow(self.holdCheck)
        self.combineCombo.addItem("Any condition (OR)", userData="any")
        self.combineCombo.addItem("All conditions (AND)", userData="all")
        form.addRow("Combine conditions", self.combineCombo)
        self.consecutiveSpin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.consecutiveSpin.setRange(1, 10000)
        self.consecutiveSpin.setValue(1)
        form.addRow("Consecutive samples required", self.consecutiveSpin)
        groupLayout.addLayout(form)

        groupLayout.addWidget(QLabel("Stop when:", group))
        groupLayout.addWidget(self._conditionsContainer)
        groupLayout.addWidget(self.addConditionButton)

        self.warningLabel.setWordWrap(True)
        self.warningLabel.setText(
            "This is a software-layer stop. It is not a hardware interlock. "
            "Trigger timing follows Genesis sampling (including extra reads during "
            "software slew and waitForSetpoint progress). Holding settings leaves "
            "outputs at their triggered values until you Stop, Abort, or Initialize."
        )
        self.warningLabel.setStyleSheet("color: #f5c16c; font-weight: 500;")
        groupLayout.addWidget(self.warningLabel)
        root.addWidget(group)

        self.enabledCheck.toggled.connect(self._onEnabledToggled)
        self.holdCheck.toggled.connect(lambda _checked: self.changed.emit())
        self.combineCombo.currentIndexChanged.connect(lambda _idx: self.changed.emit())
        self.consecutiveSpin.valueChanged.connect(lambda _v: self.changed.emit())
        self.addConditionButton.clicked.connect(self._onAddConditionClicked)

    def setAvailableSignals(self, availableSignals: list[MeasurementSignalRef]) -> None:
        self._availableSignals = list(availableSignals)
        for row in self._rows:
            row.setAvailableSignals(self._availableSignals)
        self._syncEnabledState()

    def _onEnabledToggled(self, checked: bool) -> None:
        if checked and not self._rows:
            self._addConditionRow({})
        self._syncEnabledState()
        self.changed.emit()

    def _syncEnabledState(self) -> None:
        enabled = self.enabledCheck.isChecked()
        self.holdCheck.setEnabled(enabled)
        self.combineCombo.setEnabled(enabled)
        self.consecutiveSpin.setEnabled(enabled)
        self.addConditionButton.setEnabled(enabled and bool(self._availableSignals))
        for row in self._rows:
            row.setEnabled(enabled)

    def _onAddConditionClicked(self) -> None:
        self._addConditionRow({})
        self.changed.emit()

    def _addConditionRow(self, definition: dict[str, Any] | None) -> None:
        row = _ConditionRow(self._availableSignals, parent=self._conditionsContainer)
        if definition:
            row.setDefinition(definition)
        row.changed.connect(self.changed.emit)
        row.removeRequested.connect(self._onRemoveRow)
        self._rows.append(row)
        self._conditionsLayout.addWidget(row)
        row.setEnabled(self.enabledCheck.isChecked())

    def _onRemoveRow(self, row: object) -> None:
        if not isinstance(row, _ConditionRow) or row not in self._rows:
            return
        self._rows = [item for item in self._rows if item is not row]
        row.setParent(None)
        self.changed.emit()

    def _restoreData(self, combo: NoWheelComboBox, value: Any) -> None:
        target = str(value or "")
        idx = combo.findData(target)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def toDefinition(self) -> dict[str, Any]:
        config = CriticalRampingConfig.from_mapping(
            {
                "enabled": self.enabledCheck.isChecked(),
                "holdSettingsOnTrigger": self.holdCheck.isChecked(),
                "combine": str(self.combineCombo.currentData() or "any"),
                "consecutiveHitsRequired": int(self.consecutiveSpin.value()),
                "conditions": [row.toDefinition() for row in self._rows],
            }
        )
        return config.to_dict()

    def setDefinition(self, definition: dict[str, Any] | None) -> None:
        config = CriticalRampingConfig.from_mapping(
            definition if isinstance(definition, dict) else {}
        )
        self.enabledCheck.blockSignals(True)
        self.enabledCheck.setChecked(config.enabled)
        self.enabledCheck.blockSignals(False)
        self.holdCheck.setChecked(config.hold_settings_on_trigger)
        self._restoreData(self.combineCombo, config.combine)
        self.consecutiveSpin.setValue(max(1, int(config.consecutive_hits_required)))
        for row in list(self._rows):
            row.setParent(None)
        self._rows = []
        for condition in config.conditions:
            self._addConditionRow(condition.to_dict())
        if config.enabled and not self._rows:
            self._addConditionRow({})
        self._syncEnabledState()

    def validateDefinition(self) -> str | None:
        return validate_critical_ramping_config(
            CriticalRampingConfig.from_mapping(self.toDefinition())
        )
