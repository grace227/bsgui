"""Extra controls for plan editor sync fields and actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Mapping, Optional, Sequence

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .plan_editor_utils import (
    DEFAULT_DISABLED_STYLE,
    ParameterRow,
    SYNC_VALUE_STYLE,
    build_type_validator,
    normalize_key_map,
    read_parameter_row_value,
)

if TYPE_CHECKING:  # pragma: no cover - typing helper
    from ..core.qserver_controller import QServerController


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


class PlanEditorExtraPanel(QWidget):
    """Auxiliary sync controls used by instrument-specific plan editors."""

    def __init__(
        self,
        *,
        controller: Optional["QServerController"],
        roi_key_map: Optional[Mapping[str, object]],
        sync_buttons: Optional[Sequence[object]],
        sync_inputs: Optional[Sequence[object]],
        parameter_rows_getter: Callable[[], Dict[str, ParameterRow]],
        apply_roi_callback: Callable[[Mapping[str, object]], None],
        set_status_callback: Callable[[str, bool], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._roi_key_map = normalize_key_map(roi_key_map)
        self._sync_actions = self._normalize_sync_actions(sync_buttons)
        self._sync_input_fields = self._normalize_sync_input_fields(sync_inputs)
        self._parameter_rows_getter = parameter_rows_getter
        self._apply_roi_callback = apply_roi_callback
        self._set_status_callback = set_status_callback
        self._sync_input_widgets: Dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

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
                    line_edit.setStyleSheet(DEFAULT_DISABLED_STYLE)
                validator = build_type_validator(field.type_name, line_edit)
                if validator is not None:
                    line_edit.setValidator(validator)
                self._sync_input_widgets[field.name] = line_edit
                sync_input_layout.addWidget(line_edit, row, column + 1)
            layout.addLayout(sync_input_layout)

        if self._sync_actions:
            sync_layout = QHBoxLayout()
            sync_layout.addStretch(1)
            sync_layout.setSpacing(6)
            for action in self._sync_actions:
                button = QPushButton(action.text)
                button.clicked.connect(lambda _checked=False, action=action: self._handle_sync_action(action))
                sync_layout.addWidget(button)
            layout.addLayout(sync_layout)

    def has_content(self) -> bool:
        return bool(self._sync_actions or self._sync_input_fields)

    @staticmethod
    def _normalize_sync_actions(config: Optional[Sequence[object]]) -> List[SyncAction]:
        actions: List[SyncAction] = []
        if not isinstance(config, Sequence) or isinstance(config, (str, bytes)):
            return actions
        for entry in config:
            action = PlanEditorExtraPanel._normalize_sync_action_entry(entry)
            if action is not None:
                actions.append(action)
        return actions

    @staticmethod
    def _normalize_sync_action_entry(entry: object) -> Optional[SyncAction]:
        if not isinstance(entry, Mapping):
            return None
        text = entry.get("text")
        function_name = entry.get("qserver_function")
        if not isinstance(text, str) or not text.strip():
            return None
        if not isinstance(function_name, str) or not function_name.strip():
            return None
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
        return SyncAction(
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

    def _handle_sync_action(self, action: SyncAction) -> None:
        if self._controller is None:
            self._set_status_callback("No controller available to sync plan parameters", True)
            return
        call_kwargs = self._build_sync_call_kwargs(action)
        if call_kwargs is None:
            return
        api = getattr(self._controller, "_api", None)
        sync_method = getattr(api, action.qserver_function, None) if api is not None else None
        if not callable(sync_method):
            self._set_status_callback(
                f"QServer API method '{action.qserver_function}' is not available",
                True,
            )
            return
        result = sync_method(timeout=action.timeout, **call_kwargs)
        payload = self._normalize_sync_result(result, action)
        if not payload:
            self._set_status_callback(f"No sync data returned from '{action.qserver_function}'", True)
            return
        if action.result_target == "inputs":
            self._apply_sync_result_to_inputs(payload)
        else:
            self._apply_roi_callback(payload)
        self._set_status_callback(f"Updated plan parameters from '{action.text}'", False)

    def _build_sync_call_kwargs(
        self,
        action: SyncAction,
        *,
        overrides: Optional[Mapping[str, object]] = None,
    ) -> Optional[Dict[str, object]]:
        kwargs: Dict[str, object] = {}
        if action.input_map:
            for arg_name, source_name in action.input_map.items():
                value = overrides.get(source_name) if overrides and source_name in overrides else self._read_sync_input_value(source_name)
                if value is None:
                    self._set_status_callback(
                        f"Unable to resolve sync input '{source_name}' for '{action.text}'",
                        True,
                    )
                    return None
                kwargs[arg_name] = value
        if action.parameter_map:
            for arg_name, source_name in action.parameter_map.items():
                value = (
                    overrides.get(source_name)
                    if overrides and source_name in overrides
                    else self._resolve_sync_parameter_value(source_name)
                )
                if value is None:
                    self._set_status_callback(
                        f"Unable to resolve sync parameter '{source_name}' for '{action.text}'",
                        True,
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

    def apply_sync_result_to_inputs(
        self,
        payload: Mapping[str, object],
        *,
        mapping: Optional[Mapping[str, str]] = None,
    ) -> None:
        if mapping:
            translated = {
                str(target_name): payload[source_name]
                for target_name, source_name in mapping.items()
                if source_name in payload
            }
            self._apply_sync_result_to_inputs(translated)
            return
        self._apply_sync_result_to_inputs(payload)

    def get_sync_input_value(self, name: str) -> object | None:
        return self._read_sync_input_value(name)

    def execute_action(
        self,
        spec: Mapping[str, object],
        *,
        overrides: Optional[Mapping[str, object]] = None,
    ) -> Optional[Dict[str, object]]:
        if self._controller is None:
            self._set_status_callback("No controller available to sync plan parameters", True)
            return None
        action = self._normalize_sync_action_entry(spec)
        if action is None:
            self._set_status_callback("Invalid sync action configuration", True)
            return None
        call_kwargs = self._build_sync_call_kwargs(action, overrides=overrides)
        if call_kwargs is None:
            return None
        api = getattr(self._controller, "_api", None)
        sync_method = getattr(api, action.qserver_function, None) if api is not None else None
        if not callable(sync_method):
            self._set_status_callback(
                f"QServer API method '{action.qserver_function}' is not available",
                True,
            )
            return None
        result = sync_method(timeout=action.timeout, **call_kwargs)
        payload = self._normalize_sync_result(result, action)
        if not payload:
            self._set_status_callback(f"No sync data returned from '{action.qserver_function}'", True)
            return None
        return payload

    def _resolve_sync_parameter_value(self, source_name: str) -> object | None:
        parameter_rows = self._parameter_rows_getter()
        row = parameter_rows.get(source_name)
        if row is not None:
            return read_parameter_row_value(row)

        for alias in self._roi_key_map.get(source_name, []):
            alias_row = parameter_rows.get(alias)
            if alias_row is None:
                continue
            return read_parameter_row_value(alias_row)
        return None

    def _normalize_sync_result(self, result: object, action: SyncAction) -> Dict[str, object]:
        if action.transform:
            return self._apply_sync_transform(result, action.transform)
        if isinstance(result, Mapping):
            return {str(key): value for key, value in result.items()}
        if action.result_keys and isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
            return {key: value for key, value in zip(action.result_keys, result)}
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
            value = PlanEditorExtraPanel._extract_sync_source_value(result, source)
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
        return PlanEditorExtraPanel._extract_sync_source_value(result, spec)

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


PlanEditorExtraWidget = PlanEditorExtraPanel
