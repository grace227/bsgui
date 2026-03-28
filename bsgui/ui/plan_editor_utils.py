"""Shared helpers for the plan editor widgets."""

from __future__ import annotations

import ast
import json
from typing import Any, Dict, Iterable, List, Mapping, Optional, TypeAlias

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QDoubleValidator, QIntValidator, QRegularExpressionValidator
from PySide6.QtWidgets import QCheckBox, QLineEdit

from ..core.qserver_controller import PlanParameter

OVERHEAD_FACTOR = 3
SYNC_VALUE_STYLE = "color: #2e7d32;"
DEFAULT_DISABLED_STYLE = "color: #666666;"

ParameterRow: TypeAlias = tuple[QCheckBox, QLineEdit, PlanParameter, object | None, str]


def convert_extra_parameters(config: Any) -> List[PlanParameter]:
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


def build_type_validator(type_name: str, line_edit: QLineEdit):
    type_name = (type_name or "str").lower()
    if type_name == "int":
        validator = QIntValidator(line_edit)
        validator.setRange(-2147483648, 2147483647)
        return validator
    if type_name == "float":
        validator = QDoubleValidator(line_edit)
        validator.setNotation(QDoubleValidator.StandardNotation)
        validator.setDecimals(10)
        return validator
    if type_name == "bool":
        regex = QRegularExpression("^(?i)(true|false|1|0|yes|no|on|off|y|n)$")
        return QRegularExpressionValidator(regex, line_edit)
    return None


def infer_parameter_type(parameter: PlanParameter) -> str:
    if hasattr(parameter, "inferred_type"):
        return parameter.inferred_type().lower()
    return (parameter.type_name or "str").lower()


def coerce_parameter_value(parameter: PlanParameter, value_text: str) -> object:
    type_name = (
        parameter.inferred_type().lower()
        if hasattr(parameter, "inferred_type")
        else (parameter.type_name or "str").lower()
    )
    text = value_text.strip()
    default_value = getattr(parameter, "default", None)
    has_container_default = isinstance(default_value, (list, tuple, dict, set))

    if has_container_default or any(
        token in type_name for token in ("list", "tuple", "dict", "set", "sequence", "mapping")
    ):
        for parser in (json.loads, ast.literal_eval):
            try:
                return parser(text)
            except Exception:
                continue

    return parameter.coerce(value_text)


def read_parameter_row_value(row: ParameterRow) -> object | None:
    checkbox, line_edit, parameter, default_value, default_label = row
    if checkbox.isChecked():
        value_text = line_edit.text().strip()
        if not value_text or value_text == default_label:
            return None
        try:
            return coerce_parameter_value(parameter, value_text)
        except (ValueError, TypeError):
            return None
    return default_value


def apply_parameter_row_value(row: ParameterRow, value: object, *, style: str = SYNC_VALUE_STYLE) -> None:
    checkbox, line_edit, _parameter, _default_value, _default_label = row
    checkbox.blockSignals(True)
    checkbox.setChecked(True)
    checkbox.blockSignals(False)
    line_edit.setEnabled(True)
    line_edit.setStyleSheet(style)
    line_edit.setText(str(value))


def format_default_label(text: str) -> str:
    display = text if text else "None"
    return f"{display} (default)"


def normalize_key_map(raw_map: Optional[Mapping[str, object]]) -> Dict[str, List[str]]:
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


def normalize_string_map(raw_map: Optional[Mapping[str, object]]) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    if not isinstance(raw_map, Mapping):
        return normalized
    for key, value in raw_map.items():
        if isinstance(key, str) and isinstance(value, str):
            normalized[key] = value
    return normalized
