"""Matplotlib canvas widget used by data viewers."""

from __future__ import annotations

from typing import Sequence

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtWidgets import QVBoxLayout, QWidget, QSizePolicy
import numpy as np
from matplotlib.colors import LogNorm, Normalize

class PlotCanvasWidget(QWidget):
    """Wrapper holding a Matplotlib canvas and exposing helper plotting methods."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._figure = Figure(figsize=(5, 5))
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._axes = self._figure.add_subplot(111)
        self._colorbar = None
        self._image = None
        self._image_raw_data: np.ndarray | None = None
        self._image_vmax_percentile = 99.0
        self._color_log_scale = False

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

        self._image_raw_data = np.array(z_arr, copy=True)
        self._image_vmax_percentile = float(np.clip(vmax_th, 0.0, 100.0))
        self._color_log_scale = bool(color_log_scale)
        display_data, norm, self._color_log_scale = self._prepare_image_display(
            self._image_raw_data,
            color_log_scale=self._color_log_scale,
            vmax_percentile=self._image_vmax_percentile,
        )

        extent = (
            float(x_arr.min()),
            float(x_arr.max()),
            float(y_arr.min()),
            float(y_arr.max()),
        )

        self._image = self._axes.imshow(
            display_data,
            cmap=color_map,
            extent=extent,
            origin="lower",
            norm=norm,
            aspect="equal",
        )

        self._axes.set_title(title)
        self._axes.set_xlabel(xlabel)
        self._axes.set_ylabel(ylabel)
        self._axes.grid(grid)

        if show_colorbar:
            self._colorbar = self._figure.colorbar(self._image, ax=self._axes)
            if color_bar_label:
                self._colorbar.set_label(color_bar_label)

        self._canvas.draw()

    @property
    def color_log_scale(self) -> bool:
        return self._color_log_scale

    def set_color_log_scale(self, enabled: bool) -> bool:
        """Toggle log color normalization for the current image only."""

        if self._image is None or self._image_raw_data is None:
            self._color_log_scale = bool(enabled)
            return self._color_log_scale

        display_data, norm, applied = self._prepare_image_display(
            self._image_raw_data,
            color_log_scale=bool(enabled),
            vmax_percentile=self._image_vmax_percentile,
        )
        self._image.set_data(display_data)
        self._image.set_norm(norm)
        self._color_log_scale = applied
        if self._colorbar is not None:
            self._colorbar.update_normal(self._image)
        self._canvas.draw_idle()
        return self._color_log_scale

    @staticmethod
    def _prepare_image_display(
        data: np.ndarray,
        *,
        color_log_scale: bool,
        vmax_percentile: float,
    ) -> tuple[np.ndarray | np.ma.MaskedArray, Normalize, bool]:
        finite_values = data[np.isfinite(data)]
        if finite_values.size == 0:
            return data, Normalize(), False

        percentile = float(np.clip(vmax_percentile, 0.0, 100.0))
        if color_log_scale:
            positive = finite_values[finite_values > 0]
            if positive.size:
                vmin = float(positive.min())
                vmax = float(np.nanpercentile(positive, percentile))
                if vmax <= vmin:
                    vmax = float(positive.max())
                if vmax <= vmin:
                    vmax = vmin * 10.0
                masked = np.ma.masked_where((~np.isfinite(data)) | (data <= 0), data)
                return masked, LogNorm(vmin=vmin, vmax=vmax), True

        vmax = float(np.nanpercentile(finite_values, percentile))
        return data, Normalize(vmin=None, vmax=vmax), False
    
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
        self._image = None
        self._image_raw_data = None
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
