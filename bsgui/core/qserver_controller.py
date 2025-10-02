"""Shared controller wiring QServer API into Qt-friendly signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from .qserver_api import QServerAPI


@dataclass
class QueueSnapshot:
    pending: list[dict]
    running: Optional[dict]
    completed: list[dict]
    progress: Optional[int]


class QServerController(QObject):
    """Single point of contact for Bluesky QServer interactions."""

    statusUpdated = Signal(dict)
    queueUpdated = Signal(QueueSnapshot)

    def __init__(
        self,
        api: Optional[QServerAPI] = None,
        *,
        poll_interval_ms: int = 2000,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._api = api or QServerAPI()
        self._poll_interval_ms = poll_interval_ms
        self._timer: Optional[QTimer] = None

    # ----------------------------------------------------------------------------
    # Control

    def request_connect(self) -> None:
        status = self._refresh_status()
        if status.get("connected"):
            self.start_polling()

    def start_polling(self) -> None:
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._poll)
        if self._timer.isActive():
            return
        self._timer.start(self._poll_interval_ms)

    def stop_polling(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    # ----------------------------------------------------------------------------
    # Internal helpers

    def _poll(self) -> None:
        status = self._refresh_status()
        if status.get("connected"):
            snapshot = self._fetch_queue()
            if snapshot:
                self.queueUpdated.emit(snapshot)

    def _refresh_status(self) -> dict:
        try:
            status = self._api.get_status()
            payload = {
                "connected": True,
                "queue_state": status.get("queue_state", "unknown"),
                "re_state": status.get("re_state", "unknown"),
            }
        except Exception as exc:  # pragma: no cover - network path
            payload = {
                "connected": False,
                "queue_state": "error",
                "re_state": str(exc),
            }
        self.statusUpdated.emit(payload)
        return payload

    def _fetch_queue(self) -> Optional[QueueSnapshot]:
        try:
            snapshot = self._api.get_queue()
            running = snapshot.get("running_queue_uid")
            active = None
            if running:
                active = {
                    "uid": running,
                    "name": snapshot.get("running_plan_uid", ""),
                    "state": snapshot.get("running_plan_state", ""),
                }
            pending = snapshot.get("queue", [])
            completed = snapshot.get("history", [])
            return QueueSnapshot(
                pending=pending,
                running=active,
                completed=completed,
                progress=None,
            )
        except Exception:  # pragma: no cover - network path
            return None

    # ----------------------------------------------------------------------------
    # Public convenience

    def fetch_snapshot(self) -> Optional[QueueSnapshot]:
        return self._fetch_queue()
