"""Widget for displaying scan status and progress from console output."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from ..core.scan_progress import (
    DONE_SCAN_TEXT,
    ScanTimingState,
    extract_inner_status,
    extract_outer_progress,
    extract_progress,
    extract_scan_remaining_seconds,
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
        outer_progress_mode: str = "angles",
    ) -> None:
        super().__init__(parent)
        self._controller: Optional[QServerController] = None
        self._timing_state = ScanTimingState()
        self._outer_progress_mode = str(outer_progress_mode or "angles").lower()
        self._queue_completed = 0
        self._queue_total = 0
        self._queue_pending_seconds: Optional[float] = None

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

        if self._outer_progress_mode == "disabled":
            self._outer_status_label.setVisible(False)
            self._outer_progress.setVisible(False)

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

    def set_queue_progress(
        self,
        completed: int,
        total: int,
        pending_seconds: Optional[float] = None,
    ) -> None:
        """Update the outer bar when it is configured for queue progress."""
        self._queue_completed = max(0, int(completed))
        self._queue_total = max(0, int(total))
        self._queue_pending_seconds = (
            max(0.0, float(pending_seconds))
            if pending_seconds is not None
            else None
        )
        if self._outer_progress_mode != "queue":
            return
        self._refresh_queue_progress_display()

    def _refresh_queue_progress_display(self, console_text: str = "") -> None:
        progress = int((self._queue_completed / self._queue_total) * 100) if self._queue_total else 0
        label = f"Queue Progress ({self._queue_completed}/{self._queue_total} items)"
        pending_seconds = self._queue_pending_seconds or 0.0
        scan_remaining = extract_scan_remaining_seconds(console_text)
        remaining_seconds = pending_seconds + (scan_remaining or 0.0)
        if self._queue_total and (
            self._queue_pending_seconds is not None or scan_remaining is not None
        ):
            finish_time = datetime.now() + timedelta(seconds=remaining_seconds)
            label += f" | Estimated finish: {finish_time:%Y-%m-%d %H:%M:%S}"
        self._outer_status_label.setText(label)
        self._outer_progress.setValue(max(0, min(100, progress)))

    def set_controller(self, controller: QServerController) -> None:
        self._controller = controller
        # Refresh through the Qt timer only.  The console receiver runs in a
        # background thread, so updating widgets directly from its signal can
        # interrupt other GUI controls such as Scan Setup combo boxes.
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

        if self._outer_progress_mode == "queue":
            self._refresh_queue_progress_display(controller._api.console_monitor.text())

        console_text = controller._api.console_monitor.text()
        update_scan_timing(console_text, self._timing_state)
        if DONE_SCAN_TEXT in console_text:
            status_text = DONE_SCAN_TEXT
            eta_text = format_eta(console_text, self._timing_state)
            self._status_label.setText(f"{status_text} | {eta_text}" if eta_text else status_text)
            self._inner_status_label.setText("Scan Progress")
            self._progress.setValue(0)
            if self._outer_progress_mode == "angles" and self._outer_progress.value() >= 100:
                self._outer_status_label.setText("Done scanning all angles")
                self._outer_progress.setValue(0)
                self._timing_state.reset()
                controller._api.console_monitor.clear()
            controller._api.console_monitor.clear_matching([DONE_SCAN_TEXT])
            return

        status_text = extract_status(console_text) or "Waiting for console output"
        eta_text = format_eta(console_text, self._timing_state)
        self._status_label.setText(f"{status_text} | {eta_text}" if eta_text else status_text)
        if self._outer_progress_mode == "angles":
            outer_status, outer_progress = extract_outer_progress(console_text)
            self._outer_status_label.setText(outer_status or "3D Scan Progress")
            self._outer_progress.setValue(outer_progress or 0)
        self._inner_status_label.setText(extract_inner_status(console_text) or "Scan Progress")
        self._progress.setValue(extract_progress(console_text) or 0)


__all__ = ["ScanMonitorWidget"]
