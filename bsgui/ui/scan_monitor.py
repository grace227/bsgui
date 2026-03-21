"""Widget for displaying scan status and progress from console output."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from ..core.scan_progress import (
    DONE_SCAN_TEXT,
    ScanTimingState,
    extract_inner_status,
    extract_outer_progress,
    extract_progress,
    extract_status,
    format_eta,
    update_scan_timing,
)
from ..core.qserver_controller import QServerController


class ScanMonitorWidget(QWidget):
    """Simple scan monitor backed by buffered Queue Server console text."""

    def __init__(
        self,
        *,
        parent: Optional[QWidget] = None,
        title: str = "Scan Monitor",
        poll_interval_ms: int = 500,
    ) -> None:
        super().__init__(parent)
        self._controller: Optional[QServerController] = None
        self._timing_state = ScanTimingState()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._title_label = QLabel(title)
        self._status_label = QLabel("Idle")
        self._status_label.setWordWrap(True)
        self._outer_status_label = QLabel("3D Scan Progress")
        self._outer_status_label.setWordWrap(True)

        self._outer_progress = QProgressBar()
        self._outer_progress.setRange(0, 100)
        self._outer_progress.setValue(0)
        self._outer_progress.setFormat("%p%")

        self._inner_status_label = QLabel("Scan Progress")
        self._inner_status_label.setWordWrap(True)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFormat("%p%")

        layout.addWidget(self._title_label)
        layout.addWidget(self._status_label)
        layout.addWidget(self._outer_status_label)
        layout.addWidget(self._outer_progress)
        layout.addWidget(self._inner_status_label)
        layout.addWidget(self._progress)

        self._timer = QTimer(self)
        self._timer.setInterval(max(100, int(poll_interval_ms)))
        self._timer.timeout.connect(self.refresh)

    def set_controller(self, controller: QServerController) -> None:
        self._controller = controller
        controller.consoleMessageReceived.connect(lambda _msg: self.refresh())
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        controller = self._controller
        if controller is None:
            self._status_label.setText("No controller")
            self._outer_status_label.setText("3D Scan Progress")
            self._outer_progress.setValue(0)
            self._inner_status_label.setText("Scan Progress")
            self._progress.setValue(0)
            return

        console_text = controller._api.console_monitor.text()
        update_scan_timing(console_text, self._timing_state)
        if DONE_SCAN_TEXT in console_text:
            status_text = DONE_SCAN_TEXT
            eta_text = format_eta(console_text, self._timing_state)
            self._status_label.setText(f"{status_text} | {eta_text}" if eta_text else status_text)
            self._inner_status_label.setText("Scan Progress")
            self._progress.setValue(0)
            if self._outer_progress.value() >= 100:
                self._outer_status_label.setText("Done scanning all angles")
                self._outer_progress.setValue(0)
                self._timing_state.reset()
                controller._api.console_monitor.clear()
            controller._api.console_monitor.clear_matching([DONE_SCAN_TEXT])
            return

        status_text = extract_status(console_text) or "Waiting for console output"
        eta_text = format_eta(console_text, self._timing_state)
        self._status_label.setText(f"{status_text} | {eta_text}" if eta_text else status_text)
        outer_status, outer_progress = extract_outer_progress(console_text)
        self._outer_status_label.setText(outer_status or "3D Scan Progress")
        self._outer_progress.setValue(outer_progress or 0)
        self._inner_status_label.setText(extract_inner_status(console_text) or "Scan Progress")
        self._progress.setValue(extract_progress(console_text) or 0)


__all__ = ["ScanMonitorWidget"]
