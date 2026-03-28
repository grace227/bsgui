"""Plan editor widget that queries Bluesky QServer for available plans."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from ..core.qserver_controller import PlanDefinition, PlanParameter
from .qserver_planning import emit_plan_added
from .plan_editor_extra import PlanEditorExtraPanel
from .plan_editor_utils import (
    DEFAULT_DISABLED_STYLE,
    OVERHEAD_FACTOR,
    ParameterRow,
    apply_parameter_row_value,
    build_type_validator,
    coerce_parameter_value,
    convert_extra_parameters,
    format_default_label,
    infer_parameter_type,
    normalize_key_map,
    normalize_string_map,
)

if TYPE_CHECKING:  # pragma: no cover - typing helper
    from ..core.qserver_controller import QServerController

class PlanEditorWidget(QWidget):
    """Widget for browsing plan definitions and preparing submissions."""

    planSubmitted = Signal(dict)

    def __init__(
        self,
        *,
        controller: Optional["QServerController"] = None,
        kinds: Optional[Sequence[str]] = None,
        kind_overrides: Optional[Mapping[str, Iterable[dict]]] = None,
        roi_key_map: Optional[Mapping[str, object]] = None,
        roi_context_map: Optional[Mapping[str, object]] = None,
        sync_buttons: Optional[Sequence[object]] = None,
        sync_inputs: Optional[Sequence[object]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:

        super().__init__(parent)
        self._controller = controller
        self._kinds = list(kinds) if kinds else ["plan", "instruction"]
        self._definitions: List[PlanDefinition] = []
        self._extra_parameters: Dict[str, List[PlanParameter]] = {}
        if isinstance(kind_overrides, Mapping):
            for kind in self._kinds:
                self._extra_parameters[kind] = convert_extra_parameters(kind_overrides.get(kind, []))
        else:
            for kind in self._kinds:
                self._extra_parameters[kind] = []
        self._selected_dataset: Dict[str, object] | None = None
        self._selected_dataset_values: Dict[str, object] = {}
        self._parameter_rows: Dict[str, ParameterRow] = {}
        self._roi_key_map = normalize_key_map(roi_key_map)
        self._roi_context_map = normalize_string_map(roi_context_map)

        self._tabs = QTabWidget()
        self._extra_panel = PlanEditorExtraPanel(
            controller=self._controller,
            roi_key_map=self._roi_key_map,
            sync_buttons=sync_buttons,
            sync_inputs=sync_inputs,
            parameter_rows_getter=lambda: self._parameter_rows,
            apply_roi_callback=self._apply_roi_to_parameters,
            set_status_callback=self._set_status,
        )
        self._tabs.addTab(self._build_plan_editor_panel(), "Bluesky Plan")

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self._tabs)

    def _build_plan_editor_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Kind selector row
        selector_layout = QHBoxLayout()
        selector_layout.setSpacing(12)
        self._kind_group = QButtonGroup(self)
        self._kind_buttons: Dict[str, QRadioButton] = {}
        for index, kind in enumerate(self._kinds):
            label = kind.replace("_", " ").title()
            button = QRadioButton(label)
            if index == 0:
                button.setChecked(True)
            self._kind_group.addButton(button)
            self._kind_buttons[kind] = button
            selector_layout.addWidget(button)
            button.toggled.connect(lambda checked, kind=kind: self._handle_kind_change(kind) if checked else None)

        selector_layout.addSpacing(12)
        selector_layout.addWidget(QLabel("Available:"))
        self._plan_combo = QComboBox()
        self._plan_combo.currentIndexChanged.connect(self._populate_parameters)
        selector_layout.addWidget(self._plan_combo, stretch=1)

        layout.addLayout(selector_layout)

        # Parameter table
        self._parameter_table = QTableWidget(0, 2)
        self._parameter_table.setHorizontalHeaderLabels(["Parameter", "Value"])
        self._parameter_table.horizontalHeader().setStretchLastSection(True)
        self._parameter_table.verticalHeader().setVisible(False)
        self._parameter_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._parameter_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        layout.addWidget(self._parameter_table, stretch=1)

        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)

        self._batch_button = QPushButton("Batch Generation")
        self._batch_button.setEnabled(False)
        self._add_button = QPushButton("Add to Queue")
        self._add_button.clicked.connect(self._emit_submission)
        self._planning_button = QPushButton("Planning")
        self._planning_button.clicked.connect(self._emit_planning)
        self._reset_button = QPushButton("Reset")
        self._reset_button.clicked.connect(self._populate_parameters)

        for button in [
            self._batch_button,
            self._add_button,
            self._planning_button,
            self._reset_button,
        ]:
            button_layout.addWidget(button)

        layout.addLayout(button_layout)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #666666;")

        layout.addWidget(self._status_label)
        if self._extra_panel.has_content():
            layout.addWidget(self._extra_panel)

        return widget


    # Selection hooks ----------------------------------------------------

    def handle_point_drawn(self, point: Mapping[str, object]) -> None:
        """Record point coordinates emitted from the toolbar."""
        self._apply_roi_to_parameters(self._augment_roi_with_context(point))
        self._set_status("Point applied to plan parameters")

    def handle_roi_drawn(self, roi: Mapping[str, object]) -> None:
        """Receive ROI data emitted from the visualization toolbar."""
        self._apply_roi_to_parameters(self._augment_roi_with_context(roi))
        self._set_status("ROI applied to plan parameters")

    def handle_dataset_changed(self, payload: Mapping[str, object]) -> None:
        self._selected_dataset = dict(payload)
        dataset_values = payload.get("dataset_values")
        if isinstance(dataset_values, Mapping):
            self._selected_dataset_values = dict(dataset_values)
        else:
            self._selected_dataset_values = {}

    def handle_plans_update(self, worker_status: str) -> None:
        if worker_status == "closed" or worker_status == "":
            self._plan_combo.blockSignals(True)
            self._plan_combo.clear()
            self._plan_combo.blockSignals(False)
            self._parameter_table.setRowCount(0)
            self._parameter_rows.clear()
        elif any([worker_status == "idle",
                  worker_status == "executing_plan"]) and self._plan_combo.count() == 0:
            self.refresh_from_controller()

    def refresh_from_controller(self) -> None:
        if self._controller is None:
            return
        definitions = self._controller.get_allowed_plan_definitions(kind=self._current_kind)
        if not definitions:
            return
        self._definitions = definitions
        self._refresh_plan_combo()

    def current_plan(self) -> Optional[PlanDefinition]:
        index = self._plan_combo.currentIndex()
        if index < 0 or index >= len(self._definitions):
            return None
        return self._definitions[index]

    # Internal helpers ---------------------------------------------------

    @property
    def _current_kind(self) -> str:
        for kind, button in self._kind_buttons.items():
            if button.isChecked():
                return kind
        return self._kinds[0]

    def _handle_kind_change(self, kind: str) -> None:
        self._refresh_plan_combo()
        self._refresh_btn_state()

    def _refresh_plan_combo(self) -> None:
        definitions = self._definitions
        self._plan_combo.blockSignals(True)
        self._plan_combo.clear()
        for definition in definitions:
            self._plan_combo.addItem(definition.name, definition)
        self._plan_combo.blockSignals(False)
        if definitions:
            for index, definition in enumerate(definitions):
                tooltip = definition.description or ""
                self._plan_combo.setItemData(index, tooltip, Qt.ItemDataRole.ToolTipRole)
        if definitions:
            self._plan_combo.setCurrentIndex(0)
            self._populate_parameters()
        else:
            self._parameter_table.setRowCount(0)

    def _refresh_btn_state(self) -> None:
        self._set_status(f"Selected add mode: {self._current_kind}")
        if self._current_kind == "single":
            self._batch_button.setEnabled(False)
            self._add_button.setEnabled(True)
        elif self._current_kind == "batch":
            self._batch_button.setEnabled(True)
            self._add_button.setEnabled(False)
        
    def _populate_parameters(self) -> None:
        definition = self.current_plan()
        if definition is None:
            self._parameter_table.setRowCount(0)
            return

        extras = self._extra_parameters.get(self._current_kind, [])
        parameters = list(extras) + list(definition.parameters)

        self._parameter_table.setRowCount(len(parameters))
        self._parameter_rows.clear()

        for row, parameter in enumerate(parameters):
            name_item = QTableWidgetItem(parameter.name)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            if parameter in extras:
                font = name_item.font()
                font.setBold(True)
                name_item.setFont(font)
            if parameter.description:
                name_item.setToolTip(parameter.description)
            self._parameter_table.setItem(row, 0, name_item)

            default_value = parameter.default
            default_text = parameter.default_as_text()
            default_label = format_default_label(default_text)

            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)

            checkbox = QCheckBox()
            line_edit = QLineEdit(default_label)
            line_edit.setEnabled(False)
            line_edit.setStyleSheet(DEFAULT_DISABLED_STYLE)
            if parameter.description:
                line_edit.setToolTip(parameter.description)

            inferred_type = infer_parameter_type(parameter)
            validator = self._build_validator(parameter, line_edit)
            if validator is not None:
                line_edit.setValidator(validator)
            if inferred_type == "bool":
                line_edit.setPlaceholderText("True / False")
            elif inferred_type == "int":
                line_edit.setPlaceholderText("Enter integer")
            elif inferred_type == "float":
                line_edit.setPlaceholderText("Enter number")

            def handle_toggle(checked: bool, le: QLineEdit = line_edit, text=default_text, label=default_label) -> None:
                if checked:
                    le.setEnabled(True)
                    le.setStyleSheet("")
                    if le.text() == label:
                        le.setText("" if text == "None" else text)
                else:
                    le.setEnabled(False)
                    le.setStyleSheet(DEFAULT_DISABLED_STYLE)
                    le.setText(label)
                self._update_eta_display()

            checkbox.toggled.connect(handle_toggle)
            line_edit.textEdited.connect(lambda _text: self._update_eta_display())

            layout.addWidget(checkbox)
            layout.addWidget(line_edit, 1)
            self._parameter_table.setCellWidget(row, 1, container)

            self._parameter_rows[parameter.name] = (checkbox, line_edit, parameter, default_value, default_label)

        self._update_eta_display()

    def _build_validator(self, parameter: PlanParameter, line_edit: QLineEdit):
        type_name = infer_parameter_type(parameter)
        return build_type_validator(type_name, line_edit)

    def _update_eta_display(self) -> None:
        eta = self._get_plan_time()
        if eta is None:
            self._set_status("ETA unavailable", error=True)
        else:
            self._set_status(f"Estimated time: {eta:.2f} seconds", error=False)

    def _extract_numeric_value(self, row: ParameterRow) -> Optional[float]:
        checkbox, line_edit, parameter, default_value, default_label = row
        if checkbox.isChecked():
            text = line_edit.text().strip()
            if not text:
                return None
            try:
                coerced = parameter.coerce(text)
            except (ValueError, TypeError):
                return None
        else:
            coerced = default_value
        if coerced is None:
            return None
        try:
            return float(coerced)
        except (TypeError, ValueError):
            return None

    def _apply_roi_to_parameters(self, roi: Mapping[str, object]) -> None:
        if not self._parameter_rows or not self._roi_key_map:
            return
        for roi_key, value in roi.items():
            targets = self._roi_key_map.get(str(roi_key))
            if not targets:
                continue
            for target_name in targets:
                row = self._parameter_rows.get(target_name)
                if not row:
                    continue
                apply_parameter_row_value(row, value)
        self._update_eta_display()

    def _augment_roi_with_context(self, roi: Mapping[str, object]) -> Dict[str, object]:
        payload = dict(roi)
        if not self._roi_context_map or not self._selected_dataset_values:
            return payload
        for roi_key, dataset_key in self._roi_context_map.items():
            if dataset_key in self._selected_dataset_values:
                payload[roi_key] = self._selected_dataset_values[dataset_key]
        return payload

    
    def _emit_planning(self) -> None:
        definition = self.current_plan()

        if definition is None:
            return

        #TODO: modify the self._get_plan_time() to check against the plan type instead of just the time
        # if self._get_plan_time() is None or self._get_plan_time() <= 0:
        #     self._set_status("Invalid plan time", error=True)
        #     # return

        print(f"definition: {definition}")
        plan_item = {
            "item_type": "plan",
            "name": definition.name,
            "kwargs": {},
        }

        for name, (checkbox, line_edit, parameter, default_value, default_label) in self._parameter_rows.items():
            expected_type = infer_parameter_type(parameter)
            if checkbox.isChecked():
                value_text = line_edit.text()
                try:
                    value = coerce_parameter_value(parameter, value_text)
                    plan_item["kwargs"][name] = value
                except (ValueError, TypeError):
                    self._set_status(
                        f"Invalid value '{value_text}' for parameter '{name}' (expected {expected_type})",
                        error=True,
                    )
                    return

        emit_plan_added(plan_item)
        self._set_status(f"Planned '{definition.name}'.")


    def _emit_submission(self) -> None:
        definition = self.current_plan()

        if definition is None:
            return

        #TODO: modify the self._get_plan_time() to check against the plan type instead of just the time
        # if self._get_plan_time() is None or self._get_plan_time() <= 0:
        #     self._set_status("Invalid plan time", error=True)
        #     return

        queue_item = {
            "item_type": "plan",
            "name": definition.name,
            "kwargs": {},
        }

        for name, (checkbox, line_edit, parameter, default_value, default_label) in self._parameter_rows.items():
            expected_type = infer_parameter_type(parameter)
            if checkbox.isChecked():
                value_text = line_edit.text()
                try:
                    value = coerce_parameter_value(parameter, value_text)
                    queue_item['kwargs'][name] = value
                except (ValueError, TypeError):
                    self._set_status(f"Invalid value '{value_text}' for parameter '{name}' (expected {expected_type})", error=True)
                    return

            else:
                value = default_value

        if self._controller is None:
            self._set_status('No controller available to queue plan', error=True)
            return

        self._controller._api.item_add(queue_item)
        self._set_status(f"Plan '{definition.name}' queued")

    def _set_status(self, message: str, error: bool = False) -> None:
        self._status_label.setText(message)
        color = "#2e7d32" if not error else "#c62828"
        self._status_label.setStyleSheet(f"color: {color};")

    def _get_plan_time(self) -> Optional[float]:
        required = ["width", "height", "stepsize_x", "stepsize_y", "dwell"]
        values: Dict[str, float] = {}
        for key in required:
            targets = self._roi_key_map.get(key, [])
            for target in targets:
                row = self._parameter_rows.get(target)
                if not row:
                    continue
                numeric = self._extract_numeric_value(row)
                if numeric is not None:
                    if key == "dwell" and "ms" in target:
                        numeric /= 1000
                    values[key] = numeric
                    break
        if len(values) != len(required):
            return None

        steps_x = values["stepsize_x"]
        steps_y = values["stepsize_y"]
        width = values["width"]
        height = values["height"]
        dwell = values["dwell"]
        if any(value == 0 for value in [steps_x, steps_y, width, height, dwell]):
            return None

        return (width / steps_x) * (height / steps_y) * dwell * OVERHEAD_FACTOR
