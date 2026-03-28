"""Plan editor widget that queries Bluesky QServer for available plans."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGridLayout,
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
from PySide6.QtGui import QDoubleValidator, QIntValidator, QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression
from ..core.qserver_controller import PlanDefinition, PlanParameter
from .qserver_planning import emit_plan_added

if TYPE_CHECKING:  # pragma: no cover - typing helper
    from ..core.qserver_controller import QServerController

OVERHEAD_FACTOR = 3
SYNC_VALUE_STYLE = "color: #2e7d32;"
DEFAULT_DISABLED_STYLE = "color: #666666;"


@dataclass(frozen=True)
class SyncAction:
    text: str
    qserver_function: str
    result_keys: tuple[str, ...] = ()
    transform: Optional[Mapping[str, object]] = None
    parameter_map: Optional[Mapping[str, str]] = None
    input_map: Optional[Mapping[str, str]] = None
    result_target: str = "parameters"
    user_group: str = "root"
    timeout: float = 5.0


@dataclass(frozen=True)
class SyncInputField:
    name: str
    label: str
    editable: bool = False
    type_name: str = "float"
    placeholder: str = ""

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
                self._extra_parameters[kind] = self._convert_extra_parameters(kind_overrides.get(kind, []))
        else:
            for kind in self._kinds:
                self._extra_parameters[kind] = []
        self._selected_dataset: Dict[str, object] | None = None
        self._parameter_rows: Dict[str, tuple[QCheckBox, QLineEdit, PlanParameter, object | None, str]] = {}
        self._roi_key_map = self._normalize_key_map(roi_key_map)
        self._sync_actions = self._normalize_sync_actions(sync_buttons)
        self._sync_input_fields = self._normalize_sync_input_fields(sync_inputs)
        self._sync_input_widgets: Dict[str, QLineEdit] = {}

        self._tabs = QTabWidget()
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

        if self._sync_input_fields:
            sync_input_layout = QGridLayout()
            sync_input_layout.setHorizontalSpacing(6)
            sync_input_layout.setVerticalSpacing(6)
            fields_per_row = max(1, (len(self._sync_input_fields) + 1) // 2)
            for index, field in enumerate(self._sync_input_fields):
                row = index // fields_per_row
                column = (index % fields_per_row) * 2
                sync_input_layout.addWidget(QLabel(field.label), row, column)
                line_edit = QLineEdit()
                line_edit.setPlaceholderText(field.placeholder)
                line_edit.setReadOnly(not field.editable)
                if not field.editable:
                    line_edit.setStyleSheet("color: #666666;")
                validator = self._build_type_validator(field.type_name, line_edit)
                if validator is not None:
                    line_edit.setValidator(validator)
                self._sync_input_widgets[field.name] = line_edit
                sync_input_layout.addWidget(line_edit, row, column + 1)
            layout.addLayout(sync_input_layout)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #666666;")

        if self._sync_actions:
            sync_layout = QHBoxLayout()
            sync_layout.addStretch(1)
            sync_layout.setSpacing(6)
            for action in self._sync_actions:
                button = QPushButton(action.text)
                button.clicked.connect(lambda _checked=False, action=action: self._handle_sync_action(action))
                sync_layout.addWidget(button)
            layout.addLayout(sync_layout)

        layout.addWidget(self._status_label)

        return widget


    # Selection hooks ----------------------------------------------------

    def handle_point_drawn(self, point: Mapping[str, object]) -> None:
        """Record point coordinates emitted from the toolbar."""
        self._apply_roi_to_parameters(point)
        self._set_status("Point applied to plan parameters")

    def handle_roi_drawn(self, roi: Mapping[str, object]) -> None:
        """Receive ROI data emitted from the visualization toolbar."""
        self._apply_roi_to_parameters(roi)
        self._set_status("ROI applied to plan parameters")

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
            default_label = self._format_default_label(default_text)

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

            inferred_type = parameter.inferred_type().lower() if hasattr(parameter, "inferred_type") else (parameter.type_name or "str").lower()
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

    @staticmethod
    def _convert_extra_parameters(config: Any) -> List[PlanParameter]:
        if isinstance(config, Mapping):
            entries = config.get("parameters", [])
        else:
            entries = config
        parameters: List[PlanParameter] = []
        if not isinstance(entries, Iterable):
            return parameters
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            name = entry.get("name")
            if not isinstance(name, str):
                continue
            parameters.append(
                PlanParameter(
                    name=name,
                    default=entry.get("default"),
                    type_name=entry.get("type_name"),
                    required=bool(entry.get("required", False)),
                    description=entry.get("description") if isinstance(entry.get("description"), str) else None,
                )
            )
        return parameters

    @staticmethod
    def _normalize_sync_actions(config: Optional[Sequence[object]]) -> List[SyncAction]:
        actions: List[SyncAction] = []
        if not isinstance(config, Sequence) or isinstance(config, (str, bytes)):
            return actions
        for entry in config:
            if not isinstance(entry, Mapping):
                continue
            text = entry.get("text")
            function_name = entry.get("qserver_function")
            if not isinstance(text, str) or not text.strip():
                continue
            if not isinstance(function_name, str) or not function_name.strip():
                continue
            raw_result_keys = entry.get("result_keys")
            result_keys: tuple[str, ...] = ()
            if isinstance(raw_result_keys, Sequence) and not isinstance(raw_result_keys, (str, bytes)):
                result_keys = tuple(str(item) for item in raw_result_keys if isinstance(item, str))
            transform = entry.get("transform") if isinstance(entry.get("transform"), Mapping) else None
            parameter_map = (
                {str(key): str(value) for key, value in entry.get("parameter_map", {}).items()}
                if isinstance(entry.get("parameter_map"), Mapping)
                else None
            )
            input_map = (
                {str(key): str(value) for key, value in entry.get("input_map", {}).items()}
                if isinstance(entry.get("input_map"), Mapping)
                else None
            )
            result_target = entry.get("result_target") if isinstance(entry.get("result_target"), str) else "parameters"
            user_group = entry.get("user_group") if isinstance(entry.get("user_group"), str) else "root"
            timeout = float(entry.get("timeout", 5.0))
            actions.append(
                SyncAction(
                    text=text.strip(),
                    qserver_function=function_name.strip(),
                    result_keys=result_keys,
                    transform=transform,
                    parameter_map=parameter_map,
                    input_map=input_map,
                    result_target=result_target,
                    user_group=user_group,
                    timeout=timeout,
                )
            )
        return actions

    @staticmethod
    def _normalize_sync_input_fields(config: Optional[Sequence[object]]) -> List[SyncInputField]:
        fields: List[SyncInputField] = []
        if not isinstance(config, Sequence) or isinstance(config, (str, bytes)):
            return fields
        for entry in config:
            if not isinstance(entry, Mapping):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            label = entry.get("label") if isinstance(entry.get("label"), str) else name
            type_name = entry.get("type_name") if isinstance(entry.get("type_name"), str) else "float"
            placeholder = entry.get("placeholder") if isinstance(entry.get("placeholder"), str) else ""
            fields.append(
                SyncInputField(
                    name=name.strip(),
                    label=label.strip(),
                    editable=bool(entry.get("editable", False)),
                    type_name=type_name,
                    placeholder=placeholder,
                )
            )
        return fields

    def _build_validator(self, parameter: PlanParameter, line_edit: QLineEdit):
        type_name = parameter.inferred_type().lower() if hasattr(parameter, 'inferred_type') else (parameter.type_name or 'str').lower()
        return self._build_type_validator(type_name, line_edit)

    @staticmethod
    def _build_type_validator(type_name: str, line_edit: QLineEdit):
        type_name = (type_name or "str").lower()
        if type_name == 'int':
            validator = QIntValidator(line_edit)
            validator.setRange(-2147483648, 2147483647)
            return validator
        if type_name == 'float':
            validator = QDoubleValidator(line_edit)
            validator.setNotation(QDoubleValidator.StandardNotation)
            validator.setDecimals(10)
            return validator
        if type_name == 'bool':
            regex = QRegularExpression('^(?i)(true|false|1|0|yes|no|on|off|y|n)$')
            return QRegularExpressionValidator(regex, line_edit)
        return None

    def _update_eta_display(self) -> None:
        eta = self._get_plan_time()
        if eta is None:
            self._set_status("ETA unavailable", error=True)
        else:
            self._set_status(f"Estimated time: {eta:.2f} seconds", error=False)

    def _extract_numeric_value(self, row: tuple) -> Optional[float]:
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

    @staticmethod
    def _coerce_parameter_value(parameter: PlanParameter, value_text: str) -> object:
        type_name = parameter.inferred_type().lower() if hasattr(parameter, "inferred_type") else (parameter.type_name or "str").lower()
        text = value_text.strip()
        default_value = getattr(parameter, "default", None)
        has_container_default = isinstance(default_value, (list, tuple, dict, set))

        # Parse structured literals before fallback coercion so queue payloads
        # preserve container types instead of being submitted as strings.
        if has_container_default or any(token in type_name for token in ("list", "tuple", "dict", "set", "sequence", "mapping")):
            for parser in (json.loads, ast.literal_eval):
                try:
                    return parser(text)
                except Exception:
                    continue

        return parameter.coerce(value_text)


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
                checkbox, line_edit, parameter, default_value, default_label = row
                checkbox.blockSignals(True)
                checkbox.setChecked(True)
                checkbox.blockSignals(False)
                line_edit.setEnabled(True)
                line_edit.setStyleSheet(SYNC_VALUE_STYLE)
                line_edit.setText(str(value))
        self._update_eta_display()

    def _handle_sync_action(self, action: SyncAction) -> None:
        if self._controller is None:
            self._set_status("No controller available to sync plan parameters", error=True)
            return
        call_kwargs = self._build_sync_call_kwargs(action)
        if call_kwargs is None:
            return
        api = getattr(self._controller, "_api", None)
        sync_method = getattr(api, action.qserver_function, None) if api is not None else None
        if not callable(sync_method):
            self._set_status(
                f"QServer API method '{action.qserver_function}' is not available",
                error=True,
            )
            return
        result = sync_method(timeout=action.timeout, **call_kwargs)
        payload = self._normalize_sync_result(result, action)
        if not payload:
            self._set_status(f"No sync data returned from '{action.qserver_function}'", error=True)
            return
        if action.result_target == "inputs":
            self._apply_sync_result_to_inputs(payload)
        else:
            self._apply_roi_to_parameters(payload)
        self._set_status(f"Updated plan parameters from '{action.text}'")

    def _build_sync_call_kwargs(self, action: SyncAction) -> Optional[Dict[str, object]]:
        kwargs: Dict[str, object] = {}
        if action.input_map:
            for arg_name, source_name in action.input_map.items():
                value = self._read_sync_input_value(source_name)
                if value is None:
                    self._set_status(
                        f"Unable to resolve sync input '{source_name}' for '{action.text}'",
                        error=True,
                    )
                    return None
                kwargs[arg_name] = value
        if action.parameter_map:
            for arg_name, source_name in action.parameter_map.items():
                value = self._resolve_sync_parameter_value(source_name)
                if value is None:
                    self._set_status(
                        f"Unable to resolve sync parameter '{source_name}' for '{action.text}'",
                        error=True,
                    )
                    return None
                kwargs[arg_name] = value
        return kwargs

    def _read_sync_input_value(self, name: str) -> object | None:
        widget = self._sync_input_widgets.get(name)
        if widget is None:
            return None
        value_text = widget.text().strip()
        if not value_text:
            return None
        try:
            return float(value_text)
        except ValueError:
            return value_text

    def _apply_sync_result_to_inputs(self, payload: Mapping[str, object]) -> None:
        for key, value in payload.items():
            widget = self._sync_input_widgets.get(str(key))
            if widget is not None:
                widget.setStyleSheet(SYNC_VALUE_STYLE)
                widget.setText(str(value))

    def _resolve_sync_parameter_value(self, source_name: str) -> object | None:
        row = self._parameter_rows.get(source_name)
        if row is not None:
            return self._read_parameter_row_value(row)

        for alias in self._roi_key_map.get(source_name, []):
            alias_row = self._parameter_rows.get(alias)
            if alias_row is None:
                continue
            return self._read_parameter_row_value(alias_row)
        return None

    def _read_parameter_row_value(
        self,
        row: tuple[QCheckBox, QLineEdit, PlanParameter, object | None, str],
    ) -> object | None:
        checkbox, line_edit, parameter, default_value, default_label = row
        if checkbox.isChecked():
            value_text = line_edit.text().strip()
            if not value_text or value_text == default_label:
                return None
            try:
                return self._coerce_parameter_value(parameter, value_text)
            except (ValueError, TypeError):
                return None
        return default_value

    def _normalize_sync_result(self, result: object, action: SyncAction) -> Dict[str, object]:
        if action.transform:
            return self._apply_sync_transform(result, action.transform)
        if isinstance(result, Mapping):
            return {str(key): value for key, value in result.items()}
        if action.result_keys and isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
            return {
                key: value
                for key, value in zip(action.result_keys, result)
            }
        return {}

    def _apply_sync_transform(self, result: object, transform: Mapping[str, object]) -> Dict[str, object]:
        payload: Dict[str, object] = {}
        for target_key, spec in transform.items():
            value = self._resolve_transform_value(result, spec)
            if value is not None:
                payload[str(target_key)] = value
        return payload

    @staticmethod
    def _resolve_transform_value(result: object, spec: object) -> object | None:
        if isinstance(spec, Mapping):
            source = spec.get("source")
            value = PlanEditorWidget._extract_sync_source_value(result, source)
            if value is None:
                return None
            if isinstance(value, (int, float)):
                scale = spec.get("scale", 1)
                offset = spec.get("offset", 0)
                try:
                    value = value * float(scale) + float(offset)
                except (TypeError, ValueError):
                    return None
                digits = spec.get("round")
                if digits is not None:
                    try:
                        value = round(value, int(digits))
                    except (TypeError, ValueError):
                        return None
            return value
        return PlanEditorWidget._extract_sync_source_value(result, spec)

    @staticmethod
    def _extract_sync_source_value(result: object, source: object) -> object | None:
        if isinstance(source, int):
            if isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
                return result[source] if -len(result) <= source < len(result) else None
            return None
        if isinstance(source, str):
            if isinstance(result, Mapping):
                return result.get(source)
            return None
        return None

    @staticmethod
    def _format_default_label(text: str) -> str:
        display = text if text else "None"
        return f"{display} (default)"

    @staticmethod
    def _normalize_key_map(raw_map: Optional[Mapping[str, object]]) -> Dict[str, List[str]]:
        normalized: Dict[str, List[str]] = {}
        if not isinstance(raw_map, Mapping):
            return normalized
        for key, targets in raw_map.items():
            if isinstance(targets, str):
                normalized[str(key)] = [targets]
            elif isinstance(targets, Iterable) and not isinstance(targets, (str, bytes)):
                collected = [str(item) for item in targets if isinstance(item, str)]
                if collected:
                    normalized[str(key)] = collected
        return normalized

    
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
            expected_type = parameter.inferred_type() if hasattr(parameter, "inferred_type") else (parameter.type_name or "str")
            if checkbox.isChecked():
                value_text = line_edit.text()
                try:
                    value = self._coerce_parameter_value(parameter, value_text)
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
            expected_type = parameter.inferred_type() if hasattr(parameter, 'inferred_type') else (parameter.type_name or 'str')
            if checkbox.isChecked():
                value_text = line_edit.text()
                try:
                    value = self._coerce_parameter_value(parameter, value_text)
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
