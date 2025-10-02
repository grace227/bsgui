"""Matplotlib canvas widget used by data viewers."""

from __future__ import annotations

from typing import Sequence

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QVBoxLayout, QWidget, QSizePolicy
import numpy as np
from matplotlib.colors import LogNorm

class PlotCanvasWidget(QWidget):
    """Wrapper holding a Matplotlib canvas and exposing helper plotting methods."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._figure = Figure(figsize=(5, 4))
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._axes = self._figure.add_subplot(111)
        self._colorbar = None

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas)

    @property
    def canvas(self) -> FigureCanvasQTAgg:
        return self._canvas

    @property
    def axes(self):  # type: ignore[override]
        return self._axes

    def imshow(
        self,
        xval: Sequence[float] | np.ndarray,
        yval: Sequence[float] | np.ndarray,
        zval: np.ndarray,
        title: str,
        xlabel: str = "X",
        ylabel: str = "Y",
        grid: bool = False,
        vmax_th: float = 99,
        show_colorbar: bool = True,
        color_map: str = "inferno",
        color_bar_label: str = "Intensity",
        color_log_scale: bool = False,
    ) -> None:
        x_arr = np.asarray(xval)
        y_arr = np.asarray(yval)
        z_arr = np.asarray(zval)

        if x_arr.size == 0 or y_arr.size == 0 or z_arr.size == 0:
            self.show_message("No image data")
            return

        if self._colorbar is not None:
            self._colorbar.remove()
            self._colorbar = None

        self._axes.clear()

        vmin = None
        vmax = None
        finite_values = z_arr[np.isfinite(z_arr)]
        if finite_values.size:
            percentile = float(np.clip(vmax_th, 0.0, 100.0))
            vmax = float(np.nanpercentile(finite_values, percentile))
            if color_log_scale:
                positive = finite_values[finite_values > 0]
                if positive.size:
                    vmin = float(positive.min())
                    if vmax is None or vmax <= vmin:
                        vmax = float(positive.max())
                else:
                    color_log_scale = False
                    vmin = None

        extent = (
            float(x_arr.min()),
            float(x_arr.max()),
            float(y_arr.min()),
            float(y_arr.max()),
        )

        image = self._axes.imshow(
            z_arr,
            cmap=color_map,
            extent=extent,
            origin="lower",
            vmin=vmin,
            vmax=vmax,
            norm=LogNorm(vmin=vmin, vmax=vmax) if color_log_scale else None,
            aspect="equal",
        )

        self._axes.set_title(title)
        self._axes.set_xlabel(xlabel)
        self._axes.set_ylabel(ylabel)
        self._axes.grid(grid)

        if show_colorbar:
            self._colorbar = self._figure.colorbar(image, ax=self._axes)
            if color_bar_label:
                self._colorbar.set_label(color_bar_label)

        self._canvas.draw()
    
    def plot_xy(
        self,
        x: Sequence[float],
        y: Sequence[float],
        *,
        title: str,
        xlabel: str = "X",
        ylabel: str = "Y",
        grid: bool = True,
    ) -> None:
        self._axes.clear()
        self._axes.plot(x, y, marker="o")
        self._axes.set_title(title)
        self._axes.set_xlabel(xlabel)
        self._axes.set_ylabel(ylabel)
        self._axes.grid(grid)
        self._canvas.draw()

    def show_message(self, message: str) -> None:
        self._axes.clear()
        self._axes.text(
            0.5,
            0.5,
            message,
            ha="center",
            va="center",
            transform=self._axes.transAxes,
        )
        self._canvas.draw()
