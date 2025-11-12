"""Loader widgets for specific beamline data types."""

from __future__ import annotations

import pathlib
from typing import List, Mapping, Optional, Sequence, TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QPushButton, QFileDialog, QWidget

from ..core.data_controller import DataVisualizationController
if TYPE_CHECKING:
    from ..core.qserver_controller import QServerController


class BaseLoaderWidget(QWidget):
    """Base class for loader widgets that emit selections for plotting."""

    selectionChanged = Signal(pathlib.Path, dict)

    def __init__(self, *, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._controller: Optional[DataVisualizationController] = None

    def set_controller(self, controller: DataVisualizationController) -> None:
        self._controller = controller
        self.initialize()

    def initialize(self) -> None:
        """Populate UI after a controller is assigned."""

    def _ensure_controller(self) -> DataVisualizationController:
        if self._controller is None:
            raise RuntimeError("Controller has not been set on loader widget")
        return self._controller


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
        self._current_folder: Optional[pathlib.Path] = None
        self._file_patterns = list(file_patterns) if file_patterns is not None else []
        self._initial_folder = initial_folder
        self._qserver_controller: Optional["QServerController"] = None
        self._worker_status: Optional[str] = None

        self._folder_button = QPushButton("XRF Folder")
        self._folder_button.clicked.connect(self._choose_folder)

        self._folder_label = QLabel("–")

        self._file_label = QLabel("XRF Files:")
        self._file_combo = QComboBox()
        self._file_combo.currentIndexChanged.connect(self._update_element_options)

        self._element_label = QLabel("Elements:")
        self._element_combo = QComboBox()
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
        initial_dir = self._resolve_dialog_directory()
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select XRF Folder",
            str(initial_dir) if initial_dir is not None else "",
        )
        if folder:
            self._set_folder(pathlib.Path(folder))

    def _resolve_dialog_directory(self) -> Optional[pathlib.Path]:
        controller = self._qserver_controller
        if controller is not None:
            path = controller.get_save_data_path()
            return pathlib.Path(path) if path else None

    def _set_folder(self, folder: pathlib.Path) -> None:
        self._current_folder = folder
        self._folder_label.setText(str(folder))
        self._ensure_controller().set_search_paths([folder])
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

    def _collect_files(self, folder: pathlib.Path) -> List[pathlib.Path]:
        files: List[pathlib.Path] = []
        for pattern in self._file_patterns:
            files.extend(sorted(folder.glob(pattern)))
        return files

    def _update_element_options(self, folder: Optional[pathlib.Path]) -> None:
        if isinstance(folder, int):
            folder = self._current_folder

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


class PtychographyLoaderWidget(BaseLoaderWidget):
    """Loader UI tailored for Ptychography reconstructions."""

    def __init__(
        self,
        *,
        roi_types: Optional[Sequence[str]] = None,
        scan_numbers: Optional[Sequence[str]] = None,
        recon_methods: Optional[Sequence[str]] = None,
        iteration_files: Optional[Sequence[str]] = None,
        initial_folder: Optional[pathlib.Path] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent=parent)
        self._current_folder: Optional[pathlib.Path] = None
        self._preset_iteration_files = list(iteration_files or [])
        self._initial_folder = initial_folder

        self._folder_button = QPushButton("Ptycho Folder")
        self._folder_button.clicked.connect(self._choose_folder)
        self._folder_label = QLabel("–")

        self._scan_label = QLabel("Scan Number:")
        self._scan_combo = QComboBox()
        if scan_numbers:
            self._scan_combo.addItems(list(scan_numbers))
        self._scan_combo.currentIndexChanged.connect(self._emit_selection)

        self._roi_label = QLabel("ROI Type (optional):")
        self._roi_combo = QComboBox()
        if roi_types:
            self._roi_combo.addItems(list(roi_types))
        self._roi_combo.currentIndexChanged.connect(self._emit_selection)

        self._recon_label = QLabel("Recon Method:")
        self._recon_combo = QComboBox()
        if recon_methods:
            self._recon_combo.addItems(list(recon_methods))
        self._recon_combo.currentIndexChanged.connect(self._emit_selection)

        self._iteration_label = QLabel("# Iterations:")
        self._iteration_combo = QComboBox()
        if self._preset_iteration_files:
            self._iteration_combo.addItems(self._preset_iteration_files)
        self._iteration_combo.currentIndexChanged.connect(self._emit_selection)

        layout = QGridLayout(self)
        layout.addWidget(self._folder_button, 0, 0)
        layout.addWidget(self._folder_label, 0, 1, 1, 3)
        layout.addWidget(self._scan_label, 1, 0)
        layout.addWidget(self._scan_combo, 1, 1, 1, 3)
        layout.addWidget(self._roi_label, 2, 0)
        layout.addWidget(self._roi_combo, 2, 1, 1, 3)
        layout.addWidget(self._recon_label, 3, 0)
        layout.addWidget(self._recon_combo, 3, 1, 1, 3)
        layout.addWidget(self._iteration_label, 4, 0)
        layout.addWidget(self._iteration_combo, 4, 1, 1, 3)
        layout.setColumnStretch(1, 1)

    def initialize(self) -> None:
        controller = self._ensure_controller()
        last_path = controller.last_path
        if last_path and last_path.exists():
            self._set_folder(last_path.parent)
            self._select_iteration(last_path)
            return
        if self._initial_folder and self._initial_folder.exists():
            self._set_folder(self._initial_folder)
            return
        paths = controller.normalized_paths
        if paths:
            self._set_folder(paths[0])

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Ptychography Folder")
        if folder:
            self._set_folder(pathlib.Path(folder))

    def _set_folder(self, folder: pathlib.Path) -> None:
        self._current_folder = folder
        self._folder_label.setText(str(folder))
        self._ensure_controller().set_search_paths([folder])
        self._refresh_iteration_files()

    def _refresh_iteration_files(self) -> None:
        self._iteration_combo.blockSignals(True)
        self._iteration_combo.clear()
        folder = self._current_folder
        if folder and folder.exists():
            for path in sorted(folder.glob("*.tif")):
                self._iteration_combo.addItem(path.name, path)
        elif self._preset_iteration_files:
            for name in self._preset_iteration_files:
                path = (folder / name) if folder else pathlib.Path(name)
                self._iteration_combo.addItem(name, path)
        self._iteration_combo.blockSignals(False)
        if self._iteration_combo.count() > 0:
            self._iteration_combo.setCurrentIndex(0)
            self._emit_selection()

    def _select_iteration(self, target: pathlib.Path) -> None:
        for index in range(self._iteration_combo.count()):
            path = self._iteration_combo.itemData(index)
            if path == target:
                self._iteration_combo.setCurrentIndex(index)
                self._emit_selection()
                break

    def _emit_selection(self) -> None:
        index = self._iteration_combo.currentIndex()
        if index < 0:
            return
        path = self._iteration_combo.itemData(index)
        if isinstance(path, pathlib.Path) and path.exists():
            metadata = {
                "scan": self._scan_combo.currentText(),
                "roi": self._roi_combo.currentText(),
                "recon_method": self._recon_combo.currentText(),
                "title": f"{self._scan_combo.currentText()} – {path.name}",
                "xlabel": "Pixel",
                "ylabel": "Value",
            }
            self.selectionChanged.emit(path, metadata)
