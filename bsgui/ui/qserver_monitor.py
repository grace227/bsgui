"""Bluesky QServer monitoring widget."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


# @dataclass(frozen=True)
# class QueueItem:
#     """Representation of a queued or running plan."""

#     uid: str
#     name: str
#     args: str
#     state: str


# @dataclass(frozen=True)
# class QueueSnapshot:
#     """Aggregate queue information returned by a client."""

#     pending: Sequence[QueueItem]
#     running: Optional[QueueItem]
#     completed: Sequence[QueueItem]
#     progress: Optional[int] = None  # percent 0-100


# class BlueskyQueueClient(Protocol):
#     """Minimal protocol for fetching queue information from Bluesky QServer."""

#     def fetch_snapshot(self) -> QueueSnapshot:
#         ...


class QServerWidget(QWidget):
    """Widget that displays queue state and progress for Bluesky QServer."""

    def __init__(
        self,
        client: Optional[BlueskyQueueClient] = None,
        poll_interval_ms: int = 2000,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self._client = client
        self._queue_table = QTableWidget(0, 3)
        self._queue_table.setHorizontalHeaderLabels(["Plan", "Arguments", "State"])
        self._queue_table.horizontalHeader().setStretchLastSection(True)

        self._active_label = QLabel("Idle")
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)

        self._completed_list = QListWidget()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Queued Plans"))
        layout.addWidget(self._queue_table)
        layout.addWidget(QLabel("Active Plan"))
        layout.addWidget(self._active_label)
        layout.addWidget(self._progress)
        layout.addWidget(QLabel("Recently Completed"))
        layout.addWidget(self._completed_list)

        self._timer: Optional[QTimer] = None
        if self._client is not None:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.refresh_from_client)
            self._timer.start(poll_interval_ms)

    def refresh_from_client(self) -> None:
        if self._client is None:
            return
        snapshot = self._client.fetch_snapshot()
        self.update_queue(snapshot.pending)
        self.update_active(snapshot.running, snapshot.progress)
        self.update_completed(snapshot.completed)

    def update_queue(self, queue: Sequence[QueueItem]) -> None:
        self._queue_table.setRowCount(len(queue))
        for row, item in enumerate(queue):
            self._queue_table.setItem(row, 0, QTableWidgetItem(item.name))
            self._queue_table.setItem(row, 1, QTableWidgetItem(item.args))
            self._queue_table.setItem(row, 2, QTableWidgetItem(item.state))

    def update_active(self, item: Optional[QueueItem], progress: Optional[int]) -> None:
        if item is None:
            self._active_label.setText("Idle")
            self._progress.setValue(0)
            self._progress.setMaximum(100)
            return

        self._active_label.setText(f"{item.name} ({item.uid[:8]})")
        if progress is None:
            self._progress.setRange(0, 0)  # busy indicator
        else:
            self._progress.setRange(0, 100)
            self._progress.setValue(max(0, min(progress, 100)))

    def update_completed(self, completed: Sequence[QueueItem]) -> None:
        self._completed_list.clear()
        for item in completed:
            label = f"{item.name} – {item.state}"
            list_item = QListWidgetItem(label)
            self._completed_list.addItem(list_item)

    def stop_polling(self) -> None:
        if self._timer is not None:
            self._timer.stop()
