"""XRF loader widget."""

from __future__ import annotations

import pathlib
from typing import List, Mapping, Optional, Sequence, TYPE_CHECKING

from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton, QWidget

from .base_loader import BaseLoaderWidget, PopupListViewComboBox, RefreshingComboBox

if TYPE_CHECKING:
    from ..core.qserver_controller import QServerController


class XRFLoaderWidget(BaseLoaderWidget):
    """Loader UI tailored for XRF datasets."""

    def __init__(
        self,
        *,
        file_patterns: Optional[Sequence[str]] = None,
        initial_folder: Optional[pathlib.Path] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent=parent)
        self._file_patterns = list(file_patterns) if file_patterns is not None else []
        self._initial_folder = initial_folder
        self._worker_status: Optional[str] = None

        self._folder_button = QPushButton("XRF Folder")
        self._folder_button.clicked.connect(self._choose_folder)

        self._folder_label = QLabel("–")

        self._file_label = QLabel("XRF Files:")
        self._file_combo = self._configure_combo_box(RefreshingComboBox())
        self._file_combo.currentIndexChanged.connect(self._update_element_options)
        self._file_combo.aboutToShowPopup.connect(self._refresh_files_dropdown)

        self._element_label = QLabel("Elements:")
        self._element_combo = self._configure_combo_box(PopupListViewComboBox())
        self._element_combo.currentIndexChanged.connect(self._emit_selection)

        layout = QGridLayout(self)
        layout.addWidget(self._folder_button, 0, 0)
        layout.addWidget(self._folder_label, 0, 1)
        layout.addWidget(self._file_label, 1, 0)
        layout.addWidget(self._file_combo, 1, 1)
        layout.addWidget(self._element_label, 1, 2)
        layout.addWidget(self._element_combo, 1, 3)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

    def set_qserver_controller(self, controller: Optional["QServerController"]) -> None:
        self._qserver_controller = controller

    def handle_status_update(self, status: Mapping[str, object]) -> None:
        worker_status = status.get("worker_environment_state") if isinstance(status, Mapping) else None
        if isinstance(worker_status, str):
            self._worker_status = worker_status
        elif worker_status is None:
            self._worker_status = None

    def _choose_folder(self) -> None:
        folder = self._choose_directory(
            title="Select XRF Folder",
            initial_dir=self._resolve_dialog_directory(),
        )
        if folder:
            self._set_folder(folder)

    def _set_folder(self, folder: pathlib.Path) -> None:
        self._set_folder_state(folder, self._folder_label)
        self._update_element_options(folder)
        self._refresh_files()

    def _refresh_files(self) -> None:
        self._file_combo.blockSignals(True)
        self._file_combo.clear()
        folder = self._current_folder
        if folder and folder.exists():
            files = self._collect_files(folder)
            for path in files:
                self._file_combo.addItem(path.name, path)
        self._file_combo.blockSignals(False)
        if self._file_combo.count() > 0:
            self._file_combo.setCurrentIndex(0)
            self._update_element_options(self._current_folder)

    def _refresh_files_dropdown(self) -> None:
        folder = self._current_folder
        latest_files: List[pathlib.Path] = []
        if folder and folder.exists():
            latest_files = self._collect_files(folder)

        combo_count = self._file_combo.count()
        if combo_count == len(latest_files):
            matches = True
            for i in range(combo_count):
                item_path = self._file_combo.itemData(i)
                if not isinstance(item_path, pathlib.Path) or item_path != latest_files[i]:
                    matches = False
                    break
            if matches:
                return

        previous_selection = self._file_combo.itemData(self._file_combo.currentIndex())
        self._file_combo.blockSignals(True)
        self._file_combo.clear()
        for path in latest_files:
            self._file_combo.addItem(path.name, path)

        new_index = -1
        if isinstance(previous_selection, pathlib.Path) and previous_selection in latest_files:
            new_index = latest_files.index(previous_selection)
        elif latest_files:
            new_index = 0

        if new_index >= 0:
            self._file_combo.setCurrentIndex(new_index)
        self._file_combo.blockSignals(False)

    def _collect_files(self, folder: pathlib.Path) -> List[pathlib.Path]:
        files: List[pathlib.Path] = []
        for pattern in self._file_patterns:
            files.extend(sorted(folder.glob(pattern), key=self._numeric_sort_key))
        return files

    def _update_element_options(self, folder: Optional[pathlib.Path]) -> None:
        self._refresh_files_dropdown()

        index = self._file_combo.currentIndex()
        if index < 0:
            self._element_combo.clear()
            return

        path = self._file_combo.itemData(index)
        if not isinstance(path, pathlib.Path) or not path.exists():
            self._element_combo.clear()
            return

        controller = self._ensure_controller()

        try:
            controller.load(path, load_type="xrf")
        except Exception:
            raise RuntimeError(f"Failed to load XRF data from {path}")
        elements = controller.elms

        if not elements:
            elements = []

        previous = self._element_combo.currentText()
        self._element_combo.blockSignals(True)
        self._element_combo.clear()
        self._element_combo.addItems(elements)
        self._element_combo.blockSignals(False)
        if previous and previous in elements:
            self._element_combo.setCurrentText(previous)
            self._emit_selection()
        elif self._element_combo.count() > 0:
            self._element_combo.setCurrentIndex(0)
            self._emit_selection()

    @property
    def _current_element(self) -> str:
        return self._element_combo.currentText()

    def _emit_selection(self) -> None:
        index = self._file_combo.currentIndex()
        if index < 0:
            return
        path = self._file_combo.itemData(index)
        if isinstance(path, pathlib.Path) and path.exists():
            element = self._current_element
            metadata = {
                "element": element,
                "title": f"{path.name} – {element}" if element else path.name,
                "xlabel": "Sample-X",
                "ylabel": "Sample-Y",
            }
            self.selectionChanged.emit(path, metadata)


__all__ = ["XRFLoaderWidget"]
