"""Planning list widget for queue submissions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Optional, Sequence

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.queue_item_utils import format_scalar
from .status_bus import emit_status


class PlanningSignal(QObject):
    planAdded = Signal(dict, object)


_planning_bus: PlanningSignal | None = None
PLAN_COLUMN = "Plan"
PARAMETERS_PLACEHOLDER = "Parameters"
PARAMETER_KEY_ROLE = Qt.ItemDataRole.UserRole + 1


def get_planning_bus() -> PlanningSignal:
    global _planning_bus
    if _planning_bus is None:
        _planning_bus = PlanningSignal()
    return _planning_bus


def emit_plan_added(payload: Mapping[str, Any], *, iterate_variable: str | None = None) -> None:
    get_planning_bus().planAdded.emit(dict(payload), iterate_variable)


class QServerPlanningWidget(QWidget):
    """Widget that holds planned queue items and can submit them."""

    def __init__(
        self,
        *,
        controller=None,
        layout: Optional[QVBoxLayout] = None,
        planning_column_order: Optional[Sequence[str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._plans: list[dict[str, Any]] = []
        self._column_keys: list[str] = []
        self._suppress_item_changed = False
        self._planning_column_order = [
            str(key) for key in (planning_column_order or []) if isinstance(key, str)
        ]

        if layout is None:
            layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("Planning List"))
        header.addStretch(1)
        self._submit_button = QPushButton("Add to Queue")
        self._submit_button.clicked.connect(self._submit_selected)
        header.addWidget(self._submit_button)
        self._delete_button = QPushButton("Delete Selected Plan")
        self._delete_button.clicked.connect(self._delete_selected)
        header.addWidget(self._delete_button)
        self._clear_button = QPushButton("Clear All Plans")
        self._clear_button.clicked.connect(self._clear_all)
        header.addWidget(self._clear_button)
        layout.addLayout(header)

        self._table = QTableWidget(0, 2, self)
        self._table.setHorizontalHeaderLabels([PLAN_COLUMN, PARAMETERS_PLACEHOLDER])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        self._table.itemChanged.connect(self._handle_item_changed)
        layout.addWidget(self._table)

        get_planning_bus().planAdded.connect(self.add_plan_item)

    def set_controller(self, controller) -> None:
        self._controller = controller

    def add_plan_item(self, payload: Mapping[str, Any], iterate_variable: object = None) -> None:
        if not isinstance(payload, Mapping):
            return
        plan = deepcopy(dict(payload))
        if isinstance(iterate_variable, str) and iterate_variable:
            plan["_bsgui_iterate_variable"] = iterate_variable
        self._plans.append(plan)
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        """Rebuild columns from the union of planned-item keyword arguments."""
        column_keys: list[str] = []
        seen: set[str] = set()
        for plan in self._plans:
            kwargs = plan.get("kwargs")
            if not isinstance(kwargs, Mapping):
                kwargs = {}
            for key in kwargs:
                key = str(key)
                if key not in seen:
                    seen.add(key)
                    column_keys.append(key)

        ordered_keys: list[str] = []
        seen_ordered: set[str] = set()
        iterate_variables = [
            str(plan["_bsgui_iterate_variable"])
            for plan in self._plans
            if isinstance(plan.get("_bsgui_iterate_variable"), str)
        ]
        for configured_key in self._planning_column_order:
            keys = iterate_variables if configured_key == "{iterate_variable}" else [configured_key]
            for key in keys:
                if key in seen_ordered or key not in column_keys:
                    continue
                seen_ordered.add(key)
                ordered_keys.append(key)
        ordered_keys.extend(key for key in column_keys if key not in seen_ordered)

        self._column_keys = ordered_keys
        headers = [PLAN_COLUMN, *ordered_keys] if ordered_keys else [PLAN_COLUMN, PARAMETERS_PLACEHOLDER]

        self._suppress_item_changed = True
        self._table.blockSignals(True)
        try:
            self._table.setColumnCount(len(headers))
            self._table.setHorizontalHeaderLabels(headers)
            self._table.setRowCount(len(self._plans))
            for row, plan in enumerate(self._plans):
                plan_item = QTableWidgetItem(str(plan.get("name") or "Unknown"))
                plan_item.setFlags(plan_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, 0, plan_item)

                kwargs = plan.get("kwargs")
                kwargs = kwargs if isinstance(kwargs, Mapping) else {}
                for column, key in enumerate(ordered_keys, start=1):
                    value = kwargs.get(key)
                    cell = QTableWidgetItem("" if value is None else format_scalar(value))
                    cell.setData(PARAMETER_KEY_ROLE, key)
                    self._table.setItem(row, column, cell)
        finally:
            self._table.blockSignals(False)
            self._suppress_item_changed = False

        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setStretchLastSection(True)

    def _handle_item_changed(self, cell: QTableWidgetItem) -> None:
        if self._suppress_item_changed or cell.column() == 0:
            return
        row = cell.row()
        if row < 0 or row >= len(self._plans):
            return
        key = cell.data(PARAMETER_KEY_ROLE)
        if not isinstance(key, str):
            return

        plan = self._plans[row]
        kwargs = plan.setdefault("kwargs", {})
        if not isinstance(kwargs, dict):
            kwargs = dict(kwargs) if isinstance(kwargs, Mapping) else {}
            plan["kwargs"] = kwargs

        previous_value = kwargs.get(key)
        old_value = previous_value if previous_value is not None else self._infer_column_type(key, row)
        try:
            value = self._coerce_value(cell.text(), old_value)
            if value is None:
                kwargs.pop(key, None)
            else:
                kwargs[key] = value
        except ValueError as exc:
            self._suppress_item_changed = True
            self._table.blockSignals(True)
            try:
                cell.setText("" if previous_value is None else format_scalar(previous_value))
            finally:
                self._table.blockSignals(False)
                self._suppress_item_changed = False
            emit_status(f"Invalid value for {key}: {exc}")

    def _infer_column_type(self, key: str, row: int) -> Any:
        """Use another planned row to infer the type of a blank cell."""
        for index, plan in enumerate(self._plans):
            if index == row:
                continue
            kwargs = plan.get("kwargs")
            if isinstance(kwargs, Mapping) and kwargs.get(key) is not None:
                return kwargs[key]
        return None

    @staticmethod
    def _coerce_value(text: str, original: Any) -> Any:
        value = text.strip()
        if value == "":
            return None
        if isinstance(original, bool):
            normalized = value.lower()
            if normalized in {"true", "1", "yes", "y", "on"}:
                return True
            if normalized in {"false", "0", "no", "n", "off"}:
                return False
            raise ValueError("expected true or false")
        if isinstance(original, int) and not isinstance(original, bool):
            return int(value)
        if isinstance(original, float):
            return float(value)
        if isinstance(original, str) or original is None:
            return value
        return value

    def _submit_selected(self) -> None:
        if not self._plans:
            emit_status("Planning list is empty.")
            return
        controller = self._controller
        api = getattr(controller, "_api", None) if controller else None
        if api is None:
            emit_status("Queue controller unavailable.")
            return

        rows = {index.row() for index in self._table.selectionModel().selectedRows()} if self._table.selectionModel() else set()
        if not rows:
            emit_status("No planned rows selected.")
            return

        for row in sorted(rows):
            if row < 0 or row >= len(self._plans):
                continue
            try:
                payload = {
                    key: deepcopy(value)
                    for key, value in self._plans[row].items()
                    if key not in {"_bsgui_iterate_variable", "_bsgui_display_kwargs"}
                }
                kwargs = payload.get("kwargs")
                if isinstance(kwargs, Mapping):
                    payload["kwargs"] = {
                        key: deepcopy(value)
                        for key, value in kwargs.items()
                        if value is not None
                    }
                display_kwargs = self._plans[row].get("_bsgui_display_kwargs")
                if isinstance(display_kwargs, Mapping) and display_kwargs:
                    meta = payload.get("meta")
                    meta = deepcopy(meta) if isinstance(meta, Mapping) else {}
                    meta.update(display_kwargs)
                    payload["meta"] = meta
                api.item_add(payload)
            except Exception:
                emit_status("Failed to submit planned queue item.")
                return
        emit_status(f"Submitted {len(rows)} planned item(s) to queue.")

    def _delete_selected(self) -> None:
        if not self._plans:
            emit_status("Planning list is empty.")
            return
        selection = self._table.selectionModel()
        rows = {index.row() for index in selection.selectedRows()} if selection else set()
        if not rows:
            emit_status("No planned rows selected.")
            return

        for row in sorted(rows, reverse=True):
            if 0 <= row < len(self._plans):
                self._plans.pop(row)
        self._rebuild_table()
        emit_status(f"Deleted {len(rows)} planned item(s).")

    def _clear_all(self) -> None:
        if not self._plans:
            emit_status("Planning list is empty.")
            return
        count = len(self._plans)
        self._plans.clear()
        self._column_keys.clear()
        self._rebuild_table()
        emit_status(f"Cleared {count} planned item(s).")
