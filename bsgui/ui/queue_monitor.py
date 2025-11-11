"""Bluesky QServer monitoring widget."""

from __future__ import annotations

from collections.abc import MutableMapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette, QBrush

from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QAbstractItemView, QGridLayout, QHeaderView

from .queue_controls import QueueTableCursorController, QUEUE_ITEM_COLUMN_ROLE
from .status_bus import emit_status

QUEUE_ITEM_UID_ROLE = Qt.ItemDataRole.UserRole + 1
QUEUE_ITEM_STATE_ROLE = Qt.ItemDataRole.UserRole + 2
QUEUE_ITEM_STATE_PENDING = "pending"
QUEUE_ITEM_STATE_COMPLETED = "completed"
QUEUE_ITEM_STATE_RUNNING = "running"
QUEUE_ITEM_KWARG_KEY_ROLE = Qt.ItemDataRole.UserRole + 4

from ..core.qserver_controller import PlanDefinition, QServerController, QueueSnapshot
from ..core.queue_item_utils import (
    apply_item_edit,
    build_update_payload,
    clone_item,
    extract_item_field,
    normalize_roi_map,
    prepare_display_item,
    resolve_queue_value,
)


@dataclass(frozen=True)
class QueueColumnSpec:
    column_id: str
    label: str
    stretch: bool = False


class QueueMonitorWidget(QWidget):
    """Widget that displays queue state and progress for Bluesky QServer."""

    def __init__(
        self,
        *,
        controller: Optional[QServerController] = None,
        roi_key_map: Optional[Mapping[str, Sequence[str]]] = None,
        columns: Optional[Sequence[Mapping[str, Any]]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self._controller: Optional[QServerController] = None
        self._roi_key_map = normalize_roi_map(roi_key_map)
        self._roi_value_aliases = {
            alias for values in self._roi_key_map.values() for alias in values if alias != "title"
        }

        self._columns: list[QueueColumnSpec] = []
        self._plan_definitions: dict[str, PlanDefinition] = {}
        self._plan_param_cache: dict[str, set[str]] = {}
        self._pending_raw_items: list[dict[str, Any]] = []
        self._pending_items: list[dict[str, Any]] = []
        self._completed_items: list[dict[str, Any]] = []
        self._running_item: dict[str, Any] = {}
        self._queue_controls: Optional[QueueTableCursorController] = None
        self._suppress_item_changed = False
        self._pending_table_refresh = False
        self._has_active_plan = False

        self._queue_table = QTableWidget(0, 0)
        self._queue_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._queue_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._queue_table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._queue_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._queue_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._configure_queue_table()
        self._queue_controls = QueueTableCursorController(
            self._queue_table,
            controller=None,
            refresh_callback=self._handle_local_pending_reorder,
        )
        self._queue_table.itemChanged.connect(self._handle_item_changed)

        self._active_label = QLabel("Idle")
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)

        self._completed_list = QListWidget()
        self._completed_text_color = QColor("#5c5c5c")
        self._running_item_color = QColor("#2e7d32")
        self._start_queue_button = QPushButton("Start Queue")
        self._start_queue_button.clicked.connect(self._handle_start_queue)
        self._start_queue_button.setEnabled(True)

        self._stop_queue_button = QPushButton("Stop Queue")
        self._stop_queue_button.clicked.connect(self._handle_stop_queue)
        self._stop_queue_button.setEnabled(True)

        self._duplicate_queue_button = QPushButton("Duplicate Selected")
        self._duplicate_queue_button.clicked.connect(self._handle_duplicate_queue)
        self._duplicate_queue_button.setEnabled(True)

        self._delete_queue_button = QPushButton("Delete Selected")
        self._delete_queue_button.clicked.connect(self._handle_delete_queue)
        self._delete_queue_button.setEnabled(True)

        self._clear_queue_button = QPushButton("Clear Queue")
        self._clear_queue_button.clicked.connect(self._handle_clear_queue)
        self._clear_queue_button.setEnabled(True)

        self._clear_history_button = QPushButton("Clear History")
        self._clear_history_button.clicked.connect(self._handle_clear_history)
        self._clear_history_button.setEnabled(True)

        layout = QVBoxLayout(self)
        header_layout = QGridLayout()
        header_layout.addWidget(self._start_queue_button, 0, 0)
        header_layout.addWidget(self._stop_queue_button, 0, 1)
        header_layout.addWidget(self._duplicate_queue_button, 0, 2)
        header_layout.addWidget(self._delete_queue_button, 1, 0)
        header_layout.addWidget(self._clear_queue_button, 1, 1)
        header_layout.addWidget(self._clear_history_button, 1, 2)
        layout.addLayout(header_layout)
        table_container = QScrollArea()
        table_container.setWidgetResizable(True)
        table_container.setWidget(self._queue_table)
        layout.addWidget(table_container)

        self._status_label = QLabel("")
        self._status_label.setObjectName("queueStatusLabel")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addWidget(QLabel("Active Plan"))
        layout.addWidget(self._active_label)
        layout.addWidget(self._progress)
        layout.addWidget(QLabel("Recently Completed"))
        layout.addWidget(self._completed_list)

        self._update_queue_actions()

        if controller is not None:
            self.set_controller(controller)

    # ------------------------------------------------------------------
    # Controller wiring

    def set_controller(self, controller: Optional[QServerController]) -> None:
        if controller is self._controller:
            return
        if self._controller is not None:
            try:
                self._controller.queueUpdated.disconnect(self._handle_queue_updated)
            except (RuntimeError, AttributeError):
                pass
        self._controller = controller
        self._plan_param_cache.clear()
        self._load_plan_definitions()
        if self._queue_controls is not None:
            self._queue_controls.set_controller(controller)
        self._update_queue_actions()
        if controller is None:
            return
        controller.queueUpdated.connect(self._handle_queue_updated)
        snapshot = controller.fetch_snapshot()
        if snapshot:
            self._apply_snapshot(snapshot)

    # ------------------------------------------------------------------
    # Snapshot/application helpers

    def _handle_queue_updated(self, snapshot: QueueSnapshot) -> None:
        if not self._plan_definitions:
            self._load_plan_definitions()
        self._apply_snapshot(snapshot)

    def _load_plan_definitions(self) -> None:
        self._plan_definitions = {}
        controller = self._controller
        if controller is None:
            return
        try:
            definitions = controller.get_allowed_plan_definitions()
        except Exception:
            return
        self._plan_definitions = {definition.name: definition for definition in definitions}
        self._plan_param_cache.clear()

    def _apply_snapshot(self, snapshot: QueueSnapshot) -> None:
        self.update_completed(snapshot.completed or [])
        self.update_queue(snapshot.pending or [])
        self.update_active(snapshot.running, snapshot.progress)

    # ------------------------------------------------------------------
    # View helpers

    def update_queue(self, queue: Sequence[Mapping[str, Any]]) -> None:
        self._pending_raw_items = [clone_item(item) for item in queue]
        self._pending_items = [prepare_display_item(item) for item in self._pending_raw_items]
        self._refresh_queue_table()
        self._update_queue_actions()

    def update_active(
        self,
        item: Optional[Mapping[str, Any]],
        progress: Optional[int],
    ) -> None:
        self._running_item = clone_item(item)
        self._refresh_queue_table()

    def update_completed(self, completed: Sequence[Mapping[str, Any]]) -> None:
        self._completed_list.clear()
        self._completed_items = [prepare_display_item(item, completed=True) for item in completed]
        self._completed_items = self._completed_items[::-1]
        self._refresh_queue_table()

    def _update_queue_actions(self) -> None:
        if self._controller is not None:
            api = getattr(self._controller, "_api", None)
            if api is not None:
                queue_running = api.isqueue_running()
                re_closed = api.isRE_closed()
                queue_stop_pending = api.queue_stop_pending()
                if not queue_stop_pending:
                    if not re_closed and queue_running:
                        self._start_queue_button.setEnabled(False)
                        self._stop_queue_button.setEnabled(True)
                    elif not re_closed and not queue_running:
                        self._start_queue_button.setEnabled(True)
                        self._stop_queue_button.setEnabled(False)
                        self._stop_queue_button.setDown(False)
                    else:
                        self._start_queue_button.setEnabled(False)
                        self._stop_queue_button.setEnabled(False)


    def _handle_start_queue(self) -> None:
        button = self._start_queue_button

        if self._controller is None:
            self._set_status_message("Queue controller unavailable.")
            button.setDown(False)
            self._update_queue_actions()
            return

        api = getattr(self._controller, "_api", None)
        if api is None:
            self._set_status_message("Queue API unavailable.")
            button.setDown(False)
            self._update_queue_actions()
            return

        button.setDown(True)
        button.setEnabled(False)

        try:
            response = api.queue_start()
        except Exception:
            self._set_status_message("Failed to submit queue start request.")
            button.setDown(False)
            button.setEnabled(True)
            self._update_queue_actions()
            return

        message = "Queue start request sent."
        success = True
        if isinstance(response, Mapping):
            success = bool(response.get("success", False))
            message = response.get("msg", message) or message

        self._set_status_message(message)
        if success:
            self._has_active_plan = True

        self._update_queue_actions()

        if success:
            button.setDown(True)
            button.setEnabled(False)
        else:
            button.setDown(False)
            button.setEnabled(True)


    def _handle_duplicate_queue(self) -> None:
        table = self._queue_table
        if table is None:
            self._set_status_message("Queue table unavailable.")
            return

        controller = self._controller
        if controller is None:
            self._set_status_message("Queue controller unavailable.")
            return

        api = getattr(controller, "_api", None)
        if api is None:
            self._set_status_message("Queue API unavailable.")
            return

        queue_controls = self._queue_controls
        if queue_controls is None:
            self._set_status_message("Queue controls unavailable.")
            return

        pending_uids = queue_controls.selected_row_uids(pending_only=False)
        print(f"pending_uids: {pending_uids}")
        if not queue_controls.has_selection():
            self._set_status_message("No queue rows selected.")
            return

        try:
            api.duplicate_queue(pending_uids)
            table.selectRow(len(pending_uids))
        except Exception:
            self._set_status_message("Failed to duplicate selected queue items.")
            return

        count = len(pending_uids)
        suffix = "" if count == 1 else "s"
        self._set_status_message(f"Duplicate request sent for {count} queued plan{suffix}.")
        self._update_queue_actions()



    def _handle_stop_queue(self) -> None:
        button = self._stop_queue_button

        if self._controller is None:
            self._set_status_message("Queue controller unavailable.")
            button.setDown(False)
            self._update_queue_actions()
            return

        api = getattr(self._controller, "_api", None)
        if api is None:
            self._set_status_message("Queue API unavailable.")
            button.setDown(False)
            self._update_queue_actions()
            return

        button.setDown(True)
        button.setEnabled(False)

        try:
            response = api.queue_stop()
        except Exception:
            self._set_status_message("Failed to submit queue stop request.")
            button.setDown(False)
            button.setEnabled(True)
            self._update_queue_actions()
            return
        
        message = "Queue stop request sent."
        success = True
        if isinstance(response, Mapping):
            success = bool(response.get("success", False))
            message = response.get("msg", message) or message

        self._set_status_message(message)
        self._update_queue_actions()

        if success:
            button.setDown(True)
            button.setEnabled(False)
        else:
            button.setDown(False)
            button.setEnabled(True)

    def _handle_clear_queue(self) -> None:
        if self._controller is None:
            self._set_status_message("Queue controller unavailable.")
            return
            
        api = getattr(self._controller, "_api", None)
        if api is None:
            self._set_status_message("Queue API unavailable.")
            return
        try:
            api.clear_queue()
        except Exception:
            self._set_status_message("Failed to clear queue.")
            return
        self._set_status_message("Queue cleared.")
        self._update_queue_actions()

    def _handle_clear_history(self) -> None:
        if self._controller is None:
            self._set_status_message("Queue controller unavailable.")
            self._update_queue_actions()
            return
        self._controller._api.clear_history()
        self._set_status_message("History cleared.")
        self._update_queue_actions()

    def _handle_delete_queue(self) -> None:
        table = self._queue_table
        if table is None:
            self._set_status_message("Queue table unavailable.")
            return

        controller = self._controller
        if controller is None:
            self._set_status_message("Queue controller unavailable.")
            return

        api = getattr(controller, "_api", None)
        if api is None:
            self._set_status_message("Queue API unavailable.")
            return

        queue_controls = self._queue_controls
        if queue_controls is None:
            self._set_status_message("Queue controls unavailable.")
            return

        pending_uids = queue_controls.selected_row_uids(pending_only=True)
        if not queue_controls.has_selection():
            self._set_status_message("No queue rows selected.")
            return
        if not pending_uids:
            self._set_status_message("Only queued plans can be deleted.")
            return

        try:
            api.delete_queue(pending_uids)
        except Exception:
            self._set_status_message("Failed to delete selected queue items.")
            return

        count = len(pending_uids)
        suffix = "" if count == 1 else "s"
        self._set_status_message(f"Delete request sent for {count} queued plan{suffix}.")
        self._update_queue_actions()
        

    # ------------------------------------------------------------------
    # Internal utilities

    def _handle_local_pending_reorder(self, uid: str, target_index: int) -> None:
        if not self._pending_items:
            return

        def resolve_uid(item: Mapping[str, Any]) -> str:
            value = extract_item_field(item, "item_uid") or extract_item_field(item, "uid")
            return str(value or "")

        source_index: Optional[int] = None
        for idx, item in enumerate(self._pending_items):
            if resolve_uid(item) == uid:
                source_index = idx
                break

        if source_index is None or target_index < 0:
            return

        target_index = max(0, min(target_index, len(self._pending_items) - 1))
        if source_index == target_index:
            return

        item = self._pending_items.pop(source_index)
        raw_item = self._pending_raw_items.pop(source_index)
        if source_index < target_index:
            target_index -= 1
        self._pending_items.insert(target_index, item)
        self._pending_raw_items.insert(target_index, raw_item)
        self._refresh_queue_table()

    def _refresh_queue_table(self) -> None:
        if self._queue_table.state() == QAbstractItemView.EditingState:
            if not self._pending_table_refresh:
                self._pending_table_refresh = True
                QTimer.singleShot(200, self._attempt_refresh_after_edit)
            return
        self._pending_table_refresh = False
        self._rebuild_queue_table()

    def _attempt_refresh_after_edit(self) -> None:
        if self._queue_table.state() == QAbstractItemView.EditingState:
            QTimer.singleShot(200, self._attempt_refresh_after_edit)
            return
        if self._pending_table_refresh:
            self._pending_table_refresh = False
            self._rebuild_queue_table()

    def _get_uid(self, item: Mapping[str, Any]) -> str:
        return extract_item_field(item, "item_uid") or extract_item_field(item, "uid") or ""

    def _rebuild_queue_table(self) -> None:

        running_uid = self._get_uid(self._running_item)
        
        if running_uid != "":
            all_items: list[Mapping[str, Any]] = [self._running_item, *self._pending_items, *self._completed_items]
        else:
            all_items: list[Mapping[str, Any]] = [*self._pending_items, *self._completed_items]
        
        self._ensure_columns(all_items)
        self._suppress_item_changed = True
        self._queue_table.blockSignals(True)
        
        try:
            self._queue_table.setRowCount(len(all_items))
            for row, item in enumerate(all_items):
                running = False
                uid = self._get_uid(item)
                
                pending_index = row - (1 if running_uid else 0)

                if running_uid and uid == running_uid:
                    state = QUEUE_ITEM_STATE_RUNNING
                    running = True
                elif 0 <= pending_index < len(self._pending_raw_items):
                    state = QUEUE_ITEM_STATE_PENDING
                else:
                    state = QUEUE_ITEM_STATE_COMPLETED

                plan_name = self._extract_plan_name(item)
                param_names = self._plan_parameters_for(plan_name)

                for column_index, spec in enumerate(self._columns):
                    display_value, source_key = resolve_queue_value(
                        spec.column_id,
                        item,
                        row,
                        roi_key_map=self._roi_key_map,
                        roi_value_aliases=self._roi_value_aliases,
                        available_params=param_names,
                        running=running,
                    )

                    cell = QTableWidgetItem(display_value)
                    if state == QUEUE_ITEM_STATE_COMPLETED:
                        cell.setForeground(QBrush(self._completed_text_color))
                    if state == QUEUE_ITEM_STATE_RUNNING:
                        cell.setForeground(QBrush(self._running_item_color))
                        font = cell.font()
                        font.setBold(True)
                        cell.setFont(font)

                    cell.setData(QUEUE_ITEM_UID_ROLE, str(uid))
                    cell.setData(QUEUE_ITEM_STATE_ROLE, state)
                    cell.setData(QUEUE_ITEM_COLUMN_ROLE, spec.column_id)

                    effective_key = source_key or spec.column_id
                    if effective_key and effective_key in param_names:
                        cell.setData(QUEUE_ITEM_KWARG_KEY_ROLE, effective_key)

                    flags = cell.flags()
                    if state == QUEUE_ITEM_STATE_PENDING:
                        flags |= Qt.ItemIsDragEnabled | Qt.ItemIsEditable
                    else:
                        flags &= ~Qt.ItemIsDragEnabled
                        flags &= ~Qt.ItemIsEditable
                    cell.setFlags(flags)

                    self._queue_table.setItem(row, column_index, cell)
            
        finally:
            self._queue_table.blockSignals(False)
            self._suppress_item_changed = False

        if self._queue_controls is not None:
            self._queue_controls.sync_pending_items(self._pending_raw_items)

    def _handle_item_changed(self, cell: QTableWidgetItem) -> None:
        
        if self._suppress_item_changed:
            return
        if self._controller is None:
            return

        row = cell.row()
        column_index = cell.column()
        running_uid = self._get_uid(self._running_item)
        if running_uid != "":
            row -= 1
        if row < 0 or column_index < 0:
            return
        if row >= len(self._pending_raw_items):
            # Editing completed or running row: revert.
            self._restore_cell_from_cache(cell, row, column_index)
            self._set_status_message("Only queued plans can be edited.")
            return
        if column_index >= len(self._columns):
            return

        column_id = cell.data(QUEUE_ITEM_COLUMN_ROLE)
        if not isinstance(column_id, str):
            column_id = self._columns[column_index].column_id

        plan_name = self._extract_plan_name(self._pending_raw_items[row])
        source_key = cell.data(QUEUE_ITEM_KWARG_KEY_ROLE)
        target_key = source_key if isinstance(source_key, str) and source_key else column_id
        raw_item = self._pending_raw_items[row]
        if not isinstance(raw_item, MutableMapping):
            self._revert_pending_edit(row, column_index, cell, "Unable to edit this entry.")
            return

        previous_raw = deepcopy(raw_item)
        previous_display = self._pending_items[row]
        old_text = self._format_queue_value(column_id, previous_display, row)
        new_text = cell.text()
        if new_text == old_text:
            return

        if not apply_item_edit(
            raw_item,
            target_key,
            new_text,
            plan_name=plan_name,
            plan_definitions=self._plan_definitions,
            roi_key_map=self._roi_key_map,
        ):
            self._revert_pending_edit(row, column_index, cell, f"Cannot edit column '{column_id}'.", previous_raw, previous_display, old_text)
            return

        # # Update cached display version
        self._pending_items[row] = prepare_display_item(raw_item)
        row_values = self._get_row_values(row + (1 if running_uid != "" else 0))

        api = getattr(self._controller, "_api", None)
        if api is None:
            self._revert_pending_edit(row, column_index, cell, "Queue controller unavailable.", previous_raw, previous_display, old_text)
            return

        try:
            payload = build_update_payload(
                raw_item,
                row_values,
                exclude_keys={"name", "status"},
                plan_definitions=self._plan_definitions,
                plan_name=plan_name,
            )
        except ValueError as exc:
            self._revert_pending_edit(
                row + (1 if running_uid != "" else 0),
                column_index,
                cell,
                f"Invalid value: {exc}",
                previous_raw,
                previous_display,
                old_text,
            )
            return

        update_fn = getattr(api, "item_update", None) or getattr(api, "queue_item_update", None)
        if update_fn is None:
            self._revert_pending_edit(row, column_index, cell, "Queue API does not support updates.", previous_raw, previous_display, old_text)
            return

        try:
            response = update_fn(item=payload, replace=False)
        except Exception:
            self._revert_pending_edit(row, column_index, cell, "Failed to submit queue item update.", previous_raw, previous_display, old_text)
            return

        message = "Queue item updated."
        success = True
        if isinstance(response, Mapping):
            success = bool(response.get("success", False))
            message = response.get("msg", message) or message

        if not success:
            self._set_status_message(message or "Queue item update rejected.")
            self._pending_raw_items[row] = previous_raw
            self._pending_items[row] = previous_display
            self._restore_cell_from_value(cell, old_text)
            return

        self._set_status_message(message)
        self._refresh_queue_table()

    def _revert_pending_edit(
        self,
        row: int,
        column_index: int,
        cell: QTableWidgetItem,
        message: str,
        previous_raw: Optional[Mapping[str, Any]] = None,
        previous_display: Optional[Mapping[str, Any]] = None,
        old_text: Optional[str] = None,
    ) -> None:
        self._set_status_message(message)
        if previous_raw is not None and 0 <= row < len(self._pending_raw_items):
            self._pending_raw_items[row] = clone_item(previous_raw)
        if previous_display is not None and 0 <= row < len(self._pending_items):
            self._pending_items[row] = prepare_display_item(previous_display)
        if old_text is not None:
            self._restore_cell_from_value(cell, old_text)
        else:
            self._restore_cell_from_cache(cell, row, column_index)

    def _restore_cell_from_cache(self, cell: QTableWidgetItem, row: int, column_index: int) -> None:
        if 0 <= row < len(self._pending_items) and 0 <= column_index < len(self._columns):
            column_id = self._columns[column_index].column_id
            value = self._format_queue_value(column_id, self._pending_items[row], row)
            self._restore_cell_from_value(cell, value)
        else:
            self._restore_cell_from_value(cell, cell.text())

    def _restore_cell_from_value(self, cell: QTableWidgetItem, value: str) -> None:
        self._suppress_item_changed = True
        self._queue_table.blockSignals(True)
        try:
            cell.setText(value)
        finally:
            self._queue_table.blockSignals(False)
            self._suppress_item_changed = False

    def _plan_parameters_for(self, plan_name: str) -> set[str]:
        plan_name = str(plan_name or "").strip()
        if not plan_name:
            return set()
        cached = self._plan_param_cache.get(plan_name)
        if cached is not None:
            return cached
        controller = self._controller
        if controller is None:
            params: set[str] = set()
        else:
            try:
                names = controller.get_plan_parameters_names(name=plan_name)
            except Exception:
                names = []
            params = {str(name) for name in names if isinstance(name, str)}
        self._plan_param_cache[plan_name] = params
        return params

    def _extract_plan_name(self, item: Mapping[str, Any]) -> str:
        direct = item.get("name") if isinstance(item, Mapping) else None
        if direct:
            return str(direct)
        extracted = extract_item_field(item, "name") if isinstance(item, Mapping) else None
        return str(extracted or "")

    def _configure_queue_table(self, minimum_section_size: float = 90) -> None:
        header = self._queue_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Stretch)
        vertical_header = self._queue_table.verticalHeader()
        vertical_header.setSectionResizeMode(QHeaderView.Stretch)
        for index, spec in enumerate(self._columns):
            header_item = self._queue_table.horizontalHeaderItem(index)
            if header_item is None:
                header_item = QTableWidgetItem(spec.label)
                self._queue_table.setHorizontalHeaderItem(index, header_item)
            else:
                header_item.setText(spec.label)
                
        header.setMinimumSectionSize(minimum_section_size)
        header.setSectionResizeMode(QHeaderView.Interactive)

    def _ensure_columns(self, queue: Sequence[Mapping[str, Any]]) -> None:
        required: list[QueueColumnSpec] = []
        seen: set[str] = set()

        def add(column_id: str, label: str) -> None:
            if not column_id or column_id in seen:
                return
            seen.add(column_id)
            required.append(QueueColumnSpec(column_id, label, True))

        # Base plan name column
        add("status", "Status")
        add("name", "Plan")
        add("scan_ids", "Scan ID")

        # ROI mapped columns in declared order
        for key in self._roi_key_map.keys():
            if key == "title":
                label = "Comments"
            else:   
                label = key.replace("_", " ").title()
            add(key, label)

        # Dynamically add kwargs keys not already covered by ROI aliases
        for item in queue:
            if not isinstance(item, Mapping):
                continue
            kwargs_sources: list[Mapping[str, Any]] = []
            kwargs = item.get("kwargs")
            if isinstance(kwargs, Mapping):
                kwargs_sources.append(kwargs)
            nested_item = item.get("item")
            if isinstance(nested_item, Mapping):
                nested_kwargs = nested_item.get("kwargs")
                if isinstance(nested_kwargs, Mapping):
                    kwargs_sources.append(nested_kwargs)
            for mapping in kwargs_sources:
                for key in mapping.keys():
                    if key in self._roi_value_aliases:
                        continue
                    label = str(key).replace("_", " ").title()
                    key_str = str(key)
                    add(key_str, label)

        if len(required) != len(self._columns) or any(a.column_id != b.column_id for a, b in zip(required, self._columns)):
            self._columns = required
            self._queue_table.setColumnCount(len(self._columns))
            self._configure_queue_table()

    def _format_queue_value(
        self,
        column_id: str,
        item: Mapping[str, Any],
        row_index: int,
    ) -> str:
        text, _ = resolve_queue_value(
            column_id,
            item,
            row_index,
            roi_key_map=self._roi_key_map,
            roi_value_aliases=self._roi_value_aliases,
        )
        return text

    def _get_row_values(self, row: int) -> dict[str, str]:
        table = self._queue_table
        if row < 0 or row >= table.rowCount():
            return {}

        values: dict[str, str] = {}
        for column_index, spec in enumerate(self._columns):
            item = table.item(row, column_index)
            key = spec.column_id
            if item is not None:
                kw_key = item.data(QUEUE_ITEM_KWARG_KEY_ROLE)
                if isinstance(kw_key, str) and kw_key:
                    key = kw_key
            values[key] = item.text() if item is not None else ""
        return values

    def _set_status_message(self, message: Optional[str]) -> None:
        text = "" if message is None else str(message)
        self._status_label.setText(text)

    @staticmethod
    def _prepare_display_item(item: Mapping[str, Any] | Any, *, completed: bool = False) -> dict[str, Any]:
        return prepare_display_item(item, completed=completed)
