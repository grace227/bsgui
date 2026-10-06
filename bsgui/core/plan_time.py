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


LINE_OVERHEAD_SECONDS = 2.5

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


def _configured_aliases(
    aliases: Mapping[str, Sequence[str]] | None,
) -> dict[str, tuple[str, ...]]:
    configured = dict(_ALIASES)
    if not isinstance(aliases, Mapping):
        return configured
    for key, values in aliases.items():
        if not isinstance(key, str) or not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            continue
        additions = tuple(str(value) for value in values if isinstance(value, str) and value)
        if additions:
            configured[key] = tuple(dict.fromkeys((*configured.get(key, ()), *additions)))
    return configured


def estimate_plan_time(
    plan_name: str,
    values: Mapping[str, object],
    *,
    kind: str = "single",
    line_overhead_seconds: float = LINE_OVERHEAD_SECONDS,
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> PlanTimeEstimate:
    """Estimate scan duration and point shape from plan parameter values."""

    configured_aliases = _configured_aliases(aliases)

    if kind == "batch":
        return _estimate_batch_plan(
            plan_name,
            values,
            line_overhead_seconds=line_overhead_seconds,
            aliases=configured_aliases,
        )
    return _estimate_single_plan(
        plan_name,
        values,
        line_overhead_seconds=line_overhead_seconds,
        aliases=configured_aliases,
    )


def _estimate_batch_plan(
    plan_name: str,
    values: Mapping[str, object],
    *,
    line_overhead_seconds: float,
    aliases: Mapping[str, Sequence[str]],
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
        single = _estimate_single_plan(
            plan_name,
            base_values,
            line_overhead_seconds=line_overhead_seconds,
            aliases=aliases,
        )
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
        estimates.append(
            _estimate_single_plan(
                plan_name,
                item_values,
                line_overhead_seconds=line_overhead_seconds,
                aliases=aliases,
            )
        )

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
    line_overhead_seconds: float,
    aliases: Mapping[str, Sequence[str]],
) -> PlanTimeEstimate:
    if "coarse_fine" in plan_name:
        coarse = _estimate_scan(
            values,
            line_overhead_seconds=line_overhead_seconds,
            aliases=aliases,
        )
        fine = _estimate_scan(
            values,
            width_key="width_fine",
            height_key="height_fine",
            stepsize_x_key="stepsize_x_fine",
            stepsize_y_key="stepsize_y_fine",
            dwell_key="dwell_fine",
            line_overhead_seconds=line_overhead_seconds,
            aliases=aliases,
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

    return _estimate_scan(
        values,
        line_overhead_seconds=line_overhead_seconds,
        aliases=aliases,
    )


def _estimate_scan(
    values: Mapping[str, object],
    *,
    width_key: str = "width",
    height_key: str = "height",
    stepsize_x_key: str = "stepsize_x",
    stepsize_y_key: str = "stepsize_y",
    dwell_key: str = "dwell",
    line_overhead_seconds: float,
    aliases: Mapping[str, Sequence[str]],
) -> PlanTimeEstimate:
    width = _value_for(values, width_key, aliases)
    height = _value_for(values, height_key, aliases)
    stepsize_x = _value_for(values, stepsize_x_key, aliases)
    stepsize_y = _value_for(values, stepsize_y_key, aliases)
    dwell_seconds = _dwell_seconds(values, dwell_key, aliases)

    x_pts = _point_count(width, stepsize_x)
    y_pts = _point_count(height, stepsize_y)
    if x_pts is None:
        return PlanTimeEstimate(None, None)

    point_count = x_pts if y_pts is None else x_pts * y_pts
    line_count = y_pts if y_pts is not None else 1
    seconds = (
        point_count * dwell_seconds + line_count * line_overhead_seconds
        if dwell_seconds is not None
        else None
    )
    scan_size = f"({x_pts},)" if y_pts is None else f"({x_pts}, {y_pts})"
    return PlanTimeEstimate(seconds, scan_size)


def _value_for(
    values: Mapping[str, object],
    key: str,
    aliases: Mapping[str, Sequence[str]],
) -> float | None:
    for alias in aliases.get(key, (key,)):
        value = _float_value(values.get(alias))
        if value is not None:
            return value
    return None


def _dwell_seconds(
    values: Mapping[str, object],
    key: str,
    aliases: Mapping[str, Sequence[str]],
) -> float | None:
    for alias in aliases.get(key, (key,)):
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


__all__ = ["LINE_OVERHEAD_SECONDS", "PlanTimeEstimate", "estimate_plan_time"]
