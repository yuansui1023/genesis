from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from genesis.core.runtime.expression_eval import (
    ExpressionError,
    compileExpression,
    tokenToRefString,
)
from genesis.ui.no_wheel_combo_box import NoWheelComboBox


@dataclass(frozen=True, slots=True)
class PlotVariableRef:
    instrumentId: str
    key: str
    label: str | None = None


class PlotDefinitionEditor(QWidget):
    changed = Signal()
    removeRequested = Signal()

    def __init__(
        self,
        availableVariables: list[PlotVariableRef],
        renderMode: str = "scatter",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._availableVariables = availableVariables
        self._renderMode = "heatmap" if renderMode == "heatmap" else "scatter"
        self._selectedSweepVariables: list[PlotVariableRef] = []

        self.titleLineEdit = QLineEdit(self)
        self.plotTypeLabel = QLabel(self)

        # 1D scatter controls
        self.scatterXVarCombo = NoWheelComboBox(self)
        self.yInsertInstrumentCombo = NoWheelComboBox(self)
        self.yInsertVariableCombo = NoWheelComboBox(self)
        self.yExprInsertButton = QPushButton("Insert", self)
        self.yAddExprButton = QPushButton("Add Y Expression", self)
        self.yExprStatusLabel = QLabel(self)
        self.yRowsContainer = QWidget(self)
        self.yRowsLayout = QVBoxLayout(self.yRowsContainer)
        self._yRows: list[dict[str, QWidget]] = []
        self._activeYExprLineEdit: QLineEdit | None = None
        self._lastFocusedLineEdit: QLineEdit | None = None

        # 2D heatmap controls
        self.heatmapXVarCombo = NoWheelComboBox(self)
        self.heatmapYVarCombo = NoWheelComboBox(self)
        self.zExprLineEdit = QLineEdit(self)
        self.zNameLineEdit = QLineEdit(self)
        self.zInsertInstrumentCombo = NoWheelComboBox(self)
        self.zInsertVariableCombo = NoWheelComboBox(self)
        self.zExprInsertButton = QPushButton("Insert", self)
        self.zExprStatusLabel = QLabel(self)

        self._setupUi()
        self.setAvailableVariables(availableVariables)
        self._installFocusTracking()

    def _setupUi(self) -> None:
        rootLayout = QHBoxLayout(self)
        form = QWidget(self)
        formLayout = QFormLayout(form)

        self.titleLineEdit.setPlaceholderText("Plot Title (Optional)")
        self.plotTypeLabel.setText(
            "2D Heatmap" if self._renderMode == "heatmap" else "1D Scatter"
        )
        formLayout.addRow("Title", self.titleLineEdit)
        formLayout.addRow("Type", self.plotTypeLabel)

        if self._renderMode == "heatmap":
            self._setupHeatmapUi(formLayout)
        else:
            self._setupScatterUi(formLayout)

        rootLayout.addWidget(form, 1)
        self._hideInactiveModeControls()

        self.titleLineEdit.textChanged.connect(lambda _text: self.changed.emit())

    def _hideInactiveModeControls(self) -> None:
        scatterOnly = [
            self.scatterXVarCombo,
            self.yInsertInstrumentCombo,
            self.yInsertVariableCombo,
            self.yExprInsertButton,
            self.yAddExprButton,
            self.yExprStatusLabel,
            self.yRowsContainer,
        ]
        heatmapOnly = [
            self.heatmapXVarCombo,
            self.heatmapYVarCombo,
            self.zExprLineEdit,
            self.zNameLineEdit,
            self.zInsertInstrumentCombo,
            self.zInsertVariableCombo,
            self.zExprInsertButton,
            self.zExprStatusLabel,
        ]
        if self._renderMode == "heatmap":
            for widget in scatterOnly:
                widget.hide()
        else:
            for widget in heatmapOnly:
                widget.hide()

    def _setupScatterUi(self, formLayout: QFormLayout) -> None:
        self.yExprStatusLabel.setStyleSheet("color: #b65f00;")
        self.yRowsLayout.setContentsMargins(0, 0, 0, 0)
        self.yRowsLayout.setSpacing(6)

        yRowsBox = QWidget(self)
        yRowsBoxLayout = QVBoxLayout(yRowsBox)
        yRowsBoxLayout.setContentsMargins(0, 0, 0, 0)
        yRowsBoxLayout.setSpacing(6)
        yRowsBoxLayout.addWidget(self.yRowsContainer)
        yRowsBoxLayout.addWidget(self.yAddExprButton)

        yExprPicker = QWidget(self)
        yExprPickerLayout = QHBoxLayout(yExprPicker)
        yExprPickerLayout.setContentsMargins(0, 0, 0, 0)
        yExprPickerLayout.addWidget(self.yInsertInstrumentCombo)
        yExprPickerLayout.addWidget(self.yInsertVariableCombo, 1)
        yExprPickerLayout.addWidget(self.yExprInsertButton)

        formLayout.addRow("X Variable", self.scatterXVarCombo)
        formLayout.addRow("Y Expressions", yRowsBox)
        formLayout.addRow("Insert Y Variable", yExprPicker)
        formLayout.addRow("", self.yExprStatusLabel)

        self.scatterXVarCombo.currentIndexChanged.connect(
            lambda _idx: self.changed.emit()
        )
        self.yInsertInstrumentCombo.currentIndexChanged.connect(
            lambda _idx: self._rebuildInsertVariableCombo(
                self.yInsertInstrumentCombo, self.yInsertVariableCombo
            )
        )
        self.yExprInsertButton.clicked.connect(self._insertScatterYToken)
        self.yExprInsertButton.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.yAddExprButton.clicked.connect(lambda: self._addYExpressionRow())
        self._addYExpressionRow()
        self._onScatterYExprEdited()

    def _setupHeatmapUi(self, formLayout: QFormLayout) -> None:
        self.zExprStatusLabel.setStyleSheet("color: #b65f00;")
        self.zNameLineEdit.setPlaceholderText("Name (optional)")

        zExprPicker = QWidget(self)
        zExprPickerLayout = QHBoxLayout(zExprPicker)
        zExprPickerLayout.setContentsMargins(0, 0, 0, 0)
        zExprPickerLayout.addWidget(self.zInsertInstrumentCombo)
        zExprPickerLayout.addWidget(self.zInsertVariableCombo, 1)
        zExprPickerLayout.addWidget(self.zExprInsertButton)

        formLayout.addRow("X Variable", self.heatmapXVarCombo)
        formLayout.addRow("Y Variable", self.heatmapYVarCombo)
        formLayout.addRow("Z Expression", self.zExprLineEdit)
        formLayout.addRow("Z Label", self.zNameLineEdit)
        formLayout.addRow("Insert Z Variable", zExprPicker)
        formLayout.addRow("", self.zExprStatusLabel)

        self.heatmapXVarCombo.currentIndexChanged.connect(
            lambda _idx: self.changed.emit()
        )
        self.heatmapYVarCombo.currentIndexChanged.connect(
            lambda _idx: self.changed.emit()
        )
        self.zInsertInstrumentCombo.currentIndexChanged.connect(
            lambda _idx: self._rebuildInsertVariableCombo(
                self.zInsertInstrumentCombo, self.zInsertVariableCombo
            )
        )
        self.zExprLineEdit.textChanged.connect(
            lambda _text: self._onHeatmapZExprEdited()
        )
        self.zExprLineEdit.selectionChanged.connect(
            lambda: self._rememberFocusedLineEdit(self.zExprLineEdit)
        )
        self.zExprLineEdit.cursorPositionChanged.connect(
            lambda _old, _new: self._rememberFocusedLineEdit(self.zExprLineEdit)
        )
        self.zNameLineEdit.selectionChanged.connect(
            lambda: self._rememberFocusedLineEdit(self.zNameLineEdit)
        )
        self.zNameLineEdit.cursorPositionChanged.connect(
            lambda _old, _new: self._rememberFocusedLineEdit(self.zNameLineEdit)
        )
        self.zNameLineEdit.textChanged.connect(lambda _text: self.changed.emit())
        self.zExprInsertButton.clicked.connect(self._insertHeatmapZToken)
        self.zExprInsertButton.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def setAvailableVariables(self, availableVariables: list[PlotVariableRef]) -> None:
        self._availableVariables = availableVariables

        if self._renderMode == "heatmap":
            hxCurrent = self.heatmapXVarCombo.currentData()
            hyCurrent = self.heatmapYVarCombo.currentData()
            self._populateSweepVariableCombo(self.heatmapXVarCombo, includeTime=True)
            self._populateSweepVariableCombo(self.heatmapYVarCombo, includeTime=True)
            self._restoreData(self.heatmapXVarCombo, hxCurrent)
            self._restoreData(self.heatmapYVarCombo, hyCurrent)

            zInstCurrent = self.zInsertInstrumentCombo.currentData()
            zVarCurrent = self.zInsertVariableCombo.currentData()
            self._rebuildInstrumentCombo(self.zInsertInstrumentCombo)
            self._restoreData(self.zInsertInstrumentCombo, zInstCurrent)
            self._rebuildInsertVariableCombo(
                self.zInsertInstrumentCombo, self.zInsertVariableCombo
            )
            self._restoreData(self.zInsertVariableCombo, zVarCurrent)
            self._onHeatmapZExprEdited()
            return

        xCurrent = self.scatterXVarCombo.currentData()
        self._populateSweepVariableCombo(self.scatterXVarCombo, includeTime=True)
        self._restoreData(self.scatterXVarCombo, xCurrent)

        yInstCurrent = self.yInsertInstrumentCombo.currentData()
        yVarCurrent = self.yInsertVariableCombo.currentData()
        self._rebuildInstrumentCombo(self.yInsertInstrumentCombo)
        self._restoreData(self.yInsertInstrumentCombo, yInstCurrent)
        self._rebuildInsertVariableCombo(
            self.yInsertInstrumentCombo, self.yInsertVariableCombo
        )
        self._restoreData(self.yInsertVariableCombo, yVarCurrent)
        self._onScatterYExprEdited()
        self._repopulateAxisSweepCombos()

    def setSelectedSweepVariables(
        self, selectedSweepVariables: list[PlotVariableRef]
    ) -> None:
        self._selectedSweepVariables = selectedSweepVariables
        self._repopulateAxisSweepCombos()

    def _availableSweepRefs(self) -> list[PlotVariableRef]:
        return list(self._selectedSweepVariables)

    def _isTimeSweepSelected(self) -> bool:
        return any(
            ref.instrumentId == "__time__" and ref.key == "time"
            for ref in self._selectedSweepVariables
        )

    def _populateSweepVariableCombo(self, combo: QComboBox, includeTime: bool) -> None:
        current = combo.currentData()
        combo.clear()
        allowTime = self._isTimeSweepSelected()
        for ref in sorted(
            self._availableSweepRefs(), key=lambda r: (r.instrumentId, r.key)
        ):
            if ref.instrumentId == "__time__" and ref.key == "time":
                if not allowTime or not includeTime:
                    continue
            display = f"{'Time' if ref.instrumentId == '__time__' else ref.instrumentId}:{ref.key}"
            combo.addItem(display, userData=(ref.instrumentId, ref.key))
        self._restoreData(combo, current)

    def _repopulateAxisSweepCombos(self) -> None:
        if self._renderMode == "heatmap":
            xCurrent = self.heatmapXVarCombo.currentData()
            yCurrent = self.heatmapYVarCombo.currentData()
            self._populateSweepVariableCombo(self.heatmapXVarCombo, includeTime=True)
            self._populateSweepVariableCombo(self.heatmapYVarCombo, includeTime=True)
            self._restoreData(self.heatmapXVarCombo, xCurrent)
            self._restoreData(self.heatmapYVarCombo, yCurrent)
            return
        current = self.scatterXVarCombo.currentData()
        self._populateSweepVariableCombo(self.scatterXVarCombo, includeTime=True)
        self._restoreData(self.scatterXVarCombo, current)

    def _rebuildInstrumentCombo(self, combo: QComboBox) -> None:
        combo.clear()
        allowTime = self._isTimeSweepSelected()
        for instId in sorted({ref.instrumentId for ref in self._availableVariables}):
            if instId == "__time__" and not allowTime:
                continue
            label = "Time" if instId == "__time__" else instId
            combo.addItem(label, userData=instId)

    def _restoreData(self, combo: QComboBox, data: Any) -> None:
        if data is None:
            return
        for i in range(combo.count()):
            if combo.itemData(i) == data:
                combo.setCurrentIndex(i)
                return

    def _rebuildInsertVariableCombo(
        self, instCombo: QComboBox, varCombo: QComboBox
    ) -> None:
        current = varCombo.currentData()
        instId = str(instCombo.currentData() or "")
        varCombo.clear()
        for ref in sorted(
            self._availableVariables, key=lambda r: (r.instrumentId, r.key)
        ):
            if ref.instrumentId != instId:
                continue
            token = tokenToRefString(ref.instrumentId, ref.key)
            label = (
                "time (sweep)"
                if (ref.instrumentId == "__time__" and ref.key == "time")
                else (ref.label or ref.key)
            )
            varCombo.addItem(label, userData=token)
        self._restoreData(varCombo, current)

    def _availableRefPairs(self) -> set[tuple[str, str]]:
        return {(ref.instrumentId, ref.key) for ref in self._availableVariables}

    def toDefinition(self) -> dict[str, Any]:
        title = self.titleLineEdit.text().strip()
        if self._renderMode == "heatmap":
            xRef = self.heatmapXVarCombo.currentData()
            yRef = self.heatmapYVarCombo.currentData()
            zExpr = self.zExprLineEdit.text().strip()
            zName = self.zNameLineEdit.text().strip() or "Z"

            xDef: dict[str, Any] = {"type": "var", "instrumentId": "", "key": ""}
            yDef: dict[str, Any] = {"type": "var", "instrumentId": "", "key": ""}
            if isinstance(xRef, tuple) and len(xRef) == 2:
                xDef = {
                    "type": "var",
                    "instrumentId": str(xRef[0]),
                    "key": str(xRef[1]),
                }
            if isinstance(yRef, tuple) and len(yRef) == 2:
                yDef = {
                    "type": "var",
                    "instrumentId": str(yRef[0]),
                    "key": str(yRef[1]),
                }

            zEntry: dict[str, Any] = {"name": zName, "type": "expr", "expr": zExpr}
            try:
                compiled = compileExpression(
                    zExpr, allowedRefs=self._availableRefPairs()
                )
                zEntry["dependencies"] = [
                    {"instrumentId": instId, "key": key}
                    for instId, key in sorted(compiled.dependencies)
                ]
            except ExpressionError:
                pass

            return {
                "title": title,
                "renderMode": "heatmap",
                "x": xDef,
                "heatmapY": yDef,
                "y": {"type": "expr", "expr": zExpr},
                "ySeries": [zEntry],
            }

        xRef = self.scatterXVarCombo.currentData()
        xDef: dict[str, Any] = {"type": "var", "instrumentId": "", "key": ""}
        if isinstance(xRef, tuple) and len(xRef) == 2:
            xDef = {"type": "var", "instrumentId": str(xRef[0]), "key": str(xRef[1])}

        ySeries = self._collectYExpressions()
        y: dict[str, Any] = {
            "type": "expr",
            "expr": ySeries[0]["expr"] if ySeries else "",
        }
        return {
            "title": title,
            "renderMode": "scatter",
            "x": xDef,
            "y": y,
            "ySeries": ySeries,
        }

    def validateDefinition(self) -> str | None:
        definition = self.toDefinition()
        available = self._availableRefPairs()
        renderMode = str(definition.get("renderMode", "scatter"))

        if renderMode == "heatmap":
            xDef = definition.get("x", {})
            yDef = definition.get("heatmapY", {})
            ySeries = definition.get("ySeries", [])
            if not isinstance(ySeries, list) or len(ySeries) != 1:
                return (
                    "2D heatmap plots require exactly one dependent variable (Z/color)."
                )
            if (
                not isinstance(xDef, dict)
                or not str(xDef.get("instrumentId", "")).strip()
                or not str(xDef.get("key", "")).strip()
            ):
                return "X variable is not selected."
            if (
                not isinstance(yDef, dict)
                or not str(yDef.get("instrumentId", "")).strip()
                or not str(yDef.get("key", "")).strip()
            ):
                return "Y variable is not selected."
            allowed = {
                (ref.instrumentId, ref.key) for ref in self._selectedSweepVariables
            }
            if (
                str(xDef.get("instrumentId", "")),
                str(xDef.get("key", "")),
            ) not in allowed:
                return (
                    "X variable must be one of the currently selected sweep variables."
                )
            if (
                str(yDef.get("instrumentId", "")),
                str(yDef.get("key", "")),
            ) not in allowed:
                return (
                    "Y variable must be one of the currently selected sweep variables."
                )
            zExpr = (
                str(ySeries[0].get("expr", "")).strip()
                if isinstance(ySeries[0], dict)
                else ""
            )
            if not zExpr:
                return "Z expression is empty."
            try:
                compileExpression(zExpr, allowedRefs=available)
            except ExpressionError as exc:
                return f"Invalid Z expression: {exc}"
            return None

        xDef = definition.get("x", {})
        if not isinstance(xDef, dict) or xDef.get("type") != "var":
            return "X variable is not selected."
        if (
            not str(xDef.get("instrumentId", "")).strip()
            or not str(xDef.get("key", "")).strip()
        ):
            return "X variable is not selected."
        allowed = {(ref.instrumentId, ref.key) for ref in self._selectedSweepVariables}
        if (str(xDef.get("instrumentId", "")), str(xDef.get("key", ""))) not in allowed:
            return "X variable must be the currently selected sweep variable."
        ySeries = definition.get("ySeries", [])
        if not isinstance(ySeries, list) or not ySeries:
            return "At least one Y expression is required."
        for idx, entry in enumerate(ySeries, start=1):
            expr = str(entry.get("expr", "")).strip() if isinstance(entry, dict) else ""
            if not expr:
                return f"Y expression {idx} is empty."
            try:
                compileExpression(expr, allowedRefs=available)
            except ExpressionError as exc:
                return f"Invalid Y expression {idx}: {exc}"
        return None

    def setDefinition(self, definition: dict[str, Any]) -> None:
        self.titleLineEdit.setText(str(definition.get("title", "")))
        if self._renderMode == "heatmap":
            xDef = definition.get("x", {})
            yDef = definition.get("heatmapY", {})
            if isinstance(xDef, dict):
                self._restoreData(
                    self.heatmapXVarCombo,
                    (str(xDef.get("instrumentId", "")), str(xDef.get("key", ""))),
                )
            if isinstance(yDef, dict):
                self._restoreData(
                    self.heatmapYVarCombo,
                    (str(yDef.get("instrumentId", "")), str(yDef.get("key", ""))),
                )
            ySeries = definition.get("ySeries", [])
            if isinstance(ySeries, list) and ySeries and isinstance(ySeries[0], dict):
                zDef = ySeries[0]
                self.zExprLineEdit.setText(str(zDef.get("expr", "")))
                self.zNameLineEdit.setText(str(zDef.get("name", "")))
            else:
                yDefLegacy = definition.get("y", {})
                if isinstance(yDefLegacy, dict):
                    self.zExprLineEdit.setText(str(yDefLegacy.get("expr", "")))
            self._onHeatmapZExprEdited()
            return

        xDef = definition.get("x", {})
        if isinstance(xDef, dict) and xDef.get("type") == "var":
            self._restoreData(
                self.scatterXVarCombo,
                (str(xDef.get("instrumentId", "")), str(xDef.get("key", ""))),
            )
        ySeries = definition.get("ySeries", [])
        if isinstance(ySeries, list) and ySeries:
            self._setYRowsFromSeries(ySeries)
        else:
            yDef = definition.get("y", {})
            if isinstance(yDef, dict) and yDef.get("type") == "expr":
                self._setYRowsFromSeries(
                    [{"type": "expr", "expr": str(yDef.get("expr", "")), "name": "Y1"}]
                )
            elif isinstance(yDef, dict) and yDef.get("type") == "var":
                self._setYRowsFromSeries(
                    [
                        {
                            "type": "expr",
                            "expr": f"{yDef.get('instrumentId')}:{yDef.get('key')}",
                            "name": "Y1",
                        }
                    ]
                )
            else:
                self._setYRowsFromSeries([{"type": "expr", "expr": "", "name": "Y1"}])
        self._onScatterYExprEdited()

    def _insertScatterYToken(self) -> None:
        token = str(self.yInsertVariableCombo.currentData() or "")
        if not token:
            return
        self._insertTokenIntoFocusedLineEdit(
            token, fallback=self._activeYExprLineEdit
        )

    def _insertHeatmapZToken(self) -> None:
        token = str(self.zInsertVariableCombo.currentData() or "")
        if not token:
            return
        self._insertTokenIntoFocusedLineEdit(token)

    def _onHeatmapZExprEdited(self) -> None:
        expr = self.zExprLineEdit.text().strip()
        if not expr:
            self.zExprStatusLabel.setText("Z expression required.")
            return
        try:
            compileExpression(expr, allowedRefs=self._availableRefPairs())
        except ExpressionError as exc:
            self.zExprStatusLabel.setText(str(exc))
            return
        self.zExprStatusLabel.setText("")
        self.changed.emit()

    def _onScatterYExprEdited(self) -> None:
        label = self.yExprStatusLabel
        expressions = self._collectYExpressions()
        if not expressions:
            label.setText("At least one Y expression is required.")
            return
        for idx, exprDef in enumerate(expressions, start=1):
            try:
                compileExpression(
                    str(exprDef.get("expr", "")), allowedRefs=self._availableRefPairs()
                )
            except ExpressionError as exc:
                label.setText(f"Y{idx}: {exc}")
                return
        label.setText("")
        self.changed.emit()

    def _collectYExpressions(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in self._yRows:
            exprWidget = row["expr"]  # type: ignore[index]
            nameWidget = row["name"]  # type: ignore[index]
            expr = str(exprWidget.text()).strip()
            if not expr:
                continue
            name = str(nameWidget.text()).strip()
            entry: dict[str, Any] = {"name": name, "type": "expr", "expr": expr}
            try:
                compiled = compileExpression(
                    expr, allowedRefs=self._availableRefPairs()
                )
                entry["dependencies"] = [
                    {"instrumentId": instId, "key": key}
                    for instId, key in sorted(compiled.dependencies)
                ]
            except ExpressionError:
                pass
            result.append(entry)
        return result

    def _addYExpressionRow(self, expr: str = "", name: str = "") -> None:
        rowWidget = QWidget(self.yRowsContainer)
        rowLayout = QHBoxLayout(rowWidget)
        rowLayout.setContentsMargins(0, 0, 0, 0)
        exprEdit = QLineEdit(rowWidget)
        exprEdit.setPlaceholderText("Expression, e.g. smu:senseCurrentA")
        exprEdit.setText(expr)
        nameEdit = QLineEdit(rowWidget)
        nameEdit.setPlaceholderText("Name (optional)")
        nameEdit.setText(name)
        removeBtn = QPushButton("Remove", rowWidget)
        rowLayout.addWidget(exprEdit, 2)
        rowLayout.addWidget(nameEdit, 1)
        rowLayout.addWidget(removeBtn)
        self.yRowsLayout.addWidget(rowWidget)
        row: dict[str, QWidget] = {
            "widget": rowWidget,
            "expr": exprEdit,
            "name": nameEdit,
            "remove": removeBtn,
        }
        self._yRows.append(row)

        exprEdit.textChanged.connect(lambda _text: self._onScatterYExprEdited())
        nameEdit.textChanged.connect(lambda _text: self.changed.emit())
        exprEdit.selectionChanged.connect(lambda: self._setActiveYExpr(exprEdit))
        exprEdit.cursorPositionChanged.connect(
            lambda _old, _new: self._setActiveYExpr(exprEdit)
        )
        nameEdit.selectionChanged.connect(
            lambda: self._rememberFocusedLineEdit(nameEdit)
        )
        nameEdit.cursorPositionChanged.connect(
            lambda _old, _new: self._rememberFocusedLineEdit(nameEdit)
        )
        exprEdit.installEventFilter(self)
        nameEdit.installEventFilter(self)
        removeBtn.clicked.connect(lambda: self._removeYExpressionRow(rowWidget))
        self._updateYRemoveButtons()
        self._setActiveYExpr(exprEdit)

    def _removeYExpressionRow(self, rowWidget: QWidget) -> None:
        if len(self._yRows) <= 1:
            return
        target = None
        for row in self._yRows:
            if row["widget"] is rowWidget:
                target = row
                break
        if target is None:
            return
        self._yRows = [row for row in self._yRows if row is not target]
        rowWidget.setParent(None)
        self._updateYRemoveButtons()
        self._onScatterYExprEdited()
        self.changed.emit()

    def _updateYRemoveButtons(self) -> None:
        canRemove = len(self._yRows) > 1
        for row in self._yRows:
            row["remove"].setEnabled(canRemove)  # type: ignore[index]

    def _setYRowsFromSeries(self, ySeries: list[Any]) -> None:
        for row in self._yRows:
            row["widget"].setParent(None)  # type: ignore[index]
        self._yRows = []
        parsedRows: list[tuple[str, str]] = []
        for idx, entry in enumerate(ySeries, start=1):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip()
            if entry.get("type") == "expr":
                expr = str(entry.get("expr", "")).strip()
                if expr:
                    parsedRows.append((expr, name))
            elif entry.get("type") == "var":
                instId = str(entry.get("instrumentId", "")).strip()
                key = str(entry.get("key", "")).strip()
                if instId and key:
                    parsedRows.append((f"{instId}:{key}", name))
        if not parsedRows:
            parsedRows = [("", "")]
        for expr, name in parsedRows:
            self._addYExpressionRow(expr=expr, name=name)

    def _setActiveYExpr(self, exprEdit: QLineEdit) -> None:
        self._activeYExprLineEdit = exprEdit
        self._rememberFocusedLineEdit(exprEdit)

    def _insertTokenIntoFocusedLineEdit(
        self, token: str, fallback: QLineEdit | None = None
    ) -> None:
        focused = QApplication.focusWidget()
        target = (
            focused if isinstance(focused, QLineEdit) else self._lastFocusedLineEdit
        )
        if not isinstance(target, QLineEdit):
            target = fallback
        if not isinstance(target, QLineEdit):
            return
        target.insert(token)
        target.setFocus()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.FocusIn and isinstance(watched, QLineEdit):
            self._rememberFocusedLineEdit(watched)
        elif event.type() == QEvent.Type.FocusIn:
            if self._isInsertButtonOrChild(watched):
                return super().eventFilter(watched, event)
            # Any non-text focus clears remembered text target.
            self._lastFocusedLineEdit = None
        elif (
            event.type() == QEvent.Type.MouseButtonPress
            and not isinstance(watched, QLineEdit)
            and not self._isInsertButtonOrChild(watched)
        ):
            # Clicking empty/background/non-text widgets should also clear target.
            self._lastFocusedLineEdit = None
        return super().eventFilter(watched, event)

    def _rememberFocusedLineEdit(self, edit: QLineEdit) -> None:
        self._lastFocusedLineEdit = edit

    def _isInsertButton(self, watched: QObject) -> bool:
        return watched in {
            self.yExprInsertButton,
            self.zExprInsertButton,
        }

    def _isInsertButtonOrChild(self, watched: QObject | None) -> bool:
        obj = watched
        while obj is not None:
            if self._isInsertButton(obj):
                return True
            obj = obj.parent()
        return False

    def _installFocusTracking(self) -> None:
        # Track focus on all child widgets so non-text focus can clear the target.
        self.installEventFilter(self)
        for widget in self.findChildren(QWidget):
            widget.installEventFilter(self)
