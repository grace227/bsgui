"""Data visualization canvas and composite viewer layouts."""

from __future__ import annotations

import pathlib
from typing import Any, Mapping, Optional, Sequence, Tuple

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QVBoxLayout, QWidget

from ..core.data_controller import DataVisualizationController
from .data_loader import BaseLoaderWidget
from .plot_canvas import PlotCanvasWidget
from .canvas_toolbar import CustomToolbar

LoaderDefinition = Tuple[BaseLoaderWidget, DataVisualizationController]

class DataVisualizationWidget(QWidget):
    """Light-weight widget exposing only the shared plotting canvas."""

    datasetChanged = Signal(dict)
    canvasPointSelected = Signal(dict)
    cursorMoved = Signal(dict)
    roiDrawn = Signal(dict)

    def __init__(self, *, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._canvas = PlotCanvasWidget(parent=self)
        self._last_payload: Optional[dict[str, Any]] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas)

        self.ax = self._canvas.axes
        self.line = None
        self._toolbar = CustomToolbar(self._canvas.canvas, self)  # type: ignore[arg-type]
        layout.insertWidget(0, self._toolbar)
        self._toolbar.roiDrawn.connect(self.roiDrawn.emit)

    @property
    def plot_canvas(self) -> PlotCanvasWidget:
        return self._canvas

    @property
    def last_payload(self) -> Optional[dict[str, Any]]:
        return self._last_payload

    def show_dataset(
        self,
        controller: DataVisualizationController,
        *,
        metadata: Optional[Mapping[str, Any]] = None,
        source_path: Optional[pathlib.Path] = None,
    ) -> None:

        try:
            xval = controller.x_val
            yval = controller.y_val
            sel_elm = metadata.get("element")
            zval = controller.elms_data[controller.elms.index(sel_elm)]
        except Exception:
            self.show_message("Failed to plot dataset")
            return

        payload = {
            "xval": xval,
            "yval": yval,
            "zval": zval,
            "metadata": dict(metadata) if metadata is not None else {},
            "path": source_path,
        }
        self._last_payload = payload

        title = payload["metadata"].get("title") if payload["metadata"] else None
        xlabel = payload["metadata"].get("xlabel", "X")
        ylabel = payload["metadata"].get("ylabel", "Y")

        if any([xval is None, yval is None, zval is None]):
            self.show_message("Dataset missing 'x' and 'y'")
        else:
            if not title and source_path is not None:
                title = source_path.name
            elif not title:
                title = "Dataset"
            self._canvas.imshow(xval, yval, zval, title=title, xlabel=xlabel, ylabel=ylabel)
            self._reset_toolbar()

        self.datasetChanged.emit(payload)

    def show_message(self, message: str) -> None:
        self._last_payload = None
        self._canvas.show_message(message)

    def _reset_toolbar(self) -> None:
        self._toolbar.drawRectangleAction.setChecked(False)
        self._toolbar.removeRectangleAction.setChecked(False)
        self._toolbar.is_drawing = False
        self._toolbar.is_removing = False
        self._toolbar.rectangles = []
        self._toolbar.rectangle_labels = []
        self._toolbar.lines = []
        self._toolbar.active_rectangle = None
        self._toolbar.active_line = None

    # def _handle_canvas_click(self, event) -> None:
    #     if event.xdata is None or event.ydata is None:
    #         return
    #     payload = self._last_payload or {}
    #     info = {
    #         "x": event.xdata,
    #         "y": event.ydata,
    #         "button": getattr(event, "button", None),
    #         "payload": payload,
    #     }
    #     self.canvasPointSelected.emit(info)

    # def _handle_mouse_move(self, event) -> None:
    #     if event.xdata is None or event.ydata is None:
    #         return
    #     payload = self._last_payload or {}
    #     info = {
    #         "x": event.xdata,
    #         "y": event.ydata,
    #         "payload": payload,
    #     }
    #     self.cursorMoved.emit(info)


class DataViewerPane(QWidget):
    """Composite widget arranging loader widgets around a shared canvas."""

    datasetChanged = Signal(dict)
    canvasPointSelected = Signal(dict)
    cursorMoved = Signal(dict)
    roiDrawn = Signal(dict)

    def __init__(
        self,
        loaders: Sequence[LoaderDefinition],
        *,
        extra_widgets: Optional[Sequence[Tuple[QWidget, str]]] = None,
        layout_config: Optional[dict] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        if not loaders:
            raise ValueError("DataViewerPane requires at least one loader definition")

        self._loader_entries: list[LoaderDefinition] = list(loaders)
        self._extra_widgets = list(extra_widgets or [])
        self._layout_config = layout_config if isinstance(layout_config, dict) else None

        self._viewer = DataVisualizationWidget(parent=self)
        self._loader_panel = QWidget(self)
        panel_layout = QVBoxLayout(self._loader_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(8)

        for loader_widget, controller in self._loader_entries:
            panel_layout.addWidget(loader_widget)
            loader_widget.selectionChanged.connect(
                lambda path, metadata, ctrl=controller: self._handle_selection(ctrl, path, metadata)
            )
            loader_widget.set_controller(controller)

        self._viewer.datasetChanged.connect(self.datasetChanged.emit)
        self._viewer.canvasPointSelected.connect(self.canvasPointSelected.emit)
        self._viewer.cursorMoved.connect(self.cursorMoved.emit)
        self._viewer.roiDrawn.connect(self.roiDrawn.emit)

        self._auxiliary_widgets: dict[str, QWidget] = {
            "loader_panel": self._loader_panel,
            "canvas": self._viewer,
        }
        for widget, key in self._extra_widgets:
            self._auxiliary_widgets[key] = widget

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._apply_layout(layout)

        self._viewer.show_message("No file selected")

    @property
    def visualization(self) -> DataVisualizationWidget:
        return self._viewer

    def _apply_layout(self, layout: QGridLayout) -> None:
        components = dict(self._auxiliary_widgets)

        config = self._layout_config
        if config:
            component_cfg = config.get("components", {})
            if all(isinstance(component_cfg.get(name), dict) for name in component_cfg):
                for name, widget in components.items():
                    slot = component_cfg.get(name, {})
                    try:
                        row = int(slot.get("row", 0))
                        column = int(slot.get("column", 0))
                        row_span = int(slot.get("row_span", 1))
                        column_span = int(slot.get("column_span", 1))
                    except (TypeError, ValueError):
                        row, column, row_span, column_span = 0, 0, 1, 1
                    layout.addWidget(widget, row, column, row_span, column_span)

                self._apply_stretch(layout, config)
                return

        layout.addWidget(self._loader_panel, 0, 0, 1, 2)
        layout.addWidget(self._viewer, 1, 0, 1, 2)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(0, 0)
        layout.setRowStretch(1, 1)

    @staticmethod
    def _apply_stretch(layout: QGridLayout, config: dict) -> None:
        row_stretch = config.get("row_stretch", {})
        if isinstance(row_stretch, dict):
            for row_key, stretch in row_stretch.items():
                try:
                    layout.setRowStretch(int(row_key), int(stretch))
                except (TypeError, ValueError):
                    continue

        column_stretch = config.get("column_stretch", {})
        if isinstance(column_stretch, dict):
            for col_key, stretch in column_stretch.items():
                try:
                    layout.setColumnStretch(int(col_key), int(stretch))
                except (TypeError, ValueError):
                    continue

    def _handle_selection(
        self,
        controller: DataVisualizationController,
        path: pathlib.Path,
        metadata: Mapping[str, Any],
    ) -> None:
        if not path or not path.exists():
            self._viewer.show_message("Selected file not found")
            return

        if controller._cached_data is None:
            self._viewer.show_message(f"Failed to load {path.name}")
            return

        self._viewer.show_dataset(controller, metadata=metadata, source_path=path)
