from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from genesis.core.job.variable_names import makeVariableNicknames
from genesis.ui.job_builder.instrument_instance_editor import InstrumentInstanceEditor
from genesis.ui.no_wheel_combo_box import NoWheelComboBox


class VariableMappingEditor(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._instruments: list[InstrumentInstanceEditor] = []
        self._selectedInstrument: InstrumentInstanceEditor | None = None
        layout = QVBoxLayout(self)
        description = QLabel(
            "Give each instrument variable a short nickname. Plots use these names "
            "in variable pickers, expressions, axes, and legends.",
            self,
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Instrument", self))
        self.instrumentCombo = NoWheelComboBox(self)
        toolbar.addWidget(self.instrumentCombo, 1)
        self.resetButton = QPushButton("Reset to Defaults", self)
        toolbar.addWidget(self.resetButton)
        layout.addLayout(toolbar)
        self.filterEdit = QLineEdit(self)
        self.filterEdit.setPlaceholderText("Filter variables or nicknames...")
        layout.addWidget(self.filterEdit)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Variable", "Role", "Nickname"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(320)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.statusLabel = QLabel(self)
        self.statusLabel.setWordWrap(True)
        layout.addWidget(self.statusLabel)
        helpLabel = QLabel(
            "Use unique names per instrument: letters (including Chinese), numbers, "
            "and underscores. Start with a letter or underscore.",
            self,
        )
        helpLabel.setWordWrap(True)
        layout.addWidget(helpLabel)
        layout.addWidget(self.table, 1)
        self.instrumentCombo.currentIndexChanged.connect(self._onInstrumentChanged)
        self.table.itemChanged.connect(self._onItemChanged)
        self.filterEdit.textChanged.connect(self._filterRows)
        self.resetButton.clicked.connect(self._resetSelected)
        self.setInstruments([])

    def setInstruments(self, instruments: list[InstrumentInstanceEditor]) -> None:
        self._instruments = list(instruments)
        selected = self._selectedInstrument
        self.instrumentCombo.blockSignals(True)
        self.instrumentCombo.clear()
        for editor in instruments:
            self.instrumentCombo.addItem(editor.getInstanceId())
        index = instruments.index(selected) if selected in instruments else 0
        self.instrumentCombo.setCurrentIndex(index if instruments else -1)
        self.instrumentCombo.blockSignals(False)
        self._onInstrumentChanged()

    def _onInstrumentChanged(self) -> None:
        index = self.instrumentCombo.currentIndex()
        editor = (
            self._instruments[index] if 0 <= index < len(self._instruments) else None
        )
        self._selectedInstrument = editor
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        if editor is not None:
            variables = editor.getVariableDefinitions()
            self.table.setRowCount(len(variables))
            for row, (key, label, role) in enumerate(variables):
                for column, text in enumerate(
                    (label, role, editor.variableNicknames[key])
                ):
                    item = QTableWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, key)
                    item.setToolTip(key)
                    if column < 2:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(row, column, item)
        self.table.blockSignals(False)
        self.resetButton.setEnabled(editor is not None)
        self.filterEdit.setEnabled(editor is not None)
        self.statusLabel.setStyleSheet("")
        self.statusLabel.setText(
            f"{self.table.rowCount()} variables. Double-click a nickname to edit."
            if editor is not None
            else "Add an instrument in Instruments to name its variables."
        )
        self._filterRows()

    def _onItemChanged(self, item: QTableWidgetItem) -> None:
        editor = self._selectedInstrument
        if editor is None or item.column() != 2:
            return
        key = str(item.data(Qt.ItemDataRole.UserRole))
        nickname = item.text().strip()
        error = ""
        if not nickname.isidentifier():
            error = "Use letters, numbers, and underscores; start with a letter or underscore."
        elif any(
            other != key and name.casefold() == nickname.casefold()
            for other, name in editor.variableNicknames.items()
        ):
            error = (
                "That nickname is already used by another variable in this instrument."
            )
        elif nickname in editor.variableNicknames and nickname != key:
            error = "That name is another variable's original key. Choose a different nickname."
        self.table.blockSignals(True)
        item.setText(editor.variableNicknames[key] if error else nickname)
        self.table.blockSignals(False)
        self.statusLabel.setStyleSheet("color: #b65f00;" if error else "")
        self.statusLabel.setText(error or "Nickname updated. Plots use the new name.")
        if not error:
            editor.variableNicknames[key] = nickname
            self.changed.emit()
            self._filterRows()

    def _resetSelected(self) -> None:
        editor = self._selectedInstrument
        if editor is None:
            return
        editor.variableNicknames = makeVariableNicknames(
            [(key, label) for key, label, _role in editor.getVariableDefinitions()]
        )
        self._onInstrumentChanged()
        self.changed.emit()

    def _filterRows(self) -> None:
        query = self.filterEdit.text().strip().casefold()
        for row in range(self.table.rowCount()):
            items = [self.table.item(row, column) for column in range(3)]
            text = " ".join(
                item.text() + " " + item.toolTip() for item in items if item is not None
            )
            self.table.setRowHidden(row, query not in text.casefold())
