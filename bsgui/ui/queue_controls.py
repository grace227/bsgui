"""Interactive helpers for managing queue-table drag and drop."""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QAbstractItemView, QTableWidget

from ..core.qserver_controller import QServerController
from .status_bus import emit_status

QUEUE_ITEM_UID_ROLE = Qt.ItemDataRole.UserRole + 1


class QueueTableCursorController(QObject):
    """Attach drag-and-drop helpers to the queue table."""

    def __init__(
        self,
        table: QTableWidget,
        *,
        controller: Optional[QServerController] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent or table)
        self._table = table
        self._controller = controller

        self._pending_uids: list[str] = []
        self._pending_row_count = 0
        self._pending_drop_row: Optional[int] = None
        self._pending_drag_uid: Optional[str] = None
        self._drag_enabled = False

        self._configure_table_widget()
        table.viewport().installEventFilter(self)

    # ------------------------------------------------------------------ #
    # Public API

    def set_controller(self, controller: Optional[QServerController]) -> None:
        self._controller = controller
        self._update_drag_state()

    def sync_pending_items(self, pending_items: Sequence[Mapping[str, object]]) -> None:
        """Refresh cached pending metadata and update drag state."""

        self._pending_uids = [self._extract_uid(item) or "" for item in pending_items]
        self._pending_row_count = len(self._pending_uids)
        self._update_drag_state()

    # ------------------------------------------------------------------ #
    # Qt hooks

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._table.viewport():
            if event.type() in {QEvent.DragEnter, QEvent.DragMove}:
                self._capture_pending_drag()
            elif event.type() == QEvent.Drop:
                drop_event = event if isinstance(event, QDropEvent) else None
                self._process_pending_reorder(drop_event)
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ #
    # Internal helpers

    def _configure_table_widget(self) -> None:
        table = self._table
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setDragDropOverwriteMode(False)
        table.setDropIndicatorShown(True)
        table.setDefaultDropAction(Qt.MoveAction)
        table.viewport().setAcceptDrops(False)
        table.setDragDropMode(QAbstractItemView.NoDragDrop)

    def _update_drag_state(self) -> None:
        enabled = (
            bool(self._controller)
            and self._pending_row_count > 1
            and all(uid for uid in self._pending_uids)
        )
        if enabled == self._drag_enabled:
            return

        self._drag_enabled = enabled
        mode = QAbstractItemView.InternalMove if enabled else QAbstractItemView.NoDragDrop
        self._table.setDragDropMode(mode)
        self._table.viewport().setAcceptDrops(enabled)
        self._table.setDefaultDropAction(Qt.MoveAction if enabled else Qt.IgnoreAction)

    def _process_pending_reorder(self, drop_event: Optional[QDropEvent] = None) -> None:
        if not self._drag_enabled:
            self._reset_pending_state()
            return

        if drop_event is not None:
            self._pending_drop_row = self._derive_drop_row(drop_event)

        target_row = self._resolve_drop_row()
        if target_row is None:
            emit_status("Drop location must stay within pending queue.")
            self._reset_pending_state()
            return

        uid = self._pending_drag_uid or self._current_uid()
        if not uid:
            emit_status("Unable to determine which plan was dragged.")
            self._reset_pending_state()
            return

        controller = self._controller
        api = getattr(controller, "_api", None) if controller else None
        if api is None:
            emit_status("Queue controller unavailable.")
            self._reset_pending_state()
            return

        try:
            response = api.item_move(uid=uid, pos_dest=target_row)
        except Exception:  # pragma: no cover - runtime safeguard
            emit_status("Error sending queue reorder request.")
            self._reset_pending_state()
            return

        success = True
        message = "Queue reorder request completed."
        if isinstance(response, Mapping):
            success = bool(response.get("success", False))
            message = response.get("msg", message) or message

        emit_status(message if success else message or "Queue reorder request failed.")
        self._reset_pending_state()

    def _capture_pending_drag(self) -> None:
        selection = self._table.selectionModel()
        row: Optional[int] = None
        if selection is not None and selection.hasSelection():
            indexes = selection.selectedRows()
            if indexes:
                row = indexes[0].row()
        if row is None:
            row = self._table.currentRow()

        if row is None or row < 0 or row >= self._pending_row_count:
            self._pending_drag_uid = None
            return

        self._pending_drag_uid = self._lookup_row_uid(row)

    def _derive_drop_row(self, event: QDropEvent) -> Optional[int]:
        pos = event.position().toPoint()
        index = self._table.indexAt(pos)
        if index.isValid():
            return index.row()

        row = self._table.rowAt(pos.y())
        if row >= 0:
            return row

        if pos.y() > self._table.viewport().rect().bottom():
            row_count = self._table.rowCount()
            return row_count - 1 if row_count else None
        return None

    def _resolve_drop_row(self) -> Optional[int]:
        if self._pending_row_count == 0:
            return None
        if self._pending_drop_row is None:
            return None
        return max(0, min(self._pending_drop_row, self._pending_row_count - 1))

    def _lookup_row_uid(self, row: int) -> Optional[str]:
        if row < 0 or row >= self._table.rowCount():
            return None
        for column in range(self._table.columnCount()):
            item = self._table.item(row, column)
            if item is None:
                continue
            value = item.data(QUEUE_ITEM_UID_ROLE)
            if isinstance(value, str) and value:
                return value
        return None

    def _current_uid(self) -> Optional[str]:
        row = self._table.currentRow()
        if row is None or row < 0:
            return None
        return self._lookup_row_uid(row)

    def _reset_pending_state(self) -> None:
        self._pending_drop_row = None
        self._pending_drag_uid = None

    @staticmethod
    def _extract_uid(item: Mapping[str, object]) -> Optional[str]:
        candidates = (
            item.get("item_uid"),
            item.get("uid"),
        )
        nested = item.get("item")
        if isinstance(nested, Mapping):
            candidates += (
                nested.get("item_uid"),
                nested.get("uid"),
            )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate:
                return candidate
        return None
