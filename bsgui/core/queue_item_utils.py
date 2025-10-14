"""Utility helpers for manipulating queue items."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from copy import deepcopy
from typing import Any, Optional

from .qserver_controller import PlanDefinition


def normalize_roi_map(
    roi_key_map: Optional[Mapping[str, Sequence[str]]],
) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    if not isinstance(roi_key_map, Mapping):
        return normalized
    for key, values in roi_key_map.items():
        if not isinstance(key, str):
            continue
        if isinstance(values, str):
            normalized[key] = [values]
        elif isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            collected = [str(value) for value in values if isinstance(value, str)]
            if collected:
                normalized[key] = collected
    return normalized


def clone_item(item: Any) -> dict[str, Any]:
    if isinstance(item, MutableMapping):
        return deepcopy(item)
    return {"name": str(item)}


def prepare_display_item(item: Mapping[str, Any] | Any, *, completed: bool = False) -> dict[str, Any]:
    if isinstance(item, Mapping):
        normalized: dict[str, Any] = dict(item)
    else:
        normalized = {"name": str(item)}

    nested_item = normalized.get("item")
    if isinstance(nested_item, Mapping):
        nested = dict(nested_item)
        normalized["item"] = nested
        normalized.setdefault("name", nested.get("name"))
        if "kwargs" not in normalized and isinstance(nested.get("kwargs"), Mapping):
            normalized["kwargs"] = dict(nested["kwargs"])

    kwargs = normalized.get("kwargs")
    if isinstance(kwargs, Mapping):
        normalized["kwargs"] = dict(kwargs)

    if completed:
        result = normalized.get("result")
        if isinstance(result, Mapping):
            status_from_result = result.get("status") or result.get("state")
            exit_status_from_result = result.get("exit_status")
            if status_from_result:
                normalized["status"] = status_from_result
            if exit_status_from_result:
                normalized["exit_status"] = exit_status_from_result

        nested = normalized.get("item")
        if isinstance(nested, Mapping):
            status_from_item = nested.get("status")
            exit_status_from_item = nested.get("exit_status")
            if status_from_item and "status" not in normalized:
                normalized["status"] = status_from_item
            if exit_status_from_item and "exit_status" not in normalized:
                normalized["exit_status"] = exit_status_from_item

        normalized.setdefault("status", "completed")
        normalized.setdefault("state", normalized.get("status"))

    normalized.setdefault("name", "Unknown")
    return normalized


def extract_item_field(item: Mapping[str, Any], key: str) -> Any:
    if not isinstance(item, Mapping):
        return None

    sentinel = object()
    key_parts = key.split(".") if isinstance(key, str) and "." in key else [key]

    def resolve(mapping: Mapping[str, Any]) -> Any:
        current: Any = mapping
        for part in key_parts:
            if isinstance(current, Mapping):
                if part in current:
                    current = current.get(part)
                else:
                    return sentinel
            elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)):
                next_value = sentinel
                for entry in current:
                    if isinstance(entry, Mapping) and part in entry:
                        next_value = entry.get(part)
                        break
                if next_value is sentinel:
                    return sentinel
                current = next_value
            else:
                return sentinel
        return current

    for candidate in (
        item,
        item.get("kwargs"),
        item.get("result"),
        item.get("metadata"),
        item.get("item"),
    ):
        if isinstance(candidate, Mapping):
            value = resolve(candidate)
            if value is not sentinel:
                return value

    return None


def lookup_roi_value(
    column_id: str,
    item: Mapping[str, Any],
    roi_key_map: Mapping[str, list[str]],
    *,
    include_key: bool = False,
    available_params: Optional[set[str]] = None,
) -> Any:
    candidates = roi_key_map.get(column_id, [])
    if not candidates:
        return None

    def _check_mapping(mapping: Mapping[str, Any]) -> Any:
        for candidate in candidates:
            if candidate in mapping:
                value = mapping.get(candidate)
                if value is not None:
                    return (value, candidate) if include_key else value
        if available_params is not None:
            for candidate in candidates:
                    if candidate in available_params:
                        return (None, candidate) if include_key else None
        return None

    kwargs = item.get("kwargs")
    if isinstance(kwargs, Mapping):
        value = _check_mapping(kwargs)
        if value is not None:
            return value

    nested_item = item.get("item")
    if isinstance(nested_item, Mapping):
        nested_kwargs = nested_item.get("kwargs")
        if isinstance(nested_kwargs, Mapping):
            value = _check_mapping(nested_kwargs)
            if value is not None:
                return value

    for candidate in candidates:
        value = extract_item_field(item, candidate)
        if value is not None:
            return (value, candidate) if include_key else value
    if available_params is not None:
        for candidate in candidates:
            if candidate in available_params:
                return (None, candidate) if include_key else None

    return None


def resolve_queue_value(
    column_id: str,
    item: Mapping[str, Any],
    row_index: int,
    *,
    roi_key_map: Mapping[str, list[str]],
    roi_value_aliases: set[str],
    available_params: Optional[set[str]] = None,
    running = False,
) -> tuple[str, Optional[str]]:
    if column_id == "index":
        return str(row_index + 1), None
    if column_id in roi_key_map:
        roi_value = lookup_roi_value(
            column_id,
            item,
            roi_key_map,
            include_key=True,
            available_params=available_params,
        )
        if roi_value is not None:
            value, key = roi_value
            # print(f"roi_value: {roi_value}")
            return format_scalar(value), key or column_id
    if column_id == "name":
        value = extract_item_field(item, "name") or item.get("name") or "Unknown"
        return str(value), "name"

    kwargs = item.get("kwargs") if isinstance(item, Mapping) else None
    if isinstance(kwargs, Mapping) and column_id in kwargs:
        return format_scalar(kwargs.get(column_id)), column_id

    value = extract_item_field(item, column_id)
    if column_id in {"plan", "name"}:
        return str(value or item.get("name") or "Unknown"), column_id
    if column_id in {"state", "status"}:
        if item.get("result", None) is not None:
            if item.get("result").get("exit_status", None) is not None:
                status = item.get("result").get("exit_status")
        elif item.get("status", None) is not None:
            status = item.get("status") 
        elif running:
            status = "Running"
        else:
            status = "Pending"
        return status, column_id
    if column_id == "scan_ids":
        if item.get("result", None) is not None:
            if item.get("result").get("scan_ids", None) is not None:
                scan_ids = item.get("result").get("scan_ids")
                return format_sequence(scan_ids), column_id
        return "", column_id
    if column_id in {"uid", "item_uid"}:
        uid = value or item.get("item_uid") or item.get("uid")
        return str(uid or ""), column_id
    if column_id == "args":
        args = value or item.get("args") or []
        return format_sequence(args), column_id
    if column_id == "kwargs":
        kwargs = value or item.get("kwargs") or {}
        if isinstance(kwargs, Mapping):
            text = ", ".join(f"{key}={format_scalar(val)}" for key, val in kwargs.items())
            return text, None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return format_sequence(value), column_id
    if isinstance(value, Mapping):
        text = ", ".join(f"{key}={format_scalar(val)}" for key, val in value.items())
        return text, column_id
    if value is None:
        fallback = item.get(column_id)
        if isinstance(fallback, Sequence) and not isinstance(fallback, (str, bytes)):
            return format_sequence(fallback), column_id
        if isinstance(fallback, Mapping):
            text = ", ".join(f"{key}={format_scalar(val)}" for key, val in fallback.items())
            return text, column_id
        if fallback is None:
            return "", column_id
        return format_scalar(fallback), column_id
    return format_scalar(value), column_id


def apply_item_edit(
    item: MutableMapping[str, Any],
    column_id: str,
    text_value: str,
    *,
    plan_name: str,
    plan_definitions: Mapping[str, PlanDefinition],
    roi_key_map: Mapping[str, list[str]],
) -> bool:
    value = coerce_for_key(plan_definitions, plan_name, column_id, text_value)

    if set_if_exists(item, column_id, value):
        return True

    kwargs = item.get("kwargs")
    if isinstance(kwargs, MutableMapping) and column_id in kwargs:
        kwargs[column_id] = value
        return True

    if column_id in roi_key_map:
        aliases = roi_key_map[column_id]
        for alias in aliases:
            if alias == column_id:
                continue
            if apply_item_edit(
                item,
                alias,
                text_value,
                plan_name=plan_name,
                plan_definitions=plan_definitions,
                roi_key_map=roi_key_map,
            ):
                return True
        if aliases:
            container = ensure_kwargs_container(item)
            alias = aliases[0]
            container[alias] = coerce_for_key(plan_definitions, plan_name, alias, text_value)
            return True
        return False

    for nested_key in ("item", "metadata", "result"):
        nested = item.get(nested_key)
        if isinstance(nested, MutableMapping) and apply_item_edit(
            nested,
            column_id,
            text_value,
            plan_name=plan_name,
            plan_definitions=plan_definitions,
            roi_key_map=roi_key_map,
        ):
            return True

    container = ensure_kwargs_container(item)
    container[column_id] = value
    return True


def set_if_exists(mapping: MutableMapping[str, Any], key: str, value: Any) -> bool:
    if key in mapping:
        mapping[key] = value
        return True
    return False


def ensure_kwargs_container(item: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    kwargs = item.get("kwargs")
    if not isinstance(kwargs, MutableMapping):
        kwargs = {}
        item["kwargs"] = kwargs
    return kwargs


def coerce_for_key(
    plan_definitions: Mapping[str, PlanDefinition],
    plan_name: str,
    key: str,
    text_value: str,
) -> Any:
    definition = plan_definitions.get(plan_name)
    if definition is not None:
        for parameter in definition.parameters:
            if parameter.name == key:
                return parameter.coerce(text_value)
    return text_value


def build_update_payload(
    raw_item: Mapping[str, Any],
    row_values: Mapping[str, str],
    *,
    exclude_keys: Optional[Iterable[str]] = None,
    plan_definitions: Mapping[str, PlanDefinition],
    plan_name: str,
) -> dict[str, Any]:
    exclude = set(exclude_keys or ())
    plan = plan_definitions.get(plan_name or "")
    param_lookup = {parameter.name: parameter for parameter in plan.parameters} if plan else {}

    payload = clone_item(raw_item)
    kwargs = ensure_kwargs_container(payload)

    # Start from existing kwargs so we remove blanked entries.
    updates = {}
    removals: set[str] = set()
    for key, value in row_values.items():
        if key in exclude:
            continue
        if isinstance(value, str) and value.strip() == "":
            removals.add(key)
            continue
        parameter = param_lookup.get(key)
        if parameter is not None and isinstance(value, str):
            try:
                coerced = parameter.coerce(value)
            except ValueError as exc:
                raise ValueError(f"{key}: {exc}") from exc
        else:
            coerced = value
        updates[key] = coerced

    for key in removals:
        kwargs.pop(key, None)
    kwargs.update(updates)

    return payload


def format_scalar(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def format_sequence(value: Iterable[Any]) -> str:
    return ", ".join(str(entry) for entry in value)
