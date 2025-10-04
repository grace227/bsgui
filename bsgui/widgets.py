"""Compatibility shim for the legacy `bsgui.widgets` module."""

from .core import DataLoader, DataVisualizationController, default_loader
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
    QueueServerStatusWidget,
    QServerConsoleWidget,
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
    "QueueServerStatusWidget",
    "QServerConsoleWidget",
    "XRFLoaderWidget",
    "default_loader",
]
