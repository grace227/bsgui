"""Shared loader widget infrastructure."""

from __future__ import annotations

import pathlib
import re
from typing import List, Optional, TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QLabel,
    QListView,
    QWidget,
)

from ..core.data_controller import DataVisualizationController

if TYPE_CHECKING:
    from ..core.qserver_controller import QServerController


class PopupListViewComboBox(QComboBox):
    _popup_padding = 8

    def __init__(self, *, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        popup_view = QListView(self)
        popup_view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        popup_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        popup_view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setView(popup_view)

    def showPopup(self) -> None:  # pragma: no cover - UI hook
        super().showPopup()
        self._resize_popup_height()

    def _resize_popup_height(self) -> None:
        rows = min(max(self.count(), 1), self.maxVisibleItems())
        view = self.view()
        row_height = view.sizeHintForRow(0)
        if row_height <= 0:
            row_height = view.fontMetrics().height() + 4

        frame_height = view.frameWidth() * 2
        margins = view.contentsMargins().top() + view.contentsMargins().bottom()
        scrollbar_height = (
            view.horizontalScrollBar().sizeHint().height()
            if view.horizontalScrollBar().isVisible()
            else 0
        )
        one_row_height = row_height + frame_height + margins + self._popup_padding
        popup_height = rows * row_height + frame_height + margins + scrollbar_height + self._popup_padding
        popup_height = max(one_row_height, popup_height)
        view.window().setFixedHeight(popup_height)


class RefreshingComboBox(PopupListViewComboBox):
    aboutToShowPopup = Signal()

    def showPopup(self) -> None:  # pragma: no cover - UI hook
        self.aboutToShowPopup.emit()
        super().showPopup()


class BaseLoaderWidget(QWidget):
    """Base class for loader widgets that emit selections for plotting."""

    selectionChanged = Signal(pathlib.Path, dict)
    _combo_box_max_visible_items = 30

    def __init__(self, *, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._controller: Optional[DataVisualizationController] = None
        self._current_folder: Optional[pathlib.Path] = None
        self._qserver_controller: Optional["QServerController"] = None

    def set_controller(self, controller: DataVisualizationController) -> None:
        self._controller = controller
        self.initialize()

    def initialize(self) -> None:
        """Populate UI after a controller is assigned."""

    def _ensure_controller(self) -> DataVisualizationController:
        if self._controller is None:
            raise RuntimeError("Controller has not been set on loader widget")
        return self._controller

    @staticmethod
    def _numeric_sort_key(path: pathlib.Path) -> tuple[int, str]:
        match = re.search(r"\d+", path.name)
        if match:
            return int(match.group(0)), path.name
        return -1, path.name

    def _configure_combo_box(self, combo: QComboBox) -> QComboBox:
        combo.setMaxVisibleItems(self._combo_box_max_visible_items)
        return combo

    def _resolve_dialog_directory(self) -> Optional[pathlib.Path]:
        if self._qserver_controller is not None:
            path = self._qserver_controller.get_save_data_path()
            return pathlib.Path(path) if path else None
        return self._current_folder

    def _choose_directory(
        self,
        *,
        title: str,
        initial_dir: Optional[pathlib.Path] = None,
    ) -> Optional[pathlib.Path]:
        folder = QFileDialog.getExistingDirectory(
            self,
            title,
            str(initial_dir) if initial_dir is not None else "",
        )
        return pathlib.Path(folder) if folder else None

    def _set_folder_state(self, folder: pathlib.Path, label: QLabel) -> None:
        self._current_folder = folder
        label.setText(str(folder))
        self._ensure_controller().set_search_paths([folder])

    @staticmethod
    def _collect_folders(folder: pathlib.Path) -> List[pathlib.Path]:
        return [path for path in folder.iterdir() if path.is_dir()]


__all__ = [
    "BaseLoaderWidget",
    "PopupListViewComboBox",
    "RefreshingComboBox",
]
