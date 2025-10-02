"""Modular GUI components for beamline control."""

from .config.registry import WidgetRegistry, WidgetDescriptor, registry
from .config.defaults import register_default_widgets
from .core import DataVisualizationController, DataLoader, default_loader
from .ui import (
    BaseLoaderWidget,
    CustomToolbar,
    DataVisualizationWidget,
    DataViewerPane,
    PlanDefinition,
    PlanEditorWidget,
    PlanParameter,
    PlotCanvasWidget,
    PtychographyLoaderWidget,
    QServerWidget,
    XRFLoaderWidget,
)

__all__ = [
    "BaseLoaderWidget",
    "CustomToolbar",
    "DataLoader",
    "DataVisualizationController",
    "DataVisualizationWidget",
    "DataViewerPane",
    "PlanDefinition",
    "PlanEditorWidget",
    "PlanParameter",
    "PlotCanvasWidget",
    "PtychographyLoaderWidget",
    "QServerWidget",
    "WidgetDescriptor",
    "WidgetRegistry",
    "XRFLoaderWidget",
    "default_loader",
    "register_default_widgets",
    "registry",
]
