"""Bluesky QServer monitoring widget."""

from __future__ import annotations

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

from ..core.qserver_controller import QServerController, QueueSnapshot


@dataclass(frozen=True)
class QueueColumnSpec:
    column_id: str
    label: str
    stretch: bool = False


class QServerMonitorWidget(QWidget):
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
        self._roi_value_aliases = {alias for values in self._roi_key_map.values() for alias in values}
        self._user_columns = self._normalize_user_columns(columns)
        self._columns: list[QueueColumnSpec] = []
        self._pending_items: list[dict[str, Any]] = []
        self._completed_items: list[dict[str, Any]] = []

        self._queue_table = QTableWidget(0, 0)
        self._queue_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._queue_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._queue_table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._queue_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._queue_table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._configure_queue_table()

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
        if controller is None:
            return
        controller.queueUpdated.connect(self._handle_queue_updated)
        snapshot = controller.fetch_snapshot()
        if snapshot:
            self._apply_snapshot(snapshot)

    # ------------------------------------------------------------------
    # Snapshot/application helpers

    def _handle_queue_updated(self, snapshot: QueueSnapshot) -> None:
        self._apply_snapshot(snapshot)

    def _apply_snapshot(self, snapshot: QueueSnapshot) -> None:
        self.update_completed(snapshot.completed or [])
        self.update_queue(snapshot.pending or [])
        self.update_active(snapshot.running, snapshot.progress)

    # ------------------------------------------------------------------
    # View helpers

    def update_queue(self, queue: Sequence[Mapping[str, Any]]) -> None:
        self._pending_items = [self._prepare_display_item(item) for item in queue]
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

    def _refresh_queue_table(self) -> None:
        all_items: list[Mapping[str, Any]] = [*self._pending_items, *self._completed_items]
        self._ensure_columns(all_items)
        self._queue_table.setRowCount(len(all_items))
        for row, item in enumerate(all_items):
            for column_index, spec in enumerate(self._columns):
                value = self._format_queue_value(spec.column_id, item, row)
                self._queue_table.setItem(row, column_index, QTableWidgetItem(value))

    def _configure_queue_table(self) -> None:
        header = self._queue_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.Stretch)
        for index, spec in enumerate(self._columns):
            header_item = self._queue_table.horizontalHeaderItem(index)
            if header_item is None:
                header_item = QTableWidgetItem(spec.label)
                self._queue_table.setHorizontalHeaderItem(index, header_item)
            else:
                header_item.setText(spec.label)

    def _ensure_columns(self, queue: Sequence[Mapping[str, Any]]) -> None:
        required: list[QueueColumnSpec] = []
        seen: set[str] = set()

        def add(column_id: str, label: str) -> None:
            if not column_id or column_id in seen:
                return
            seen.add(column_id)
            required.append(QueueColumnSpec(column_id, label, True))

        # Base plan name column
        add("name", "Plan")

        # User-specified columns (if any)
        for spec in self._user_columns:
            add(spec.column_id, spec.label)

        # ROI mapped columns in declared order
        for key in self._roi_key_map.keys():
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
                    add(str(key), label)

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
        if column_id == "index":
            return str(row_index + 1)
        if column_id in self._roi_key_map:
            roi_value = self._lookup_roi_value(column_id, item)
            if roi_value is not None:
                return self._format_scalar(roi_value)
        if column_id == "name":
            return str(self._extract_item_field(item, "name") or item.get("name") or "Unknown")
        kwargs = item.get("kwargs") if isinstance(item, Mapping) else None
        if isinstance(kwargs, Mapping) and column_id in kwargs:
            return self._format_scalar(kwargs.get(column_id))
        value = self._extract_item_field(item, column_id)
        if column_id in {"plan", "name"}:
            return str(value or item.get("name") or "Unknown")
        if column_id in {"state", "status"}:
            return str(value or item.get("state") or item.get("status") or "Pending")
        if column_id in {"uid", "item_uid"}:
            uid = value or item.get("item_uid") or item.get("uid")
            return str(uid or "")
        if column_id == "args":
            args = value or item.get("args") or []
            return self._format_sequence(args)
        if column_id == "kwargs":
            kwargs = value or item.get("kwargs") or {}
            if isinstance(kwargs, Mapping):
                return ", ".join(f"{key}={self._format_scalar(val)}" for key, val in kwargs.items())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return self._format_sequence(value)
        if isinstance(value, Mapping):
            return ", ".join(f"{key}={self._format_scalar(val)}" for key, val in value.items())
        if value is None:
            fallback = item.get(column_id)
            if isinstance(fallback, Sequence) and not isinstance(fallback, (str, bytes)):
                return self._format_sequence(fallback)
            if isinstance(fallback, Mapping):
                return ", ".join(f"{key}={self._format_scalar(val)}" for key, val in fallback.items())
            if fallback is None:
                return ""
            return self._format_scalar(fallback)
        return self._format_scalar(value)

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

    @staticmethod
    def _normalize_user_columns(
        columns: Optional[Sequence[Mapping[str, Any]]],
    ) -> list[QueueColumnSpec]:
        if not columns:
            return []
        normalized: list[QueueColumnSpec] = []
        seen: set[str] = set()
        for entry in columns:
            if not isinstance(entry, Mapping):
                continue
            column_id = str(entry.get("id") or "").strip()
            if not column_id or column_id in seen:
                continue
            seen.add(column_id)
            label = str(entry.get("label") or column_id.title())
            normalized.append(QueueColumnSpec(column_id, label, True))
        return normalized

    def _lookup_roi_value(self, column_id: str, item: Mapping[str, Any]) -> Any:
        candidates = self._roi_key_map.get(column_id, [])
        if not candidates:
            return None

        def _check_mapping(mapping: Mapping[str, Any]) -> Any:
            for candidate in candidates:
                if candidate in mapping:
                    value = mapping.get(candidate)
                    if value is not None:
                        return value
            return None

        kwargs = item.get("kwargs")
        if isinstance(kwargs, Mapping):
            value = _check_mapping(kwargs)
            if value is not None:
                return value
        nested_item = item.get("item")
        if isinstance(nested_item, Mapping):
            nested_kwargs = nested_item.get("kwargs")
            if isinstance(nested_kwargs, Mapping):
                value = _check_mapping(nested_kwargs)
                if value is not None:
                    return value

        for candidate in candidates:
            value = self._extract_item_field(item, candidate)
            if value is not None:
                return value

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
