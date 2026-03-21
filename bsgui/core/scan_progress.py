"""Shared scan progress parsing and timing helpers."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

OUTER_PROGRESS_PATTERN = re.compile(
    r"Preparing stage to run .*? at ([^,]+),\s*(\d+)\s+of\s+(\d+)\s+angles"
)
SCAN_PROGRESS_PATTERN = re.compile(r"Scan_progress:\s*(\d{1,3}(?:\.\d+)?)%")
PERCENT_PATTERN = re.compile(r"(\d{1,3})(?:\.\d+)?\s*%")
FRACTION_PATTERN = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")
STATUS_PREFIXES = (
    "Item updated:",
    "Item added:",
    "Removing",
    "Start executing scan",
    "Done executing scan",
    "Filename:",
)
DONE_SCAN_TEXT = "Done executing scan"
START_SCAN_TEXT = "Start executing scan"


@dataclass
class ScanTimingState:
    """Mutable timing state for ETA estimation across scans."""

    current_2d_start_time: Optional[float] = None
    completed_2d_durations: list[float] = field(default_factory=list)
    last_start_count: int = 0
    last_done_count: int = 0

    def reset(self) -> None:
        self.current_2d_start_time = None
        self.completed_2d_durations.clear()
        self.last_start_count = 0
        self.last_done_count = 0


def extract_status(console_text: str) -> str:
    lines = [
        line.strip()
        for line in console_text.splitlines()
        if any(prefix in line.strip() for prefix in STATUS_PREFIXES)
        and "Scan_progress:" not in line
    ]
    if not lines:
        return ""
    return lines[-1]


def extract_outer_progress(console_text: str) -> tuple[str, Optional[int]]:
    matches = OUTER_PROGRESS_PATTERN.findall(console_text)
    if not matches:
        return "", None

    angle_text, current_text, total_text = matches[-1]
    try:
        current = int(current_text)
        total = int(total_text)
    except ValueError:
        return f"Angle {angle_text.strip()}", None

    progress = int((current / total) * 100) if total > 0 else 0
    status = f"Angle {angle_text.strip()} ({current}/{total})"
    return status, max(0, min(100, progress))


def extract_inner_status(console_text: str) -> str:
    lines = [
        line.strip()
        for line in console_text.splitlines()
        if "Filename:" in line and "Scan_progress:" in line
    ]
    if not lines:
        return ""
    return lines[-1]


def extract_progress(console_text: str) -> Optional[int]:
    scan_progress_matches = SCAN_PROGRESS_PATTERN.findall(console_text)
    if scan_progress_matches:
        try:
            return max(0, min(100, int(float(scan_progress_matches[-1]))))
        except ValueError:
            pass

    percent_matches = PERCENT_PATTERN.findall(console_text)
    if percent_matches:
        try:
            return max(0, min(100, int(float(percent_matches[-1]))))
        except ValueError:
            pass

    fraction_matches = FRACTION_PATTERN.findall(console_text)
    if fraction_matches:
        current_text, total_text = fraction_matches[-1]
        try:
            current = int(current_text)
            total = int(total_text)
        except ValueError:
            return None
        if total > 0:
            return max(0, min(100, int((current / total) * 100)))
    return None


def update_scan_timing(
    console_text: str,
    state: ScanTimingState,
    *,
    monotonic_fn=time.monotonic,
) -> None:
    start_count = console_text.count(START_SCAN_TEXT)
    done_count = console_text.count(DONE_SCAN_TEXT)

    if start_count > state.last_start_count:
        state.current_2d_start_time = monotonic_fn()
        state.last_start_count = start_count

    if done_count > state.last_done_count:
        if state.current_2d_start_time is not None:
            duration = max(0.0, monotonic_fn() - state.current_2d_start_time)
            state.completed_2d_durations.append(duration)
            if len(state.completed_2d_durations) > 20:
                state.completed_2d_durations = state.completed_2d_durations[-20:]
        state.current_2d_start_time = None
        state.last_done_count = done_count


def format_eta(
    console_text: str,
    state: ScanTimingState,
    *,
    monotonic_fn=time.monotonic,
    now_fn=datetime.now,
) -> str:
    outer_status, _ = extract_outer_progress(console_text)
    if not outer_status or not state.completed_2d_durations:
        return ""

    matches = OUTER_PROGRESS_PATTERN.findall(console_text)
    if not matches:
        return ""

    _, current_text, total_text = matches[-1]
    try:
        current = int(current_text)
        total = int(total_text)
    except ValueError:
        return ""
    if total <= 0:
        return ""

    avg_duration = sum(state.completed_2d_durations) / len(state.completed_2d_durations)
    remaining_scans = max(0, total - current)

    if state.current_2d_start_time is not None:
        elapsed = max(0.0, monotonic_fn() - state.current_2d_start_time)
        remaining_current = max(0.0, avg_duration - elapsed)
    else:
        remaining_current = 0.0

    remaining_seconds = remaining_current + remaining_scans * avg_duration
    if remaining_seconds <= 0:
        return "ETA: soon"

    eta = now_fn() + timedelta(seconds=remaining_seconds)
    return f"ETA: {eta.strftime('%Y-%m-%d %H:%M:%S')}"


def render_progress_bar(progress: Optional[int], width: int = 20) -> str:
    if progress is None:
        return f"[{'-' * width}] --%"

    progress = max(0, min(100, int(progress)))
    filled = round((progress / 100) * width)
    return f"[{'#' * filled}{'-' * (width - filled)}] {progress}%"


__all__ = [
    "DONE_SCAN_TEXT",
    "ScanTimingState",
    "extract_inner_status",
    "extract_outer_progress",
    "extract_progress",
    "extract_status",
    "format_eta",
    "render_progress_bar",
    "update_scan_timing",
]
