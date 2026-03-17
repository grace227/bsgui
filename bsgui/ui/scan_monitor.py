"""Widget for displaying scan status and progress from console output."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from ..core.qserver_controller import QServerController


class ScanMonitorWidget(QWidget):
    """Simple scan monitor backed by buffered Queue Server console text."""

    _outer_progress_pattern = re.compile(
        r"Preparing stage to run .*? at ([^,]+),\s*(\d+)\s+of\s+(\d+)\s+angles"
    )
    _scan_progress_pattern = re.compile(r"Scan_progress:\s*(\d{1,3}(?:\.\d+)?)%")
    _percent_pattern = re.compile(r"(\d{1,3})(?:\.\d+)?\s*%")
    _fraction_pattern = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")
    _status_prefixes = (
        "Item updated:",
        "Item added:",
        "Removing",
        "Start executing scan",
        "Done executing scan",
        "Filename:",
    )
    _done_scan_text = "Done executing scan"
    _start_scan_text = "Start executing scan"

    def __init__(
        self,
        *,
        parent: Optional[QWidget] = None,
        title: str = "Scan Monitor",
        poll_interval_ms: int = 500,
    ) -> None:
        super().__init__(parent)
        self._controller: Optional[QServerController] = None
        self._current_2d_start_time: Optional[float] = None
        self._completed_2d_durations: list[float] = []
        self._last_start_count = 0
        self._last_done_count = 0

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
        self._update_scan_timing(console_text)
        if self._done_scan_text in console_text:
            status_text = self._done_scan_text
            eta_text = self._format_eta(console_text)
            self._status_label.setText(f"{status_text} | {eta_text}" if eta_text else status_text)
            self._inner_status_label.setText("Scan Progress")
            self._progress.setValue(0)
            if self._outer_progress.value() >= 100:
                self._outer_status_label.setText("Done scanning all angles")
                self._outer_progress.setValue(0)
                self._completed_2d_durations.clear()
                self._current_2d_start_time = None
                self._last_start_count = 0
                self._last_done_count = 0
                controller._api.console_monitor.clear()
            controller._api.console_monitor.clear_matching([self._done_scan_text])
            return

        status_text = self._extract_status(console_text) or "Waiting for console output"
        eta_text = self._format_eta(console_text)
        self._status_label.setText(f"{status_text} | {eta_text}" if eta_text else status_text)
        outer_status, outer_progress = self._extract_outer_progress(console_text)
        self._outer_status_label.setText(outer_status or "3D Scan Progress")
        self._outer_progress.setValue(outer_progress or 0)
        self._inner_status_label.setText(self._extract_inner_status(console_text) or "Scan Progress")
        self._progress.setValue(self._extract_progress(console_text) or 0)

    @staticmethod
    def _extract_status(console_text: str) -> str:
        lines = [
            line.strip()
            for line in console_text.splitlines()
            if any(prefix in line.strip() for prefix in ScanMonitorWidget._status_prefixes)
            and "Scan_progress:" not in line
        ]
        if not lines:
            return ""
        return lines[-1]

    @classmethod
    def _extract_outer_progress(cls, console_text: str) -> tuple[str, Optional[int]]:
        matches = cls._outer_progress_pattern.findall(console_text)
        if not matches:
            return "", None

        angle_text, current_text, total_text = matches[-1]
        try:
            current = int(current_text)
            total = int(total_text)
        except ValueError:
            return f"Angle {angle_text.strip()}", None

        progress = int((current / total) * 100) if total > 0 else 0
        status = f"Angle {angle_text.strip()} ({current}/{total})"
        return status, max(0, min(100, progress))

    @staticmethod
    def _extract_inner_status(console_text: str) -> str:
        lines = [
            line.strip()
            for line in console_text.splitlines()
            if "Filename:" in line and "Scan_progress:" in line
        ]
        if not lines:
            return ""
        return lines[-1]

    @classmethod
    def _extract_progress(cls, console_text: str) -> Optional[int]:
        scan_progress_matches = cls._scan_progress_pattern.findall(console_text)
        if scan_progress_matches:
            try:
                return max(0, min(100, int(float(scan_progress_matches[-1]))))
            except ValueError:
                pass

        percent_matches = cls._percent_pattern.findall(console_text)
        if percent_matches:
            try:
                return max(0, min(100, int(float(percent_matches[-1]))))
            except ValueError:
                pass

        fraction_matches = cls._fraction_pattern.findall(console_text)
        if fraction_matches:
            current_text, total_text = fraction_matches[-1]
            try:
                current = int(current_text)
                total = int(total_text)
            except ValueError:
                return None
            if total > 0:
                return max(0, min(100, int((current / total) * 100)))
        return None

    def _update_scan_timing(self, console_text: str) -> None:
        start_count = console_text.count(self._start_scan_text)
        done_count = console_text.count(self._done_scan_text)

        if start_count > self._last_start_count:
            self._current_2d_start_time = time.monotonic()
            self._last_start_count = start_count

        if done_count > self._last_done_count:
            if self._current_2d_start_time is not None:
                duration = max(0.0, time.monotonic() - self._current_2d_start_time)
                self._completed_2d_durations.append(duration)
                if len(self._completed_2d_durations) > 20:
                    self._completed_2d_durations = self._completed_2d_durations[-20:]
            self._current_2d_start_time = None
            self._last_done_count = done_count

    def _format_eta(self, console_text: str) -> str:
        outer_status, outer_progress = self._extract_outer_progress(console_text)
        if not outer_status or outer_progress is None or not self._completed_2d_durations:
            return ""

        match = self._outer_progress_pattern.findall(console_text)
        if not match:
            return ""

        _, current_text, total_text = match[-1]
        try:
            current = int(current_text)
            total = int(total_text)
        except ValueError:
            return ""
        if total <= 0:
            return ""

        avg_duration = sum(self._completed_2d_durations) / len(self._completed_2d_durations)
        remaining_scans = max(0, total - current)

        if self._current_2d_start_time is not None:
            elapsed = max(0.0, time.monotonic() - self._current_2d_start_time)
            remaining_current = max(0.0, avg_duration - elapsed)
        else:
            remaining_current = 0.0

        remaining_seconds = remaining_current + remaining_scans * avg_duration
        if remaining_seconds <= 0:
            return "ETA: soon"

        eta = datetime.now() + timedelta(seconds=remaining_seconds)
        return f"ETA: {eta.strftime('%Y-%m-%d %H:%M:%S')}"


__all__ = ["ScanMonitorWidget"]
