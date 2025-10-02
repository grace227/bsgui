"""Shared data loading utilities for the data visualization widget."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple, List

import numpy as np

DataLoader = Callable[[Path], Mapping[str, Any]]


def default_loader(path: Path) -> Mapping[str, Any]:
    """Load two-column numeric data from a text file."""

    data = np.loadtxt(path)
    if data.ndim == 1:
        x = np.arange(len(data))
        y = data
    else:
        x, y = data[:, 0], data[:, 1]
    return {"x": x, "y": y}

def get_item(data: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """Fetch a value from the cached dataset by *key*."""

    if data is None:
        return default
    return data.get(key, default)

class DataVisualizationController:
    """Minimal controller that loads and caches a single dataset."""

    elms: List[str] = []
    elms_data: np.ndarray = np.array([])
    x_val: np.ndarray = np.array([])
    y_val: np.ndarray = np.array([])

    def __init__(
        self,
        *,
        loader: DataLoader = default_loader,
        search_paths: Optional[Iterable[Path]] = None,
        file_patterns: Optional[Sequence[str]] = None,
    ) -> None:
        self._loader = loader
        self._paths: Tuple[Path, ...] = self._coerce_paths(search_paths)
        self._file_patterns: Tuple[str, ...] = self._coerce_patterns(file_patterns)
        self._last_path: Optional[Path] = None
        self._cached_data: Optional[dict[str, Any]] = None

    @staticmethod
    def _coerce_paths(paths: Optional[Iterable[Path]]) -> Tuple[Path, ...]:
        if not paths:
            return (Path.cwd(),)
        return tuple(Path(path) for path in paths)

    @staticmethod
    def _coerce_patterns(patterns: Optional[Sequence[str]]) -> Tuple[str, ...]:
        if not patterns:
            return ("*.dat",)
        return tuple(str(pattern) for pattern in patterns)

    def load(self, path: Path, load_type: str = "xrf"):
        """Load the file through the configured loader and cache the result."""

        resolved = Path(path)
        loaded = dict(self._loader(resolved))
        self._update_data(loaded, load_type)
        self._last_path = resolved

    def _update_data(self, data: Mapping[str, Any], load_type: str) -> None:
        """Update input data."""

        if load_type == "xrf":
            ch_names = get_item(data, "ch_names")
            scaler_names = get_item(data, "scaler_names")
            x_val = get_item(data, "x_val")
            y_val = get_item(data, "y_val")
            xrf_data = get_item(data, "data")
            scaler_data = get_item(data, "scaler_data")

            if all([ch_names is not None, scaler_names is not None]):
                self.elms = ch_names + scaler_names
                self.elms_data = np.concatenate([xrf_data, scaler_data], axis=0)
            elif scaler_names is None:
                self.elms = ch_names
                self.elms_data = xrf_data


        self.x_val = x_val
        self.y_val = y_val
        self._cached_data = data


    @property
    def last_path(self) -> Optional[Path]:
        """Return the most recently loaded path, if any."""

        return self._last_path

    @property
    def cached_data(self) -> Optional[dict[str, Any]]:
        """Return the most recently loaded dataset, if available."""

        return self._cached_data

    @property
    def normalized_paths(self) -> Sequence[Path]:
        """Return the stored search paths as `Path` objects."""

        return self._paths

    def set_search_paths(self, paths: Iterable[Path]) -> None:
        """Replace the stored search paths."""

        self._paths = self._coerce_paths(paths)

    @property
    def file_patterns(self) -> Sequence[str]:
        """Return the configured file name patterns."""

        return self._file_patterns
