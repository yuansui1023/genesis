from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from genesis.ui.no_wheel_combo_box import NoWheelComboBox


class HeatmapPlotWidget(QWidget):
    def __init__(
        self,
        title: str,
        xLabel: str,
        yLabel: str,
        xBounds: tuple[float, float] | None = None,
        yBounds: tuple[float, float] | None = None,
        xPointCount: int | None = None,
        yPointCount: int | None = None,
        allowPopout: bool = True,
        parent: QWidget | None = None,
        zLabel: str = "",
    ) -> None:
        super().__init__(parent)
        self._title = str(title)
        self._xLabel = str(xLabel)
        self._yLabel = str(yLabel)
        self._zLabel = str(zLabel)
        self._xByKey: dict[str, float] = {}
        self._yByKey: dict[str, float] = {}
        self._zByKeyPair: dict[tuple[str, str], float] = {}
        self._xBounds = xBounds
        self._yBounds = yBounds
        self._gridCols: int = max(1, int(xPointCount or 1))
        self._gridRows: int = max(1, int(yPointCount or 1))
        self._allowPopout = bool(allowPopout)
        self._popupDialog: QDialog | None = None
        self._popupHeatmap: HeatmapPlotWidget | None = None

        layout = QVBoxLayout(self)
        self._controlsWidget = QWidget(self)
        controls = QHBoxLayout(self._controlsWidget)
        controls.setContentsMargins(0, 0, 0, 0)
        self.colormapCombo = NoWheelComboBox(self)
        for name in ("viridis", "plasma", "inferno", "magma", "cividis", "turbo"):
            self.colormapCombo.addItem(name, userData=name)
        controls.addWidget(self.colormapCombo)
        if self._allowPopout:
            self.popoutButton = QPushButton("Open Large View", self)
            self.popoutButton.clicked.connect(self._openPopoutWindow)
            controls.addWidget(self.popoutButton)
        controls.addStretch(1)
        layout.addWidget(self._controlsWidget)

        self.plotWidget = pg.PlotWidget(self)
        self.plotWidget.setTitle(self._title)
        self.plotWidget.setLabel("bottom", self._xLabel)
        self.plotWidget.setLabel("left", self._yLabel)
        self.plotWidget.showGrid(x=True, y=True, alpha=0.2)
        view = self.plotWidget.getViewBox()
        if self._allowPopout:
            view.setAspectLocked(True, ratio=1.0)
        else:
            view.setAspectLocked(False)
        view.enableAutoRange(x=False, y=False)
        if self._allowPopout:
            self.plotWidget.setSizePolicy(
                QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
            )
        else:
            self.plotWidget.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
        if self._allowPopout:
            plotRow = QHBoxLayout()
            plotRow.setContentsMargins(0, 0, 0, 0)
            plotRow.addStretch(1)
            plotRow.addWidget(self.plotWidget)
            plotRow.addStretch(1)
            layout.addLayout(plotRow, 1)
        else:
            layout.addWidget(self.plotWidget, 1)

        self._image = pg.ImageItem()
        self.plotWidget.addItem(self._image)
        self._bar = pg.ColorBarItem(
            values=(0.0, 1.0), colorMap=pg.colormap.get("viridis"), label=self._zLabel
        )
        self._bar.setImageItem(self._image)

        self.colormapCombo.currentIndexChanged.connect(self._onColormapChanged)
        self._resetImageGrid()
        if self._allowPopout:
            self._updateSquareViewport()

    def appendPoint(self, x: float, y: float, z: float) -> None:
        xKey = f"{float(x):.12g}"
        yKey = f"{float(y):.12g}"
        self._xByKey[xKey] = float(x)
        self._yByKey[yKey] = float(y)
        self._zByKeyPair[(xKey, yKey)] = float(z)
        self._updateImage()
        if self._popupHeatmap is not None:
            self._popupHeatmap.appendPoint(float(x), float(y), float(z))

    def clearData(self) -> None:
        self._xByKey.clear()
        self._yByKey.clear()
        self._zByKeyPair.clear()
        self._resetImageGrid()
        if self._popupHeatmap is not None:
            self._popupHeatmap.clearData()

    def _onColormapChanged(self, _idx: int) -> None:
        name = str(self.colormapCombo.currentData() or "viridis")
        cmap = pg.colormap.get(name)
        self._image.setColorMap(cmap)
        self._bar.setColorMap(cmap)
        # Force immediate redraw so color changes apply without waiting for new data.
        imageData = self._image.image
        if imageData is not None:
            levels = self._image.getLevels()
            if levels is None:
                self._image.setImage(np.asarray(imageData), autoLevels=False)
            else:
                self._image.setImage(
                    np.asarray(imageData), autoLevels=False, levels=levels
                )
        self._image.update()
        self.plotWidget.repaint()
        if self._popupHeatmap is not None:
            idx = self._popupHeatmap.colormapCombo.findData(name)
            if idx >= 0 and self._popupHeatmap.colormapCombo.currentIndex() != idx:
                self._popupHeatmap.colormapCombo.setCurrentIndex(idx)

    def _updateImage(self) -> None:
        xVals = sorted(self._xByKey.values())
        yVals = sorted(self._yByKey.values())
        if not self._zByKeyPair:
            return

        xMin, xMax, xCount = self._resolveAxisGrid(self._xBounds, xVals, self._gridCols)
        yMin, yMax, yCount = self._resolveAxisGrid(self._yBounds, yVals, self._gridRows)
        z = np.full((xCount, yCount), np.nan, dtype=np.float64)

        xLookup: dict[str, int] | None = None
        yLookup: dict[str, int] | None = None
        if self._xBounds is None:
            xLookup = {
                f"{v:.12g}": i
                for i, v in enumerate(sorted(self._xByKey.values())[:xCount])
            }
        if self._yBounds is None:
            yLookup = {
                f"{v:.12g}": i
                for i, v in enumerate(sorted(self._yByKey.values())[:yCount])
            }

        for (xKey, yKey), value in self._zByKeyPair.items():
            xVal = self._xByKey.get(xKey)
            yVal = self._yByKey.get(yKey)
            if xVal is None or yVal is None:
                continue
            if xLookup is not None:
                xi = xLookup.get(xKey)
            else:
                xi = self._valueToGridIndex(float(xVal), xMin, xMax, xCount)
            if yLookup is not None:
                yi = yLookup.get(yKey)
            else:
                yi = self._valueToGridIndex(float(yVal), yMin, yMax, yCount)
            if xi is None or yi is None:
                continue
            z[xi, yi] = value

        self._image.setImage(z, autoLevels=True)
        xStep = self._gridStep(xMin, xMax, xCount)
        yStep = self._gridStep(yMin, yMax, yCount)
        width = max(xStep, xMax - xMin)
        height = max(yStep, yMax - yMin)
        self._image.setRect(QRectF(xMin, yMin, width, height))

        view = self.plotWidget.getViewBox()
        view.setRange(
            rect=QRectF(xMin, yMin, width, height),
            padding=0.0,
        )

    def _resetImageGrid(self) -> None:
        xMin, xMax, xCount = self._resolveAxisGrid(self._xBounds, [], self._gridCols)
        yMin, yMax, yCount = self._resolveAxisGrid(self._yBounds, [], self._gridRows)
        z = np.full((xCount, yCount), np.nan, dtype=np.float64)
        self._image.setImage(z, autoLevels=False)
        xStep = self._gridStep(xMin, xMax, xCount)
        yStep = self._gridStep(yMin, yMax, yCount)
        width = max(xStep, xMax - xMin)
        height = max(yStep, yMax - yMin)
        rect = QRectF(xMin, yMin, width, height)
        self._image.setRect(rect)
        view = self.plotWidget.getViewBox()
        view.setRange(rect=rect, padding=0.0)

    def _resolveAxisGrid(
        self, bounds: tuple[float, float] | None, values: list[float], pointCount: int
    ) -> tuple[float, float, int]:
        count = max(1, int(pointCount or 1))
        if bounds is not None:
            lower = float(min(bounds[0], bounds[1]))
            upper = float(max(bounds[0], bounds[1]))
            if lower == upper:
                upper = lower + 1.0
            return lower, upper, count
        if values:
            axisVals = sorted(float(v) for v in values)
            step = self._estimateAxisStep(axisVals)
            lower = float(axisVals[0]) - (step / 2.0)
            upper = float(axisVals[-1]) + (step / 2.0)
            count = max(count, len(axisVals))
            return lower, upper, count
        return -0.5, max(0.5, float(count) - 0.5), count

    def _gridStep(self, lower: float, upper: float, count: int) -> float:
        if count <= 1:
            return max(1e-12, float(upper) - float(lower))
        return max(1e-12, (float(upper) - float(lower)) / float(count - 1))

    def _valueToGridIndex(
        self, value: float, lower: float, upper: float, count: int
    ) -> int | None:
        if count <= 0:
            return None
        if count == 1:
            return 0
        step = self._gridStep(lower, upper, count)
        raw = int(round((float(value) - float(lower)) / step))
        return max(0, min(count - 1, raw))

    def _estimateAxisStep(self, values: list[float]) -> float:
        if len(values) < 2:
            return 1.0
        diffs = [abs(float(b) - float(a)) for a, b in zip(values[:-1], values[1:])]
        positive = [d for d in diffs if d > 0.0]
        if not positive:
            return 1.0
        return float(np.median(np.asarray(positive, dtype=np.float64)))

    def resizeEvent(self, event: QResizeEvent) -> None:
        if self._allowPopout:
            self._updateSquareViewport()
        super().resizeEvent(event)

    def _updateSquareViewport(self) -> None:
        margins = self.contentsMargins()
        availableWidth = max(
            120,
            self.width() - margins.left() - margins.right() - 12,
        )
        controlsHeight = self._controlsWidget.sizeHint().height()
        availableHeight = max(
            120,
            self.height() - margins.top() - margins.bottom() - controlsHeight - 16,
        )
        side = max(120, min(availableWidth, availableHeight))
        targetWidth = side
        targetHeight = side
        if (
            self.plotWidget.width() != targetWidth
            or self.plotWidget.height() != targetHeight
        ):
            self.plotWidget.setFixedSize(targetWidth, targetHeight)

    def _openPopoutWindow(self) -> None:
        if self._popupDialog is not None and self._popupDialog.isVisible():
            self._popupDialog.raise_()
            self._popupDialog.activateWindow()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{self._title} - Large View")
        dialogLayout = QVBoxLayout(dialog)
        popupHeatmap = HeatmapPlotWidget(
            title=self._title,
            xLabel=self._xLabel,
            yLabel=self._yLabel,
            xBounds=self._xBounds,
            yBounds=self._yBounds,
            xPointCount=self._gridCols,
            yPointCount=self._gridRows,
            allowPopout=False,
            parent=dialog,
            zLabel=self._zLabel,
        )
        dialogLayout.addWidget(popupHeatmap)
        dialog.resize(980, 980)
        self._popupDialog = dialog
        self._popupHeatmap = popupHeatmap
        # Sync colormap and existing data.
        currentMap = str(self.colormapCombo.currentData() or "viridis")
        mapIdx = popupHeatmap.colormapCombo.findData(currentMap)
        if mapIdx >= 0:
            popupHeatmap.colormapCombo.setCurrentIndex(mapIdx)
        for (xKey, yKey), value in self._zByKeyPair.items():
            xVal = self._xByKey.get(xKey)
            yVal = self._yByKey.get(yKey)
            if xVal is None or yVal is None:
                continue
            popupHeatmap.appendPoint(float(xVal), float(yVal), float(value))
        dialog.finished.connect(self._onPopoutClosed)
        dialog.show()

    def _onPopoutClosed(self, _result: int) -> None:
        self._popupHeatmap = None
        self._popupDialog = None
