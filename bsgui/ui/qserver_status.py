"""Standalone widget showing QServer connection/status information."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping, Optional, Tuple

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from .status_bus import emit_status

if TYPE_CHECKING:
    from ..core.qserver_controller import QServerController


class QueueServerStatusWidget(QWidget):
    """Simple status panel with connect button and server indicators."""

    connectRequested = Signal()
    clearPlansRequested = Signal(str)

    def __init__(
        self,
        *,
        parent: Optional[QWidget] = None,
        indicators: Optional[Mapping[str, Mapping[str, str]]] = None,
    ) -> None:
        super().__init__(parent)

        self._labels: Dict[str, QLabel] = {}
        self._default_labels: Dict[str, str] = {}
        self._controller: Optional["QServerController"] = None
        self._pending_threads: set[threading.Thread] = set()

        self._tabs = QTabWidget(self)
        self._tabs.setTabBarAutoHide(False)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(self._tabs)

        status_panel = self._build_qserver_status_panel(indicators)
        self._tabs.addTab(status_panel, "Queue Server")

        self.set_queue_status(connected=False, queue_status="Unknown", run_engine_status="Unknown")

    # Public API -----------------------------------------------------

    def set_queue_status(
        self,
        *,
        connected: Optional[bool] = None,
        queue_status: Optional[str] = None,
        run_engine_status: Optional[str] = None,
    ) -> None:
        updates: Dict[str, Any] = {}
        if connected is not None:
            updates["connected"] = connected
        if queue_status is not None:
            updates["queue_state"] = queue_status
        if run_engine_status is not None:
            updates["re_state"] = run_engine_status
        if updates:
            self.update_status(updates)

    def update_status(self, status: Mapping[str, Any]) -> None:
        if "connected" in status:
            self._apply_connected_state(status.get("connected"))
            self._connect_button.setEnabled(not status.get("connected"))
            # emit_status(f"QServer connected: {status.get('connected')}")

        for key, value in status.items():
            if key == "connected":
                continue
            label = self._labels.get(key)
            if label is None:
                continue
            default_text = self._default_labels.get(key, "Unknown")
            text = default_text if value is None else self._format_value(value)
            label.setText(text)

        worker_status = status.get("worker_environment_state")
        self._apply_worker_environment_state(worker_status)
        if worker_status == "closed" or worker_status is None:
            self._start_re_button.setEnabled(True)
            self._stop_re_button.setEnabled(False)
            self.clearPlansRequested.emit(worker_status or "")
            emit_status("RE is closed")
        elif worker_status == "initializing":
            self._start_re_button.setEnabled(False)
            self._stop_re_button.setEnabled(False)
            emit_status("RE is initializing")
        elif worker_status == "idle":
            self._start_re_button.setEnabled(False)
            self.clearPlansRequested.emit(worker_status or "")
            # emit_status("RE is idle")
            self._stop_re_button.setEnabled(True)

    def _apply_connected_state(self, value: Optional[Any]) -> None:
        label = self._labels.get("connected")
        if label is None:
            return
        if isinstance(value, bool):
            label.setText("Connected" if value else "Disconnected")
            color = "#2e7d32" if value else "#c62828"
            label.setStyleSheet(f"color: {color}; font-weight: bold;")
        elif value is None:
            label.setText(self._default_labels.get("connected", "Unknown"))
            label.setStyleSheet("")
        else:
            label.setText(self._format_value(value))
            label.setStyleSheet("")

    def _apply_worker_environment_state(self, value: Optional[Any]) -> None:
        label = self._labels.get("worker_environment_state")
        if label is None:
            return

        text = self._format_value(value)
        normalized = text.lower() if isinstance(text, str) else str(text).lower()
        if normalized in {"closed", "unknown", "none", "false", ""}:
            color = "#c62828"
        else:
            color = "#2e7d32"

        label.setText(text)
        label.setStyleSheet(f"color: {color}; font-weight: bold;")


    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if value is None:
            return "Unknown"
        return str(value)

    @staticmethod
    def _build_indicator_config(
        overrides: Optional[Mapping[str, Mapping[str, str]]],
    ) -> Iterable[Tuple[str, Dict[str, str]]]:
        entries: list[Tuple[str, Dict[str, str]]] = []
        seen = set()

        if overrides:
            for key, config in overrides.items():
                if not isinstance(config, Mapping):
                    continue
                title = config.get("title", key)
                label_value = config.get("label")
                if label_value is None:
                    label_value = "Unknown"
                seen.add(key)
                entries.append((key, {"title": str(title), "label": str(label_value)}))

        if "connected" not in seen:
            entries.insert(0, ("connected", {"title": "QServer Connected:", "label": "Disconnected"}))

        return entries

    def _build_qserver_status_panel(
        self,
        indicators: Optional[Mapping[str, Mapping[str, str]]],
    ) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        self._connect_button = QPushButton("Connect to QServer")
        self._connect_button.setFixedWidth(180)
        self._connect_button.clicked.connect(self.connectRequested.emit)

        self._start_re_button = QPushButton("Start RE")
        self._start_re_button.setFixedWidth(140)
        self._start_re_button.clicked.connect(self._handle_start_re_clicked)
        self._start_re_button.setEnabled(False)

        self._stop_re_button = QPushButton("Stop RE")
        self._stop_re_button.setFixedWidth(140)
        self._stop_re_button.clicked.connect(self._handle_stop_re_clicked)
        self._stop_re_button.setEnabled(False)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        button_row.addWidget(self._connect_button)
        button_row.addWidget(self._start_re_button)
        button_row.addWidget(self._stop_re_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self._list, 1)

        for key, config in self._build_indicator_config(indicators):
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)

            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(4, 2, 4, 2)
            row_layout.setSpacing(8)

            title = QLabel(config["title"])
            title.setMinimumWidth(140)
            row_layout.addWidget(title)

            label = QLabel(config["label"])
            label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            row_layout.addWidget(label, 1)

            self._labels[key] = label
            self._default_labels[key] = config["label"]

            item.setSizeHint(row_widget.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, row_widget)

        return widget

    def set_controller(self, controller: "QServerController") -> None:
        self._controller = controller
        self._start_re_button.setEnabled(False)
        self._stop_re_button.setEnabled(False)

    def _handle_stop_re_clicked(self) -> None:
        controller = self._controller
        if controller is None:
            print("Failed to stop RE. QServerController is None.")
            return
        self._stop_re_button.setEnabled(False)
        controller.stop_re()
        self._start_re_button.setEnabled(True)

    def _handle_start_re_clicked(self) -> None:
        controller = self._controller
        if controller is None:
            print("Failed to start RE. QServerController is None.")
            return
        self._start_re_button.setEnabled(False)
        controller.start_re()

