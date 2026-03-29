"""Viewer for Bluesky scan parameters stored in HDF5 files."""

from __future__ import annotations

import pathlib
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .base_loader import BaseLoaderWidget


class _ScanParameterLoaderWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(list, list, str, str, list)
    failed = Signal(str)

    def __init__(
        self,
        *,
        directory: pathlib.Path,
        files: Sequence[pathlib.Path],
        metadata_loader: Callable[[pathlib.Path], Mapping[str, Any]],
        parameter_key_map: Mapping[str, Sequence[str]],
    ) -> None:
        super().__init__()
        self._directory = directory
        self._files = list(files)
        self._metadata_loader = metadata_loader
        self._parameter_key_map = {
            str(header): [str(alias) for alias in aliases]
            for header, aliases in parameter_key_map.items()
        }

    def run(self) -> None:
        try:
            row_data: list[dict[str, Any]] = []
            dynamic_headers: list[str] = []
            seen_headers: set[str] = set()
            configured_headers = list(self._parameter_key_map.keys())
            skipped_files: list[str] = []

            for index, path in enumerate(self._files, start=1):
                try:
                    plan_args = self._read_plan_args(path)
                except Exception as exc:
                    skipped_files.append(f"{path.name}: {exc}")
                    self.progress.emit(index, len(self._files), path.name)
                    continue
                normalized = self._normalize_plan_args(plan_args)
                row_payload = {"File": path.name}
                row_payload.update(normalized)
                row_data.append(row_payload)
                for header in normalized.keys():
                    if header not in self._parameter_key_map and header not in seen_headers:
                        dynamic_headers.append(header)
                        seen_headers.add(header)
                self.progress.emit(index, len(self._files), path.name)

            patterns = ", ".join(sorted({f"*.{path.suffix.lstrip('.')}" for path in self._files if path.suffix})) or "*.h5"
            self.finished.emit(row_data, configured_headers, patterns, str(self._directory), skipped_files)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _read_plan_args(self, file_path: pathlib.Path) -> Mapping[str, Any]:
        loaded = self._metadata_loader(file_path)
        if not isinstance(loaded, Mapping):
            raise ValueError(f"{file_path.name} metadata loader did not return a mapping")
        return loaded

    def _normalize_plan_args(self, plan_args: Mapping[str, Any]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        consumed: set[str] = set()

        for header, aliases in self._parameter_key_map.items():
            for alias in aliases:
                if alias in plan_args:
                    normalized[header] = plan_args[alias]
                    consumed.add(alias)
                    break

        for key, value in plan_args.items():
            if key in consumed:
                continue
            normalized[str(key)] = value

        return normalized


class ScanParameterViewerWidget(BaseLoaderWidget):
    """Browse Bluesky HDF5 files and display plan arguments in a table."""

    def __init__(
        self,
        *,
        metadata_loader: Callable[[pathlib.Path], Mapping[str, Any]],
        file_patterns: Optional[Sequence[str]] = None,
        parameter_key_map: Optional[Mapping[str, object]] = None,
        initial_directory: Optional[pathlib.Path] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent=parent)
        self._metadata_loader = metadata_loader
        self._file_patterns = tuple(file_patterns or ("*.h5",))
        self._initial_directory = pathlib.Path(initial_directory) if initial_directory else None
        self._current_directory: Optional[pathlib.Path] = self._initial_directory
        self._parameter_key_map = self._normalize_parameter_key_map(parameter_key_map)
        self._loader_thread: Optional[QThread] = None
        self._loader_worker: Optional[_ScanParameterLoaderWorker] = None

        self._folder_button = QPushButton("Select Bluesky H5 Directory")
        self._folder_button.clicked.connect(self._choose_folder)

        self._folder_label = QLabel("–")
        self._status_icon = QLabel("")
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #666666;")
        self._progress_bar = QProgressBar(self)
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(True)

        self._table = QTableWidget(0, 0, self)
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        self._table.verticalHeader().setVisible(False)

        controls_layout = QGridLayout()
        controls_layout.addWidget(self._folder_button, 0, 0)
        controls_layout.addWidget(self._folder_label, 0, 1)
        controls_layout.setColumnStretch(1, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(controls_layout)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._status_icon)
        layout.addWidget(self._status_label)

        if self._initial_directory is not None:
            self._folder_label.setText(str(self._initial_directory))
            self._load_directory(self._initial_directory)

    def _choose_folder(self) -> None:
        folder = super()._choose_directory(
            title="Select Bluesky HDF5 Directory",
            initial_dir=self._resolve_dialog_directory() or self._current_directory or pathlib.Path.cwd(),
        )
        if folder:
            self._current_folder = folder
            self._folder_label.setText(str(folder))
            self._load_directory(folder)

    def _load_directory(self, directory: pathlib.Path) -> None:
        self._current_directory = directory
        files = self._collect_files(directory)
        self._cleanup_loader()
        if not files:
            self._table.clear()
            self._table.setRowCount(0)
            self._table.setColumnCount(0)
            self._progress_bar.setVisible(False)
            patterns = ", ".join(self._file_patterns) if self._file_patterns else "*.h5"
            self._set_status(
                f"No HDF5 files found in {directory} using pattern(s): {patterns}",
                icon="warning",
            )
            return

        self._progress_bar.setRange(0, len(files))
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("Loaded %v/%m files")
        self._progress_bar.setVisible(True)
        self._folder_button.setEnabled(False)
        self._set_status(f"Loading {len(files)} file(s) from {directory}", icon="loading")

        self._loader_thread = QThread(self)
        self._loader_worker = _ScanParameterLoaderWorker(
            directory=directory,
            files=files,
            metadata_loader=self._metadata_loader,
            parameter_key_map=self._parameter_key_map,
        )
        self._loader_worker.moveToThread(self._loader_thread)
        self._loader_thread.started.connect(self._loader_worker.run)
        self._loader_worker.progress.connect(self._handle_loader_progress)
        self._loader_worker.finished.connect(self._handle_loader_finished)
        self._loader_worker.failed.connect(self._handle_loader_failed)
        self._loader_worker.finished.connect(self._loader_thread.quit)
        self._loader_worker.failed.connect(self._loader_thread.quit)
        self._loader_thread.finished.connect(self._cleanup_loader)
        self._loader_thread.start()

    def _handle_loader_progress(self, current: int, total: int, file_name: str) -> None:
        self._progress_bar.setValue(current)
        directory = self._current_directory or pathlib.Path.cwd()
        self._set_status(
            f"Loading {current}/{total} file(s) from {directory}: {file_name}",
            icon="loading",
        )

    def _collect_files(self, directory: pathlib.Path) -> list[pathlib.Path]:
        files: list[pathlib.Path] = []
        for pattern in self._file_patterns:
            files.extend(directory.glob(pattern))
        
        return sorted(set(files), key=lambda path: path.name)

    def _handle_loader_finished(
        self,
        row_data: list[dict[str, Any]],
        configured_headers: list[str],
        patterns: str,
        directory: str,
        skipped_files: list[str],
    ) -> None:
        dynamic_headers: list[str] = []
        seen_headers: set[str] = set()
        for values in row_data:
            for header in values.keys():
                if header in {"File", *configured_headers} or header in seen_headers:
                    continue
                dynamic_headers.append(header)
                seen_headers.add(header)

        headers = ["File", *configured_headers, *dynamic_headers]
        self._table.setSortingEnabled(False)
        self._table.clear()
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(len(row_data))

        for row_index, values in enumerate(row_data):
            for column_index, header in enumerate(headers):
                value = values.get(header, "")
                item = QTableWidgetItem("" if value is None else str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row_index, column_index, item)

        self._table.setSortingEnabled(True)
        self._table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        self._progress_bar.setVisible(False)
        self._folder_button.setEnabled(True)
        if skipped_files:
            self._set_status(
                f"Loaded {len(row_data)} file(s) from {directory} using pattern(s): {patterns}. "
                f"Skipped {len(skipped_files)} file(s). First skipped: {skipped_files[0]}",
                icon="warning",
            )
        else:
            self._set_status(
                f"Loaded {len(row_data)} file(s) from {directory} using pattern(s): {patterns}",
                icon="success",
            )

    def _handle_loader_failed(self, message: str) -> None:
        self._progress_bar.setVisible(False)
        self._folder_button.setEnabled(True)
        self._set_status(f"Failed to load metadata: {message}", error=True, icon="warning")

    def _cleanup_loader(self) -> None:
        if self._loader_worker is not None:
            self._loader_worker.deleteLater()
            self._loader_worker = None
        if self._loader_thread is not None:
            self._loader_thread.deleteLater()
            self._loader_thread = None

    @staticmethod
    def _normalize_parameter_key_map(raw_map: Optional[Mapping[str, object]]) -> Dict[str, list[str]]:
        normalized: Dict[str, list[str]] = {}
        if not isinstance(raw_map, Mapping):
            return normalized
        for header, aliases in raw_map.items():
            if not isinstance(header, str):
                continue
            if isinstance(aliases, str):
                normalized[header] = [aliases]
            elif isinstance(aliases, Iterable) and not isinstance(aliases, (str, bytes)):
                normalized[header] = [str(alias) for alias in aliases if isinstance(alias, str)]
        return normalized

    def _set_status(self, message: str, *, error: bool = False, icon: str = "") -> None:
        self._status_label.setText(message)
        color = "#c62828" if error else "#666666"
        self._status_label.setStyleSheet(f"color: {color};")
        icon_map = {
            "loading": ("...", "#1565c0"),
            "success": ("OK", "#2e7d32"),
            "warning": ("!", "#c62828"),
        }
        icon_text, icon_color = icon_map.get(icon, ("", "#666666"))
        self._status_icon.setText(icon_text)
        self._status_icon.setStyleSheet(f"color: {icon_color}; font-weight: bold;")


__all__ = ["ScanParameterViewerWidget"]
