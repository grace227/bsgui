"""Plan editor widget that queries Bluesky QServer for available plans."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence

from PySide6.QtCore import QObject, Qt, QThread, Signal
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
from ..core.batch_generation import (
    apply_roi_payload_to_kwargs,
    build_iteration_values,
    execute_iteration_action,
    normalize_iteration_actions,
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
    read_parameter_row_value,
    read_parameter_editor_text,
    set_parameter_editor_enabled,
    set_parameter_editor_text,
)

if TYPE_CHECKING:  # pragma: no cover - typing helper
    from ..core.qserver_controller import QServerController


class BatchGenerationWorker(QObject):
    itemGenerated = Signal(dict, str, dict, dict)
    failed = Signal(str)
    finished = Signal(int)

    def __init__(
        self,
        *,
        api: object,
        plan_name: str,
        iterate_variable: str,
        iterate_values: Sequence[float],
        base_kwargs: Mapping[str, object],
        parameter_types: Mapping[str, str],
        roi_key_map: Mapping[str, Sequence[str]],
        iteration_action: Optional[Mapping[str, object]] = None,
        sync_inputs: Optional[Mapping[str, object]] = None,
    ) -> None:
        super().__init__()
        self._api = api
        self._plan_name = plan_name
        self._iterate_variable = iterate_variable
        self._iterate_values = list(iterate_values)
        self._base_kwargs = dict(base_kwargs)
        self._parameter_types = {str(key): str(value) for key, value in parameter_types.items()}
        self._roi_key_map = roi_key_map
        self._iteration_action = iteration_action
        self._sync_inputs = dict(sync_inputs or {})

    def run(self) -> None:
        generated = 0
        total = len(self._iterate_values)
        for iterate_value in self._iterate_values:
            queue_item = {
                "item_type": "plan",
                "name": self._plan_name,
                "kwargs": dict(self._base_kwargs),
            }
            queue_item["kwargs"][self._iterate_variable] = self._format_iterate_value(
                queue_item["kwargs"].get(self._iterate_variable),
                iterate_value,
            )
            payload: Dict[str, object] = {}

            if self._iteration_action:
                try:
                    payload = execute_iteration_action(
                        self._api,
                        self._iteration_action,
                        sync_inputs=self._sync_inputs,
                        iterate_value=iterate_value,
                    )
                except Exception as exc:
                    self.failed.emit(
                        f"Batch generation stopped while processing "
                        f"{self._iterate_variable}={iterate_value}: {exc}"
                    )
                    return

                result_target = self._iteration_action.get("result_target", "parameters")
                if result_target in {"parameters", "both"}:
                    apply_roi_payload_to_kwargs(
                        queue_item["kwargs"],
                        payload,
                        self._roi_key_map,
                        allowed_parameters=self._parameter_types.keys(),
                    )
                sync_input_map = self._iteration_action.get("sync_input_map")
                if isinstance(sync_input_map, Mapping):
                    for target, source in sync_input_map.items():
                        if isinstance(target, str) and isinstance(source, str) and source in payload:
                            self._sync_inputs[target] = payload[source]

            generated += 1
            status = (
                f"Added batch plan {generated}/{total}: "
                f"{self._plan_name} ({self._iterate_variable}={iterate_value})"
            )
            self.itemGenerated.emit(
                queue_item,
                status,
                payload,
                dict(self._sync_inputs),
            )

        self.finished.emit(generated)

    def _format_iterate_value(self, base_value: object, iterate_value: object) -> object:
        type_name = self._parameter_types.get(self._iterate_variable, "").lower()
        if type_name == "str":
            prefix = "" if base_value is None else str(base_value)
            return f"{prefix}{iterate_value}"
        return iterate_value

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
        self._kind_parameter_configs = kind_overrides if isinstance(kind_overrides, Mapping) else {}
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
        self._batch_thread: Optional[QThread] = None
        self._batch_worker: Optional[BatchGenerationWorker] = None

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
        self._batch_button.clicked.connect(self._emit_batch_generation)
        self._add_button = QPushButton("Add to Queue")
        self._add_button.clicked.connect(self._emit_submission)
        self._planning_button = QPushButton("Planning")
        self._planning_button.clicked.connect(self._emit_planning)
        self._reset_button = QPushButton("Reset")
        self._reset_button.clicked.connect(self._reset_parameters)

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
        
    def _populate_parameters(self, *, use_latest_kwargs: bool = True) -> None:
        definition = self.current_plan()
        if definition is None:
            self._parameter_table.setRowCount(0)
            return

        extras = self._extra_parameters.get(self._current_kind, [])
        parameters = list(extras) + list(definition.parameters)
        latest_kwargs = self._get_latest_plan_kwargs(definition.name) if use_latest_kwargs else {}

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
            editor = self._create_parameter_editor(parameter, definition, default_label)

            def handle_toggle(checked: bool, editor=editor, text=default_text, label=default_label) -> None:
                if checked:
                    set_parameter_editor_enabled(editor, True)
                    if read_parameter_editor_text(editor) == label:
                        set_parameter_editor_text(editor, "" if text == "None" else text)
                else:
                    set_parameter_editor_enabled(editor, False)
                    set_parameter_editor_text(editor, label)
                self._update_eta_display()

            checkbox.toggled.connect(handle_toggle)
            if isinstance(editor, QLineEdit):
                editor.textEdited.connect(lambda _text: self._update_eta_display())
            elif isinstance(editor, QComboBox):
                editor.currentTextChanged.connect(lambda _text: self._update_eta_display())

            layout.addWidget(checkbox)
            layout.addWidget(editor, 1)
            self._parameter_table.setCellWidget(row, 1, container)

            row_data = (checkbox, editor, parameter, default_value, default_label)
            self._parameter_rows[parameter.name] = row_data
            if parameter.name in latest_kwargs and latest_kwargs[parameter.name] is not None:
                apply_parameter_row_value(row_data, latest_kwargs[parameter.name], style="")

        self._update_eta_display()

    def _reset_parameters(self) -> None:
        self._populate_parameters(use_latest_kwargs=False)

    def _build_validator(self, parameter: PlanParameter, line_edit: QLineEdit):
        type_name = infer_parameter_type(parameter)
        return build_type_validator(type_name, line_edit)

    def _create_parameter_editor(
        self,
        parameter: PlanParameter,
        definition: PlanDefinition,
        default_label: str,
    ) -> QLineEdit | QComboBox:
        if self._current_kind == "batch" and parameter.name == "iterate_variable":
            combo = QComboBox()
            combo.setEnabled(False)
            for plan_parameter in definition.parameters:
                combo.addItem(plan_parameter.name)
            if combo.count() > 0:
                combo.setCurrentIndex(0)
            if parameter.description:
                combo.setToolTip(parameter.description)
            return combo

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
        return line_edit

    def _update_eta_display(self) -> None:
        eta = self._get_plan_time()
        if eta is None:
            self._set_status("ETA unavailable", error=True)
        else:
            self._set_status(f"Estimated time: {eta:.2f} seconds", error=False)

    def _get_latest_plan_kwargs(self, plan_name: str) -> Dict[str, object]:
        if self._controller is None:
            return {}
        snapshot = self._controller.fetch_snapshot()
        if snapshot is None:
            return {}

        candidates: list[Mapping[str, object]] = []
        if isinstance(snapshot.running, Mapping):
            candidates.append(snapshot.running)
        candidates.extend(
            item for item in reversed(snapshot.pending or []) if isinstance(item, Mapping)
        )
        candidates.extend(
            item for item in reversed(snapshot.completed or []) if isinstance(item, Mapping)
        )

        for item in candidates:
            if item.get("item_type") != "plan":
                continue
            if item.get("name") != plan_name:
                continue
            kwargs = item.get("kwargs")
            if isinstance(kwargs, Mapping):
                return dict(kwargs)
        return {}

    def _extract_numeric_value(self, row: ParameterRow) -> Optional[float]:
        checkbox, editor, parameter, default_value, default_label = row
        if checkbox.isChecked():
            text = read_parameter_editor_text(editor)
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

        for name, (checkbox, editor, parameter, default_value, default_label) in self._parameter_rows.items():
            expected_type = infer_parameter_type(parameter)
            if checkbox.isChecked():
                value_text = read_parameter_editor_text(editor)
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

    def _emit_batch_generation(self) -> None:
        definition = self.current_plan()
        if definition is None:
            return

        iterate_variable = self._require_batch_value("iterate_variable", expected_type="str")
        start_value = self._require_batch_value("iterate_starting_value", expected_type="float")
        end_value = self._require_batch_value("iterate_ending_value", expected_type="float")
        step_value = self._require_batch_value("iterate_step_size", expected_type="float")
        if iterate_variable is None or start_value is None or end_value is None or step_value is None:
            return

        parameter_names = {parameter.name for parameter in definition.parameters}
        if iterate_variable not in parameter_names:
            self._set_status(
                f"Batch iterate variable '{iterate_variable}' is not a parameter of '{definition.name}'",
                error=True,
            )
            return

        try:
            iterate_values = build_iteration_values(float(start_value), float(end_value), float(step_value))
        except ValueError as exc:
            self._set_status(str(exc), error=True)
            return

        base_kwargs = self._collect_current_plan_kwargs(definition)
        if base_kwargs is None:
            return

        iteration_action = self._get_iteration_action(iterate_variable)
        api = getattr(self._controller, "_api", None) if self._controller is not None else None
        if api is None:
            self._set_status("No controller available to generate batch plans", error=True)
            return

        sync_inputs = self._build_batch_sync_inputs(iteration_action)
        self._start_batch_worker(
            api=api,
            definition=definition,
            iterate_variable=str(iterate_variable),
            iterate_values=iterate_values,
            base_kwargs=base_kwargs,
            parameter_types={parameter.name: infer_parameter_type(parameter) for parameter in definition.parameters},
            iteration_action=iteration_action,
            sync_inputs=sync_inputs,
        )

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

        for name, (checkbox, editor, parameter, default_value, default_label) in self._parameter_rows.items():
            expected_type = infer_parameter_type(parameter)
            if checkbox.isChecked():
                value_text = read_parameter_editor_text(editor)
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

    def _collect_current_plan_kwargs(self, definition: PlanDefinition) -> Optional[Dict[str, object]]:
        queue_kwargs: Dict[str, object] = {}
        parameter_names = {parameter.name for parameter in definition.parameters}
        for name, (checkbox, editor, parameter, default_value, default_label) in self._parameter_rows.items():
            if name not in parameter_names:
                continue
            expected_type = infer_parameter_type(parameter)
            if checkbox.isChecked():
                value_text = read_parameter_editor_text(editor)
                try:
                    value = coerce_parameter_value(parameter, value_text)
                    queue_kwargs[name] = value
                except (ValueError, TypeError):
                    self._set_status(
                        f"Invalid value '{value_text}' for parameter '{name}' (expected {expected_type})",
                        error=True,
                    )
                    return None
        return queue_kwargs

    def _require_batch_value(self, name: str, *, expected_type: str) -> object | None:
        row = self._parameter_rows.get(name)
        if row is None:
            self._set_status(f"Missing batch parameter '{name}'", error=True)
            return None
        value = read_parameter_row_value(row)
        if value is None:
            self._set_status(f"Batch parameter '{name}' is required", error=True)
            return None
        if expected_type == "float":
            try:
                return float(value)
            except (TypeError, ValueError):
                self._set_status(f"Batch parameter '{name}' must be numeric", error=True)
                return None
        return str(value)

    def _get_iteration_action(self, iterate_variable: str) -> Optional[Mapping[str, object]]:
        batch_config = self._kind_parameter_configs.get("batch")
        if not isinstance(batch_config, Sequence) or isinstance(batch_config, (str, bytes)):
            return None
        for entry in batch_config:
            if not isinstance(entry, Mapping):
                continue
            if entry.get("name") != "iterate_variable":
                continue
            actions = normalize_iteration_actions(entry.get("iterate_actions"))
            return actions.get(iterate_variable)
        return None

    def _build_batch_sync_inputs(self, iteration_action: Optional[Mapping[str, object]]) -> Dict[str, object]:
        if not isinstance(iteration_action, Mapping):
            return {}
        sync_inputs: Dict[str, object] = {}
        input_map = iteration_action.get("input_map")
        if not isinstance(input_map, Mapping):
            return sync_inputs
        for source_name in input_map.values():
            if not isinstance(source_name, str) or source_name == "__iterate_value__":
                continue
            value = self._extra_panel.get_sync_input_value(source_name)
            if value is not None:
                sync_inputs[source_name] = value
        return sync_inputs

    def _start_batch_worker(
        self,
        *,
        api: object,
        definition: PlanDefinition,
        iterate_variable: str,
        iterate_values: Sequence[float],
        base_kwargs: Mapping[str, object],
        parameter_types: Mapping[str, str],
        iteration_action: Optional[Mapping[str, object]],
        sync_inputs: Mapping[str, object],
    ) -> None:
        self._cleanup_batch_worker()
        self._batch_button.setEnabled(False)
        self._set_status(f"Generating {len(iterate_values)} batch plan(s) for '{definition.name}'...", error=False)

        self._batch_thread = QThread(self)
        self._batch_worker = BatchGenerationWorker(
            api=api,
            plan_name=definition.name,
            iterate_variable=iterate_variable,
            iterate_values=iterate_values,
            base_kwargs=base_kwargs,
            parameter_types=parameter_types,
            roi_key_map=self._roi_key_map,
            iteration_action=iteration_action,
            sync_inputs=sync_inputs,
        )
        self._batch_worker.moveToThread(self._batch_thread)
        self._batch_thread.started.connect(self._batch_worker.run)
        self._batch_worker.itemGenerated.connect(self._handle_batch_item_generated)
        self._batch_worker.failed.connect(self._handle_batch_failed)
        self._batch_worker.finished.connect(self._handle_batch_finished)
        self._batch_worker.failed.connect(self._batch_thread.quit)
        self._batch_worker.finished.connect(self._batch_thread.quit)
        self._batch_thread.finished.connect(self._cleanup_batch_worker)
        self._batch_thread.start()

    def _handle_batch_item_generated(
        self,
        queue_item: Mapping[str, object],
        status: str,
        payload: Mapping[str, object],
        sync_inputs: Mapping[str, object],
    ) -> None:
        emit_plan_added(queue_item)
        if sync_inputs:
            self._extra_panel.apply_sync_result_to_inputs(sync_inputs)
        self._set_status(status, error=False)

    def _handle_batch_failed(self, message: str) -> None:
        self._batch_button.setEnabled(self._current_kind == "batch")
        self._set_status(message, error=True)

    def _handle_batch_finished(self, generated: int) -> None:
        self._batch_button.setEnabled(self._current_kind == "batch")
        if generated == 0:
            self._set_status("No batch plans generated.", error=True)

    def _cleanup_batch_worker(self) -> None:
        if self._batch_worker is not None:
            self._batch_worker.deleteLater()
            self._batch_worker = None
        if self._batch_thread is not None:
            self._batch_thread.deleteLater()
            self._batch_thread = None

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
