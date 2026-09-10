"""Data Management experiment and DAQ controls."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any
from typing import Mapping
from typing import Optional
from typing import Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout
from PySide6.QtWidgets import QHBoxLayout
from PySide6.QtWidgets import QLabel
from PySide6.QtWidgets import QLineEdit
from PySide6.QtWidgets import QPushButton
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtWidgets import QWidget

from .status_bus import emit_status

if TYPE_CHECKING:
    from ..core.qserver_controller import QServerController


@dataclass(frozen=True)
class DataManagementField:
    name: str
    label: str
    default: str = ""
    placeholder: str = ""
    tooltip: str = ""
    type_name: str = "str"
    required: bool = False


@dataclass(frozen=True)
class DataManagementAction:
    text: str
    qserver_function: str
    input_map: Mapping[str, str]
    user_group: str = "root"
    timeout: float = 30.0


class DataManagementWidget(QWidget):
    """Form-driven controls for DM experiment creation and DAQ startup."""

    statusChanged = Signal(str, bool)
    buttonsEnabledChanged = Signal(bool)

    def __init__(
        self,
        *,
        controller: Optional["QServerController"],
        config: Optional[Mapping[str, Any]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        """Build the widget from YAML-driven field and action config."""
        super().__init__(parent)
        self._controller = controller
        self._fields = self._normalize_fields(config.get("fields") if config else None)
        self._actions = self._normalize_actions(
            config.get("actions") if config else None
        )
        self._action_note = self._normalize_text(
            config.get("action_note") if config else None
        )
        self._field_widgets: dict[str, QLineEdit] = {}
        self._buttons: list[QPushButton] = []

        self.statusChanged.connect(self._set_status)
        self.buttonsEnabledChanged.connect(self._set_buttons_enabled)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        field_layout = QGridLayout()
        field_layout.setHorizontalSpacing(8)
        field_layout.setVerticalSpacing(6)

        for row, field in enumerate(self._fields):
            label = QLabel(field.label)
            if field.tooltip:
                label.setToolTip(field.tooltip)
            field_layout.addWidget(label, row, 0)
            editor = QLineEdit()
            editor.setText(field.default)
            editor.setPlaceholderText(field.placeholder)
            if field.tooltip:
                editor.setToolTip(field.tooltip)
            self._field_widgets[field.name] = editor
            field_layout.addWidget(editor, row, 1)

        layout.addLayout(field_layout)

        if self._action_note:
            note = QLabel(self._action_note)
            note.setWordWrap(True)
            note.setStyleSheet(
                "background-color: #fff8d6; "
                "border: 1px solid #d6a600; "
                "border-radius: 4px; "
                "color: #4f3b00; "
                "font-weight: 600; "
                "padding: 6px;"
            )
            layout.addWidget(note)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        for action in self._actions:
            button = QPushButton(action.text)
            button.clicked.connect(
                lambda _checked=False, action=action: self._handle_action(action)
            )
            self._buttons.append(button)
            button_layout.addWidget(button)
        layout.addLayout(button_layout)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        layout.addStretch(1)

    def set_controller(self, controller: "QServerController") -> None:
        """Attach the QServer controller after widget construction."""
        self._controller = controller

    def _handle_action(self, action: DataManagementAction) -> None:
        if self._controller is None:
            self.statusChanged.emit("QServer controller is not available.", True)
            return

        call_kwargs = self._build_call_kwargs(action)
        if call_kwargs is None:
            return

        self.buttonsEnabledChanged.emit(False)
        self.statusChanged.emit(f"Running {action.text}...", False)

        def _target() -> None:
            try:
                result = self._controller.execute_function(
                    action.qserver_function,
                    call_kwargs=call_kwargs,
                    user_group=action.user_group,
                    timeout=action.timeout,
                )
            except Exception as exc:
                self.statusChanged.emit(f"{action.text} failed: {exc}", True)
            else:
                if isinstance(result, Mapping) and result.get("success") is False:
                    error = result.get("error") or "Unknown error"
                    self.statusChanged.emit(f"{action.text} failed: {error}", True)
                elif result is None:
                    self.statusChanged.emit(f"{action.text} returned no result.", True)
                else:
                    self.statusChanged.emit(f"{action.text} complete.", False)
            finally:
                self.buttonsEnabledChanged.emit(True)

        thread = threading.Thread(
            target=_target,
            name=f"DataManagement-{action.qserver_function}",
            daemon=True,
        )
        thread.start()

    def _build_call_kwargs(
        self,
        action: DataManagementAction,
    ) -> dict[str, object] | None:
        kwargs: dict[str, object] = {}
        for arg_name, field_name in action.input_map.items():
            field = next(
                (item for item in self._fields if item.name == field_name),
                None,
            )
            editor = self._field_widgets.get(field_name)
            if field is None or editor is None:
                self.statusChanged.emit(f"Missing field '{field_name}'.", True)
                return None
            text = editor.text().strip()
            if field.required and not text:
                self.statusChanged.emit(f"{field.label} is required.", True)
                return None
            try:
                value = self._coerce_value(text, field.type_name)
            except ValueError as exc:
                self.statusChanged.emit(str(exc), True)
                return None
            if value is not None:
                kwargs[arg_name] = value
        return kwargs

    @staticmethod
    def _coerce_value(text: str, type_name: str) -> object | None:
        if text == "":
            return None
        normalized_type = type_name.lower()
        if normalized_type == "bool":
            normalized = text.lower()
            if normalized in {"1", "true", "yes", "y", "on"}:
                return True
            if normalized in {"0", "false", "no", "n", "off"}:
                return False
            raise ValueError(f"Invalid bool value: {text}")
        if normalized_type == "int":
            return int(text)
        if normalized_type == "float":
            return float(text)
        return text

    def _set_buttons_enabled(self, enabled: bool) -> None:
        for button in self._buttons:
            button.setEnabled(enabled)

    def _set_status(self, message: str, error: bool) -> None:
        color = "#c62828" if error else "#2e7d32"
        self._status_label.setText(message)
        self._status_label.setStyleSheet(f"color: {color};")
        emit_status(message)

    @staticmethod
    def _normalize_fields(config: object) -> list[DataManagementField]:
        if not isinstance(config, Sequence) or isinstance(config, (str, bytes)):
            return []
        fields: list[DataManagementField] = []
        for entry in config:
            if not isinstance(entry, Mapping):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            label = entry.get("label") if isinstance(entry.get("label"), str) else name
            default = entry.get("default", "")
            placeholder = entry.get("placeholder", "")
            tooltip = entry.get("tooltip", "")
            fields.append(
                DataManagementField(
                    name=name.strip(),
                    label=label.strip(),
                    default="" if default is None else str(default),
                    placeholder="" if placeholder is None else str(placeholder),
                    tooltip="" if tooltip is None else str(tooltip),
                    type_name=str(entry.get("type_name", "str")),
                    required=bool(entry.get("required", False)),
                )
            )
        return fields

    @staticmethod
    def _normalize_actions(config: object) -> list[DataManagementAction]:
        if not isinstance(config, Sequence) or isinstance(config, (str, bytes)):
            return []
        actions: list[DataManagementAction] = []
        for entry in config:
            if not isinstance(entry, Mapping):
                continue
            text = entry.get("text")
            function_name = entry.get("qserver_function")
            input_map = entry.get("input_map")
            if not isinstance(text, str) or not text.strip():
                continue
            if not isinstance(function_name, str) or not function_name.strip():
                continue
            if not isinstance(input_map, Mapping):
                input_map = {}
            user_group = entry.get("user_group")
            actions.append(
                DataManagementAction(
                    text=text.strip(),
                    qserver_function=function_name.strip(),
                    input_map={
                        str(arg_name): str(field_name)
                        for arg_name, field_name in input_map.items()
                    },
                    user_group=user_group if isinstance(user_group, str) else "root",
                    timeout=float(entry.get("timeout", 30.0)),
                )
            )
        return actions

    @staticmethod
    def _normalize_text(value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()


__all__ = ["DataManagementWidget"]
