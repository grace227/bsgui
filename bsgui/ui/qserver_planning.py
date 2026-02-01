"""Planning list widget for queue submissions."""

from __future__ import annotations

from typing import Any, Mapping, Optional

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
    planAdded = Signal(dict)


_planning_bus: PlanningSignal | None = None


def get_planning_bus() -> PlanningSignal:
    global _planning_bus
    if _planning_bus is None:
        _planning_bus = PlanningSignal()
    return _planning_bus


def emit_plan_added(payload: Mapping[str, Any]) -> None:
    get_planning_bus().planAdded.emit(dict(payload))


class QServerPlanningWidget(QWidget):
    """Widget that holds planned queue items and can submit them."""

    def __init__(self, *, controller=None, layout: Optional[QVBoxLayout] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._plans: list[dict[str, Any]] = []

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
        self._table.setHorizontalHeaderLabels(["Plan", "Parameters"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

        get_planning_bus().planAdded.connect(self.add_plan_item)

    def set_controller(self, controller) -> None:
        self._controller = controller

    def add_plan_item(self, payload: Mapping[str, Any]) -> None:
        if not isinstance(payload, Mapping):
            return
        plan_name = str(payload.get("name") or "Unknown")
        kwargs = payload.get("kwargs")
        params_text = self._format_params(kwargs)

        self._plans.append(dict(payload))
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(plan_name))
        self._table.setItem(row, 1, QTableWidgetItem(params_text))

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
                api.item_add(self._plans[row])
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
                self._table.removeRow(row)
        emit_status(f"Deleted {len(rows)} planned item(s).")

    def _clear_all(self) -> None:
        if not self._plans:
            emit_status("Planning list is empty.")
            return
        count = len(self._plans)
        self._plans.clear()
        self._table.setRowCount(0)
        emit_status(f"Cleared {count} planned item(s).")

    @staticmethod
    def _format_params(kwargs: Any) -> str:
        if not isinstance(kwargs, Mapping):
            return ""
        parts = [f"{key}={format_scalar(value)}" for key, value in kwargs.items()]
        return ", ".join(parts)
