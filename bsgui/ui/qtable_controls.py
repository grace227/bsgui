"""Interactive helpers for managing queue-table drag and drop."""

from __future__ import annotations

from typing import Callable, Mapping, Optional, Sequence

from PySide6.QtCore import QEvent, QObject, Qt, QRegularExpression
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtGui import QRegularExpressionValidator
from shiboken6 import Shiboken

from ..core.qserver_controller import QServerController
from ..core.queue_item_utils import build_update_payload, format_scalar
from .status_bus import emit_status

QUEUE_ITEM_UID_ROLE = Qt.ItemDataRole.UserRole + 1
QUEUE_ITEM_STATE_ROLE = Qt.ItemDataRole.UserRole + 2
QUEUE_ITEM_COLUMN_ROLE = Qt.ItemDataRole.UserRole + 3
QUEUE_ITEM_STATE_PENDING = "pending"


class QueueTableCursorController(QObject):
    """Attach drag-and-drop helpers to the queue table."""

    def __init__(
        self,
        table: QTableWidget,
        *,
        controller: Optional[QServerController] = None,
        refresh_callback: Optional[Callable[[str, int], None]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent or table)
        self._table = table
        self._controller = controller
        self._refresh_callback = refresh_callback

        self._pending_uids: list[str] = []
        self._pending_row_count = 0
        self._pending_drop_row: Optional[int] = None
        self._pending_drag_uid: Optional[str] = None
        self._drag_enabled = False

        self._configure_table_widget()
        table.viewport().installEventFilter(self)
        table.destroyed.connect(self._handle_table_destroyed)

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

    def has_selection(self) -> bool:
        """Return True if any queue rows are currently selected."""
        return bool(self._gather_selected_rows())

    def selected_row_uids(self, pending_only: bool = True) -> list[str]:
        """Return UIDs for selected rows that correspond to pending items."""
        table = self._table
        if table is None or not Shiboken.isValid(table):
            return []
        rows = self._gather_selected_rows()
        pending_uids: list[str] = []
        for row in sorted(rows):
            uid, state = self._lookup_row_uid_and_state(row)
            if pending_only:
                if uid and state == QUEUE_ITEM_STATE_PENDING:
                    pending_uids.append(uid)
            else:
                pending_uids.append(uid)
        return pending_uids

    # ------------------------------------------------------------------ #
    # Qt hooks

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        table = self._table
        if table is None or not Shiboken.isValid(table):
            return False

        viewport = table.viewport() if Shiboken.isValid(table) else None
        if obj is viewport:
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
        if table is None or not Shiboken.isValid(table):
            return
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setDragDropOverwriteMode(False)
        table.setDropIndicatorShown(True)
        table.setDefaultDropAction(Qt.MoveAction)
        table.viewport().setAcceptDrops(False)
        table.setDragDropMode(QAbstractItemView.NoDragDrop)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.customContextMenuRequested.connect(self._handle_context_menu)

    def _update_drag_state(self) -> None:
        table = self._table
        if table is None or not Shiboken.isValid(table):
            self._drag_enabled = False
            return
        enabled = (
            bool(self._controller)
            and self._pending_row_count > 1
            and all(uid for uid in self._pending_uids)
        )
        if enabled == self._drag_enabled:
            return

        self._drag_enabled = enabled
        mode = QAbstractItemView.InternalMove if enabled else QAbstractItemView.NoDragDrop
        table.setDragDropMode(mode)
        table.viewport().setAcceptDrops(enabled)
        table.setDefaultDropAction(Qt.MoveAction if enabled else Qt.IgnoreAction)

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
            self._table.selectRow(target_row)

        emit_status(message if success else message or "Queue reorder request failed.")
        if success and self._refresh_callback is not None:
            self._refresh_callback(uid, target_row)
        self._reset_pending_state()

    def _capture_pending_drag(self) -> None:
        table = self._table
        if table is None or not Shiboken.isValid(table):
            self._pending_drag_uid = None
            return

        selection = table.selectionModel()
        row: Optional[int] = None
        if selection is not None and selection.hasSelection():
            indexes = selection.selectedRows()
            if indexes:
                # Prefer the most-recently focused row to keep drag/drop predictable
                focused_row = table.currentRow()
                if focused_row is not None and focused_row >= 0:
                    row = focused_row
                else:
                    row = indexes[0].row()
        if row is None:
            row = table.currentRow()

        if row is None or row < 0 or row >= self._pending_row_count:
            self._pending_drag_uid = None
            return

        self._pending_drag_uid = self._lookup_row_uid(row)

    def _derive_drop_row(self, event: QDropEvent) -> Optional[int]:
        table = self._table
        if table is None or not Shiboken.isValid(table):
            return None

        pos = event.position().toPoint()
        index = table.indexAt(pos)
        if index.isValid():
            return index.row()

        row = table.rowAt(pos.y())
        if row >= 0:
            return row

        if pos.y() > table.viewport().rect().bottom():
            row_count = table.rowCount()
            return row_count - 1 if row_count else None
        return None

    def _resolve_drop_row(self) -> Optional[int]:
        if self._pending_row_count == 0:
            return None
        if self._pending_drop_row is None:
            return None
        return max(0, min(self._pending_drop_row, self._pending_row_count - 1))

    def _lookup_row_uid(self, row: int) -> Optional[str]:
        uid, _ = self._lookup_row_uid_and_state(row)
        return uid

    def _lookup_row_uid_and_state(self, row: int) -> tuple[Optional[str], Optional[str]]:
        table = self._table
        if table is None or not Shiboken.isValid(table):
            return None, None
        if row < 0 or row >= table.rowCount():
            return None, None
        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item is None:
                continue
            uid = item.data(QUEUE_ITEM_UID_ROLE)
            state = item.data(QUEUE_ITEM_STATE_ROLE)
            uid_str = str(uid) if isinstance(uid, str) and uid else None
            state_str = str(state) if isinstance(state, str) else None
            if uid_str:
                return uid_str, state_str
        return None, None

    def _current_uid(self) -> Optional[str]:
        table = self._table
        if table is None or not Shiboken.isValid(table):
            return None
        row = table.currentRow()
        if row is None or row < 0:
            return None
        return self._lookup_row_uid(row)

    def _reset_pending_state(self) -> None:
        self._pending_drop_row = None
        self._pending_drag_uid = None

    def _handle_table_destroyed(self) -> None:
        self._table = None
        self._controller = None
        self._pending_uids = []
        self._pending_row_count = 0
        self._pending_drop_row = None
        self._pending_drag_uid = None
        self._drag_enabled = False

    def _handle_context_menu(self, pos) -> None:
        table = self._table
        if table is None or not Shiboken.isValid(table):
            return
        row = table.rowAt(pos.y())
        if row < 0:
            return
        table.setCurrentCell(row, max(0, table.currentColumn()))
        menu = QMenu(table)
        edit_action = menu.addAction("Edit Plan Parameters")
        chosen = menu.exec(table.viewport().mapToGlobal(pos))
        print(f"chosen: {chosen}, edit_action: {edit_action}")
        if chosen == edit_action:
            self._open_parameter_editor(row)

    def _open_parameter_editor(self, row: int) -> None:
        table = self._table
        if table is None or not Shiboken.isValid(table):
            return
        uid, state = self._lookup_row_uid_and_state(row)
        if not uid:
            emit_status("Unable to determine queue item UID.")
            return
        if state != QUEUE_ITEM_STATE_PENDING:
            emit_status("Only queued plans can be edited.")
            return

        controller = self._controller
        api = getattr(controller, "_api", None) if controller else None
        if api is None:
            emit_status("Queue controller unavailable.")
            return

        try:
            raw_item = api.fetch_from_queue_history(uid)
        except Exception:  # pragma: no cover - runtime safeguard
            emit_status("Failed to fetch queue item details.")
            return
        if not isinstance(raw_item, Mapping):
            emit_status("Queue item payload unavailable.")
            return

        plan_name = self._extract_plan_name(raw_item)
        if not plan_name:
            emit_status("Unable to determine plan name.")
            return

        definitions = controller.get_allowed_plan_definitions() if controller else []
        plan_definitions = {definition.name: definition for definition in definitions}
        definition = plan_definitions.get(plan_name)
        if definition is None:
            emit_status(f"No plan definition available for '{plan_name}'.")
            return

        dialog = QueueItemParameterDialog(
            plan_name=plan_name,
            parameters=definition.parameters,
            raw_item=raw_item,
            parent=table,
        )
        if dialog.exec() != QDialog.Accepted:
            return

        row_values = dialog.collect_values()
        try:
            payload = build_update_payload(
                raw_item,
                row_values,
                exclude_keys={"name", "status"},
                plan_definitions=plan_definitions,
                plan_name=plan_name,
            )
        except ValueError as exc:
            emit_status(f"Invalid value: {exc}")
            return

        update_fn = getattr(api, "item_update", None) or getattr(api, "queue_item_update", None)
        if update_fn is None:
            emit_status("Queue API does not support updates.")
            return

        try:
            response = update_fn(item=payload, replace=False)
        except Exception:  # pragma: no cover - runtime safeguard
            emit_status("Failed to submit queue item update.")
            return

        message = "Queue item updated."
        success = True
        if isinstance(response, Mapping):
            success = bool(response.get("success", False))
            message = response.get("msg", message) or message

        emit_status(message if success else message or "Queue item update rejected.")

    @staticmethod
    def _extract_plan_name(item: Mapping[str, object]) -> str:
        direct = item.get("name") if isinstance(item, Mapping) else None
        if isinstance(direct, str) and direct:
            return direct
        nested = item.get("item") if isinstance(item, Mapping) else None
        if isinstance(nested, Mapping):
            nested_name = nested.get("name")
            if isinstance(nested_name, str) and nested_name:
                return nested_name
        return ""

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

    def _gather_selected_rows(self) -> set[int]:
        table = self._table
        if table is None or not Shiboken.isValid(table):
            return set()
        selection = table.selectionModel()
        rows: set[int] = set()
        if selection is not None and selection.hasSelection():
            rows.update(index.row() for index in selection.selectedRows())
        current_row = table.currentRow()
        if current_row is not None and current_row >= 0:
            rows.add(current_row)
        return rows


class QueueItemParameterDialog(QDialog):
    """Edit plan parameters for a queued item."""

    def __init__(
        self,
        *,
        plan_name: str,
        parameters,
        raw_item: Mapping[str, object],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit Parameters: {plan_name}")
        self._parameters = list(parameters)
        self._raw_item = raw_item
        self._value_fields: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Plan: {plan_name}"))

        self._table = QTableWidget(0, 2, self)
        self._table.setHorizontalHeaderLabels(["Parameter", "Value"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

        self._build_rows()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def collect_values(self) -> dict[str, str]:
        return {name: field.text() for name, field in self._value_fields.items()}

    def _build_rows(self) -> None:
        current_values = self._extract_current_values()
        self._table.setRowCount(len(self._parameters))

        for row, parameter in enumerate(self._parameters):
            name_text = parameter.name
            if getattr(parameter, "required", False):
                name_text = f"{name_text} *"
            name_item = QTableWidgetItem(name_text)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if getattr(parameter, "description", None):
                name_item.setToolTip(parameter.description)
            self._table.setItem(row, 0, name_item)

            field = QLineEdit()
            if parameter.name in current_values:
                field.setText(current_values[parameter.name])
            else:
                default_label = self._format_default_label(parameter)
                if default_label:
                    field.setPlaceholderText(default_label)
            if getattr(parameter, "description", None):
                field.setToolTip(parameter.description)
            validator = self._build_validator(parameter, field)
            if validator is not None:
                field.setValidator(validator)

            container = QWidget()
            container_layout = QHBoxLayout(container)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.addWidget(field)
            self._table.setCellWidget(row, 1, container)

            self._value_fields[parameter.name] = field

    def _extract_current_values(self) -> dict[str, str]:
        values: dict[str, str] = {}
        kwargs_sources = []
        direct_kwargs = self._raw_item.get("kwargs")
        if isinstance(direct_kwargs, Mapping):
            kwargs_sources.append(direct_kwargs)
        nested_item = self._raw_item.get("item")
        if isinstance(nested_item, Mapping):
            nested_kwargs = nested_item.get("kwargs")
            if isinstance(nested_kwargs, Mapping):
                kwargs_sources.append(nested_kwargs)

        for parameter in self._parameters:
            found = False
            value = None
            for mapping in kwargs_sources:
                if parameter.name in mapping:
                    value = mapping.get(parameter.name)
                    found = True
                    break
            if found:
                if value is None:
                    values[parameter.name] = "None"
                else:
                    values[parameter.name] = format_scalar(value)
        return values

    @staticmethod
    def _format_default_label(parameter) -> str:
        default = getattr(parameter, "default", None)
        if default is None:
            return ""
        return f"Default: {format_scalar(default)}"

    @staticmethod
    def _build_validator(parameter, field: QLineEdit):
        type_name = (
            parameter.inferred_type().lower()
            if hasattr(parameter, "inferred_type")
            else (parameter.type_name or "str").lower()
        )
        if type_name == "int":
            validator = QIntValidator(field)
            validator.setRange(-2147483648, 2147483647)
            return validator
        if type_name == "float":
            validator = QDoubleValidator(field)
            validator.setNotation(QDoubleValidator.StandardNotation)
            validator.setDecimals(10)
            return validator
        if type_name == "bool":
            regex = QRegularExpression("^(?i)(true|false|1|0|yes|no|on|off|y|n)$")
            return QRegularExpressionValidator(regex, field)
        return None
