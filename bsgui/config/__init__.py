"""Configuration helpers for assembling the default widget set."""

from .defaults import register_default_widgets
from .registry import WidgetDescriptor, WidgetRegistry, registry

__all__ = [
    "WidgetDescriptor",
    "WidgetRegistry",
    "register_default_widgets",
    "registry",
]
