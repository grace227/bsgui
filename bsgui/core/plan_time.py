"""Helpers for estimating plan duration and scan point shape."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from .batch_generation import build_iteration_values


@dataclass(frozen=True)
class PlanTimeEstimate:
    seconds: float | None
    scan_size: str | None


OVERHEAD_FACTOR = 3.0

_ALIASES = {
    "width": ("width", "width_mm", "length", "width_keV"),
    "height": ("height", "height_mm"),
    "stepsize_x": ("stepsize_x", "stepsize_x_mm", "stepsize", "stepsize_keV"),
    "stepsize_y": ("stepsize_y", "stepsize_y_mm"),
    "dwell": ("dwell_ms", "dwell_time_ms", "dwell_s", "dwell_time", "dwell"),
    "width_fine": ("width_fine", "width_fine_mm"),
    "height_fine": ("height_fine", "height_fine_mm"),
    "stepsize_x_fine": ("stepsize_x_fine", "stepsize_x_fine_mm"),
    "stepsize_y_fine": ("stepsize_y_fine", "stepsize_y_fine_mm"),
    "dwell_fine": ("dwell_ms_fine", "dwell_time_ms_fine", "dwell_s_fine", "dwell_time_fine", "dwell_fine"),
}


def estimate_plan_time(
    plan_name: str,
    values: Mapping[str, object],
    *,
    kind: str = "single",
    overhead_factor: float = OVERHEAD_FACTOR,
) -> PlanTimeEstimate:
    """Estimate scan duration and point shape from plan parameter values."""

    if kind == "batch":
        return _estimate_batch_plan(plan_name, values, overhead_factor=overhead_factor)
    return _estimate_single_plan(plan_name, values, overhead_factor=overhead_factor)


def _estimate_batch_plan(
    plan_name: str,
    values: Mapping[str, object],
    *,
    overhead_factor: float,
) -> PlanTimeEstimate:
    iterate_variable = _string_value(values.get("iterate_variable"))
    start = _float_value(values.get("iterate_starting_value"))
    end = _float_value(values.get("iterate_ending_value"))
    step = _float_value(values.get("iterate_step_size"))
    base_values = {
        key: value
        for key, value in values.items()
        if key not in {"iterate_variable", "iterate_starting_value", "iterate_ending_value", "iterate_step_size"}
    }

    if not iterate_variable or start is None or end is None or step in (None, 0):
        single = _estimate_single_plan(plan_name, base_values, overhead_factor=overhead_factor)
        return PlanTimeEstimate(single.seconds, single.scan_size)

    try:
        iterate_values = build_iteration_values(start, end, step)
    except ValueError:
        return PlanTimeEstimate(None, None)
    if not iterate_values:
        return PlanTimeEstimate(None, None)

    estimates: list[PlanTimeEstimate] = []
    for iterate_value in iterate_values:
        item_values = dict(base_values)
        item_values[iterate_variable] = iterate_value
        estimates.append(_estimate_single_plan(plan_name, item_values, overhead_factor=overhead_factor))

    seconds = None
    if all(estimate.seconds is not None for estimate in estimates):
        seconds = sum(float(estimate.seconds) for estimate in estimates)

    first_size = estimates[0].scan_size
    last_size = estimates[-1].scan_size
    if first_size is None:
        scan_size = f"{len(estimates)} scans"
    elif first_size == last_size:
        scan_size = f"{len(estimates)} x {first_size}"
    else:
        scan_size = f"{len(estimates)} scans; first {first_size}, last {last_size}"
    return PlanTimeEstimate(seconds, scan_size)


def _estimate_single_plan(
    plan_name: str,
    values: Mapping[str, object],
    *,
    overhead_factor: float,
) -> PlanTimeEstimate:
    if "coarse_fine" in plan_name:
        coarse = _estimate_scan(values, overhead_factor=overhead_factor)
        fine = _estimate_scan(
            values,
            width_key="width_fine",
            height_key="height_fine",
            stepsize_x_key="stepsize_x_fine",
            stepsize_y_key="stepsize_y_fine",
            dwell_key="dwell_fine",
            overhead_factor=overhead_factor,
        )
        seconds = None
        if coarse.seconds is not None and fine.seconds is not None:
            seconds = coarse.seconds + fine.seconds
        sizes = []
        if coarse.scan_size:
            sizes.append(f"coarse {coarse.scan_size}")
        if fine.scan_size:
            sizes.append(f"fine {fine.scan_size}")
        return PlanTimeEstimate(seconds, "; ".join(sizes) if sizes else None)

    return _estimate_scan(values, overhead_factor=overhead_factor)


def _estimate_scan(
    values: Mapping[str, object],
    *,
    width_key: str = "width",
    height_key: str = "height",
    stepsize_x_key: str = "stepsize_x",
    stepsize_y_key: str = "stepsize_y",
    dwell_key: str = "dwell",
    overhead_factor: float,
) -> PlanTimeEstimate:
    width = _value_for(values, width_key)
    height = _value_for(values, height_key)
    stepsize_x = _value_for(values, stepsize_x_key)
    stepsize_y = _value_for(values, stepsize_y_key)
    dwell_seconds = _dwell_seconds(values, dwell_key)

    x_pts = _point_count(width, stepsize_x)
    y_pts = _point_count(height, stepsize_y)
    if x_pts is None:
        return PlanTimeEstimate(None, None)

    point_count = x_pts if y_pts is None else x_pts * y_pts
    seconds = point_count * dwell_seconds * overhead_factor if dwell_seconds is not None else None
    scan_size = f"({x_pts},)" if y_pts is None else f"({x_pts}, {y_pts})"
    return PlanTimeEstimate(seconds, scan_size)


def _value_for(values: Mapping[str, object], key: str) -> float | None:
    for alias in _ALIASES.get(key, (key,)):
        value = _float_value(values.get(alias))
        if value is not None:
            return value
    return None


def _dwell_seconds(values: Mapping[str, object], key: str) -> float | None:
    for alias in _ALIASES.get(key, (key,)):
        value = _float_value(values.get(alias))
        if value is None:
            continue
        return value / 1000.0 if "ms" in alias else value
    return None


def _point_count(width: float | None, stepsize: float | None) -> int | None:
    if width is None or stepsize in (None, 0):
        return None
    points = abs(width / stepsize)
    if not math.isfinite(points) or points <= 0:
        return None
    return max(1, int(round(points)))


def _float_value(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["OVERHEAD_FACTOR", "PlanTimeEstimate", "estimate_plan_time"]
