"""Plan editor widget that queries Bluesky QServer for available plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Protocol, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class PlanParameter:
    """Metadata describing a single plan parameter."""

    name: str
    default: object | None = None
    required: bool = False
    description: str | None = None

    def default_as_text(self) -> str:
        if self.default is None:
            return "None (default)"
        return repr(self.default)


@dataclass(frozen=True)
class PlanDefinition:
    """Representation of a plan or instruction exposed by QServer."""

    name: str
    kind: str  # "plan" or "instruction"
    parameters: Sequence[PlanParameter]
    description: str | None = None


class PlanCatalogClient(Protocol):
    """Protocol for clients that can fetch plan definitions from QServer."""

    def fetch_definitions(self, kind: str) -> Sequence[PlanDefinition]:
        ...


class PlanEditorWidget(QWidget):
    """Widget for browsing plan definitions and preparing submissions."""

    planSubmitted = Signal(dict)

    def __init__(
        self,
        *,
        client: Optional[PlanCatalogClient] = None,
        kinds: Optional[Sequence[str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._kinds = list(kinds) if kinds else ["plan", "instruction"]
        if not self._kinds:
            self._kinds = ["plan"]
        self._definitions: Dict[str, List[PlanDefinition]] = {kind: [] for kind in self._kinds}
        self._selected_dataset: Dict[str, object] | None = None
        self._roi_regions: List[dict] = []

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_plan_editor_panel(), "Bluesky Plan")

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self._tabs)

        if self._client is not None:
            self.refresh_from_client()

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
        print(f"self._kinds: {self._kinds}")
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
        self._parameter_table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)

        layout.addWidget(self._parameter_table, stretch=1)

        # Action buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch(1)

        self._batch_button = QPushButton("Batch Upload")
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

    def set_selected_dataset(self, payload: Mapping[str, object]) -> None:
        """Store the latest dataset/metadata selection from the viewer."""

        self._selected_dataset = dict(payload)

    def handle_canvas_interaction(self, payload: Mapping[str, object]) -> None:
        """Record canvas interaction details (e.g., clicks)."""

        self._selected_dataset = dict(payload)

    def selected_dataset(self) -> Optional[Dict[str, object]]:
        return dict(self._selected_dataset) if self._selected_dataset is not None else None

    def handle_roi_drawn(self, roi: Mapping[str, object]) -> None:
        """Receive ROI data emitted from the visualization toolbar."""

        self._roi_regions.append(dict(roi))

    def roi_regions(self) -> List[dict]:
        return list(self._roi_regions)

    # Public API ---------------------------------------------------------

    def set_client(self, client: PlanCatalogClient, *, refresh: bool = True) -> None:
        self._client = client
        if refresh:
            self.refresh_from_client()

    def refresh_from_client(self) -> None:
        if self._client is None:
            return
        for kind in self._kinds:
            definitions = list(self._client.fetch_definitions(kind))
            self._definitions[kind] = definitions
        self._refresh_plan_combo()

    def load_definitions(self, definitions: Iterable[PlanDefinition]) -> None:
        for definition in definitions:
            bucket = self._definitions.setdefault(definition.kind, [])
            bucket.append(definition)
        self._refresh_plan_combo()

    def current_plan(self) -> Optional[PlanDefinition]:
        kind = self._current_kind
        index = self._plan_combo.currentIndex()
        if index < 0:
            return None
        try:
            return self._definitions[kind][index]
        except (KeyError, IndexError):
            return None

    # Internal helpers ---------------------------------------------------

    @property
    def _current_kind(self) -> str:
        for kind, button in self._kind_buttons.items():
            if button.isChecked():
                return kind
        return self._kinds[0]

    def _handle_kind_change(self, kind: str) -> None:
        if kind != self._current_kind:
            return
        self._refresh_plan_combo()

    def _refresh_plan_combo(self) -> None:
        kind = self._current_kind
        definitions = self._definitions.get(kind, [])
        self._plan_combo.blockSignals(True)
        self._plan_combo.clear()
        for definition in definitions:
            label = definition.name
            if definition.description:
                label = f"{definition.name} – {definition.description}"
            self._plan_combo.addItem(label, definition)
        self._plan_combo.blockSignals(False)
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

        parameters = list(definition.parameters)
        self._parameter_table.setRowCount(len(parameters))
        for row, parameter in enumerate(parameters):
            name_item = QTableWidgetItem(parameter.name)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._parameter_table.setItem(row, 0, name_item)

            value_item = QTableWidgetItem(parameter.default_as_text())
            value_item.setFlags(
                Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsEditable
            )
            self._parameter_table.setItem(row, 1, value_item)

    def _emit_submission(self) -> None:
        definition = self.current_plan()
        if definition is None:
            return

        payload = {
            "kind": self._current_kind,
            "name": definition.name,
            "parameters": {},
        }

        for row in range(self._parameter_table.rowCount()):
            param_name = self._parameter_table.item(row, 0).text()
            value_item = self._parameter_table.item(row, 1)
            value_text = value_item.text() if value_item is not None else ""
            payload["parameters"][param_name] = value_text

        self.planSubmitted.emit(payload)
