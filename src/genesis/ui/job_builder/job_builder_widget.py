from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from genesis.app.user_dirs import genesis_jobs_dir
from genesis.core.instrument.registry import InstrumentRegistry
from genesis.core.runtime.expression_eval import ExpressionError, compileExpression
from genesis.ui.job_builder.critical_condition_editor import (
    CriticalConditionEditor,
    MeasurementSignalRef,
)
from genesis.ui.job_builder.instrument_instance_editor import InstrumentInstanceEditor
from genesis.ui.job_builder.plot_definition_editor import (
    PlotDefinitionEditor,
    PlotVariableRef,
)
from genesis.ui.job_builder.sweep_definition_editor import (
    SweepDefinitionEditor,
    SweepVariableRef,
)


class JobBuilderWidget(QWidget):
    def __init__(
        self, instrumentRegistry: InstrumentRegistry, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.instrumentRegistry = instrumentRegistry
        self.jobNameLabel = QLabel(self)
        self.jobIdLabel = QLabel(self)

        self._instanceEditors: list[InstrumentInstanceEditor] = []
        self._plotEditors: list[PlotDefinitionEditor] = []
        self._sweepEditors: list[SweepDefinitionEditor] = []
        self._currentJobPath: Path | None = None

        self._setupUi()

    def _setupUi(self) -> None:
        rootLayout = QVBoxLayout(self)

        scrollArea = QScrollArea(self)
        scrollArea.setWidgetResizable(True)
        scrollArea.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rootLayout.addWidget(scrollArea)

        contentWidget = QWidget(scrollArea)
        scrollArea.setWidget(contentWidget)

        contentLayout = QVBoxLayout(contentWidget)

        idRow = QHBoxLayout()
        idRow.addWidget(QLabel("Job Name", self))
        self.jobNameLabel.setTextInteractionFlags(
            self.jobNameLabel.textInteractionFlags()
        )
        self.jobNameLabel.setStyleSheet("color: #666;")
        self.jobNameLabel.setText("(none)")
        idRow.addWidget(self.jobNameLabel)
        idRow.addSpacing(16)
        idRow.addWidget(QLabel("Job ID", self))
        self.jobIdLabel.setTextInteractionFlags(self.jobIdLabel.textInteractionFlags())
        self.jobIdLabel.setStyleSheet("color: #666;")
        self.jobIdLabel.setText("(none)")
        idRow.addWidget(self.jobIdLabel)
        idRow.addStretch(1)
        contentLayout.addLayout(idRow)

        buttonRow = QHBoxLayout()
        self.createNewJobButton = QPushButton("Create New Job File", self)
        self.openJobButton = QPushButton("Open Job JSON...", self)
        self.saveCurrentJobButton = QPushButton("Save", self)
        self.saveJobButton = QPushButton("Save As...", self)
        self.saveJobButton.setEnabled(False)
        self.saveCurrentJobButton.setEnabled(False)
        buttonRow.addWidget(self.createNewJobButton)
        buttonRow.addWidget(self.openJobButton)
        buttonRow.addWidget(self.saveCurrentJobButton)
        buttonRow.addWidget(self.saveJobButton)
        buttonRow.addStretch(1)
        contentLayout.addLayout(buttonRow)

        sectionTabs = QTabWidget(contentWidget)
        contentLayout.addWidget(sectionTabs, 1)

        instrumentsTab = QWidget(sectionTabs)
        instrumentsTabLayout = QVBoxLayout(instrumentsTab)
        sectionTabs.addTab(instrumentsTab, "Instruments")

        sweepsTab = QWidget(sectionTabs)
        sweepsTabLayout = QVBoxLayout(sweepsTab)
        sectionTabs.addTab(sweepsTab, "Sweep")

        plotsTab = QWidget(sectionTabs)
        plotsTabLayout = QVBoxLayout(plotsTab)
        sectionTabs.addTab(plotsTab, "Plots")

        sectionTabs.setCurrentIndex(0)

        selectionBox = QGroupBox("Add Instruments", self)
        selectionLayout = QVBoxLayout(selectionBox)
        selectionRow = QHBoxLayout()

        self.availableInstrumentList = QListWidget(selectionBox)
        self.availableInstrumentList.setSelectionMode(
            self.availableInstrumentList.SelectionMode.SingleSelection
        )

        for instrumentTypeKey in self.instrumentRegistry.listInstruments():
            instrumentType = self.instrumentRegistry.getInstrumentType(
                instrumentTypeKey
            )
            displayName = getattr(instrumentType, "displayName", instrumentTypeKey)
            item = QListWidgetItem(str(displayName), self.availableInstrumentList)
            item.setData(256, instrumentTypeKey)  # Qt.ItemDataRole.UserRole = 256

        self.addInstrumentButton = QPushButton("Add to List", selectionBox)

        buttonCol = QVBoxLayout()
        buttonCol.addWidget(self.addInstrumentButton)
        buttonCol.addStretch(1)

        selectionRow.addWidget(self.availableInstrumentList, 1)
        buttonContainer = QWidget(selectionBox)
        buttonContainer.setLayout(buttonCol)
        selectionRow.addWidget(buttonContainer)

        selectionLayout.addLayout(selectionRow)
        instrumentsTabLayout.addWidget(selectionBox)

        instancesBox = QGroupBox("Instrument Instances", self)
        instancesLayout = QVBoxLayout(instancesBox)

        self.instancesTabWidget = QTabWidget(instancesBox)
        instancesLayout.addWidget(self.instancesTabWidget)

        instancesButtonRow = QHBoxLayout()
        self.cloneInstrumentButton = QPushButton(
            "Clone Selected Instrument", instancesBox
        )
        self.removeInstrumentButton = QPushButton(
            "Remove Selected Instrument", instancesBox
        )
        instancesButtonRow.addWidget(self.cloneInstrumentButton)
        instancesButtonRow.addWidget(self.removeInstrumentButton)
        instancesButtonRow.addStretch(1)
        instancesLayout.addLayout(instancesButtonRow)

        instrumentsTabLayout.addWidget(instancesBox)
        instrumentsTabLayout.addStretch(1)

        sweepsBox = QGroupBox("", self)
        sweepsLayout = QVBoxLayout(sweepsBox)
        self.sweepsContainer = QWidget(sweepsBox)
        self.sweepsContainerLayout = QVBoxLayout(self.sweepsContainer)
        self.sweepsContainerLayout.setContentsMargins(0, 0, 0, 0)
        self.sweepsContainerLayout.setSpacing(8)
        sweepsLayout.addWidget(self.sweepsContainer)
        sweepsTabLayout.addWidget(sweepsBox)
        self.criticalEditor = CriticalConditionEditor(parent=sweepsTab)
        sweepsTabLayout.addWidget(self.criticalEditor)
        sweepsTabLayout.addStretch(1)

        plotsBox = QGroupBox("Plot Configuration", self)
        plotsLayout = QVBoxLayout(plotsBox)
        self.plotsList = QListWidget(plotsBox)
        plotsLayout.addWidget(QLabel("Added Plots", plotsBox))
        plotsLayout.addWidget(self.plotsList)

        plotsButtonRow = QHBoxLayout()
        self.add1dPlotButton = QPushButton("Add 1D Plot", plotsBox)
        self.add2dPlotButton = QPushButton("Add 2D Plot", plotsBox)
        self.clonePlotButton = QPushButton("Clone Selected Plot", plotsBox)
        self.removePlotButton = QPushButton("Remove Selected Plot", plotsBox)
        plotsButtonRow.addWidget(self.add1dPlotButton)
        plotsButtonRow.addWidget(self.add2dPlotButton)
        plotsButtonRow.addWidget(self.clonePlotButton)
        plotsButtonRow.addWidget(self.removePlotButton)
        plotsButtonRow.addStretch(1)
        plotsLayout.addLayout(plotsButtonRow)

        self.plotsContainer = QWidget(plotsBox)
        self.plotsContainerLayout = QVBoxLayout(self.plotsContainer)
        self.plotsContainerLayout.setContentsMargins(0, 0, 0, 0)
        self.plotsContainerLayout.setSpacing(8)

        plotsLayout.addWidget(self.plotsContainer)
        plotsTabLayout.addWidget(plotsBox)
        plotsTabLayout.addStretch(1)

        self.createNewJobButton.clicked.connect(self._onCreateNewJobClicked)
        self.openJobButton.clicked.connect(self._onOpenJobClicked)
        self.saveCurrentJobButton.clicked.connect(self._onSaveCurrentJobClicked)
        self.saveJobButton.clicked.connect(self._onSaveJobClicked)
        self.addInstrumentButton.clicked.connect(self._onAddInstrumentClicked)
        self.cloneInstrumentButton.clicked.connect(self._onCloneInstrumentClicked)
        self.removeInstrumentButton.clicked.connect(self._onRemoveInstrumentClicked)
        self.add1dPlotButton.clicked.connect(lambda: self._onAddPlotClicked("scatter"))
        self.add2dPlotButton.clicked.connect(lambda: self._onAddPlotClicked("heatmap"))
        self.clonePlotButton.clicked.connect(self._onClonePlotClicked)
        self.removePlotButton.clicked.connect(self._onRemovePlotClicked)
        self.plotsList.currentRowChanged.connect(self._onPlotListSelectionChanged)
        self._ensureSingleSweepEditor()

    def _resetDraft(self) -> None:
        self._clearInstanceEditors()
        self._ensureSingleSweepEditor()
        newJobId = str(uuid.uuid4())
        self.jobIdLabel.setText(newJobId)
        self._currentJobPath = None
        self._refreshJobLabels()
        self.saveJobButton.setEnabled(True)
        self.saveCurrentJobButton.setEnabled(False)
        self.criticalEditor.setDefinition({})

    def _clearInstanceEditors(self) -> None:
        self._instanceEditors.clear()
        while self.instancesTabWidget.count() > 0:
            widget = self.instancesTabWidget.widget(0)
            self.instancesTabWidget.removeTab(0)
            if widget is not None:
                widget.setParent(None)
        self._clearPlotEditors()
        self._clearSweepEditors()
        self._refreshPlotEditorsAvailableVariables()

    def _clearPlotEditors(self) -> None:
        self._plotEditors.clear()
        self.plotsList.clear()
        while self.plotsContainerLayout.count():
            item = self.plotsContainerLayout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

    def _clearSweepEditors(self) -> None:
        self._sweepEditors.clear()
        while self.sweepsContainerLayout.count():
            item = self.sweepsContainerLayout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

    def _onCreateNewJobClicked(self) -> None:
        self._resetDraft()

    def _onOpenJobClicked(self) -> None:
        pathStr = QFileDialog.getOpenFileName(
            self,
            "Open Job JSON",
            str(genesis_jobs_dir()),
            filter="JSON files (*.json)",
        )[0]
        if not pathStr:
            return
        path = Path(pathStr)
        try:
            payload = json.loads(path.read_text(encoding="utf8"))
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self._loadJobDefinition(payload, sourcePath=path)

    def _loadJobDefinition(
        self, definition: dict[str, Any], sourcePath: Path | None = None
    ) -> None:
        self._clearInstanceEditors()
        jobId = str(definition.get("jobId", "")).strip() or str(uuid.uuid4())
        self.jobIdLabel.setText(jobId)
        self._currentJobPath = sourcePath
        self._refreshJobLabels()
        self.saveJobButton.setEnabled(True)
        self.saveCurrentJobButton.setEnabled(sourcePath is not None)

        for instDef in list(definition.get("instruments", [])):
            instrumentTypeKey = str(instDef.get("type", ""))
            if not instrumentTypeKey:
                continue
            try:
                instrumentType = self.instrumentRegistry.getInstrumentType(
                    instrumentTypeKey
                )
            except Exception:
                continue
            nextIndex = self._getNextInstanceIndex(instrumentTypeKey)
            editor = InstrumentInstanceEditor(
                instrumentTypeKey=instrumentTypeKey,
                instrumentType=instrumentType,
                instanceIndex=nextIndex,
                initialValues=dict(instDef),
                parent=self.instancesTabWidget,
            )
            self._instanceEditors.append(editor)
            self.instancesTabWidget.addTab(editor, editor.getInstanceId())
            editor.instanceIdChanged.connect(
                lambda _text, ed=editor: self._syncTabTitle(ed)
            )
            editor.instanceIdChanged.connect(
                lambda _text: self._refreshVariableDependentEditors()
            )

        self._refreshVariableDependentEditors()

        sweepDef = self._extractSingleSweepDefinition(definition)
        self._ensureSingleSweepEditor(mode=str(sweepDef.get("mode", "1d")))
        if self._sweepEditors:
            self._sweepEditors[0].setDefinition(sweepDef)
        criticalRaw = definition.get("criticalRamping")
        self.criticalEditor.setDefinition(
            dict(criticalRaw) if isinstance(criticalRaw, dict) else {}
        )

        for plotDef in list(definition.get("plots", [])):
            available = self._getPlottableVariableRefs()
            renderMode = str(dict(plotDef).get("renderMode", "scatter"))
            editor = PlotDefinitionEditor(
                availableVariables=available,
                renderMode=("heatmap" if renderMode == "heatmap" else "scatter"),
                parent=self.plotsContainer,
            )
            editor.setSelectedSweepVariables(self._getSelectedSweepPlotVariableRefs())
            editor.setDefinition(dict(plotDef))
            editor.removeRequested.connect(lambda ed=editor: self._removePlotEditor(ed))
            editor.changed.connect(self._refreshPlotsListLabels)
            self._plotEditors.append(editor)
            self.plotsList.addItem(
                QListWidgetItem(self._plotLabelForEditor(editor), self.plotsList)
            )
            self.plotsContainerLayout.addWidget(editor)

        self._refreshPlotsListLabels()
        self._onPlotListSelectionChanged(self.plotsList.currentRow())

    def _validatePlotDefinitions(self) -> str | None:
        for index, editor in enumerate(self._plotEditors, start=1):
            error = editor.validateDefinition()
            if error:
                return f"Plot {index} has an invalid expression:\n{error}"
        criticalError = self.criticalEditor.validateDefinition()
        if criticalError:
            return criticalError
        return None

    def _onSaveCurrentJobClicked(self) -> None:
        if self._currentJobPath is None:
            self._onSaveJobClicked()
            return
        validationError = self._validatePlotDefinitions()
        if validationError is not None:
            QMessageBox.critical(self, "Save failed", validationError)
            return
        try:
            jobDefinition = self._buildJobDefinition()
        except ValueError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        try:
            self._currentJobPath.write_text(
                json.dumps(jobDefinition, indent=2), encoding="utf8"
            )
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._refreshJobLabels()

    def _onAddInstrumentClicked(self) -> None:
        if self.jobIdLabel.text().strip() in ("", "(none)"):
            self._resetDraft()

        item = self.availableInstrumentList.currentItem()
        if item is None:
            return

        instrumentTypeKey = str(item.data(256))
        instrumentType = self.instrumentRegistry.getInstrumentType(instrumentTypeKey)

        nextIndex = self._getNextInstanceIndex(instrumentTypeKey)

        editor = InstrumentInstanceEditor(
            instrumentTypeKey=instrumentTypeKey,
            instrumentType=instrumentType,
            instanceIndex=nextIndex,
            parent=self.instancesTabWidget,
        )
        self._instanceEditors.append(editor)
        self.instancesTabWidget.addTab(editor, editor.getInstanceId())
        self.instancesTabWidget.setCurrentWidget(editor)
        editor.instanceIdChanged.connect(
            lambda _text, ed=editor: self._syncTabTitle(ed)
        )
        editor.instanceIdChanged.connect(
            lambda _text: self._refreshVariableDependentEditors()
        )
        self._refreshVariableDependentEditors()

    def _onCloneInstrumentClicked(self) -> None:
        idx = self.instancesTabWidget.currentIndex()
        if idx < 0 or idx >= len(self._instanceEditors):
            return
        source = self._instanceEditors[idx]
        values = source.getValues()
        instrumentTypeKey = str(values.get("type", ""))
        if not instrumentTypeKey:
            return
        instrumentType = self.instrumentRegistry.getInstrumentType(instrumentTypeKey)

        nextIndex = self._getNextInstanceIndex(instrumentTypeKey)
        values["id"] = f"{instrumentTypeKey}-{nextIndex}"

        editor = InstrumentInstanceEditor(
            instrumentTypeKey=instrumentTypeKey,
            instrumentType=instrumentType,
            instanceIndex=nextIndex,
            initialValues=values,
            parent=self.instancesTabWidget,
        )
        self._instanceEditors.append(editor)
        self.instancesTabWidget.addTab(editor, editor.getInstanceId())
        self.instancesTabWidget.setCurrentWidget(editor)
        editor.instanceIdChanged.connect(
            lambda _text, ed=editor: self._syncTabTitle(ed)
        )
        editor.instanceIdChanged.connect(
            lambda _text: self._refreshVariableDependentEditors()
        )
        self._refreshVariableDependentEditors()

    def _getNextInstanceIndex(self, instrumentTypeKey: str) -> int:
        usedIds = {editor.getInstanceId() for editor in self._instanceEditors}
        n = 1
        while f"{instrumentTypeKey}-{n}" in usedIds:
            n += 1
        return n

    def _syncTabTitle(self, editor: InstrumentInstanceEditor) -> None:
        idx = self.instancesTabWidget.indexOf(editor)
        if idx < 0:
            return
        self.instancesTabWidget.setTabText(idx, editor.getInstanceId())

    def _onRemoveInstrumentClicked(self) -> None:
        index = self.instancesTabWidget.currentIndex()
        if index < 0:
            return

        widget = self.instancesTabWidget.widget(index)
        self.instancesTabWidget.removeTab(index)

        if widget is not None:
            self._instanceEditors = [
                e for e in self._instanceEditors if e is not widget
            ]
            widget.setParent(None)
            self._refreshVariableDependentEditors()

    def _onAddPlotClicked(self, renderMode: str) -> None:
        available = self._getPlottableVariableRefs()
        editor = PlotDefinitionEditor(
            availableVariables=available,
            renderMode=("heatmap" if renderMode == "heatmap" else "scatter"),
            parent=self.plotsContainer,
        )
        editor.setSelectedSweepVariables(self._getSelectedSweepPlotVariableRefs())
        editor.removeRequested.connect(lambda ed=editor: self._removePlotEditor(ed))
        editor.changed.connect(self._refreshPlotsListLabels)
        self._plotEditors.append(editor)
        item = QListWidgetItem(self._plotLabelForEditor(editor), self.plotsList)
        self.plotsList.addItem(item)
        self.plotsContainerLayout.addWidget(editor)
        self.plotsList.setCurrentRow(len(self._plotEditors) - 1)
        self._onPlotListSelectionChanged(self.plotsList.currentRow())

    def _onClonePlotClicked(self) -> None:
        idx = self.plotsList.currentRow()
        if idx < 0 or idx >= len(self._plotEditors):
            return
        source = self._plotEditors[idx]
        definition = source.toDefinition()
        available = self._getPlottableVariableRefs()
        editor = PlotDefinitionEditor(
            availableVariables=available,
            renderMode=(
                "heatmap"
                if str(definition.get("renderMode", "scatter")) == "heatmap"
                else "scatter"
            ),
            parent=self.plotsContainer,
        )
        editor.setSelectedSweepVariables(self._getSelectedSweepPlotVariableRefs())
        editor.setDefinition(definition)
        editor.removeRequested.connect(lambda ed=editor: self._removePlotEditor(ed))
        editor.changed.connect(self._refreshPlotsListLabels)
        self._plotEditors.append(editor)
        item = QListWidgetItem(self._plotLabelForEditor(editor), self.plotsList)
        self.plotsList.addItem(item)
        self.plotsContainerLayout.addWidget(editor)
        self.plotsList.setCurrentRow(len(self._plotEditors) - 1)
        self._onPlotListSelectionChanged(self.plotsList.currentRow())

    def _onRemovePlotClicked(self) -> None:
        idx = self.plotsList.currentRow()
        if idx < 0 or idx >= len(self._plotEditors):
            return
        self._removePlotEditor(self._plotEditors[idx])

    def _removePlotEditor(self, editor: PlotDefinitionEditor) -> None:
        idx = self._plotEditors.index(editor) if editor in self._plotEditors else -1
        self._plotEditors = [e for e in self._plotEditors if e is not editor]
        if idx >= 0:
            self.plotsList.takeItem(idx)
        editor.setParent(None)
        self._onPlotListSelectionChanged(self.plotsList.currentRow())

    def _getPlottableVariableRefs(self) -> list[PlotVariableRef]:
        refs: list[PlotVariableRef] = []
        seen: set[tuple[str, str]] = set()
        for instEditor in self._instanceEditors:
            instId = instEditor.getInstanceId()
            for key in instEditor.getMeasuredSignals():
                pair = (instId, key)
                if pair in seen:
                    continue
                seen.add(pair)
                refs.append(
                    PlotVariableRef(
                        instrumentId=instId, key=key, label=f"{key} (measurement)"
                    )
                )
            for key in instEditor.getAvailableSweepConfigKeys():
                pair = (instId, key)
                if pair in seen:
                    continue
                seen.add(pair)
                refs.append(
                    PlotVariableRef(
                        instrumentId=instId, key=key, label=f"{key} (sweep)"
                    )
                )
        for sweepEditor in self._sweepEditors:
            sweepDef = sweepEditor.toDefinition()
            axisDefs: list[dict[str, Any]] = []
            outer = sweepDef.get("outer", {})
            if isinstance(outer, dict):
                axisDefs.append(outer)
            if str(sweepDef.get("mode", "1d")) == "2d":
                inner = sweepDef.get("inner", {})
                if isinstance(inner, dict):
                    axisDefs.append(inner)
            for axis in axisDefs:
                instId = str(axis.get("instrumentId", ""))
                key = str(axis.get("key", ""))
                if not instId or not key:
                    continue
                pair = (instId, key)
                if pair in seen:
                    continue
                seen.add(pair)
                if instId == "__time__" and key == "time":
                    refs.append(
                        PlotVariableRef(
                            instrumentId=instId,
                            key=key,
                            label="time (sweep)",
                        )
                    )
                else:
                    refs.append(
                        PlotVariableRef(
                            instrumentId=instId,
                            key=key,
                            label=f"{key} (sweep)",
                        )
                    )
        return refs

    def _getSelectedSweepPlotVariableRefs(self) -> list[PlotVariableRef]:
        if not self._sweepEditors:
            return []
        sweepDef = self._sweepEditors[0].toDefinition()
        refs: list[PlotVariableRef] = []
        axisDefs: list[dict[str, Any]] = []
        outer = sweepDef.get("outer", {})
        if isinstance(outer, dict):
            axisDefs.append(outer)
        if str(sweepDef.get("mode", "1d")) == "2d":
            inner = sweepDef.get("inner", {})
            if isinstance(inner, dict):
                axisDefs.append(inner)
        for axis in axisDefs:
            instId = str(axis.get("instrumentId", ""))
            key = str(axis.get("key", ""))
            if not instId or not key:
                continue
            label = (
                "time (sweep)"
                if (instId == "__time__" and key == "time")
                else f"{key} (sweep)"
            )
            refs.append(PlotVariableRef(instrumentId=instId, key=key, label=label))
        return refs

    def _refreshPlotEditorsAvailableVariables(self) -> None:
        available = self._getPlottableVariableRefs()
        selectedSweep = self._getSelectedSweepPlotVariableRefs()
        for ed in self._plotEditors:
            ed.setAvailableVariables(available)
            ed.setSelectedSweepVariables(selectedSweep)
        self._refreshPlotsListLabels()

    def _refreshSweepEditorsAvailableVariables(self) -> None:
        self._ensureSingleSweepEditor()
        available = self._getSweepVariableRefs()
        for ed in self._sweepEditors:
            ed.setAvailableVariables(available)

    def _refreshVariableDependentEditors(self) -> None:
        self._refreshPlotEditorsAvailableVariables()
        self._refreshSweepEditorsAvailableVariables()
        self._refreshCriticalEditorAvailableSignals()
        self._applySweepSelectionToInstrumentEditors()

    def _getMeasurementSignalRefs(self) -> list[MeasurementSignalRef]:
        refs: list[MeasurementSignalRef] = []
        for instEditor in self._instanceEditors:
            instId = instEditor.getInstanceId()
            if not instId:
                continue
            for (
                key,
                label,
            ) in instEditor.instrumentType.getAvailableMeasurementSignals():
                refs.append(
                    MeasurementSignalRef(
                        instrumentId=instId,
                        key=str(key),
                        label=str(label),
                    )
                )
        return refs

    def _refreshCriticalEditorAvailableSignals(self) -> None:
        self.criticalEditor.setAvailableSignals(self._getMeasurementSignalRefs())

    def _plotLabelForEditor(self, editor: PlotDefinitionEditor) -> str:
        definition = editor.toDefinition()
        title = str(definition.get("title", "")).strip()
        if title:
            return title

        def _axisLabel(axisDef: Any) -> str:
            if isinstance(axisDef, dict) and axisDef.get("type") == "time":
                return "time"
            if isinstance(axisDef, dict) and axisDef.get("type") == "var":
                return f"{axisDef.get('instrumentId')}:{axisDef.get('key')}"
            if isinstance(axisDef, dict) and axisDef.get("type") == "expr":
                return str(axisDef.get("expr", "")).strip() or "expr"
            return "?"

        renderMode = str(definition.get("renderMode", "scatter") or "scatter")
        if renderMode == "heatmap":
            xLabel = _axisLabel(definition.get("x"))
            yLabel = _axisLabel(definition.get("heatmapY"))
            zSeries = definition.get("ySeries", [])
            zLabel = (
                _axisLabel(zSeries[0])
                if isinstance(zSeries, list)
                and zSeries
                and isinstance(zSeries[0], dict)
                else _axisLabel(definition.get("y"))
            )
            return f"{zLabel} vs {xLabel},{yLabel}"

        xLabel = _axisLabel(definition.get("x"))
        ySeries = definition.get("ySeries", [])
        if isinstance(ySeries, list) and ySeries:
            yLabel = ", ".join(
                _axisLabel(axis) for axis in ySeries if isinstance(axis, dict)
            )
            yLabel = yLabel or _axisLabel(definition.get("y"))
        else:
            yLabel = _axisLabel(definition.get("y"))
        return f"{yLabel} vs {xLabel}"

    def _refreshPlotsListLabels(self) -> None:
        for i, editor in enumerate(self._plotEditors):
            if i < self.plotsList.count():
                self.plotsList.item(i).setText(self._plotLabelForEditor(editor))

    def _onPlotListSelectionChanged(self, row: int) -> None:
        for i, editor in enumerate(self._plotEditors):
            editor.setVisible(i == row if row >= 0 else False)

    def _getSweepVariableRefs(self) -> list[SweepVariableRef]:
        refs: list[SweepVariableRef] = [
            SweepVariableRef(instrumentId="__time__", key="time")
        ]
        for instEditor in self._instanceEditors:
            instId = instEditor.getInstanceId()
            for key in instEditor.getAvailableSweepConfigKeys():
                refs.append(SweepVariableRef(instrumentId=instId, key=key))
        return refs

    def _ensureSingleSweepEditor(self, mode: str = "1d") -> None:
        if self._sweepEditors:
            return
        available = self._getSweepVariableRefs()
        editor = SweepDefinitionEditor(
            availableVariables=available,
            mode=mode,
            parent=self.sweepsContainer,
        )
        editor.changed.connect(self._onSweepDefinitionChanged)
        self._sweepEditors = [editor]
        self.sweepsContainerLayout.addWidget(editor)
        self._onSweepDefinitionChanged()

    def _onSweepDefinitionChanged(self) -> None:
        self._refreshPlotEditorsAvailableVariables()
        self._applySweepSelectionToInstrumentEditors()

    def _applySweepSelectionToInstrumentEditors(self) -> None:
        if not self._sweepEditors:
            for instEditor in self._instanceEditors:
                instEditor.setSweptConfigKeys(set())
            return
        sweepDef = self._sweepEditors[0].toDefinition()
        pairsByInstrumentId: dict[str, set[str]] = {}
        outer = sweepDef.get("outer", {})
        if isinstance(outer, dict):
            instId = str(outer.get("instrumentId", ""))
            key = str(outer.get("key", ""))
            if instId and key:
                pairsByInstrumentId.setdefault(instId, set()).add(key)
        if str(sweepDef.get("mode", "1d")) == "2d":
            inner = sweepDef.get("inner", {})
            if isinstance(inner, dict):
                instId = str(inner.get("instrumentId", ""))
                key = str(inner.get("key", ""))
                if instId and key:
                    pairsByInstrumentId.setdefault(instId, set()).add(key)
        for instEditor in self._instanceEditors:
            instEditor.setSweptConfigKeys(
                pairsByInstrumentId.get(instEditor.getInstanceId(), set())
            )

    def _extractSingleSweepDefinition(
        self, definition: dict[str, Any]
    ) -> dict[str, Any]:
        members = [s for s in list(definition.get("sweeps", [])) if isinstance(s, dict)]
        if not members:
            return {"mode": "1d", "outer": {}}
        if len(members) >= 2:
            return {"mode": "2d", "outer": dict(members[0]), "inner": dict(members[1])}
        if len(members) == 1:
            return {"mode": "1d", "outer": dict(members[0])}
        return {"mode": "1d", "outer": {}}

    def _buildJobDefinition(self) -> dict[str, Any]:
        self._ensureSingleSweepEditor()
        instruments: list[dict[str, Any]] = []
        for editor in self._instanceEditors:
            instruments.append(editor.getValues())
        plots = [ed.toDefinition() for ed in self._plotEditors]
        sweepDefinitions = [ed.toDefinition() for ed in self._sweepEditors]
        sweeps: list[dict[str, Any]] = []
        for sweepDef in sweepDefinitions:
            mode = str(sweepDef.get("mode", "1d"))
            outer = (
                sweepDef.get("outer", {})
                if isinstance(sweepDef.get("outer", {}), dict)
                else {}
            )
            if (
                not str(outer.get("instrumentId", "")).strip()
                or not str(outer.get("key", "")).strip()
            ):
                raise ValueError("Sweep is missing an outer variable.")
            sweeps.append(dict(outer))
            if mode == "2d":
                inner = (
                    sweepDef.get("inner", {})
                    if isinstance(sweepDef.get("inner", {}), dict)
                    else {}
                )
                if (
                    not str(inner.get("instrumentId", "")).strip()
                    or not str(inner.get("key", "")).strip()
                ):
                    raise ValueError("2D sweep is missing an inner variable.")
                firstRef = (
                    str(outer.get("instrumentId", "")),
                    str(outer.get("key", "")),
                )
                secondRef = (
                    str(inner.get("instrumentId", "")),
                    str(inner.get("key", "")),
                )
                if firstRef == secondRef:
                    raise ValueError(
                        "2D sweep uses the same variable for outer and inner axes."
                    )
                sweeps.append(dict(inner))
        plotVariables = [
            {
                "instrumentId": ref.instrumentId,
                "key": ref.key,
                "label": ref.label or ref.key,
            }
            for ref in self._getPlottableVariableRefs()
        ]
        customVariables: list[dict[str, Any]] = []
        for idx, plotDef in enumerate(plots):
            for axisKey in ("x", "y"):
                axisDef = plotDef.get(axisKey, {})
                if not isinstance(axisDef, dict) or axisDef.get("type") != "expr":
                    continue
                expr = str(axisDef.get("expr", "")).strip()
                if not expr:
                    continue
                dependencies = list(axisDef.get("dependencies", []))
                if not dependencies:
                    try:
                        compiled = compileExpression(expr)
                        dependencies = [
                            {"instrumentId": instId, "key": key}
                            for instId, key in sorted(compiled.dependencies)
                        ]
                    except ExpressionError:
                        dependencies = []
                customVariables.append(
                    {
                        "id": f"plot{idx + 1}_{axisKey}",
                        "expression": expr,
                        "dependencies": dependencies,
                        "source": {
                            "plotIndex": idx,
                            "axis": axisKey,
                            "title": str(plotDef.get("title", "")).strip(),
                        },
                    }
                )

        # Build a per-instrument safe-state map for convenience.
        safeState: dict[str, Any] = {}
        for instrument in instruments:
            instrumentId = str(instrument.get("id", ""))
            instrumentSafeConfig = instrument.get("safeConfig", {})
            if instrumentId:
                safeState[instrumentId] = instrumentSafeConfig

        return {
            "jobId": self.jobIdLabel.text(),
            "instruments": instruments,
            # v1 placeholders; the execution system will be added later.
            "steps": [],
            "liveViews": [],
            "plots": plots,
            "plotVariables": plotVariables,
            "customVariables": customVariables,
            "sweeps": sweeps,
            "criticalRamping": self.criticalEditor.toDefinition(),
            "sweepMode": (
                str(sweepDefinitions[0].get("mode", "1d")) if sweepDefinitions else "1d"
            ),
            "exports": [],
            "safeState": safeState,
        }

    def _onSaveJobClicked(self) -> None:
        validationError = self._validatePlotDefinitions()
        if validationError is not None:
            QMessageBox.critical(self, "Save failed", validationError)
            return
        try:
            jobDefinition = self._buildJobDefinition()
        except ValueError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        jobId = str(jobDefinition.get("jobId", "")).strip() or "job"
        safeName = re.sub(r"[^A-Za-z0-9._-]+", "_", jobId) or "job"
        defaultPath = self._currentJobPath or (genesis_jobs_dir() / f"{safeName}.json")

        pathStr = QFileDialog.getSaveFileName(
            self,
            "Save Job JSON",
            str(defaultPath),
            filter="JSON files (*.json)",
        )[0]
        if not pathStr:
            return

        path = Path(pathStr)
        try:
            path.write_text(json.dumps(jobDefinition, indent=2), encoding="utf8")
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._currentJobPath = path
        self._refreshJobLabels()
        self.saveCurrentJobButton.setEnabled(True)

    def _refreshJobLabels(self) -> None:
        if self._currentJobPath is not None:
            self.jobNameLabel.setText(self._currentJobPath.name)
            return
        if self.jobIdLabel.text().strip() in ("", "(none)"):
            self.jobNameLabel.setText("(none)")
        else:
            self.jobNameLabel.setText("Untitled")
