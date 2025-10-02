"""Widget registry for assembling the beamline control UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class WidgetDescriptor:
    """Metadata for registered widgets."""

    key: str
    title: str
    description: str
    factory: Callable[[], QWidget]


class WidgetRegistry:
    """Registry holding widget factories keyed by a short identifier."""

    def __init__(self) -> None:
        self._widgets: Dict[str, WidgetDescriptor] = {}

    def register(self, descriptor: WidgetDescriptor) -> None:
        if descriptor.key in self._widgets:
            raise ValueError(f"Widget with key '{descriptor.key}' already registered")
        self._widgets[descriptor.key] = descriptor

    def get(self, key: str) -> WidgetDescriptor:
        return self._widgets[key]

    def create(self, key: str) -> QWidget:
        descriptor = self.get(key)
        return descriptor.factory()

    def list_descriptors(self) -> List[WidgetDescriptor]:
        return list(self._widgets.values())


# Singleton registry that higher level code can import and populate.
registry = WidgetRegistry()
