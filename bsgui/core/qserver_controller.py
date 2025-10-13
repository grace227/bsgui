"""Shared controller wiring QServer API into Qt-friendly signals."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
from typing import Dict, List, Optional, Sequence, Tuple, Mapping, Any

from PySide6.QtCore import QObject, Signal

from .qserver_api import QServerAPI

_logger = logging.getLogger(__name__)


@dataclass
class QueueSnapshot:
    pending: list[dict]
    running: Optional[dict]
    completed: list[dict]
    progress: Optional[int]


@dataclass(frozen=True)
class PlanParameter:
    name: str
    default: object | None = None
    latest: object | None = None
    type_name: str | None = None
    required: bool = False
    description: str | None = None

    def default_as_text(self) -> str:
        if self.default is None:
            return "None"
        if isinstance(self.default, str):
            return self.default
        return str(self.default)

    def inferred_type(self) -> str:
        if self.type_name:
            return self.type_name
        if isinstance(self.default, bool):
            return "bool"
        if isinstance(self.default, int):
            return "int"
        if isinstance(self.default, float):
            return "float"
        return "str"

    def coerce(self, text: str) -> object:
        type_name = self.inferred_type().lower()
        if text is None:
            return None
        stripped = text.strip()
        if stripped == "":
            return None
        if stripped == "None":
            return None
        if type_name == "str":
            return text
        if type_name == "int":
            return int(stripped)
        if type_name == "float":
            return float(stripped)
        if type_name == "bool":
            normalized = stripped.lower()
            if normalized in {"true", "1", "yes", "y", "on"}:
                return True
            if normalized in {"false", "0", "no", "n", "off"}:
                return False
            raise ValueError(f"Invalid bool value: {text}")
        return text


@dataclass(frozen=True)
class PlanDefinition:
    item_type: str
    name: str
    parameters: Sequence[PlanParameter]
    description: str | None = None


class QServerController(QObject):
    """Single point of contact for Bluesky QServer interactions."""

    statusUpdated = Signal(dict)
    queueUpdated = Signal(QueueSnapshot)
    consoleMessageReceived = Signal(dict)

    def __init__(
        self,
        api: Optional[QServerAPI] = None,
        *,
        poll_interval_ms: int = 2000,
        status_keys: Optional[Sequence[str]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._api = api or QServerAPI()
        self._poll_interval_ms = poll_interval_ms
        self._poll_thread: Optional[threading.Thread] = None
        self._poll_stop = threading.Event()
        self._status_keys: Optional[Tuple[str, ...]] = tuple(status_keys) if status_keys else None
        self._console_thread: Optional[threading.Thread] = None
        self._console_stop = threading.Event()

    # ----------------------------------------------------------------------------
    # Control

    def request_connect(self) -> None:
        status = self._refresh_status()
        if status.get("connected"):
            self.start_polling()

    def start_polling(self) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()

        def _target() -> None:
            interval = max(self._poll_interval_ms, 0)
            wait_seconds = interval / 1000 if interval else 0
            try:
                while not self._poll_stop.is_set():
                    self._poll()
                    if wait_seconds:
                        if self._poll_stop.wait(wait_seconds):
                            break
                    else:
                        self._poll_stop.wait(0.001)
            finally:
                self._poll_thread = None

        self._poll_thread = threading.Thread(target=_target, name="QServerStatusPoller", daemon=True)
        self._poll_thread.start()

    def stop_polling(self) -> None:
        thread = self._poll_thread
        if thread and thread.is_alive():
            self._poll_stop.set()
            if threading.current_thread() is thread:
                return
            thread.join(timeout=1.0)
        self._poll_thread = None
        self._poll_stop.clear()
        self.stop_console_monitor()

    def start_console_monitor(self) -> None:
        if self._console_thread and self._console_thread.is_alive():
            return
        self._console_stop.clear()

        def _target() -> None:
            try:
                while not self._console_stop.is_set():
                    message = self._api.recv_console_message(timeout=0.2)
                    if message:
                        self.consoleMessageReceived.emit(message)
            finally:
                # self._api.stop_console_stream()
                self._console_thread = None

        self._console_thread = threading.Thread(target=_target, name="QServerConsoleMonitor", daemon=True)
        self._console_thread.start()

    def stop_console_monitor(self) -> None:
        thread = self._console_thread
        if thread is None:
            return
        self._console_stop.set()
        self._api.stop_console_stream()
        if thread.is_alive():  # pragma: no cover - thread timing
            thread.join(timeout=0.5)
        self._console_thread = None
        self._console_stop.clear()

    def start_re(self) -> None:
        try:
            _logger.info("Opening Run Engine environment...")
            self._api.environment_open()
        except Exception:
            _logger.exception("Error starting RE Environment")

    def stop_re(self) -> None:
        try:
            _logger.info("Closing Run Engine environment...")
            self._api.environment_close()
            _logger.info("Run Engine environment stopped")
        except Exception:
            _logger.exception("Error stopping RE Environment")

    # ----------------------------------------------------------------------------
    # Internal helpers

    def _poll(self) -> None:
        status = self._refresh_status()
        if status.get("connected"):
            snapshot = self._fetch_queue()
            if snapshot:
                self.queueUpdated.emit(snapshot)

    def _refresh_status(self) -> dict:
        selected_keys = list(self._status_keys) if self._status_keys is not None else None
        status = self._api.get_status(selected_keys)
        self.statusUpdated.emit(status)
        return status

    def _fetch_queue(self) -> Optional[QueueSnapshot]:
        pending: list[dict] = []
        running: Optional[dict] = None
        completed: list[dict] = []

        try:
            queue_response = self._api.queue_get()
            history_response = self._api.history_get()
        except Exception:
            _logger.exception("Error requesting queue information")
            return None

        if isinstance(queue_response, Mapping) and queue_response.get("success"):
            items = queue_response.get("items")
            if isinstance(items, Sequence):
                pending = [item for item in items if isinstance(item, Mapping)]
            active = queue_response.get("running_item")
            if isinstance(active, Mapping):
                running = dict(active)

        if isinstance(history_response, Mapping) and history_response.get("success"):
            items = history_response.get("items")
            if isinstance(items, Sequence):
                completed = [item for item in items if isinstance(item, Mapping)]

        return QueueSnapshot(
            pending=[dict(item) for item in pending],
            running=running,
            completed=[dict(item) for item in completed],
            progress=self._extract_progress(running),
        )

    @staticmethod
    def _extract_progress(running_item: Optional[Mapping[str, Any]]) -> Optional[int]:
        if not isinstance(running_item, Mapping):
            return None
        progress = running_item.get("progress")
        if isinstance(progress, (int, float)):
            return int(progress)
        return None
 

    # ----------------------------------------------------------------------------
    # Public convenience

    def fetch_snapshot(self) -> Optional[QueueSnapshot]:
        try:
            return self._fetch_queue()
        except Exception:
            _logger.exception("Error fetching queue snapshot")
            return None

    def get_allowed_plans(self, *, normalize: bool = False) -> Dict[str, dict]:
        try:
            return self._api.get_allowed_plans(normalize=normalize)
        except Exception:
            return {}

    def get_allowed_plan_definitions(self, *, kind: str = "plan") -> List[PlanDefinition]:
        plans = self.get_allowed_plans(normalize=True)
        return self._convert_allowed_plans(plans, kind=kind)

    def get_plan_parameters_names(self, *, name: str) -> List[str]:
        definitions = self.get_allowed_plan_definitions()
        parms: List[str] = []
        for definition in definitions:
            if definition.name == name:
                parameters = definition.parameters
                if parameters is not None:
                    for p in parameters:
                        parms.append(p.name)
        return parms

    @staticmethod
    def _convert_allowed_plans(plans: Dict[str, dict], *, kind: str = "plan") -> List[PlanDefinition]:
        definitions: List[PlanDefinition] = []
        for name, spec in sorted(plans.items()):
            if not isinstance(spec, dict):
                continue
            description = spec.get("description") if isinstance(spec.get("description"), str) else None
            parameters: List[PlanParameter] = []
            for param in spec.get("parameters", []):
                if not isinstance(param, dict):
                    continue
                pname = param.get("name")
                if not isinstance(pname, str):
                    continue
                default = param.get("default")
                param_desc = param.get("description") if isinstance(param.get("description"), str) else None
                raw_type = param.get("type_name")
                if not raw_type and isinstance(param_desc, str) and "Type:" in param_desc:
                    raw_type = param_desc.split("Type:")[-1].strip()
                if not raw_type and default is not None:
                    if isinstance(default, bool):
                        raw_type = "bool"
                    elif isinstance(default, int):
                        raw_type = "int"
                    elif isinstance(default, float):
                        raw_type = "float"
                parameters.append(
                    PlanParameter(
                        name=pname,
                        default=default,
                        type_name=raw_type,
                        required=bool(param.get("required", False)),
                        description=param_desc,
                    )
                )
            definitions.append(
                PlanDefinition(
                    name=str(spec.get("name", name)),
                    item_type=kind,
                    parameters=tuple(parameters),
                    description=description,
                )
            )
        return definitions
