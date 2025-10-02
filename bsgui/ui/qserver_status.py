"""Standalone widget showing QServer connection/status information."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget


class QueueServerStatusWidget(QWidget):
    """Simple status panel with connect button and server indicators."""

    connectRequested = Signal()

    def __init__(self, *, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)

        self._tabs = QTabWidget(self)
        self._tabs.setTabBarAutoHide(True)
        outer.addWidget(self._tabs)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._connect_button = QPushButton("Connect to QServer")
        self._connect_button.setFixedWidth(180)
        self._connect_button.clicked.connect(self.connectRequested.emit)
        layout.addWidget(self._connect_button, alignment=Qt.AlignmentFlag.AlignLeft)

        self._labels = {
            "connected": QLabel("Disconnected"),
            "queue": QLabel("Unknown"),
            "run_engine": QLabel("Unknown"),
        }

        titles = {
            "connected": "QServer Connected:",
            "queue": "Queue Status:",
            "run_engine": "RunEngine Status:",
        }

        for key, label in self._labels.items():
            row = QHBoxLayout()
            title = QLabel(titles[key])
            title.setMinimumWidth(140)
            row.addWidget(title)
            row.addWidget(label, 1)
            layout.addLayout(row)

        layout.addStretch(1)

        self._tabs.addTab(page, "")
        self._tabs.tabBar().hide()

        self.set_queue_status(connected=False, queue_status="Unknown", run_engine_status="Unknown")

    # Public API -----------------------------------------------------

    def set_queue_status(
        self,
        *,
        connected: Optional[bool] = None,
        queue_status: Optional[str] = None,
        run_engine_status: Optional[str] = None,
    ) -> None:
        if connected is not None:
            label = self._labels["connected"]
            label.setText("Connected" if connected else "Disconnected")
            color = "#2e7d32" if connected else "#c62828"
            label.setStyleSheet(f"color: {color}; font-weight: bold;")

        if queue_status is not None:
            self._labels["queue"].setText(queue_status)

        if run_engine_status is not None:
            self._labels["run_engine"].setText(run_engine_status)

    def set_connect_enabled(self, enabled: bool) -> None:
        self._connect_button.setEnabled(enabled)
