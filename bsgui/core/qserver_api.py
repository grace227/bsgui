from __future__ import annotations

import ast
from collections import deque
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional

from bluesky_queueserver_api.zmq import REManagerAPI
from bluesky_queueserver_api import BFunc
from bluesky_queueserver import ReceiveConsoleOutput
import time

class _ConsoleMonitorBuffer:
    def __init__(self, *, max_messages: int = 2000) -> None:
        self._buffer = deque(maxlen=max(1, max_messages))

    def append(self, message: Mapping[str, Any] | str) -> None:
        if isinstance(message, Mapping):
            text = ""
            for key in ("text", "msg", "message"):
                value = message.get(key)
                if value:
                    text = str(value)
                    break
        else:
            text = str(message)
        if text:
            self._buffer.append(text)

    def clear(self) -> None:
        self._buffer.clear()

    def clear_matching(self, patterns: list[str] | tuple[str, ...]) -> None:
        if not patterns:
            return
        retained = [entry for entry in self._buffer if not any(pattern in entry for pattern in patterns)]
        self._buffer = deque(retained, maxlen=self._buffer.maxlen)

    def text(self) -> str:
        return "".join(self._buffer)


class QServerAPI(REManagerAPI):
    """API wrapper that handles connection state tracking for Bluesky QServer."""

    _rm_status: Dict[str, Any] = {}
    _console_output: Optional[ReceiveConsoleOutput] = None

    def __init__(self, *args, **kwargs) -> None:
        self._beamline_monitor_manifest_path = kwargs.pop("beamline_monitor_manifest_path", None)
        super().__init__(*args, **kwargs)
        self._save_data_path = None
        self._console_output = ReceiveConsoleOutput(zmq_subscribe_addr=kwargs.get("zmq_info_addr", None))
        self._console_monitor = _ConsoleMonitorBuffer()

    @property
    def console_monitor(self) -> _ConsoleMonitorBuffer:
        return self._console_monitor

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

    def scan_pause(self) -> Dict[str, Any]:
        try:
            response = self.re_pause(option="immediate")
        except Exception as exc:
            msg = f"Not able to pause the scan: {exc}"
            print(msg)
            return {"success": False, "msg": msg}

        if isinstance(response, Mapping):
            return dict(response)
        return {"success": True, "msg": "Pause request sent."}

    def scan_resume(self) -> Dict[str, Any]:
        try:
            response = self.re_resume()
        except Exception as exc:
            msg = f"Not able to resume the scan: {exc}"
            print(msg)
            return {"success": False, "msg": msg}

        if isinstance(response, Mapping):
            return dict(response)
        return {"success": True, "msg": "Resume request sent."}

    def scan_abort(self) -> Dict[str, Any]:
        try:
            response = self.re_abort()
        except Exception as exc:
            msg = f"Not able to abort the scan: {exc}"
            print(msg)
            return {"success": False, "msg": msg}

        if isinstance(response, Mapping):
            return dict(response)
        return {"success": True, "msg": "Abort request sent."}
    
    def isqueue_running(self) -> bool:
        return self._rm_status.get("manager_state") not in {"idle", "paused"}

    def isRE_paused(self) -> bool:
        return (self.status().get("re_state") == 'paused')

    def isRE_closed(self) -> bool:
        return self._rm_status.get("re_state") == "closed"

    def queue_stop_pending(self) -> bool:
        return self.status().get("queue_stop_pending", False)

    def get_queue(self) -> Dict[str, Any]:
        try:
            queue = self.queue_get()
        except Exception as exc:  # pragma: no cover - network path
            print(f"Error fetching queue: {exc}")
            self._connected = False
            return {}
        self._connected = True
        return queue

    def clear_queue(self) -> None:
        try:
            self.queue_clear()
        except Exception as exc:  # pragma: no cover - network path
            print(f"Error clearing queue: {exc}")
            return

    def delete_queue(self, queue_ids: List[str]) -> None:
        try:
            self.item_remove_batch(uids = queue_ids)
        except Exception as exc:  # pragma: no cover - network path
            print(f"Error deleting queue: {exc}")
            return

    def clear_queue(self) -> None:
        try:
            self.queue_clear()
        except Exception as exc:  # pragma: no cover - network path
            print(f"Error clearing queue: {exc}")
            return

    def duplicate_queue(self, queue_ids: List[str]) -> None:
        for uid in queue_ids:
            print(f"duplicating item {uid}")
            item = self.fetch_from_queue_history(uid)
            if item is not None:
                try:
                    self.item_add(item = item, pos="front")
                except Exception as exc:  # pragma: no cover - network path
                    print(f"Error duplicating item {uid}: {exc}")
                    return

    def fetch_from_queue_history(self, queue_id: str) -> Dict[str, Any]:
        history = self.history_get().get("items", [])
        queue = self.queue_get().get("items", [])
        running = self.queue_get().get("running_item", [])

        combine = queue + history + [running]

        item_uids = [item.get("item_uid", None) for item in combine]
        if queue_id in item_uids:
            return combine[item_uids.index(queue_id)]
        return None

    def get_allowed_plans(self, *, normalize: bool = False) -> Dict[str, Any]:
        try:
            plans = self.plans_allowed()["plans_allowed"]
        except Exception as exc:  # pragma: no cover - network path
            print(f"Error fetching allowed plans: {exc}")
            return {}
        processed = self._normalize_allowed_plans(plans) if normalize else dict(plans)
        processed.pop("make_devices", None)
        return processed

    def start_queue(self, queue: Dict[str, Any]) -> None:
        success = False
        try:
            response = self.queue_start()
            success = response.get("success", False)
        except Exception as exc:  # pragma: no cover - network path
            print(f"Error starting queue: {exc}")
            return success
        return success

    def get_save_data_path(self, *, timeout: float = 5.0) -> Optional[str]:
        return self.execute_function("get_save_data_path", timeout=timeout)

    def get_global_health_snapshot(self, *, timeout: float = 30.0) -> Any:
        return self.execute_function(
            "get_global_health_snapshot",
            call_kwargs={"manifest_path": self._beamline_monitor_manifest_path},
            timeout=timeout,
        )

    def execute_function(
        self,
        function_name: str,
        *,
        call_kwargs: Optional[Mapping[str, Any]] = None,
        user_group: str = "root",
        timeout: float = 30.0,
        run_in_background: bool = True,
    ) -> Any:
        """Execute a qserver function and return its ``return_value``."""

        func = BFunc(function_name, **dict(call_kwargs or {}))
        try:
            reply = self.function_execute(func, user_group=user_group, run_in_background=run_in_background)
            if not reply.get("success"):
                print(f"QueueServer rejected {function_name}(): {reply.get('msg')}")
                return None

            task_uid = reply.get("task_uid")
            if not task_uid:
                print(f"No task UID returned for {function_name}(): {reply}")
                return None

            self.wait_for_completed_task(task_uid, timeout=timeout)
            result = self.task_result(task_uid=task_uid).get("result") or {}
            return result.get("return_value")
        except (self.WaitTimeoutError, self.WaitCancelError) as exc:
            print(f"Timed out waiting for {function_name}(): {exc}")
        except Exception as exc:  # pragma: no cover - network path
            print(f"Error running {function_name}(): {exc}")
        return None

    def syncXYZ(self, *, timeout: float = 5.0) -> Any:
        return self.execute_function("syncXYZ", timeout=timeout)

    def syncXYZ_transform(
        self,
        *,
        x: object | None = None,
        y: object | None = None,
        z: object | None = None,
        theta: object | None = None,
        timeout: float = 30.0,
    ) -> Any:
        call_kwargs: Dict[str, Any] = {}
        if x is not None:
            call_kwargs["x"] = x
        if y is not None:
            call_kwargs["y"] = y
        if z is not None:
            call_kwargs["z"] = z
        if theta is not None:
            call_kwargs["theta"] = theta
        return self.execute_function("syncXYZ_transform", call_kwargs=call_kwargs, timeout=timeout)

    def recover_detector(
        self,
        device_name: str,
        *,
        retries: int = 1,
        timeout: float = 15.0,
        pause_timeout: float = 10.0,
        resume_timeout: float = 10.0,
        settle_time_s: float = 0.2,
        progress_callback: Callable[[str], None] | None = None,
    ) -> Any:
        return self.execute_function_while_paused(
            "recover_detector",
            call_kwargs={"device_name": device_name, "retries": retries},
            timeout=timeout,
            pause_timeout=pause_timeout,
            resume_timeout=resume_timeout,
            settle_time_s=settle_time_s,
            progress_callback=progress_callback,
            result_context=device_name,
            start_message="Pausing RE",
            run_message=f"Resetting detector {device_name}",
        )

    def execute_function_while_paused(
        self,
        function_name: str,
        *,
        call_kwargs: Optional[Mapping[str, Any]] = None,
        user_group: str = "root",
        timeout: float = 15.0,
        pause_timeout: float = 10.0,
        resume_timeout: float = 10.0,
        settle_time_s: float = 0.2,
        progress_callback: Callable[[str], None] | None = None,
        result_context: str | None = None,
        start_message: str = "Pausing RE",
        run_message: str | None = None,
        wait_for_inner_scan_finish: bool = False,
        inner_scan_timeout: float = 30.0,
    ) -> Any:
        paused_by_request = False
        try:
            if not self.isRE_paused():
                self._emit_recovery_progress(progress_callback, start_message)
                pause_response = self.scan_pause()
                if isinstance(pause_response, Mapping) and not pause_response.get("success", False):
                    self._emit_recovery_progress(progress_callback, "Failed to pause RE")
                    result: Dict[str, Any] = {
                        "success": False,
                        "error": pause_response.get("msg") or "Failed to pause scan",
                    }
                    if result_context is not None:
                        result["device"] = result_context
                    return result
                if not self._wait_for_re_state("paused", timeout=pause_timeout):
                    self._emit_recovery_progress(progress_callback, "Timed out waiting for RE pause")
                    result = {"success": False, "error": "Timed out waiting for scan to pause"}
                    if result_context is not None:
                        result["device"] = result_context
                    return result
                self._emit_recovery_progress(progress_callback, "RE paused")
                paused_by_request = True

            if wait_for_inner_scan_finish:
                self._emit_recovery_progress(progress_callback, "Waiting for inner scan to finish")
                if not self._wait_for_inner_scan_finish(timeout=inner_scan_timeout):
                    self._emit_recovery_progress(progress_callback, "Timed out waiting for inner scan to finish")
                    result = {"success": False, "error": "Timed out waiting for inner scan to finish"}
                    if result_context is not None:
                        result["device"] = result_context
                    return result
                self._emit_recovery_progress(progress_callback, "Inner scan finished")

            if settle_time_s > 0:
                self._emit_recovery_progress(progress_callback, f"Settling for {settle_time_s:.1f}s")
                time.sleep(settle_time_s)

            self._emit_recovery_progress(progress_callback, run_message or f"Running {function_name}")
            result = self.execute_function(
                function_name,
                call_kwargs=call_kwargs,
                user_group=user_group,
                timeout=timeout,
            )
            if isinstance(result, Mapping):
                return dict(result)
            payload = {"success": bool(result is not None), "result": result}
            if result_context is not None:
                payload["device"] = result_context
            return payload
        finally:
            if paused_by_request:
                if settle_time_s > 0:
                    self._emit_recovery_progress(progress_callback, f"Settling for {settle_time_s:.1f}s before resume")
                    time.sleep(settle_time_s)
                current_state = self._current_re_state()
                if current_state == "paused":
                    self._emit_recovery_progress(progress_callback, "Resuming RE")
                    resume_response = self.scan_resume()
                    if isinstance(resume_response, Mapping) and not resume_response.get("success", False):
                        self._emit_recovery_progress(progress_callback, "Failed to resume RE")
                        print(f"Failed to resume after detector recovery: {resume_response.get('msg')}")
                    elif not self._wait_for_re_state_change("paused", timeout=resume_timeout):
                        self._emit_recovery_progress(progress_callback, "Timed out waiting for RE resume")
                        print("Timed out waiting for scan to resume after detector recovery")
                    else:
                        self._emit_recovery_progress(progress_callback, "RE resumed")
                else:
                    self._emit_recovery_progress(
                        progress_callback,
                        f"Skipping resume because RE state is {current_state or 'unknown'}",
                    )

    def _wait_for_re_state(self, expected_state: str, *, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() <= deadline:
            state = self._current_re_state()
            if state is None:
                time.sleep(0.2)
                continue
            if state == expected_state:
                return True
            time.sleep(0.2)
        return False

    def _wait_for_re_state_change(self, previous_state: str, *, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() <= deadline:
            state = self._current_re_state()
            if state is None:
                time.sleep(0.2)
                continue
            if state != previous_state:
                return True
            time.sleep(0.2)
        return False

    def _wait_for_inner_scan_finish(self, *, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() <= deadline:
            if self._inner_scan_finished():
                return True
            time.sleep(0.2)
        return False

    def _inner_scan_finished(self) -> bool:
        try:
            snapshot = self.get_active_plan_monitor_snapshot()
        except Exception:
            return False
        if not isinstance(snapshot, Mapping):
            return False
        devices = snapshot.get("devices")
        if not isinstance(devices, Mapping):
            return False
        scanrecord = devices.get("scanrecord")
        if not isinstance(scanrecord, Mapping):
            return False
        pvs = scanrecord.get("pvs")
        if not isinstance(pvs, Mapping):
            return False
        pv = pvs.get("inner.execute_scan")
        if not isinstance(pv, Mapping):
            return False
        value = pv.get("value")
        if value is None:
            value = pv.get("char_value")
        try:
            return float(value) == 0.0
        except Exception:
            text = str(value).strip().lower()
            return text in {"0", "idle", "done", "false", "off"}

    def _current_re_state(self) -> str | None:
        try:
            state = self.status().get("re_state")
        except Exception:
            return None
        return state if isinstance(state, str) else None

    @staticmethod
    def _emit_recovery_progress(progress_callback: Callable[[str], None] | None, message: str) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(message)
        except Exception:
            pass

    def get_plan_monitor_snapshot(
        self,
        plan_name: str,
        *,
        plan_args: Optional[Mapping[str, Any]] = None,
        include_baseline: bool = True,
        timeout: float = 30.0,
    ) -> Any:
        return self.execute_function(
            "get_plan_monitor_snapshot",
            call_kwargs={
                "plan_name": plan_name,
                "plan_args": dict(plan_args or {}),
                "include_baseline": include_baseline,
                "manifest_path": self._beamline_monitor_manifest_path,
            },
            timeout=timeout,
        )

    def get_running_item(self) -> Optional[Dict[str, Any]]:
        try:
            queue = self.queue_get()
        except Exception as exc:  # pragma: no cover - network path
            print(f"Error fetching running item: {exc}")
            return None

        running_item = queue.get("running_item")
        if isinstance(running_item, Mapping):
            return dict(running_item)
        return None

    def get_active_plan_monitor_snapshot(
        self,
        *,
        include_baseline: bool = True,
        timeout: float = 5.0,
    ) -> Any:
        running_item = self.get_running_item()
        return self.get_running_item_monitor_snapshot(
            running_item,
            include_baseline=include_baseline,
            timeout=timeout,
        )

    def get_running_item_monitor_snapshot(
        self,
        running_item: Mapping[str, Any] | None,
        *,
        include_baseline: bool = True,
        timeout: float = 5.0,
    ) -> Any:
        if not isinstance(running_item, Mapping):
            return {
                "timestamp": None,
                "plan_name": None,
                "plan_args": {},
                "device_names": [],
                "devices": {},
                "pv_backend": "qserver-worker",
                "manifest_path": self._beamline_monitor_manifest_path,
                "error": "No running queue item found",
            }

        item_type = running_item.get("item_type", "plan")
        plan_name = running_item.get("name")
        if item_type != "plan" or not isinstance(plan_name, str) or not plan_name:
            return {
                "timestamp": None,
                "plan_name": plan_name if isinstance(plan_name, str) else None,
                "plan_args": {},
                "device_names": [],
                "devices": {},
                "pv_backend": "qserver-worker",
                "manifest_path": self._beamline_monitor_manifest_path,
                "error": "Running item is not an executable plan",
            }

        plan_args = running_item.get("kwargs")
        if not isinstance(plan_args, Mapping):
            plan_args = {}
        merged_args = self._merge_plan_kwargs_with_defaults(plan_name, plan_args)
        return self.get_plan_monitor_snapshot(
            plan_name,
            plan_args=merged_args,
            include_baseline=include_baseline,
            timeout=timeout,
        )

    def _merge_plan_kwargs_with_defaults(
        self,
        plan_name: str,
        plan_args: Mapping[str, Any] | None,
    ) -> Dict[str, Any]:
        merged = dict(plan_args or {})
        plan_spec = self.get_allowed_plans(normalize=True).get(plan_name)
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
                # normalized_param["default"] = QServerAPI._coerce_default_value(param.get("default"))
                annotated_type, has_annotated_default, annotated_default = QServerAPI._coerce_annotate_value(
                    param.get("annotation")
                )
                if annotated_type:
                    normalized_param["type_name"] = annotated_type
                if has_annotated_default:
                    normalized_param["default"] = annotated_default
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

    @staticmethod
    def _coerce_annotate_value(annotation: Any) -> tuple[Optional[str], bool, Any]:
        """
        Extract a normalized type name and optional default value from the annotation field.

        Returns:
            tuple[type_name, has_default, default_value]
        """
        if annotation is None:
            return None, False, None

        raw_type: Any
        has_default = False
        default_value: Any = None

        if isinstance(annotation, Mapping):
            raw_type = annotation.get("type")
            if "default" in annotation:
                has_default = True
                default_value = QServerAPI._coerce_default_value(annotation.get("default"))
        else:
            raw_type = annotation
        if raw_type is None:
            return None, has_default, default_value
        if isinstance(raw_type, type):
            return raw_type.__name__, has_default, default_value
        if isinstance(raw_type, str):
            stripped = raw_type.strip()
            if stripped == "":
                return None, has_default, default_value
            # Remove surrounding quotes if present
            if (stripped.startswith("'") and stripped.endswith("'")) or (
                stripped.startswith('"') and stripped.endswith('"')
            ):
                stripped = stripped[1:-1].strip()
            if stripped.startswith("<class ") and stripped.endswith(">"):
                stripped = stripped[len("<class ") : -1].strip().strip("'\"")
            simplified = stripped.replace("typing.", "").replace("builtins.", "").replace("types.", "")
            if simplified.startswith("Optional[") and simplified.endswith("]"):
                simplified = simplified[len("Optional[") : -1].strip()
            elif simplified.startswith("Union[") and simplified.endswith("]"):
                union_members = [part.strip() for part in simplified[len("Union[") : -1].split(",")]
                for member in union_members:
                    if member not in {"None", "NoneType"} and member:
                        simplified = member
                        break
            if "[" not in simplified and "." in simplified:
                simplified = simplified.split(".")[-1]
            lowered = simplified.lower()
            if lowered in {"none", "nonetype"}:
                return None, has_default, default_value
            if lowered in {"bool", "boolean"}:
                return "bool", has_default, default_value
            if lowered in {"int", "integer"}:
                return "int", has_default, default_value
            if lowered in {"float", "double"}:
                return "float", has_default, default_value
            if lowered in {"str", "string"}:
                return "str", has_default, default_value
            return simplified, has_default, default_value
        return str(raw_type), has_default, default_value


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
            self._console_monitor.append(message)
            return message
        self._console_monitor.append(message)
        return {"text": message}
