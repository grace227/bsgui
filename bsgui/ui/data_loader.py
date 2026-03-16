"""Loader widgets for specific beamline data types."""

from __future__ import annotations

import pathlib
from typing import List, Mapping, Optional, Sequence, TYPE_CHECKING
import re

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QGridLayout, QLabel, QPushButton, QFileDialog, QWidget

from ..core.data_controller import DataVisualizationController
if TYPE_CHECKING:
    from ..core.qserver_controller import QServerController


class RefreshingComboBox(QComboBox):
    aboutToShowPopup = Signal()

    def showPopup(self) -> None:  # pragma: no cover - UI hook
        self.aboutToShowPopup.emit()
        super().showPopup()


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
        self._file_combo = RefreshingComboBox()
        self._file_combo.currentIndexChanged.connect(self._update_element_options)
        self._file_combo.aboutToShowPopup.connect(self._refresh_files_dropdown)

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
            files.extend(sorted(folder.glob(pattern)))
        return files

    def _update_element_options(self, folder: Optional[pathlib.Path]) -> None:
        self._refresh_files_dropdown()
        # if isinstance(folder, int):
        #     folder = self._current_folder
        
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
        ptychi_recon: Optional[bool] = True,
        qserver_controller: Optional["QServerController"] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent=parent)
        self._qserver_controller = qserver_controller
        self._current_folder: Optional[pathlib.Path] = None
        self._mda_folder: Optional[pathlib.Path] = None
        self._preset_iteration_files = list(iteration_files or [])
        self._initial_folder = initial_folder

        self._folder_button = QPushButton("Ptycho Folder")
        self._folder_button.clicked.connect(self._choose_folder)
        self._folder_label = QLabel("–")

        self._mda_button = QPushButton("MDA Folder")
        self._mda_button.clicked.connect(self._choose_mda_folder)
        self._mda_label = QLabel("–")

        self._scan_label = QLabel("Scan Number:")
        self._scan_combo = QComboBox()
        self._scan_combo.currentIndexChanged.connect(self._refresh_roi_directory)

        self._roi_label = QLabel("ROI Type (optional):")
        self._roi_combo = QComboBox()
        if roi_types:
            self._roi_combo.addItems(list(roi_types))
        self._roi_combo.currentIndexChanged.connect(self._refresh_recon_directory)

        self._recon_label = QLabel("Recon Method:")
        self._recon_combo = QComboBox()
        if recon_methods:
            self._recon_combo.addItems(list(recon_methods))
        self._recon_combo.currentIndexChanged.connect(self._refresh_iteration_files)

        self._iteration_label = QLabel("# Iterations:")
        self._iteration_combo = QComboBox()
        if self._preset_iteration_files:
            self._iteration_combo.addItems(self._preset_iteration_files)
        self._iteration_combo.currentIndexChanged.connect(self._emit_selection)


        layout = QGridLayout(self)
        layout.addWidget(self._mda_button, 0, 0)
        layout.addWidget(self._mda_label, 0, 1, 1, 3)
        layout.addWidget(self._folder_button, 1, 0)
        layout.addWidget(self._folder_label, 1, 1, 1, 3)
        layout.addWidget(self._scan_label, 2, 0)
        layout.addWidget(self._scan_combo, 2, 1, 1, 3)
        layout.addWidget(self._roi_label, 3, 0)
        layout.addWidget(self._roi_combo, 3, 1, 1, 3)
        layout.addWidget(self._recon_label, 4, 0)
        layout.addWidget(self._recon_combo, 4, 1, 1, 3)
        layout.addWidget(self._iteration_label, 5, 0)
        layout.addWidget(self._iteration_combo, 5, 1, 1, 3)
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
        initial_dir = self._resolve_dialog_directory()
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Ptychography Folder",
            str(initial_dir) if initial_dir is not None else "",
        )
        if folder:
            self._set_folder(pathlib.Path(folder))

    def _choose_mda_folder(self) -> None:
        initial_dir = self._resolve_dialog_directory()
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select MDA Folder",
            str(initial_dir) if initial_dir is not None else "",
        )
        if folder:
            self._set_mda_folder(pathlib.Path(folder))

    def _resolve_dialog_directory(self) -> Optional[pathlib.Path]:
        controller = self._qserver_controller
        if controller is not None:
            # path = controller.get_save_data_path()
            path = '/net/micdata/data1/2ide/2026-1/ptycho-Ca'
            return pathlib.Path(path) if path else None

 
    def _set_folder(self, folder: pathlib.Path) -> None:
        self._current_folder = folder
        self._folder_label.setText(str(folder))
        self._ensure_controller().set_search_paths([folder])
        self._refresh_scan_numbers()
        self._refresh_roi_directory()

    def _set_mda_folder(self, folder: pathlib.Path) -> None:
        self._mda_folder = folder
        self._mda_label.setText(str(folder))

    def _collect_folders(self, folder: pathlib.Path) -> List[pathlib.Path]:
        subfolders = [d for d in folder.iterdir() if d.is_dir()]
        return subfolders

    @staticmethod
    def _path_contains_ml_recon(path: pathlib.Path) -> bool:
        return "ML_recon" in path.parts

    def _selected_scan_path(self) -> Optional[pathlib.Path]:
        path = self._scan_combo.currentData()
        return path if isinstance(path, pathlib.Path) else None

    def _selected_scan_number(self) -> Optional[int]:
        data = self._scan_combo.currentData()
        if isinstance(data, pathlib.Path):
            scan_num_str = data.name
            scan_num = re.search(r'\d+', scan_num_str)
            if scan_num:
                return int(scan_num.group(0))
        return None

    def _get_mda_path(self) -> Optional[pathlib.Path]:
        scan_number = self._selected_scan_number()
        if scan_number is None:
            return None
        folder = self._mda_folder
        if folder is None or not folder.exists():
            return None
        return folder / f"2xfm_{scan_number:04d}.mda"

    def _selected_roi_path(self) -> Optional[pathlib.Path]:
        path = self._roi_combo.currentData()
        return path if isinstance(path, pathlib.Path) else None

    def _selected_recon_path(self) -> Optional[pathlib.Path]:
        path = self._recon_combo.currentData()
        return path if isinstance(path, pathlib.Path) else None


    def _refresh_scan_numbers(self) -> None:
        self._scan_combo.blockSignals(True)
        self._scan_combo.clear()
        self._roi_combo.clear()
        self._recon_combo.clear()
        self._iteration_combo.clear()

        folder = self._current_folder
        if folder and folder.exists():
            scan_numbers = self._collect_folders(folder)
            for path in scan_numbers:
                if path.name != 'analysis':
                    self._scan_combo.addItem(path.name, path)
        self._scan_combo.blockSignals(False)
        if self._scan_combo.count() > 0:
            self._scan_combo.setCurrentIndex(0)

    def _refresh_roi_directory(self) -> None:
        self._roi_combo.blockSignals(True)
        self._roi_combo.clear()
        self._recon_combo.clear()
        self._iteration_combo.clear()
        self._roi_combo.setEnabled(False)

        selected_scan_path = self._selected_scan_path()
        if selected_scan_path is None or not selected_scan_path.exists():
            self._roi_combo.blockSignals(False)
            return

        if self._path_contains_ml_recon(selected_scan_path):
            self.ptychirecon_dir_selected = False
            roi_folders = self._collect_folders(selected_scan_path)
            for path in roi_folders:
                self._roi_combo.addItem(path.name, path)
            self._roi_combo.setEnabled(self._roi_combo.count() > 0)
        else:
            self.ptychirecon_dir_selected = True

        self._roi_combo.blockSignals(False)
        if self._roi_combo.count() > 0:
            self._roi_combo.setCurrentIndex(0)
            self._refresh_recon_directory()
        else:
            self._refresh_recon_directory()

    def _refresh_recon_directory(self) -> None:
        self._recon_combo.blockSignals(True)
        self._recon_combo.clear()
        self._iteration_combo.clear()
        selected_scan_path = self._selected_scan_path()
        selected_roi_path = self._selected_roi_path()

        base_path = selected_roi_path if selected_roi_path is not None else selected_scan_path
        if base_path is None or not base_path.exists():
            self._recon_combo.blockSignals(False)
            return

        recon_folders = self._collect_folders(base_path)
        for path in recon_folders:
            self._recon_combo.addItem(path.name, path)

        self.ptychirecon_dir_selected = not self._path_contains_ml_recon(base_path)
        self._recon_combo.blockSignals(False)
        if self._recon_combo.count() > 0:
            self._recon_combo.setCurrentIndex(0)
            self._refresh_iteration_files()
        else:
            self._refresh_iteration_files()

    def _refresh_iteration_files(self) -> None:
        self._iteration_combo.blockSignals(True)
        self._iteration_combo.clear()

        folder = self._selected_recon_path()
        if folder is not None:
            subfolder = "object_ph" if self.ptychirecon_dir_selected else "O_phase_roi"
            folder = folder / subfolder
        else:
            folder = self._selected_roi_path()
        if folder is None:
            folder = self._selected_scan_path()

        if folder and folder.exists():
            for path in sorted(folder.glob("*.tif*")):
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
        tiff_path = self._iteration_combo.itemData(index)
        mda_path = self._get_mda_path()

        print(f"Loading data: {tiff_path}, {mda_path}")

        try:
            self._controller.load(tiff_path, load_type="ptycho", mda_path=mda_path)
        except Exception as e:
            print(f"Error loading data: {e}")
            return

        if isinstance(tiff_path, pathlib.Path) and tiff_path.exists():
            metadata = {
                "element": 'ptycho',
                "title": f"{tiff_path.name}",
                "xlabel": "Sample-X",
                "ylabel": "Sample-Y",
                'color_map': 'gray',
            }
            self.selectionChanged.emit(tiff_path, metadata)
