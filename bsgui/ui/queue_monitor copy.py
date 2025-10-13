"""Bluesky QServer monitoring widget."""

from __future__ import annotations

from collections.abc import MutableMapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from PySide6.QtCore import Qt

from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QAbstractItemView, QHeaderView

from .queue_controls import QueueTableCursorController, QUEUE_ITEM_COLUMN_ROLE
from .status_bus import emit_status

QUEUE_ITEM_UID_ROLE = Qt.ItemDataRole.UserRole + 1
QUEUE_ITEM_STATE_ROLE = Qt.ItemDataRole.UserRole + 2
QUEUE_ITEM_STATE_PENDING = "pending"
QUEUE_ITEM_STATE_COMPLETED = "completed"
QUEUE_ITEM_KWARG_KEY_ROLE = Qt.ItemDataRole.UserRole + 4

from ..core.qserver_controller import QServerController, QueueSnapshot


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
        self._roi_key_map = self._normalize_roi_map(roi_key_map)
        self._roi_value_aliases = {
            alias for values in self._roi_key_map.values() for alias in values if alias != "title"
        }

        self._columns: list[QueueColumnSpec] = []
        self._plan_definitions: dict[str, PlanDefinition] = {}
        self._plan_param_cache: dict[str, set[str]] = {}
        self._pending_raw_items: list[dict[str, Any]] = []
        self._pending_items: list[dict[str, Any]] = []
        self._completed_items: list[dict[str, Any]] = []
        self._queue_controls: Optional[QueueTableCursorController] = None
        self._suppress_item_changed = False

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

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Queued Plans"))
        table_container = QScrollArea()
        table_container.setWidgetResizable(True)
        table_container.setWidget(self._queue_table)
        layout.addWidget(table_container)
        layout.addWidget(QLabel("Active Plan"))
        layout.addWidget(self._active_label)
        layout.addWidget(self._progress)
        layout.addWidget(QLabel("Recently Completed"))
        layout.addWidget(self._completed_list)

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
        self._pending_raw_items = [self._clone_item(item) for item in queue]
        self._pending_items = [self._prepare_display_item(item) for item in self._pending_raw_items]
        self._refresh_queue_table()

    def update_active(
        self,
        item: Optional[Mapping[str, Any]],
        progress: Optional[int],
    ) -> None:
        if not isinstance(item, Mapping):
            self._active_label.setText("Idle")
            self._progress.setRange(0, 100)
            self._progress.setValue(0)
            return

        plan_name = self._extract_item_field(item, "name") or "Unknown"
        uid = self._extract_item_field(item, "uid") or self._extract_item_field(item, "item_uid")
        display_uid = f" ({str(uid)[:8]})" if uid else ""
        self._active_label.setText(f"{plan_name}{display_uid}")

        if progress is None:
            self._progress.setRange(0, 0)
        else:
            value = max(0, min(int(progress), 100))
            self._progress.setRange(0, 100)
            self._progress.setValue(value)

    def update_completed(self, completed: Sequence[Mapping[str, Any]]) -> None:
        self._completed_list.clear()
        self._completed_items = [self._prepare_display_item(item, completed=True) for item in completed]
        self._refresh_queue_table()

    # ------------------------------------------------------------------
    # Internal utilities

    def _handle_local_pending_reorder(self, uid: str, target_index: int) -> None:
        if not self._pending_items:
            return

        def resolve_uid(item: Mapping[str, Any]) -> str:
            value = self._extract_item_field(item, "item_uid") or self._extract_item_field(item, "uid")
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
        all_items: list[Mapping[str, Any]] = [*self._pending_items, *self._completed_items]
        self._ensure_columns(all_items)
        self._suppress_item_changed = True
        self._queue_table.blockSignals(True)
        try:
            self._queue_table.setRowCount(len(all_items))
            for row, item in enumerate(all_items):
                if row < len(self._pending_raw_items):
                    raw_reference = self._pending_raw_items[row]
                else:
                    raw_reference = item
                plan_name = self._extract_plan_name(raw_reference)
                param_names = self._plan_parameters_for(plan_name)
                for column_index, spec in enumerate(self._columns):
                    
                    display_value, source_key = self._resolve_queue_value(spec.column_id, item, row, plan_parms=param_names)
                    cell = QTableWidgetItem(display_value)
                    state = (
                        QUEUE_ITEM_STATE_PENDING
                        if row < len(self._pending_items)
                        else QUEUE_ITEM_STATE_COMPLETED
                    )
                    uid = self._extract_item_field(item, "item_uid") or self._extract_item_field(item, "uid") or ""
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


    def _handle_item_changed(self, cell: QTableWidgetItem) -> None:
        if self._suppress_item_changed:
            return
        if self._controller is None:
            return

        row = cell.row()
        column_index = cell.column()
        if row < 0 or column_index < 0:
            return
        if row >= len(self._pending_raw_items):
            # Editing completed or running row: revert.
            self._restore_cell_from_cache(cell, row, column_index)
            emit_status("Only queued plans can be edited.")
            return
        if column_index >= len(self._columns):
            return

        column_id = cell.data(QUEUE_ITEM_COLUMN_ROLE)
        if not isinstance(column_id, str):
            column_id = self._columns[column_index].column_id

        row_values = self._get_row_values(row)
        plan_name = row_values.get("name", "")
        source_key = cell.data(QUEUE_ITEM_KWARG_KEY_ROLE)
        target_key = source_key if isinstance(source_key, str) and source_key else column_id
        raw_item = self._pending_raw_items[row]
        if not isinstance(raw_item, MutableMapping):
            emit_status("Unable to edit this entry.")
            self._restore_cell_from_cache(cell, row, column_index)
            return

        previous_raw = deepcopy(raw_item)
        previous_display = self._pending_items[row]
        old_text = self._format_queue_value(column_id, previous_display, row)
        new_text = cell.text()
        if new_text == old_text:
            return

        if not self._apply_item_edit(raw_item, target_key, new_text, plan_name=plan_name):
            emit_status(f"Cannot edit column '{column_id}'.")
            self._pending_raw_items[row] = previous_raw
            self._restore_cell_from_value(cell, old_text)
            return

        # Update cached display version
        self._pending_items[row] = self._prepare_display_item(raw_item)

        api = getattr(self._controller, "_api", None)
        if api is None:
            emit_status("Queue controller unavailable.")
            self._pending_raw_items[row] = previous_raw
            self._pending_items[row] = previous_display
            self._restore_cell_from_value(cell, old_text)
            return

        row_values = self._get_row_values(row)
        row_kwargs = {k:v for k, v in row_values.items() if not (isinstance(v, str) and v.strip() == "")}
        row_kwargs.pop("status", None)
        row_kwargs.pop("name", None)
        mod_row = deepcopy(raw_item)
        mod_row["kwargs"] = row_kwargs

        update_fn = getattr(api, "item_update", None) or getattr(api, "queue_item_update", None)
        try:
            if update_fn is None:
                raise AttributeError("item_update")
            print(mod_row)
            response = update_fn(item=mod_row, replace=False)
        except Exception:
            emit_status("Failed to submit queue item update.")
            self._pending_raw_items[row] = previous_raw
            self._pending_items[row] = previous_display
            self._restore_cell_from_value(cell, old_text)
            return

        message = "Queue item updated."
        success = True
        if isinstance(response, Mapping):
            success = bool(response.get("success", False))
            message = response.get("msg", message) or message

        if not success:
            emit_status(message or "Queue item update rejected.")
            self._pending_raw_items[row] = previous_raw
            self._pending_items[row] = previous_display
            self._restore_cell_from_value(cell, old_text)
            return

        emit_status(message)
        self._refresh_queue_table()

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

    def _apply_item_edit(
        self,
        item: MutableMapping[str, Any],
        column_id: str,
        text_value: str,
        *,
        plan_name: str,
    ) -> bool:
        value = self._coerce_for_key(plan_name, column_id, text_value)

        if self._set_if_exists(item, column_id, value):
            return True

        kwargs = item.get("kwargs")
        if isinstance(kwargs, MutableMapping) and column_id in kwargs:
            kwargs[column_id] = value
            return True

        if column_id in self._roi_key_map:
            aliases = self._roi_key_map[column_id]
            for alias in aliases:
                if alias == column_id:
                    continue
                if self._apply_item_edit(item, alias, text_value, plan_name=plan_name):
                    return True
            if aliases:
                container = self._ensure_kwargs_container(item)
                alias = aliases[0]
                container[alias] = self._coerce_for_key(plan_name, alias, text_value)
                return True
            return False

        for nested_key in ("item", "metadata", "result"):
            nested = item.get(nested_key)
            if isinstance(nested, MutableMapping) and self._apply_item_edit(
                nested,
                column_id,
                text_value,
                plan_name=plan_name,
            ):
                return True

        container = self._ensure_kwargs_container(item)
        container[column_id] = value
        return True

    @staticmethod
    def _set_if_exists(mapping: MutableMapping[str, Any], key: str, value: Any) -> bool:
        if key in mapping:
            mapping[key] = value
            return True
        return False

    @staticmethod
    def _ensure_kwargs_container(item: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
        kwargs = item.get("kwargs")
        if not isinstance(kwargs, MutableMapping):
            kwargs = {}
            item["kwargs"] = kwargs
        return kwargs

    def _coerce_for_key(self, plan_name: str, key: str, text_value: str) -> Any:
        definition = self._plan_definitions.get(plan_name)
        if definition is not None:
            for parameter in definition.parameters:
                if parameter.name == key:
                    return parameter.coerce(text_value)
        return text_value

    def _extract_plan_name(self, item: Mapping[str, Any]) -> str:
        if isinstance(item, Mapping):
            direct = item.get("name")
            if direct:
                return str(direct)
        extracted = self._extract_item_field(item, "name") if isinstance(item, Mapping) else None
        return str(extracted or "")

    @staticmethod
    def _clone_item(item: Any) -> dict[str, Any]:
        if isinstance(item, MutableMapping):
            return deepcopy(item)
        return {"name": str(item)}

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

    def _resolve_queue_value(
        self,
        column_id: str,
        item: Mapping[str, Any],
        row_index: int,
        plan_parms: list[str] = []
    ) -> tuple[str, Optional[str]]:
        if column_id == "index":
            return str(row_index + 1), None
        if column_id in self._roi_key_map:
            roi_value = self._lookup_roi_value(column_id, item, include_key=True, plan_parms=plan_parms)
            # if item.get("state", None) is None:
            #     print(f"item: {item}")
            #     print(f"roi_value: {roi_value}")
            if roi_value is not None:
                value, key = roi_value
                return self._format_scalar(value), key or column_id
        if column_id == "name":
            value = self._extract_item_field(item, "name") or item.get("name") or "Unknown"
            return str(value), "name"
        kwargs = item.get("kwargs") if isinstance(item, Mapping) else None
        if isinstance(kwargs, Mapping) and column_id in kwargs:
            return self._format_scalar(kwargs.get(column_id)), column_id
        value = self._extract_item_field(item, column_id)
        if column_id in {"plan", "name"}:
            return str(value or item.get("name") or "Unknown"), column_id
        if column_id in {"state", "status"}:
            return str(value or item.get("state") or item.get("status") or "Pending"), column_id
        if column_id in {"uid", "item_uid"}:
            uid = value or item.get("item_uid") or item.get("uid")
            return str(uid or ""), column_id
        if column_id == "args":
            args = value or item.get("args") or []
            return self._format_sequence(args), column_id
        if column_id == "kwargs":
            kwargs = value or item.get("kwargs") or {}
            if isinstance(kwargs, Mapping):
                text = ", ".join(f"{key}={self._format_scalar(val)}" for key, val in kwargs.items())
                return text, None
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return self._format_sequence(value), column_id
        if isinstance(value, Mapping):
            text = ", ".join(f"{key}={self._format_scalar(val)}" for key, val in value.items())
            return text, column_id
        if value is None:
            fallback = item.get(column_id)
            if isinstance(fallback, Sequence) and not isinstance(fallback, (str, bytes)):
                return self._format_sequence(fallback), column_id
            if isinstance(fallback, Mapping):
                text = ", ".join(f"{key}={self._format_scalar(val)}" for key, val in fallback.items())
                return text, column_id
            if fallback is None:
                return "", column_id
            return self._format_scalar(fallback), column_id
        return self._format_scalar(value), column_id

    def _format_queue_value(
        self,
        column_id: str,
        item: Mapping[str, Any],
        row_index: int,
    ) -> str:
        text, _ = self._resolve_queue_value(column_id, item, row_index)
        return text

    @staticmethod
    def _format_scalar(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    @staticmethod
    def _format_sequence(value: Iterable[Any]) -> str:
        return ", ".join(str(entry) for entry in value)

    @staticmethod
    def _extract_item_field(item: Mapping[str, Any], key: str) -> Any:
        if not isinstance(item, Mapping):
            return None

        sentinel = object()
        key_parts = key.split(".") if isinstance(key, str) and "." in key else [key]

        def resolve(mapping: Mapping[str, Any]) -> Any:
            current: Any = mapping
            for part in key_parts:
                if isinstance(current, Mapping):
                    if part in current:
                        current = current.get(part)
                    else:
                        return sentinel
                elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
                    next_value = sentinel
                    for entry in current:
                        if isinstance(entry, Mapping) and part in entry:
                            next_value = entry.get(part)
                            break
                    if next_value is sentinel:
                        return sentinel
                    current = next_value
                else:
                    return sentinel
            return current

        for candidate in (
            item,
            item.get("kwargs"),
            item.get("result"),
            item.get("metadata"),
            item.get("item"),
        ):
            if isinstance(candidate, Mapping):
                value = resolve(candidate)
                if value is not sentinel:
                    return value

        return None

    @staticmethod
    def _normalize_roi_map(
        roi_key_map: Optional[Mapping[str, Sequence[str]]],
    ) -> dict[str, list[str]]:
        normalized: dict[str, list[str]] = {}
        if not isinstance(roi_key_map, Mapping):
            return normalized
        for key, values in roi_key_map.items():
            if not isinstance(key, str):
                continue
            if isinstance(values, str):
                normalized[key] = [values]
            elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                collected = [str(value) for value in values if isinstance(value, str)]
                if collected:
                    normalized[key] = collected
        return normalized

    def _lookup_roi_value(
        self,
        column_id: str,
        item: Mapping[str, Any],
        *,
        include_key: bool = False,
        plan_parms: list[str] = [],
    ) -> Any:
        candidates = self._roi_key_map.get(column_id, [])
        if not candidates:
            return None

        def _check_mapping(mapping: Mapping[str, Any]) -> Any:
            for candidate in candidates:
                if candidate in mapping:
                    value = mapping.get(candidate)
                    if value is not None:
                        return (value, candidate) if include_key else value
            for candidate in candidates:
                if candidate in plan_parms:
                    return (None, candidate) if include_key else None
            return None

        kwargs = item.get("kwargs")
        if isinstance(kwargs, Mapping):
            value = _check_mapping(kwargs)
            if value is not None:
                return value

        # nested_item = item.get("item")
        # if isinstance(nested_item, Mapping):
        #     nested_kwargs = nested_item.get("kwargs")
        #     if isinstance(nested_kwargs, Mapping):
        #         value = _check_mapping(nested_kwargs)
        #         if value is not None:
        #             return value

        # for candidate in candidates:
        #     value = self._extract_item_field(item, candidate)
        #     if value is not None:
        #         return (value, candidate) if include_key else value

        return None

    @staticmethod
    def _prepare_display_item(item: Mapping[str, Any] | Any, *, completed: bool = False) -> dict[str, Any]:
        if isinstance(item, Mapping):
            normalized: dict[str, Any] = dict(item)
        else:
            normalized = {"name": str(item)}

        nested_item = normalized.get("item")
        if isinstance(nested_item, Mapping):
            nested = dict(nested_item)
            normalized["item"] = nested
            normalized.setdefault("name", nested.get("name"))
            if "kwargs" not in normalized and isinstance(nested.get("kwargs"), Mapping):
                normalized["kwargs"] = dict(nested["kwargs"])

        kwargs = normalized.get("kwargs")
        if isinstance(kwargs, Mapping):
            normalized["kwargs"] = dict(kwargs)

        if completed:
            nested = normalized.get("item")
            if isinstance(nested, Mapping):
                exit_status_from_item = nested.get("exit_status")
                status_from_item = nested.get("status")
                if exit_status_from_item:
                    normalized["exit_status"] = exit_status_from_item
                if status_from_item:
                    normalized.setdefault("status", status_from_item)

            result = normalized.get("result")
            if isinstance(result, Mapping):
                status_from_result = result.get("status") or result.get("state")
                exit_status_from_result = result.get("exit_status")
                if status_from_result:
                    normalized["status"] = status_from_result
                if exit_status_from_result and "exit_status" not in normalized:
                    normalized["exit_status"] = exit_status_from_result

            normalized.setdefault("status", "completed")
            normalized.setdefault("state", normalized.get("status"))

        normalized.setdefault("name", "Unknown")
        return normalized
