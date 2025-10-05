"""Plan editor widget that queries Bluesky QServer for available plans."""

from __future__ import annotations

from dataclasses import dataclass
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

from .status_bus import emit_status

from ..core.qserver_controller import PlanDefinition, PlanParameter

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
        # self._roi_regions: List[dict] = []
        self._parameter_rows: Dict[str, tuple[QCheckBox, QLineEdit, PlanParameter, str, str]] = {}
        self._roi_key_map = self._normalize_key_map(roi_key_map)

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
        self._add_button = QPushButton("Add to Queue")
        self._add_button.clicked.connect(self._emit_submission)
        self._save_button = QPushButton("Save")
        self._save_button.setEnabled(False)
        self._reset_button = QPushButton("Reset")
        self._reset_button.clicked.connect(self._populate_parameters)
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.setEnabled(False)

        for button in [
            self._batch_button,
            self._add_button,
            self._save_button,
            self._reset_button,
            self._cancel_button,
        ]:
            button_layout.addWidget(button)

        layout.addLayout(button_layout)

        return widget


    # Selection hooks ----------------------------------------------------

    def handle_point_drawn(self, point: Mapping[str, object]) -> None:
        """Record point coordinates emitted from the toolbar."""
        self._apply_roi_to_parameters(point)
        emit_status("Point applied to plan parameters")

    def handle_roi_drawn(self, roi: Mapping[str, object]) -> None:
        """Receive ROI data emitted from the visualization toolbar."""
        self._apply_roi_to_parameters(roi)
        emit_status("ROI applied to plan parameters")

    def handle_plans_update(self, worker_status: str) -> None:
        if worker_status == "closed" or worker_status == "":
            self._plan_combo.blockSignals(True)
            self._plan_combo.clear()
            self._plan_combo.blockSignals(False)
            self._parameter_table.setRowCount(0)
            self._parameter_rows.clear()
        elif worker_status == "idle" and self._plan_combo.count() == 0:
            self.refresh_from_controller()

    def refresh_from_controller(self) -> None:
        if self._controller is None:
            return
        definitions = self._controller.get_allowed_plan_definitions(kind=self._current_kind)
        if not definitions:
            return
        self._definitions = definitions
        self._refresh_plan_combo()

    def load_definitions(self, definitions: Iterable[PlanDefinition]) -> None:
        self._definitions.extend(definitions)
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

            raw_text = parameter.default_as_text()
            default_label = self._format_default_label(raw_text)

            container = QWidget()
            layout = QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(6)

            checkbox = QCheckBox()
            line_edit = QLineEdit(default_label)
            line_edit.setEnabled(False)
            line_edit.setStyleSheet("color: #666666;")
            if parameter.description:
                line_edit.setToolTip(parameter.description)

            def handle_toggle(checked: bool, le: QLineEdit = line_edit, default=raw_text, label=default_label) -> None:
                if checked:
                    le.setEnabled(True)
                    le.setStyleSheet("")
                    if le.text() == label:
                        le.setText(default)
                else:
                    le.setEnabled(False)
                    le.setStyleSheet("color: #666666;")
                    le.setText(label)

            checkbox.toggled.connect(handle_toggle)

            layout.addWidget(checkbox)
            layout.addWidget(line_edit, 1)
            self._parameter_table.setCellWidget(row, 1, container)

            self._parameter_rows[parameter.name] = (checkbox, line_edit, parameter, raw_text, default_label)

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
                    required=bool(entry.get("required", False)),
                    description=entry.get("description") if isinstance(entry.get("description"), str) else None,
                )
            )
        return parameters

    def _apply_roi_to_parameters(self, roi: Mapping[str, object]) -> None:
        if not self._parameter_rows or not self._roi_key_map:
            return
        for roi_key, value in roi.items():
            targets = self._roi_key_map.get(str(roi_key))
            print(f"roi_key: {roi_key}, targets: {targets}")
            if not targets:
                continue
            for target_name in targets:
                row = self._parameter_rows.get(target_name)
                if not row:
                    continue
                checkbox, line_edit, _, raw_default, default_label = row
                checkbox.blockSignals(True)
                checkbox.setChecked(True)
                checkbox.blockSignals(False)
                line_edit.setEnabled(True)
                line_edit.setStyleSheet("")
                line_edit.setText(str(value))

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

    def _emit_submission(self) -> None:
        definition = self.current_plan()
        if definition is None:
            return

        payload = {
            "kind": self._current_kind,
            "name": definition.name,
            "parameters": {},
        }

        for name, (checkbox, line_edit, parameter, raw_default, default_label) in self._parameter_rows.items():
            if checkbox.isChecked():
                value_text = line_edit.text()
            else:
                value_text = raw_default
            payload["parameters"][name] = value_text

        self.planSubmitted.emit(payload)
