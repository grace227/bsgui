"""Detector recovery helpers for beamline monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .health import detector_hung
from .pv import PVCache

DETECTOR_RECOVERY_COOLDOWN_SECONDS = 30.0


def configure_detector_recovery(*, detector_recovery_cooldown_seconds: float | None = None) -> None:
    global DETECTOR_RECOVERY_COOLDOWN_SECONDS
    if detector_recovery_cooldown_seconds is not None:
        DETECTOR_RECOVERY_COOLDOWN_SECONDS = float(detector_recovery_cooldown_seconds)
        DEFAULT_RECOVERY_MANAGER._cooldown_seconds = max(0.0, DETECTOR_RECOVERY_COOLDOWN_SECONDS)


@dataclass(frozen=True)
class DetectorCommand:
    pvname: str
    value: Any
    wait: bool = False


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _command_pvname(readback_pvname: Any) -> str | None:
    if not isinstance(readback_pvname, str) or not readback_pvname:
        return None
    if readback_pvname.endswith("_RBV"):
        return readback_pvname[:-4]
    return readback_pvname


def _device_pvs(snapshot: Mapping[str, Any], device_name: str) -> Mapping[str, Any]:
    devices = snapshot.get("devices")
    if not isinstance(devices, Mapping):
        return {}
    device = devices.get(device_name)
    if not isinstance(device, Mapping):
        return {}
    pvs = device.get("pvs")
    return pvs if isinstance(pvs, Mapping) else {}


def _pvname_from_key(snapshot: Mapping[str, Any], device_name: str, key: str) -> str | None:
    pvs = _device_pvs(snapshot, device_name)
    pv = pvs.get(key)
    if not isinstance(pv, Mapping):
        return None
    return _command_pvname(pv.get("pvname"))


def _xmap_prefix(snapshot: Mapping[str, Any]) -> str | None:
    pvs = _device_pvs(snapshot, "xmap")
    capture = pvs.get("fileplugin.capture")
    if not isinstance(capture, Mapping):
        return None
    pvname = capture.get("pvname")
    if not isinstance(pvname, str):
        return None
    marker = ":netCDF1:"
    if marker in pvname:
        return pvname.split(marker, 1)[0]
    return None


def _build_recovery_commands(device_name: str, snapshot: Mapping[str, Any]) -> list[DetectorCommand]:
    commands: list[DetectorCommand] = []

    capture_pv = _pvname_from_key(snapshot, device_name, "fileplugin.capture")
    acquire_pv = _pvname_from_key(snapshot, device_name, "cam.acquire")

    if device_name == "xmap":
        xmap_prefix = _xmap_prefix(snapshot)
        if xmap_prefix:
            commands.append(DetectorCommand(f"{xmap_prefix}:StopAll", 1, wait=True))
        if capture_pv:
            commands.append(DetectorCommand(capture_pv, 0, wait=True))
        return commands

    if acquire_pv:
        commands.append(DetectorCommand(acquire_pv, 0, wait=True))
    if capture_pv:
        commands.append(DetectorCommand(capture_pv, 0, wait=True))
    return commands


class DetectorRecoveryManager:
    """Stateful detector recovery with basic cooldown protection."""

    def __init__(self, *, pv_cache: PVCache | None = None, cooldown_seconds: float = DETECTOR_RECOVERY_COOLDOWN_SECONDS):
        self._pv_cache = pv_cache or PVCache()
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._last_attempt_at: dict[str, datetime] = {}

    def should_attempt(self, device_name: str, *, now: datetime | None = None) -> bool:
        current = now or _utc_now()
        previous = self._last_attempt_at.get(device_name)
        if previous is None:
            return True
        return current - previous >= timedelta(seconds=self._cooldown_seconds)

    def recover_detector(self, device_name: str, snapshot: Mapping[str, Any], *, force: bool = False) -> dict[str, Any]:
        now = _parse_timestamp(snapshot.get("timestamp")) or _utc_now()
        if device_name not in {"xmap", "xp3", "eiger"}:
            return {
                "device": device_name,
                "attempted": False,
                "recovered": False,
                "reason": "Unsupported detector",
                "commands": [],
                "timestamp": now.isoformat(),
            }

        if not detector_hung(device_name, snapshot):
            return {
                "device": device_name,
                "attempted": False,
                "recovered": False,
                "reason": "Detector is not hung",
                "commands": [],
                "timestamp": now.isoformat(),
            }

        if not force and not self.should_attempt(device_name, now=now):
            return {
                "device": device_name,
                "attempted": False,
                "recovered": False,
                "reason": "Cooldown active",
                "commands": [],
                "timestamp": now.isoformat(),
            }

        commands = _build_recovery_commands(device_name, snapshot)
        if not commands:
            return {
                "device": device_name,
                "attempted": False,
                "recovered": False,
                "reason": "No recovery commands available",
                "commands": [],
                "timestamp": now.isoformat(),
            }

        self._last_attempt_at[device_name] = now
        command_results = [
            self._pv_cache.put(command.pvname, command.value, wait=command.wait)
            for command in commands
        ]
        recovered = all(bool(result.get("ok")) for result in command_results)
        return {
            "device": device_name,
            "attempted": True,
            "recovered": recovered,
            "reason": None if recovered else "One or more recovery PV writes failed",
            "commands": command_results,
            "timestamp": now.isoformat(),
        }

    def recover_hung_detectors(
        self,
        snapshot: Mapping[str, Any],
        *,
        detector_names: Iterable[str] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        names = list(detector_names or ("xmap", "xp3", "eiger"))
        results = [self.recover_detector(name, snapshot, force=force) for name in names]
        return {
            "timestamp": (_parse_timestamp(snapshot.get("timestamp")) or _utc_now()).isoformat(),
            "results": results,
        }


DEFAULT_RECOVERY_MANAGER = DetectorRecoveryManager()


def recover_hung_detectors(snapshot: Mapping[str, Any], *, force: bool = False) -> dict[str, Any]:
    return DEFAULT_RECOVERY_MANAGER.recover_hung_detectors(snapshot, force=force)


__all__ = [
    "DEFAULT_RECOVERY_MANAGER",
    "DETECTOR_RECOVERY_COOLDOWN_SECONDS",
    "DetectorCommand",
    "DetectorRecoveryManager",
    "configure_detector_recovery",
    "recover_hung_detectors",
]
