"""Ptychography loader widget."""

from __future__ import annotations

import pathlib
import re
from typing import Optional, Sequence, TYPE_CHECKING

from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton, QWidget

from .base_loader import BaseLoaderWidget, PopupListViewComboBox, RefreshingComboBox

if TYPE_CHECKING:
    from ..core.qserver_controller import QServerController


class PtychographyLoaderWidget(BaseLoaderWidget):
    """Loader UI tailored for Ptychography reconstructions."""

    def __init__(
        self,
        *,
        roi_types: Optional[Sequence[str]] = None,
        scan_numbers: Optional[Sequence[str]] = None,
        recon_methods: Optional[Sequence[str]] = None,
        iteration_files: Optional[Sequence[str]] = None,
        mda_file_patterns: Optional[Sequence[str]] = None,
        initial_folder: Optional[pathlib.Path] = None,
        ptychi_recon: Optional[bool] = True,
        qserver_controller: Optional["QServerController"] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent=parent)
        self._qserver_controller = qserver_controller
        self._mda_folder: Optional[pathlib.Path] = None
        self._mda_file_patterns = list(mda_file_patterns or ["2xfm_{scan_number:04d}.mda"])
        self._preset_iteration_files = list(iteration_files or [])
        self._initial_folder = initial_folder

        self._folder_button = QPushButton("Ptycho Folder")
        self._folder_button.clicked.connect(self._choose_folder)
        self._folder_label = QLabel("–")

        self._mda_button = QPushButton("MDA Folder")
        self._mda_button.clicked.connect(self._choose_mda_folder)
        self._mda_label = QLabel("–")

        self._scan_label = QLabel("Scan Number:")
        self._scan_combo = self._configure_combo_box(RefreshingComboBox())
        self._scan_combo.currentIndexChanged.connect(self._refresh_roi_directory)
        self._scan_combo.aboutToShowPopup.connect(self._refresh_scan_dropdown)

        self._roi_label = QLabel("ROI Type (optional):")
        self._roi_combo = self._configure_combo_box(PopupListViewComboBox())
        if roi_types:
            self._roi_combo.addItems(list(roi_types))
        self._roi_combo.currentIndexChanged.connect(self._refresh_recon_directory)

        self._recon_label = QLabel("Recon Method:")
        self._recon_combo = self._configure_combo_box(PopupListViewComboBox())
        if recon_methods:
            self._recon_combo.addItems(list(recon_methods))
        self._recon_combo.currentIndexChanged.connect(self._refresh_iteration_files)

        self._iteration_label = QLabel("# Iterations:")
        self._iteration_combo = self._configure_combo_box(PopupListViewComboBox())
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
        folder = self._choose_directory(
            title="Select Ptychography Folder",
            initial_dir=self._resolve_dialog_directory(),
        )
        if folder:
            self._set_folder(folder)

    def _choose_mda_folder(self) -> None:
        folder = self._choose_directory(
            title="Select MDA Folder",
            initial_dir=self._resolve_dialog_directory(),
        )
        if folder:
            self._set_mda_folder(folder)

    def _set_folder(self, folder: pathlib.Path) -> None:
        self._set_folder_state(folder, self._folder_label)
        self._refresh_scan_numbers()
        self._refresh_roi_directory()

    def _set_mda_folder(self, folder: pathlib.Path) -> None:
        self._mda_folder = folder
        self._mda_label.setText(str(folder))

    @staticmethod
    def _path_contains_ml_recon(path: pathlib.Path) -> bool:
        return "ML_recon" in path.parts

    def _selected_scan_path(self) -> Optional[pathlib.Path]:
        path = self._scan_combo.currentData()
        return path if isinstance(path, pathlib.Path) else None

    def _selected_scan_number(self) -> Optional[int]:
        data = self._scan_combo.currentData()
        if isinstance(data, pathlib.Path):
            scan_num = re.search(r"\d+", data.name)
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

        for pattern in self._mda_file_patterns:
            candidate = folder / pattern.format(scan_number=scan_number)
            if candidate.exists():
                return candidate

        return folder / self._mda_file_patterns[0].format(scan_number=scan_number)

    def _selected_roi_path(self) -> Optional[pathlib.Path]:
        path = self._roi_combo.currentData()
        return path if isinstance(path, pathlib.Path) else None

    def _selected_recon_path(self) -> Optional[pathlib.Path]:
        path = self._recon_combo.currentData()
        return path if isinstance(path, pathlib.Path) else None

    def _collect_scan_paths(self, folder: pathlib.Path) -> list[pathlib.Path]:
        return sorted(
            [path for path in self._collect_folders(folder) if path.name != "analysis"],
            key=self._numeric_sort_key,
        )

    def _refresh_scan_numbers(self) -> None:
        self._scan_combo.blockSignals(True)
        self._scan_combo.clear()
        self._roi_combo.clear()
        self._recon_combo.clear()
        self._iteration_combo.clear()

        folder = self._current_folder
        if folder and folder.exists():
            for path in self._collect_scan_paths(folder):
                self._scan_combo.addItem(path.name, path)
        self._scan_combo.blockSignals(False)
        if self._scan_combo.count() > 0:
            self._scan_combo.setCurrentIndex(0)

    def _refresh_scan_dropdown(self) -> None:
        folder = self._current_folder
        latest_scans: list[pathlib.Path] = []
        if folder and folder.exists():
            latest_scans = self._collect_scan_paths(folder)

        combo_count = self._scan_combo.count()
        if combo_count == len(latest_scans):
            matches = True
            for i in range(combo_count):
                item_path = self._scan_combo.itemData(i)
                if not isinstance(item_path, pathlib.Path) or item_path != latest_scans[i]:
                    matches = False
                    break
            if matches:
                return

        previous_selection = self._scan_combo.itemData(self._scan_combo.currentIndex())
        self._scan_combo.blockSignals(True)
        self._scan_combo.clear()
        for path in latest_scans:
            self._scan_combo.addItem(path.name, path)

        new_index = -1
        if isinstance(previous_selection, pathlib.Path) and previous_selection in latest_scans:
            new_index = latest_scans.index(previous_selection)
        elif latest_scans:
            new_index = 0

        if new_index >= 0:
            self._scan_combo.setCurrentIndex(new_index)
        self._scan_combo.blockSignals(False)

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
            for path in sorted(folder.glob("*.tif*"), key=self._numeric_sort_key):
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
                "element": "ptycho",
                "title": f"{tiff_path.name}",
                "xlabel": "Sample-X",
                "ylabel": "Sample-Y",
                "color_map": "Greys_r",
            }
            self.selectionChanged.emit(tiff_path, metadata)


__all__ = ["PtychographyLoaderWidget"]
