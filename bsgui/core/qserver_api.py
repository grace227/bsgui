from __future__ import annotations

import ast
from typing import Any, Dict, Iterator, List, Mapping, Optional

from bluesky_queueserver_api.zmq import REManagerAPI
from bluesky_queueserver import ReceiveConsoleOutput


class QServerAPI(REManagerAPI):
    """API wrapper that handles connection state tracking for Bluesky QServer."""

    _rm_status: Dict[str, Any] = {}
    _console_output: Optional[ReceiveConsoleOutput] = None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rm_status = {}
        self._console_output = ReceiveConsoleOutput(zmq_subscribe_addr=kwargs.get("zmq_info_addr", None))

    def get_status(self, selected_keys: Optional[List[str]] = None) -> Dict[str, Any]:
        try:
            status = self.status()
            self._rm_status["connected"] = True
            if selected_keys is not None:
                for key in selected_keys:
                    self._rm_status[key] = status.get(key, None)
            else:
                self._rm_status = status
            self._rm_status["qserver_address"] = self._zmq_info_addr

        except Exception as exc:  # pragma: no cover - network path
            print(f"Error fetching status: {exc}")
            # self._connected = False
            self._rm_status = {} 
            self._rm_status["connected"] = False

        return self._rm_status

    def get_queue(self) -> Dict[str, Any]:
        try:
            queue = self.queue_get()
        except Exception as exc:  # pragma: no cover - network path
            print(f"Error fetching queue: {exc}")
            self._connected = False
            return {}
        self._connected = True
        return queue

    def get_allowed_plans(self, *, normalize: bool = False) -> Dict[str, Any]:
        try:
            plans = self.plans_allowed()["plans_allowed"]
        except Exception as exc:  # pragma: no cover - network path
            print(f"Error fetching allowed plans: {exc}")
            return {}
        processed = self._normalize_allowed_plans(plans) if normalize else dict(plans)
        processed.pop("make_devices", None)
        return processed

    @staticmethod
    def _normalize_allowed_plans(plans: Mapping[str, Any]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        for name, spec in plans.items():
            if not isinstance(spec, Mapping):
                continue
            parameters = []
            for param in spec.get("parameters", []):
                if not isinstance(param, Mapping):
                    continue
                p_name = param.get("name")
                if not isinstance(p_name, str):
                    continue
                normalized_param = dict(param)
                normalized_param["default"] = QServerAPI._coerce_default_value(param.get("default"))
                parameters.append(normalized_param)
            norm_spec = dict(spec)
            norm_spec["parameters"] = parameters
            normalized[name] = norm_spec
        return normalized

    @staticmethod
    def _coerce_default_value(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if stripped == "":
                return ""
            try:
                return ast.literal_eval(stripped)  # type: ignore[arg-type]
            except Exception:
                if (stripped.startswith("'") and stripped.endswith("'")) or (
                    stripped.startswith('"') and stripped.endswith('"')
                ):
                    return stripped[1:-1]
                return stripped
        return value



    def recv_console_message(self, timeout: float = 1) -> Optional[Dict[str, Any]]:
        receiver = self._console_output

        timeout_ms: Optional[int]
        if timeout is None:
            timeout_ms = None
        else:
            timeout_ms = max(0, int(timeout * 1000))

        try:
            message = receiver.recv(timeout=timeout_ms)
        except TimeoutError:
            return None
        except Exception:  # pragma: no cover - network path
            return None

        if not message:
            return None
        if isinstance(message, dict):
            return message
        return {"text": message}
