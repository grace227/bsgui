"""Manifest-backed plan-to-device and device-to-PV mappings for beamline monitoring."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class PVSpec:
    key: str
    pvname: str


@dataclass(frozen=True)
class DeviceSpec:
    name: str
    category: str
    pvs: tuple[PVSpec, ...]


DEFAULT_DEVICE_SPECS: dict[str, DeviceSpec] = {}


DEFAULT_PLAN_MONITOR_PROFILES: dict[str, dict[str, Any]] = {
    "fly2d_scanrecord": {
        "baseline": ("sample", "scanrecord", "fly_dwell", "bda"),
        "conditional_detectors": {
            "xmap_on": "xmap",
            "xp3_on": "xp3",
            "eiger_on": "eiger",
        },
    },
    "coarse_fine_scanrecord": {
        "baseline": ("sample", "scanrecord", "fly_dwell", "bda"),
        "conditional_detectors": {
            "xmap_on": "xmap",
            "xp3_on": "xp3",
            "eiger_on": "eiger",
        },
    },
}


def _manifest_candidates(manifest_path: str | Path | None = None) -> list[Path]:
    env_path = None
    try:
        import os

        env_path = os.getenv("BEAMLINE_MONITOR_MANIFEST")
    except Exception:
        env_path = None

    candidates: list[Path] = []
    if manifest_path:
        candidates.append(Path(manifest_path).expanduser())
    if env_path:
        candidates.append(Path(env_path).expanduser())

    here = Path(__file__).resolve()
    repo_root = here.parents[2]
    candidates.append(repo_root / "beamline_monitor.json")
    candidates.append(repo_root.parent / "bluesky-mic" / "src" / "bnp" / "qserver" / "beamline_monitor.json")
    return candidates


def _load_manifest(
    manifest_path: str | Path | None = None,
) -> tuple[dict[str, DeviceSpec], dict[str, dict[str, Any]], str | None]:
    for candidate in _manifest_candidates(manifest_path):
        if not candidate.exists():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue

        raw_devices = payload.get("devices")
        raw_plans = payload.get("plans")
        if not isinstance(raw_devices, Mapping) or not isinstance(raw_plans, Mapping):
            continue

        devices: dict[str, DeviceSpec] = {}
        for device_name, device_payload in raw_devices.items():
            if not isinstance(device_payload, Mapping):
                continue
            signals = device_payload.get("signals")
            extras = device_payload.get("extras")
            pvs: list[PVSpec] = []
            for source in (signals, extras):
                if not isinstance(source, Mapping):
                    continue
                for key, record in source.items():
                    if not isinstance(record, Mapping):
                        continue
                    pvname = record.get("pvname") or record.get("read_pv") or record.get("write_pv")
                    if isinstance(pvname, str) and pvname:
                        pvs.append(PVSpec(str(key), pvname))
            devices[device_name] = DeviceSpec(
                name=str(device_name),
                category=str(device_payload.get("category", "device")),
                pvs=tuple(pvs),
            )

        plans = {str(key): dict(value) for key, value in raw_plans.items() if isinstance(value, Mapping)}
        if devices and plans:
            return devices, plans, str(candidate)

    return DEFAULT_DEVICE_SPECS, DEFAULT_PLAN_MONITOR_PROFILES, None


DEVICE_SPECS, PLAN_MONITOR_PROFILES, LOADED_MANIFEST_PATH = _load_manifest()


def load_monitor_profiles(
    manifest_path: str | Path | None = None,
) -> tuple[dict[str, DeviceSpec], dict[str, dict[str, Any]], str | None]:
    return _load_manifest(manifest_path)


def resolve_monitor_device_names(
    plan_name: str,
    *,
    plan_args: Mapping[str, Any] | None = None,
    include_baseline: bool = True,
) -> list[str]:
    profile = PLAN_MONITOR_PROFILES.get(plan_name, {})
    args = dict(plan_args or {})
    names: list[str] = []
    if include_baseline:
        names.extend(profile.get("baseline", ()))
    for arg_name, device_name in profile.get("conditional_detectors", {}).items():
        if bool(args.get(arg_name)):
            names.append(device_name)

    resolved: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name not in seen:
            seen.add(name)
            resolved.append(name)
    return resolved


def resolve_monitor_device_specs(
    plan_name: str,
    *,
    plan_args: Mapping[str, Any] | None = None,
    include_baseline: bool = True,
) -> list[DeviceSpec]:
    return [
        DEVICE_SPECS[name]
        for name in resolve_monitor_device_names(
            plan_name,
            plan_args=plan_args,
            include_baseline=include_baseline,
        )
        if name in DEVICE_SPECS
    ]
