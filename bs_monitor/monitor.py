"""Build live beamline snapshots from Queue Server metadata and EPICS polling."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .profiles import (
    DEVICE_SPECS,
    LOADED_MANIFEST_PATH,
    PLAN_MONITOR_PROFILES,
    DeviceSpec,
    load_monitor_profiles,
    resolve_monitor_device_specs,
)
from .pv import PVCache


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _to_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_json_value(v) for v in value]
    return str(value)


def _is_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "no", "off", "none"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
        return False
    return bool(value)


def _merge_plan_args_with_defaults(
    plan_name: str,
    plan_args: Mapping[str, Any] | None,
    *,
    allowed_plans: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged = dict(plan_args or {})
    plans = allowed_plans if isinstance(allowed_plans, Mapping) else {}
    plan_spec = plans.get(plan_name)
    if not isinstance(plan_spec, Mapping):
        return merged

    parameters = plan_spec.get("parameters")
    if not isinstance(parameters, list):
        return merged

    for parameter in parameters:
        if not isinstance(parameter, Mapping):
            continue
        name = parameter.get("name")
        if not isinstance(name, str) or name in merged:
            continue
        if "default" in parameter:
            merged[name] = parameter.get("default")
    return merged


def snapshot_device(spec: DeviceSpec, *, pv_cache: PVCache | None = None) -> dict[str, Any]:
    cache = pv_cache or PVCache()
    pvs = {pv.key: cache.snapshot(pv.pvname).as_dict() for pv in spec.pvs}
    return {
        "name": spec.name,
        "category": spec.category,
        "pv_count": len(spec.pvs),
        "pvs": pvs,
    }


def capture_named_device_snapshot(
    device_names: list[str],
    *,
    pv_cache: PVCache | None = None,
    device_specs: Mapping[str, DeviceSpec] | None = None,
    manifest_path: str | None = None,
) -> dict[str, Any]:
    cache = pv_cache or PVCache()
    resolved_specs = dict(device_specs or DEVICE_SPECS)
    loaded_manifest_path = manifest_path or LOADED_MANIFEST_PATH
    if manifest_path:
        resolved_specs, _, loaded_manifest_path = load_monitor_profiles(manifest_path)
    specs = [resolved_specs[name] for name in device_names if name in resolved_specs]
    return {
        "timestamp": _utc_now(),
        "device_names": [spec.name for spec in specs],
        "devices": {spec.name: snapshot_device(spec, pv_cache=cache) for spec in specs},
        "pv_backend": "pyepics" if cache.available else "unavailable",
        "manifest_path": loaded_manifest_path,
        "error": None,
    }


def capture_running_item_snapshot(
    running_item: Mapping[str, Any] | None,
    *,
    include_baseline: bool = True,
    pv_cache: PVCache | None = None,
    manifest_path: str | None = None,
    allowed_plans: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = _utc_now()
    resolved_specs = DEVICE_SPECS
    resolved_profiles = PLAN_MONITOR_PROFILES
    loaded_manifest_path = LOADED_MANIFEST_PATH
    if manifest_path:
        resolved_specs, resolved_profiles, loaded_manifest_path = load_monitor_profiles(manifest_path)

    if not isinstance(running_item, Mapping):
        return {
            "timestamp": timestamp,
            "plan_name": None,
            "plan_args": {},
            "device_names": [],
            "devices": {},
            "manifest_path": loaded_manifest_path,
            "error": "No running queue item found",
        }

    item_type = running_item.get("item_type", "plan")
    plan_name = running_item.get("name")
    if item_type != "plan" or not isinstance(plan_name, str) or not plan_name:
        return {
            "timestamp": timestamp,
            "plan_name": plan_name if isinstance(plan_name, str) else None,
            "plan_args": {},
            "device_names": [],
            "devices": {},
            "manifest_path": loaded_manifest_path,
            "error": "Running item is not an executable plan",
        }

    plan_args = running_item.get("kwargs")
    if not isinstance(plan_args, Mapping):
        plan_args = {}
    plan_args = _merge_plan_args_with_defaults(
        plan_name,
        plan_args,
        allowed_plans=allowed_plans,
    )

    profile = resolved_profiles.get(plan_name, {})
    names: list[str] = []
    if include_baseline:
        names.extend(profile.get("baseline", ()))
    for arg_name, device_name in profile.get("conditional_detectors", {}).items():
        if _is_enabled(plan_args.get(arg_name)):
            names.append(device_name)
    deduped = []
    seen: set[str] = set()
    for name in names:
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    specs = [resolved_specs[name] for name in deduped if name in resolved_specs]
    cache = pv_cache or PVCache()

    return {
        "timestamp": timestamp,
        "plan_name": plan_name,
        "plan_args": _to_json_value(dict(plan_args)),
        "device_names": [spec.name for spec in specs],
        "devices": {spec.name: snapshot_device(spec, pv_cache=cache) for spec in specs},
        "pv_backend": "pyepics" if cache.available else "unavailable",
        "manifest_path": loaded_manifest_path,
        "error": None,
    }


def capture_active_snapshot(
    rm_api: Any,
    *,
    include_baseline: bool = True,
    pv_cache: PVCache | None = None,
    manifest_path: str | None = None,
) -> dict[str, Any]:
    allowed_plans = None
    get_allowed_plans = getattr(rm_api, "get_allowed_plans", None)
    if callable(get_allowed_plans):
        try:
            allowed_plans = get_allowed_plans(normalize=True)
        except Exception:
            allowed_plans = None

    try:
        queue_response = rm_api.queue_get()
    except Exception as exc:
        return {
            "timestamp": _utc_now(),
            "plan_name": None,
            "plan_args": {},
            "device_names": [],
            "devices": {},
            "error": f"Failed to fetch queue state: {exc}",
        }

    running_item = queue_response.get("running_item") if isinstance(queue_response, Mapping) else None
    return capture_running_item_snapshot(
        running_item,
        include_baseline=include_baseline,
        pv_cache=pv_cache,
        manifest_path=manifest_path,
        allowed_plans=allowed_plans,
    )
