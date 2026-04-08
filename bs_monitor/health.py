"""Custom device health evaluation for beamline monitoring snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

DETECTOR_TIMEOUT_FACTOR = 3.0
SAMPLE_POSITION_TOLERANCE = 0.1


def configure_health(
    *,
    detector_timeout_factor: float | None = None,
    sample_position_tolerance: float | None = None,
) -> None:
    global DETECTOR_TIMEOUT_FACTOR, SAMPLE_POSITION_TOLERANCE
    if detector_timeout_factor is not None:
        DETECTOR_TIMEOUT_FACTOR = float(detector_timeout_factor)
    if sample_position_tolerance is not None:
        SAMPLE_POSITION_TOLERANCE = float(sample_position_tolerance)


def _pv_value(pv: Any) -> Any:
    if isinstance(pv, Mapping):
        value = pv.get("char_value")
        if value not in (None, ""):
            return value
        return pv.get("value")
    return None


def _truthy_pv(pv: Any) -> bool:
    value = _pv_value(pv)
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized not in {"", "0", "false", "done", "idle", "off", "go"}
    return bool(value)


def _pv_at_least_one(pv: Any) -> bool:
    value = _pv_value(pv)
    try:
        return float(value) >= 1
    except Exception:
        return False


def _pv_float(pv: Any) -> float | None:
    value = _pv_value(pv)
    try:
        return float(value)
    except Exception:
        return None


def _parse_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _scanrecord_paused(pvs: Mapping[str, Any]) -> bool:
    return (
        _truthy_pv(pvs.get("pause_signal"))
        or _pv_at_least_one(pvs.get("inner.wait"))
        or _pv_at_least_one(pvs.get("outer.wait"))
    )


def _sample_hung_axes(snapshot: Mapping[str, Any]) -> list[str]:
    devices = snapshot.get("devices")
    if not isinstance(devices, Mapping):
        return []
    sample = devices.get("sample")
    if not isinstance(sample, Mapping):
        return []
    pvs = sample.get("pvs")
    if not isinstance(pvs, Mapping):
        return []
    if _truthy_pv(pvs.get("busy")):
        return []

    axes: list[str] = []
    axis_checks = {
        "x": [("x.piezo.setpoint", "x.piezo.readback"), ("x.stepper.setpoint", "x.stepper.readback")],
        "y": [("y.piezo.setpoint", "y.piezo.readback"), ("y.stepper.setpoint", "y.stepper.readback")],
        "z": [("z.setpoint", "z.readback")],
        "theta": [("theta.setpoint", "theta.readback")],
    }
    for axis, pairs in axis_checks.items():
        for setpoint_key, readback_key in pairs:
            setpoint = _pv_float(pvs.get(setpoint_key))
            readback = _pv_float(pvs.get(readback_key))
            if setpoint is None or readback is None:
                continue
            if abs(setpoint - readback) > SAMPLE_POSITION_TOLERANCE:
                axes.append(axis)
                break
    return axes


def _ring_current(snapshot: Mapping[str, Any]) -> float | None:
    devices = snapshot.get("devices")
    if not isinstance(devices, Mapping):
        return None
    ring = devices.get("ring")
    if not isinstance(ring, Mapping):
        return None
    pvs = ring.get("pvs")
    if not isinstance(pvs, Mapping):
        return None
    try:
        return float(_pv_value(pvs.get("current")))
    except Exception:
        return None


def _ring_mode(snapshot: Mapping[str, Any]) -> str:
    devices = snapshot.get("devices")
    if not isinstance(devices, Mapping):
        return ""
    ring = devices.get("ring")
    if not isinstance(ring, Mapping):
        return ""
    pvs = ring.get("pvs")
    if not isinstance(pvs, Mapping):
        return ""
    value = _pv_value(pvs.get("operating_mode"))
    return str(value or "")


def _fly_dwell_seconds(snapshot: Mapping[str, Any]) -> float | None:
    devices = snapshot.get("devices")
    if not isinstance(devices, Mapping):
        return None
    fly_dwell = devices.get("fly_dwell")
    if not isinstance(fly_dwell, Mapping):
        return None
    pvs = fly_dwell.get("pvs")
    if not isinstance(pvs, Mapping):
        return None
    try:
        dwell_ms = float(_pv_value(pvs.get("value")))
        return max(0.0, dwell_ms / 1000.0)
    except Exception:
        return None


def _inner_scan_point_count(snapshot: Mapping[str, Any]) -> float | None:
    devices = snapshot.get("devices")
    if not isinstance(devices, Mapping):
        return None
    scanrecord = devices.get("scanrecord")
    if not isinstance(scanrecord, Mapping):
        return None
    pvs = scanrecord.get("pvs")
    if not isinstance(pvs, Mapping):
        return None
    try:
        return float(_pv_value(pvs.get("inner.number_points")))
    except Exception:
        return None


def _scan_phase_waiting_for_detectors(snapshot: Mapping[str, Any]) -> bool:
    devices = snapshot.get("devices")
    if not isinstance(devices, Mapping):
        return False
    scanrecord = devices.get("scanrecord")
    if not isinstance(scanrecord, Mapping):
        return False
    pvs = scanrecord.get("pvs")
    if not isinstance(pvs, Mapping):
        return False
    waiting_phases = {"WAIT:DETCTRS", "WAIT:AFTER_SCAN"}
    for key in ("inner.scan_phase", "outer.scan_phase"):
        phase = _pv_value(pvs.get(key))
        if isinstance(phase, str) and phase.strip() in waiting_phases:
            return True
    return False


def _detector_hung(device_name: str, snapshot: Mapping[str, Any]) -> bool:
    devices = snapshot.get("devices")
    if not isinstance(devices, Mapping):
        return False
    device = devices.get(device_name)
    if not isinstance(device, Mapping):
        return False
    pvs = device.get("pvs")
    if not isinstance(pvs, Mapping):
        return False

    scanrecord = devices.get("scanrecord")
    if not isinstance(scanrecord, Mapping):
        return False
    scanrecord_pvs = scanrecord.get("pvs")
    if not isinstance(scanrecord_pvs, Mapping):
        return False

    if _scanrecord_paused(scanrecord_pvs):
        return False
    if not _scan_phase_waiting_for_detectors(snapshot):
        return False

    ring_current = _ring_current(snapshot)
    if ring_current is None or ring_current <= 0:
        return False

    acquiring = False
    if device_name == "xmap":
        acquiring = _truthy_pv(pvs.get("fileplugin.capture")) or _truthy_pv(pvs.get("fileplugin.write_file"))
    else:
        acquiring = _truthy_pv(pvs.get("cam.acquire")) or _truthy_pv(pvs.get("fileplugin.capture"))
    if not acquiring:
        return False

    dwell_seconds = _fly_dwell_seconds(snapshot)
    if dwell_seconds is None or dwell_seconds <= 0:
        return False
    inner_point_count = _inner_scan_point_count(snapshot)
    if inner_point_count is None or inner_point_count <= 0:
        return False

    capture = pvs.get("fileplugin.capture")
    if not isinstance(capture, Mapping):
        return False

    now_ts = _parse_timestamp(snapshot.get("timestamp"))
    capture_ts = _parse_timestamp(capture.get("timestamp"))
    if now_ts is None or capture_ts is None:
        return False

    age = (now_ts - capture_ts).total_seconds()
    timeout_seconds = DETECTOR_TIMEOUT_FACTOR * inner_point_count * dwell_seconds
    return age > timeout_seconds


def evaluate_device_health(device_name: str, device: Mapping[str, Any], snapshot: Mapping[str, Any]) -> str:
    pvs = device.get("pvs")
    if not isinstance(pvs, Mapping) or not pvs:
        return "warning"

    if device_name == "ring":
        current = _ring_current(snapshot)
        mode = _ring_mode(snapshot).upper()
        if current is not None and current < 10 and "NO BEAM" in mode:
            return "error"
        if current is not None and current < 100:
            return "warning"

    if device_name == "scanrecord" and _scanrecord_paused(pvs):
        return "warning"

    if device_name == "sample" and _sample_hung_axes(snapshot):
        return "error"

    if device_name in {"xmap", "xp3", "eiger"} and _detector_hung(device_name, snapshot):
        return "error"

    connected = 0
    disconnected = 0
    degraded = 0
    for pv in pvs.values():
        if not isinstance(pv, Mapping):
            degraded += 1
            continue
        if pv.get("error"):
            degraded += 1
        if pv.get("connected"):
            connected += 1
        else:
            disconnected += 1

    if connected and not disconnected and not degraded:
        return "ok"
    if connected:
        return "warning"
    return "error"


def detector_hung(device_name: str, snapshot: Mapping[str, Any]) -> bool:
    return _detector_hung(device_name, snapshot)


def sample_hung_axes(snapshot: Mapping[str, Any]) -> list[str]:
    return _sample_hung_axes(snapshot)


__all__ = ["configure_health", "detector_hung", "evaluate_device_health", "sample_hung_axes"]
