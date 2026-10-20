from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QLineEdit,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from genesis.core.instrument.base_instrument import BaseInstrument
from genesis.core.instrument.config_field import ConfigFieldDefinition
from genesis.core.job.variable_names import makeVariableNicknames
from genesis.core.transport.transport_factory import getAvailableTransportKeys
from genesis.ui.no_wheel_combo_box import NoWheelComboBox
from genesis.ui.job_builder.instrument_config_form import InstrumentConfigForm


class InstrumentInstanceEditor(QWidget):
    instanceIdChanged = Signal(str)
    variablesChanged = Signal()

    def __init__(
        self,
        instrumentTypeKey: str,
        instrumentType: type[BaseInstrument],
        instanceIndex: int,
        initialValues: dict[str, Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.instrumentTypeKey = instrumentTypeKey
        self.instrumentType = instrumentType
        self.instanceIndex = instanceIndex

        self.initialValues = initialValues or {}

        self._fields: list[ConfigFieldDefinition] = (
            self.instrumentType.getJobConfigFields()
        )
        self._sweptConfigKeys: set[str] = set()
        rawNames = self.initialValues.get("variableNicknames")
        self.variableNicknames = makeVariableNicknames(
            [(key, label) for key, label, _role in self.getVariableDefinitions()],
            rawNames if isinstance(rawNames, dict) else {},
        )

        rootLayout = QVBoxLayout(self)
        rootLayout.setContentsMargins(0, 0, 0, 0)

        scrollArea = QScrollArea(self)
        scrollArea.setWidgetResizable(True)
        rootLayout.addWidget(scrollArea)

        contentWidget = QWidget(scrollArea)
        scrollArea.setWidget(contentWidget)
        contentLayout = QVBoxLayout(contentWidget)
        contentLayout.setContentsMargins(0, 0, 0, 0)

        groupBox = QWidget(contentWidget)
        layout = QVBoxLayout(groupBox)
        layout.setContentsMargins(0, 0, 0, 0)

        formLayout = QFormLayout()

        self.idLineEdit = QLineEdit(groupBox)
        self.idLineEdit.setText(str(self.initialValues.get("id", self._defaultId())))
        self.idLineEdit.textChanged.connect(self.instanceIdChanged.emit)
        formLayout.addRow("Instance ID", self.idLineEdit)

        self.transportComboBox = NoWheelComboBox(groupBox)
        transportKeys: list[str] = list(self.instrumentType.getSupportedTransportKeys())
        for available in getAvailableTransportKeys():
            if available not in transportKeys:
                transportKeys.append(available)
        for transportKey in transportKeys:
            self.transportComboBox.addItem(transportKey)
        transportDefault = str(
            self.initialValues.get(
                "transport",
                self.instrumentType.getDefaultTransportKey(),
            )
        )
        idx = self.transportComboBox.findText(transportDefault)
        self.transportComboBox.setCurrentIndex(idx if idx >= 0 else 0)
        formLayout.addRow("Transport", self.transportComboBox)

        self.addressLineEdit = QLineEdit(groupBox)
        self.addressLineEdit.setText(
            str(
                self.initialValues.get(
                    "address", self.instrumentType.getDefaultAddress()
                )
            )
        )
        formLayout.addRow("Address / Resource", self.addressLineEdit)

        # What to acquire for this instrument.
        self._measurementCheckboxes: list[tuple[str, QCheckBox]] = []

        measurementSignals = self.instrumentType.getAvailableMeasurementSignals()

        measurementsInitial = set(self.initialValues.get("measurements", []))

        if measurementSignals:
            measurementCol = QVBoxLayout()
            for signalKey, label in measurementSignals:
                cb = QCheckBox(label, groupBox)
                cb.setChecked(signalKey in measurementsInitial)
                cb.toggled.connect(lambda _checked: self.variablesChanged.emit())
                self._measurementCheckboxes.append((signalKey, cb))
                measurementCol.addWidget(cb)
            measurementCol.addStretch(1)
            measurementContainer = QWidget(groupBox)
            measurementContainer.setLayout(measurementCol)
            formLayout.addRow("Measurements", measurementContainer)

        layout.addLayout(formLayout)

        initialConfig = self._buildInitialConfig()
        self.configForm = InstrumentConfigForm(
            fields=self._fields,
            initialValues=initialConfig,
            showSweepCheckboxes=False,
            parent=groupBox,
        )
        configLabel = QLabel("Configuration", groupBox)
        configLabel.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(configLabel)
        layout.addWidget(self.configForm)

        # Safe-state configuration uses the same field definitions but may hold
        # different values (e.g. more conservative sensitivity, different output
        # ranges). These are persisted into the job JSON as `safeConfig`.
        safeConfigInitial = self._buildInitialSafeConfig()
        self.safeConfigForm = InstrumentConfigForm(
            fields=self._fields,
            initialValues=safeConfigInitial,
            showSweepCheckboxes=False,
            parent=groupBox,
        )
        safeLabel = QLabel("Safe-State Configuration", groupBox)
        safeLabel.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(safeLabel)
        layout.addWidget(self.safeConfigForm)
        self._connectModeAwareSweepUi()
        self._updateModeAwareSweepUi()

        contentLayout.addWidget(groupBox)
        contentLayout.addStretch(1)

    def _displayTitle(self) -> str:
        return f"{self.instrumentTypeKey} instance {self.instanceIndex}"

    def _defaultId(self) -> str:
        return f"{self.instrumentTypeKey}-{self.instanceIndex}"

    def getInstanceId(self) -> str:
        return str(self.idLineEdit.text())

    def _buildInitialConfig(self) -> dict[str, Any]:
        config = dict(self.instrumentType.getDefaultJobConfig())
        config.update(self.initialValues.get("config", {}))
        return config

    def _buildInitialSafeConfig(self) -> dict[str, Any]:
        config = dict(self.instrumentType.getDefaultJobConfig())
        config.update(self.initialValues.get("safeConfig", {}))
        return config

    def getValues(self) -> dict[str, Any]:
        measurements: list[str] = []
        for key, cb in self._measurementCheckboxes:
            if cb.isChecked():
                measurements.append(key)

        result: dict[str, Any] = {
            "id": str(self.idLineEdit.text()),
            "type": self.instrumentTypeKey,
            "transport": str(self.transportComboBox.currentText()),
            "address": str(self.addressLineEdit.text()),
            "config": self.configForm.getValues(),
            "safeConfig": self.safeConfigForm.getValues(),
            "measurements": measurements,
            "variableNicknames": dict(self.variableNicknames),
        }
        raw_ts = self.initialValues.get("transportSettings")
        if isinstance(raw_ts, dict):
            result["transportSettings"] = dict(raw_ts)
        return result

    def getVariableDefinitions(self) -> list[tuple[str, str, str]]:
        signals = dict(self.instrumentType.getAvailableMeasurementSignals())
        variables: list[tuple[str, str, str]] = []
        for field in self._fields:
            role = "Sweep" if field.sweepable else "Setting"
            if field.key in signals:
                role += " / Readback"
            variables.append((field.key, field.label, role))
        fieldKeys = {field.key for field in self._fields}
        for key, label in signals.items():
            if key not in fieldKeys:
                variables.append((key, label, "Readback"))
        return variables

    def getMeasuredSignals(self) -> list[str]:
        measurements: list[str] = []
        for key, cb in self._measurementCheckboxes:
            if cb.isChecked():
                measurements.append(key)
        return measurements

    def getAvailableSweepConfigKeys(self) -> list[str]:
        return [field.key for field in self._fields if field.sweepable]

    def setSweptConfigKeys(self, keys: list[str] | set[str]) -> None:
        self._sweptConfigKeys = {str(k) for k in keys if str(k)}
        self._updateModeAwareSweepUi()

    def _connectModeAwareSweepUi(self) -> None:
        modeWidget = self.configForm.getWidget("forceMode")
        if isinstance(modeWidget, NoWheelComboBox):
            modeWidget.currentIndexChanged.connect(
                lambda _idx: self._updateModeAwareSweepUi()
            )
        voltageAutoWidget = self.configForm.getWidget("voltageRangeAuto")
        if isinstance(voltageAutoWidget, NoWheelComboBox):
            voltageAutoWidget.currentIndexChanged.connect(
                lambda _idx: self._updateModeAwareSweepUi()
            )
        currentAutoWidget = self.configForm.getWidget("currentRangeAuto")
        if isinstance(currentAutoWidget, NoWheelComboBox):
            currentAutoWidget.currentIndexChanged.connect(
                lambda _idx: self._updateModeAwareSweepUi()
            )
        safeModeWidget = self.safeConfigForm.getWidget("forceMode")
        if isinstance(safeModeWidget, NoWheelComboBox):
            safeModeWidget.currentIndexChanged.connect(
                lambda _idx: self._updateModeAwareSweepUi()
            )
        safeVoltageAutoWidget = self.safeConfigForm.getWidget("voltageRangeAuto")
        if isinstance(safeVoltageAutoWidget, NoWheelComboBox):
            safeVoltageAutoWidget.currentIndexChanged.connect(
                lambda _idx: self._updateModeAwareSweepUi()
            )
        safeCurrentAutoWidget = self.safeConfigForm.getWidget("currentRangeAuto")
        if isinstance(safeCurrentAutoWidget, NoWheelComboBox):
            safeCurrentAutoWidget.currentIndexChanged.connect(
                lambda _idx: self._updateModeAwareSweepUi()
            )

    def _updateModeAwareSweepUi(self) -> None:
        values = self.configForm.getValues()
        modeWidget = self.configForm.getWidget("forceMode")
        if modeWidget is not None:
            forceMode = str(values.get("forceMode", "")).upper()
            if forceMode == "VOLT":
                self.configForm.setSweepOptionEnabled("forceVoltageLevelV", True)
                self.configForm.setSweepOptionEnabled("forceCurrentLevelA", False)
                self.configForm.setFieldEnabled("forceVoltageLevelV", True)
                self.configForm.setFieldEnabled("forceCurrentLevelA", False)
            elif forceMode == "CURR":
                self.configForm.setSweepOptionEnabled("forceVoltageLevelV", False)
                self.configForm.setSweepOptionEnabled("forceCurrentLevelA", True)
                self.configForm.setFieldEnabled("forceVoltageLevelV", False)
                self.configForm.setFieldEnabled("forceCurrentLevelA", True)

            voltageAutoEnabled = int(values.get("voltageRangeAuto", 1)) != 0
            currentAutoEnabled = int(values.get("currentRangeAuto", 1)) != 0
            self.configForm.setFieldEnabled(
                "voltageSenseRangeV", not voltageAutoEnabled
            )
            self.configForm.setFieldEnabled(
                "currentSenseRangeA", not currentAutoEnabled
            )

        for key in self._sweptConfigKeys:
            self.configForm.setFieldEnabled(key, False)

        safeValues = self.safeConfigForm.getValues()
        safeModeWidget = self.safeConfigForm.getWidget("forceMode")
        if safeModeWidget is not None:
            safeForceMode = str(safeValues.get("forceMode", "")).upper()
            if safeForceMode == "VOLT":
                self.safeConfigForm.setFieldEnabled("forceVoltageLevelV", True)
                self.safeConfigForm.setFieldEnabled("forceCurrentLevelA", False)
            elif safeForceMode == "CURR":
                self.safeConfigForm.setFieldEnabled("forceVoltageLevelV", False)
                self.safeConfigForm.setFieldEnabled("forceCurrentLevelA", True)
            safeVoltageAutoEnabled = int(safeValues.get("voltageRangeAuto", 1)) != 0
            safeCurrentAutoEnabled = int(safeValues.get("currentRangeAuto", 1)) != 0
            self.safeConfigForm.setFieldEnabled(
                "voltageSenseRangeV", not safeVoltageAutoEnabled
            )
            self.safeConfigForm.setFieldEnabled(
                "currentSenseRangeA", not safeCurrentAutoEnabled
            )
