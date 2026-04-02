"""Core helpers for generating batch plan items."""

from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence


def normalize_iteration_actions(raw_actions: object) -> Dict[str, Mapping[str, object]]:
    actions: Dict[str, Mapping[str, object]] = {}
    if not isinstance(raw_actions, Mapping):
        return actions
    for key, value in raw_actions.items():
        if isinstance(key, str) and isinstance(value, Mapping):
            actions[key] = value
    return actions


def build_iteration_values(start: float, stop: float, step: float) -> List[float]:
    if step == 0:
        raise ValueError("iterate_step_size must not be zero")
    if step > 0 and start > stop:
        raise ValueError("iterate_step_size is positive but start is greater than end")
    if step < 0 and start < stop:
        raise ValueError("iterate_step_size is negative but start is less than end")

    values: List[float] = []
    current = float(start)
    epsilon = max(abs(step) * 1e-9, 1e-12)
    if step > 0:
        while current <= stop + epsilon:
            values.append(round(current, 4))
            current += step
    else:
        while current >= stop - epsilon:
            values.append(round(current, 4))
            current += step
    return values


def apply_roi_payload_to_kwargs(
    kwargs: Dict[str, object],
    payload: Mapping[str, object],
    roi_key_map: Mapping[str, Sequence[str]],
    *,
    allowed_parameters: Optional[Sequence[str]] = None,
) -> None:
    allowed = {str(name) for name in allowed_parameters} if allowed_parameters is not None else None
    for roi_key, value in payload.items():
        targets = roi_key_map.get(str(roi_key), ())
        for target in targets:
            if allowed is not None and str(target) not in allowed:
                continue
            kwargs[str(target)] = value


def execute_iteration_action(
    api: object,
    spec: Mapping[str, object],
    *,
    sync_inputs: Mapping[str, object],
    iterate_value: object,
) -> Dict[str, object]:
    function_name = spec.get("qserver_function")
    if not isinstance(function_name, str) or not function_name.strip():
        raise ValueError("Iteration action is missing qserver_function")

    input_map = spec.get("input_map")
    if not isinstance(input_map, Mapping):
        input_map = {}

    call_kwargs: Dict[str, object] = {}
    for arg_name, source_name in input_map.items():
        if not isinstance(arg_name, str) or not isinstance(source_name, str):
            continue
        if source_name == "__iterate_value__":
            value = iterate_value
        else:
            value = sync_inputs.get(source_name)
        if value is None:
            raise ValueError(f"Unable to resolve sync input '{source_name}'")
        call_kwargs[arg_name] = value

    timeout = float(spec.get("timeout", 5.0))
    sync_method = getattr(api, function_name, None)
    if not callable(sync_method):
        raise ValueError(f"QServer API method '{function_name}' is not available")

    result = sync_method(timeout=timeout, **call_kwargs)
    payload = normalize_action_result(result, spec)
    if not payload:
        raise ValueError(f"No sync data returned from '{function_name}'")
    return payload


def normalize_action_result(result: object, spec: Mapping[str, object]) -> Dict[str, object]:
    transform = spec.get("transform")
    if isinstance(transform, Mapping):
        return apply_action_transform(result, transform)
    if isinstance(result, Mapping):
        return {str(key): value for key, value in result.items()}
    result_keys = spec.get("result_keys")
    if (
        isinstance(result_keys, Sequence)
        and not isinstance(result_keys, (str, bytes))
        and isinstance(result, Sequence)
        and not isinstance(result, (str, bytes))
    ):
        return {
            str(key): value
            for key, value in zip(result_keys, result)
            if isinstance(key, str)
        }
    return {}


def apply_action_transform(result: object, transform: Mapping[str, object]) -> Dict[str, object]:
    payload: Dict[str, object] = {}
    for target_key, spec in transform.items():
        value = resolve_transform_value(result, spec)
        if value is not None:
            payload[str(target_key)] = value
    return payload


def resolve_transform_value(result: object, spec: object) -> object | None:
    if isinstance(spec, Mapping):
        source = spec.get("source")
        value = extract_sync_source_value(result, source)
        if value is None:
            return None
        if isinstance(value, (int, float)):
            scale = spec.get("scale", 1)
            offset = spec.get("offset", 0)
            try:
                value = value * float(scale) + float(offset)
            except (TypeError, ValueError):
                return None
            digits = spec.get("round")
            if digits is not None:
                try:
                    value = round(value, int(digits))
                except (TypeError, ValueError):
                    return None
        return value
    return extract_sync_source_value(result, spec)


def extract_sync_source_value(result: object, source: object) -> object | None:
    if isinstance(source, int):
        if isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
            return result[source] if -len(result) <= source < len(result) else None
        return None
    if isinstance(source, str):
        if isinstance(result, Mapping):
            return result.get(source)
        return None
    return None
