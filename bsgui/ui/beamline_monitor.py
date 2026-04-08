"""Widget for viewing plan-aware beamline monitoring snapshots."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from typing import Any, Mapping, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .status_bus import emit_status


class BeamlineMonitorWidget(QWidget):
    """Status-oriented view of the current beamline monitoring snapshot."""

    def __init__(
        self,
        *,
        parent: Optional[QWidget] = None,
        title: str = "Scan Monitor",
        poll_interval_ms: int = 3000,
        auto_refresh: bool = True,
        detector_recovery_cooldown_seconds: float | None = None,
        detector_timeout_factor: float | None = None,
        sample_position_tolerance: float | None = None,
        detector_retries: int = 1,
    ) -> None:
        super().__init__(parent)
        self._controller = None
        self._auto_refresh = bool(auto_refresh)
        self._detector_monitor_enabled = False
        self._expanded_devices: set[str] = set()
        self._last_snapshot: Mapping[str, Any] | None = None
        self._refresh_in_progress = False
        self._detector_recovery_cooldown_seconds = (
            float(detector_recovery_cooldown_seconds)
            if detector_recovery_cooldown_seconds is not None
            else 30.0
        )
        self._detector_retries = max(1, int(detector_retries))
        self._last_recovery_at: dict[str, datetime] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet("font-weight: 600; font-size: 15px;")
        header.addWidget(self._title_label)
        header.addStretch(1)

        self._detector_monitor_button = QPushButton("Detector Monitor")
        self._detector_monitor_button.setCheckable(True)
        self._detector_monitor_button.setChecked(self._detector_monitor_enabled)
        self._detector_monitor_button.clicked.connect(self._toggle_detector_monitor)
        header.addWidget(self._detector_monitor_button)

        self._auto_refresh_button = QPushButton("Auto Refresh")
        self._auto_refresh_button.setCheckable(True)
        self._auto_refresh_button.setChecked(self._auto_refresh)
        self._auto_refresh_button.clicked.connect(self._toggle_auto_refresh)
        header.addWidget(self._auto_refresh_button)
        layout.addLayout(header)

        summary_box = QFrame()
        summary_box.setFrameShape(QFrame.Shape.StyledPanel)
        summary_layout = QVBoxLayout(summary_box)
        summary_layout.setContentsMargins(10, 10, 10, 10)
        summary_layout.setSpacing(8)

        self._device_overview_title = QLabel("Active Devices")
        self._device_overview_title.setStyleSheet("font-weight: 600;")
        summary_layout.addWidget(self._device_overview_title)

        self._device_overview_widget = QWidget()
        self._device_overview_layout = QHBoxLayout(self._device_overview_widget)
        self._device_overview_layout.setContentsMargins(0, 0, 0, 0)
        # self._device_overview_layout.setSpacing(8)
        summary_layout.addWidget(self._device_overview_widget)

        self._activity_label = QLabel("Current activity: waiting for snapshot")
        self._activity_label.setWordWrap(True)
        summary_layout.addWidget(self._activity_label)

        self._timestamp_label = QLabel("Last update: --")
        self._timestamp_label.setWordWrap(True)
        self._timestamp_label.setStyleSheet("color: #616161;")
        summary_layout.addWidget(self._timestamp_label)

        self._manifest_label = QLabel("PV Json File: --")
        self._manifest_label.setWordWrap(True)
        self._manifest_label.setStyleSheet("color: #616161;")
        summary_layout.addWidget(self._manifest_label)

        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        summary_layout.addWidget(self._error_label)
        layout.addWidget(summary_box)

        self._device_list = QListWidget()
        self._device_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._device_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._device_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self._device_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self._device_list, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(max(250, int(poll_interval_ms)))
        self._timer.timeout.connect(self.refresh)
        self._update_timer_state()

    def set_controller(self, controller) -> None:
        self._controller = controller
        self.refresh()

    def refresh(self) -> None:
        if self._refresh_in_progress:
            return
        controller = self._controller
        if controller is None:
            self._set_empty_state("No QServer controller")
            return

        self._refresh_in_progress = True
        scroll_bar = self._device_list.verticalScrollBar()
        scroll_value = scroll_bar.value() if scroll_bar is not None else None

        try:
            try:
                snapshot = controller._api.get_active_plan_monitor_snapshot()
            except Exception as exc:
                self._set_empty_state("Failed to load beamline snapshot", error=str(exc))
                emit_status(f"Beamline monitor refresh failed: {exc}")
                return

            if not isinstance(snapshot, Mapping):
                self._set_empty_state("Beamline snapshot unavailable", error=str(snapshot))
                return

            if self._detector_monitor_enabled:
                self._run_detector_monitor(snapshot)
            self._render_snapshot(snapshot)
            if scroll_bar is not None and scroll_value is not None:
                scroll_bar.setValue(scroll_value)
        finally:
            self._refresh_in_progress = False

    def _toggle_auto_refresh(self, checked: bool) -> None:
        self._auto_refresh = bool(checked)
        self._update_timer_state()

    def _toggle_detector_monitor(self, checked: bool) -> None:
        self._detector_monitor_enabled = bool(checked)
        self._update_timer_state()
        self.refresh()

    def _update_timer_state(self) -> None:
        if self._auto_refresh or self._detector_monitor_enabled:
            self._timer.start()
        else:
            self._timer.stop()

    def _set_empty_state(self, message: str, *, error: str | None = None) -> None:
        self._last_snapshot = None
        self._populate_device_overview({})
        self._activity_label.setText(f"Current activity: {message}")
        self._timestamp_label.setText("Last update: --")
        self._manifest_label.setText("Manifest: --")
        self._set_error(error)
        self._device_list.clear()

    def _render_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        self._last_snapshot = snapshot
        devices = snapshot.get("devices")
        devices = dict(devices) if isinstance(devices, Mapping) else {}
        error = snapshot.get("error")
        idle_error = self._is_idle_snapshot_error(error)

        activity = str(snapshot.get("activity") or self._summarize_activity(snapshot))
        timestamp = self._format_timestamp(snapshot.get("timestamp"))
        manifest_path = snapshot.get("manifest_path") or "embedded defaults"

        self._populate_device_overview(devices, snapshot=snapshot)
        self._activity_label.setText(f"Current activity: {activity}")
        self._timestamp_label.setText(f"Last update: {timestamp}")
        self._manifest_label.setText(f"Manifest: {manifest_path}")
        self._set_error(None if idle_error else str(error) if error else None)

        self._device_list.clear()
        for device_name in self._ordered_device_names(devices):
            device = devices.get(device_name)
            if isinstance(device, Mapping):
                self._add_device_row(device_name, device, snapshot=snapshot)

    def _add_device_row(self, device_name: str, device: Mapping[str, Any], *, snapshot: Mapping[str, Any]) -> None:
        status = self._device_health(device_name, device, snapshot)
        color = self._status_color(status)
        detail = self._device_summary(device)
        updated = self._latest_device_timestamp(device)
        expanded = device_name in self._expanded_devices

        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)

        row = QFrame()
        row.setFrameShape(QFrame.Shape.StyledPanel)
        outer_layout = QVBoxLayout(row)
        outer_layout.setContentsMargins(8, 6, 8, 6)
        outer_layout.setSpacing(6)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        indicator = QLabel()
        indicator.setFixedSize(12, 12)
        indicator.setStyleSheet(
            f"background-color: {color}; border-radius: 6px; border: 1px solid #9e9e9e;"
        )
        layout.addWidget(indicator, 0, Qt.AlignmentFlag.AlignTop)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        title = QLabel(device_name)
        title.setStyleSheet("font-weight: 600;")
        header_row.addWidget(title)

        if self._device_supports_recovery(device_name, device):
            is_hung = self._device_is_hung(device)
            unhang_button = QPushButton("Unhang Detector")
            unhang_button.setCursor(Qt.CursorShape.PointingHandCursor)
            unhang_button.setEnabled(is_hung)
            if not is_hung:
                unhang_button.setToolTip("Detector recovery is available only when a hang is detected")
            unhang_button.clicked.connect(
                lambda _checked=False, name=device_name: self._recover_detector(name)
            )
            header_row.addWidget(unhang_button)

        toggle_button = QPushButton("Hide Details" if expanded else "Show Details")
        toggle_button.setFlat(True)
        toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle_button.setStyleSheet("text-align: left; color: #1565c0;")
        toggle_button.clicked.connect(
            lambda _checked=False, name=device_name: self._toggle_device_expanded(name)
        )
        header_row.addWidget(toggle_button)
        header_row.addStretch(1)
        text_col.addLayout(header_row)

        detail_label = QLabel(detail)
        detail_label.setWordWrap(True)
        text_col.addWidget(detail_label)

        updated_label = QLabel(f"Updated: {updated}")
        updated_label.setStyleSheet("color: #616161; font-size: 11px;")
        text_col.addWidget(updated_label)

        layout.addLayout(text_col, 1)
        outer_layout.addLayout(layout)

        details_label = QLabel(self._format_device_details(device))
        details_label.setWordWrap(True)
        details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_label.setTextFormat(Qt.TextFormat.RichText)
        details_label.setStyleSheet(
            "background-color: #fafafa; border: 1px solid #e0e0e0; padding: 6px; font-family: monospace;"
        )
        details_label.setVisible(expanded)
        outer_layout.addWidget(details_label)

        self._device_list.addItem(item)
        item.setSizeHint(row.sizeHint())
        self._device_list.setItemWidget(item, row)

    def _toggle_device_expanded(self, device_name: str) -> None:
        if device_name in self._expanded_devices:
            self._expanded_devices.remove(device_name)
        else:
            self._expanded_devices.add(device_name)
        self.refresh()

    def _recover_detector(self, device_name: str) -> None:
        snapshot = self._last_snapshot
        if not isinstance(snapshot, Mapping):
            self._set_error("No beamline snapshot available for detector recovery")
            return

        controller = self._controller
        if controller is None:
            self._set_error("No QServer controller available for detector recovery")
            return

        result = controller._api.recover_detector(device_name, retries=self._detector_retries)
        reason = result.get("reason")
        recovered = bool(result.get("success"))
        if recovered:
            self._last_recovery_at[device_name] = datetime.now()
            emit_status(f"{device_name} detector recovery commands sent")
            self._set_error(None)
        else:
            self._set_error(f"{device_name} recovery failed: {reason or result.get('error') or 'unknown error'}")
            emit_status(f"{device_name} detector recovery failed")
        self.refresh()

    def _run_detector_monitor(self, snapshot: Mapping[str, Any]) -> None:
        controller = self._controller
        if controller is None:
            return
        now = datetime.now()
        recovered: list[str] = []
        failed: list[str] = []
        devices = snapshot.get("devices")
        devices = dict(devices) if isinstance(devices, Mapping) else {}
        for device_name, device in devices.items():
            if not isinstance(device, Mapping) or not self._device_supports_recovery(device_name, device):
                continue
            if not self._device_is_hung(device):
                continue
            previous = self._last_recovery_at.get(device_name)
            if previous is not None and now - previous < timedelta(seconds=self._detector_recovery_cooldown_seconds):
                continue
            result = controller._api.recover_detector(device_name, retries=self._detector_retries)
            if isinstance(result, Mapping) and result.get("success"):
                self._last_recovery_at[device_name] = now
                recovered.append(device_name)
            else:
                failed.append(device_name)

        if recovered:
            emit_status(f"Detector monitor sent recovery for {', '.join(recovered)}")
        if failed:
            emit_status(f"Detector monitor failed recovery for {', '.join(failed)}")

    def _populate_device_overview(self, devices: Mapping[str, Any], *, snapshot: Mapping[str, Any] | None = None) -> None:
        while self._device_overview_layout.count():
            item = self._device_overview_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        ordered_names = self._ordered_device_names(devices)
        if not ordered_names:
            label = QLabel("No active devices")
            label.setStyleSheet("color: #616161;")
            self._device_overview_layout.addWidget(label)
            self._device_overview_layout.addStretch(1)
            return

        for device_name in ordered_names:
            device = devices.get(device_name)
            if not isinstance(device, Mapping):
                continue
            self._device_overview_layout.addWidget(
                self._make_device_chip(
                    device_name,
                    self._device_health(device_name, device, snapshot or {"devices": devices}),
                )
            )
        self._device_overview_layout.addStretch(1)

    def _make_device_chip(self, device_name: str, status: str) -> QWidget:
        color = self._status_color(status)
        chip = QFrame()
        chip.setFrameShape(QFrame.Shape.StyledPanel)
        chip.setStyleSheet("background-color: #fafafa; border: 1px solid #e0e0e0; border-radius: 8px;")
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        indicator = QLabel()
        indicator.setFixedSize(10, 10)
        indicator.setStyleSheet(
            f"background-color: {color}; border-radius: 5px; border: 1px solid #9e9e9e;"
        )
        layout.addWidget(indicator)

        name_label = QLabel(device_name)
        name_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(name_label)
        return chip

    @staticmethod
    def _ordered_device_names(devices: Mapping[str, Any]) -> list[str]:
        preferred = ["ring", "sample", "scanrecord", "bda", "fly_dwell", "xmap", "xp3", "eiger"]
        remaining = [name for name in devices.keys() if name not in preferred]
        return [name for name in preferred if name in devices] + sorted(remaining)

    @staticmethod
    def _device_supports_recovery(device_name: str, device: Mapping[str, Any]) -> bool:
        del device_name
        actions = device.get("actions")
        if isinstance(actions, Mapping) and actions.get("recover") is not None:
            return bool(actions.get("recover"))
        role = device.get("role")
        if isinstance(role, str) and role:
            return role == "detector"
        return "unhang" in str(device.get("summary") or "").lower()

    @staticmethod
    def _device_health(device_name: str, device: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str:
        del device_name, snapshot
        health = device.get("health")
        if isinstance(health, str) and health:
            return health
        if device.get("error"):
            return "error"
        pvs = device.get("pvs")
        if not isinstance(pvs, Mapping) or not pvs:
            return "warning"
        connected = sum(1 for pv in pvs.values() if isinstance(pv, Mapping) and pv.get("connected"))
        return "ok" if connected == len(pvs) else "warning" if connected else "error"

    @staticmethod
    def _device_is_hung(device: Mapping[str, Any]) -> bool:
        summary = str(device.get("summary") or "").strip().lower()
        return "hang" in summary

    @staticmethod
    def _device_summary(device: Mapping[str, Any]) -> str:
        summary = device.get("summary")
        if isinstance(summary, str) and summary:
            return summary
        pvs = device.get("pvs")
        if not isinstance(pvs, Mapping):
            return "No PV data"
        connected = sum(1 for pv in pvs.values() if isinstance(pv, Mapping) and pv.get("connected"))
        return f"{connected}/{len(pvs)} PVs connected"

    def _set_error(self, error: str | None) -> None:
        if error:
            self._error_label.setText(f"Error: {error}")
            self._error_label.setStyleSheet("color: #b71c1c; font-weight: 600;")
            self._error_label.show()
        else:
            self._error_label.clear()
            self._error_label.hide()

    @staticmethod
    def _status_color(status: str) -> str:
        return {
            "ok": "#2e7d32",
            "warning": "#f9a825",
            "error": "#c62828",
        }.get(status, "#9e9e9e")

    @staticmethod
    def _summarize_activity(snapshot: Mapping[str, Any]) -> str:
        if BeamlineMonitorWidget._is_idle_snapshot_error(snapshot.get("error")):
            return "idle"
        if snapshot.get("error"):
            return str(snapshot.get("error"))
        return str(snapshot.get("plan_name") or "No active plan")

    @staticmethod
    def _is_idle_snapshot_error(error: Any) -> bool:
        if not isinstance(error, str):
            return False
        normalized = error.strip()
        return normalized in {
            "No running queue item found",
            "Running item is not an executable plan",
        }

    @staticmethod
    def _latest_device_timestamp(device: Mapping[str, Any]) -> str:
        pvs = device.get("pvs")
        if not isinstance(pvs, Mapping):
            return "--"

        timestamps = []
        for pv in pvs.values():
            if isinstance(pv, Mapping):
                ts = pv.get("timestamp")
                if isinstance(ts, str) and ts:
                    timestamps.append(ts)
        if not timestamps:
            return "--"
        return BeamlineMonitorWidget._format_timestamp(max(timestamps))

    @staticmethod
    def _format_timestamp(raw: Any) -> str:
        if not raw:
            return "--"
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return raw
        return str(raw)

    @staticmethod
    def _pv_value(pv: Any) -> Any:
        if isinstance(pv, Mapping):
            value = pv.get("char_value")
            if value not in (None, ""):
                return value
            return pv.get("value")
        return None

    @staticmethod
    def _format_device_details(device: Mapping[str, Any]) -> str:
        pvs = device.get("pvs")
        if not isinstance(pvs, Mapping) or not pvs:
            return "No PV details available"

        lines = []
        for pv_key in sorted(pvs):
            pv = pvs.get(pv_key)
            if not isinstance(pv, Mapping):
                lines.append(f"{pv_key}: unavailable")
                continue
            connected = (
                '<span style="color: #2e7d32; font-weight: 600;">connected</span>'
                if pv.get("connected")
                else '<span style="color: #c62828; font-weight: 600;">disconnected</span>'
            )
            value = BeamlineMonitorWidget._pv_value(pv)
            pvname = pv.get("pvname") or "--"
            timestamp = BeamlineMonitorWidget._format_timestamp(pv.get("timestamp"))
            err = pv.get("error")
            line = f"{pv_key}: "
            if timestamp != "--":
                line += f"{timestamp} | "
            line += f"{pvname} ({connected}): {value}"
            if err:
                line += f" | error={err}"
            lines.append(line)
        return "<br/>".join(lines)


__all__ = ["BeamlineMonitorWidget"]
