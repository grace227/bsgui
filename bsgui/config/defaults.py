"""Default widget registrations for the beamline control UI."""

from __future__ import annotations

import os
import pathlib
import importlib
from typing import Callable, Iterable, List, Mapping, Optional, Sequence

from PySide6.QtWidgets import QWidget

from .registry import WidgetDescriptor, WidgetRegistry, registry
from ..core import DataVisualizationController, default_loader
from ..core.qserver_api import QServerAPI
from ..core.qserver_controller import QServerController
from ..ui.scan_setup import DataViewerPane
from ..ui.data_loader import PtychographyLoaderWidget, XRFLoaderWidget
from ..ui.plan_editor import PlanDefinition, PlanEditorWidget, PlanParameter
from ..ui.qserver_status import QueueServerStatusWidget
from ..ui.qserver_console import QServerConsoleWidget
from ..ui.queue_monitor import QueueMonitorWidget


def _parse_env_file(path: pathlib.Path) -> Mapping[str, str]:
    """Return key/value pairs defined in a .env file."""

    env: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                key, sep, value = line.partition("=")
                if not sep:
                    continue
                env[key.strip()] = value.strip().strip("'\"")
    except OSError:
        return {}
    return env


def _locate_env_file() -> Optional[pathlib.Path]:
    """Search for a .env file starting from this module directory upward."""

    start_dir = pathlib.Path(__file__).resolve().parent
    for directory in (start_dir, *start_dir.parents):
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None

def _coerce_paths(
    explicit_paths: Optional[Iterable[pathlib.Path]],
    fallback_paths: Sequence[pathlib.Path],
) -> Sequence[pathlib.Path]:
    if explicit_paths is None:
        return fallback_paths
    return [pathlib.Path(path) for path in explicit_paths]


def _resolve_loader_callable(options: dict, default_callable: Callable) -> Callable:
    for key in ("loader", "loader_fn", "loader_path"):
        candidate = options.get(key)
        if callable(candidate):
            return candidate
        if isinstance(candidate, str):
            return _import_callable(candidate)
    return default_callable


def _import_callable(path: str) -> Callable:
    """Import a callable specified as ``module:attr`` or dotted path."""

    module_name: str
    attr_name: str
    if ":" in path:
        module_name, attr_name = path.split(":", 1)
    else:
        module_name, _, attr_name = path.rpartition(".")
        if not module_name:
            raise ValueError(f"Unable to determine module in loader path '{path}'")

    module = importlib.import_module(module_name)
    try:
        loaded = getattr(module, attr_name)
    except AttributeError as exc:
        raise ImportError(f"Loader '{attr_name}' not found in module '{module_name}'") from exc

    if not callable(loaded):
        raise TypeError(f"Imported object '{path}' is not callable")

    return loaded


def _parse_plan_definitions(config: Iterable[dict]) -> List[PlanDefinition]:
    definitions: List[PlanDefinition] = []
    for entry in config:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        kind = entry.get("kind", "plan")
        params_cfg = entry.get("parameters", [])
        parameters: List[PlanParameter] = []
        if isinstance(params_cfg, Iterable):
            for param in params_cfg:
                if not isinstance(param, dict):
                    continue
                param_name = param.get("name")
                if not isinstance(param_name, str):
                    continue
                parameters.append(
                    PlanParameter(
                        name=param_name,
                        default=param.get("default"),
                        required=bool(param.get("required", False)),
                        description=param.get("description"),
                    )
                )
        definitions.append(
            PlanDefinition(
                name=name,
                kind=str(kind),
                parameters=parameters,
                description=entry.get("description"),
            )
        )
    return definitions


def register_default_widgets(
    registry_instance: Optional[WidgetRegistry] = None,
    *,
    data_paths: Optional[Iterable[pathlib.Path]] = None,
    data_viewer_options: Optional[dict] = None,
    qserver_kwargs: Optional[dict] = None,
) -> None:
    """Populate the registry with the default widget set."""

    target = registry_instance or registry
    fallback_paths = [pathlib.Path(path) for path in (data_paths or [pathlib.Path.cwd()])]

    viewer_cfg = data_viewer_options or {}
    loader_cfg = viewer_cfg.get("loaders", {}) if isinstance(viewer_cfg.get("loaders"), dict) else {}

    loader_factories: List[Callable[[], tuple]] = []
    extra_factories: List[Callable[[], tuple[QWidget, str]]] = []

    raw_xrf_cfg = loader_cfg.get("xrf") if isinstance(loader_cfg, dict) else None
    if raw_xrf_cfg is None:
        xrf_cfg = {}
        include_xrf = loader_cfg == {}  # default on when no config specified
    elif isinstance(raw_xrf_cfg, dict):
        xrf_cfg = raw_xrf_cfg
        include_xrf = xrf_cfg.get("enabled", True)
    else:
        xrf_cfg = {}
        include_xrf = bool(raw_xrf_cfg)
    if include_xrf:
        def make_xrf_loader() -> tuple:
            search_paths = _coerce_paths(xrf_cfg.get("search_paths"), fallback_paths)
            controller = DataVisualizationController(
                loader=_resolve_loader_callable(xrf_cfg, default_loader),
                search_paths=search_paths,
                file_patterns=tuple(xrf_cfg.get("file_patterns", ["*.dat", "*.h5"])),
            )
            loader_widget = XRFLoaderWidget(
                file_patterns=xrf_cfg.get("file_patterns"),
                initial_folder=search_paths[0] if search_paths else None,
            )
            return loader_widget, controller

        loader_factories.append(make_xrf_loader)

    raw_ptycho_cfg = loader_cfg.get("ptycho") if isinstance(loader_cfg, dict) else None
    if raw_ptycho_cfg is None:
        ptycho_cfg = {}
        include_ptycho = loader_cfg == {}
    elif isinstance(raw_ptycho_cfg, dict):
        ptycho_cfg = raw_ptycho_cfg
        include_ptycho = ptycho_cfg.get("enabled", True)
    else:
        ptycho_cfg = {}
        include_ptycho = bool(raw_ptycho_cfg)
    if include_ptycho:
        def make_ptycho_loader() -> tuple:
            search_paths = _coerce_paths(ptycho_cfg.get("search_paths"), fallback_paths)
            controller = DataVisualizationController(
                loader=_resolve_loader_callable(ptycho_cfg, default_loader),
                search_paths=search_paths,
                file_patterns=tuple(ptycho_cfg.get("file_patterns", ["*.tif"])),
            )
            loader_widget = PtychographyLoaderWidget(
                scan_numbers=ptycho_cfg.get("scan_numbers"),
                roi_types=ptycho_cfg.get("roi_types"),
                recon_methods=ptycho_cfg.get("recon_methods"),
                iteration_files=ptycho_cfg.get("iteration_files"),
                initial_folder=search_paths[0] if search_paths else None,
            )
            return loader_widget, controller

        loader_factories.append(make_ptycho_loader)

    status_cfg = viewer_cfg.get("queue_status")
    if status_cfg is None:
        include_status = True
        status_slot = "queue_status"
    elif isinstance(status_cfg, dict):
        include_status = status_cfg.get("enabled", True)
        status_slot = status_cfg.get("layout_slot", "queue_status")
    else:
        include_status = bool(status_cfg)
        status_slot = "queue_status"

    default_poll_interval = int(viewer_cfg.get("poll_interval_ms", 2000))
    if isinstance(status_cfg, dict):
        qserver_poll_interval = int(status_cfg.get("poll_interval_ms", default_poll_interval))
    else:
        qserver_poll_interval = default_poll_interval

    indicator_config: Optional[Mapping[str, Mapping[str, str]]] = None
    indicator_keys: Optional[Sequence[str]] = None
    status_keys_override: Optional[Sequence[str]] = None

    if include_status and isinstance(status_cfg, dict):
        raw_indicators = status_cfg.get("labels")
        if isinstance(raw_indicators, Mapping):
            indicator_config = raw_indicators
            indicator_keys = tuple(raw_indicators.keys())
            status_keys_override = tuple(key for key in indicator_keys if key != "connected")

    _qserver_controller: Optional[QServerController] = None

    def ensure_controller() -> QServerController:
        nonlocal _qserver_controller
        if _qserver_controller is None:
            control_address = os.getenv("QSERVER_ZMQ_CONTROL_ADDRESS")
            info_address = os.getenv("QSERVER_ZMQ_INFO_ADDRESS")
            if not control_address or not info_address:
                env_file = _locate_env_file()
                if env_file:
                    env_vars = _parse_env_file(env_file)
                    control_address = control_address or env_vars.get("QSERVER_ZMQ_CONTROL_ADDRESS")
                    info_address = info_address or env_vars.get("QSERVER_ZMQ_INFO_ADDRESS")
            if not control_address or not info_address:
                raise RuntimeError(
                    "QServer ZMQ environment variables 'QSERVER_ZMQ_CONTROL_ADDRESS' and "
                    "'QSERVER_ZMQ_INFO_ADDRESS' must be set."
                )
            api = QServerAPI(
                zmq_control_addr=control_address,
                zmq_info_addr=info_address,
            )
            _qserver_controller = QServerController(
                api=api,
                poll_interval_ms=qserver_poll_interval,
                status_keys=status_keys_override,
            )
        return _qserver_controller

    roi_key_map: Optional[Mapping[str, Sequence[str]]] = None

    plan_editor_cfg = viewer_cfg.get("plan_editor")
    if isinstance(plan_editor_cfg, dict) and plan_editor_cfg.get("enabled", True):
        plans_cfg = plan_editor_cfg.get("plans", [])
        kinds_cfg = plan_editor_cfg.get("kinds")
        kinds = list(kinds_cfg) if isinstance(kinds_cfg, Sequence) else None
        kind_parameters = (
            plan_editor_cfg.get("kind_parameters")
            if isinstance(plan_editor_cfg.get("kind_parameters"), Mapping)
            else None
        )
        roi_key_map = (
            plan_editor_cfg.get("roi_key_map")
            if isinstance(plan_editor_cfg.get("roi_key_map"), Mapping)
            else None
        )

        def make_plan_editor() -> tuple[QWidget, str]:
            controller = ensure_controller()
            widget = PlanEditorWidget(
                kinds=kinds,
                controller=controller,
                kind_overrides=kind_parameters,
                roi_key_map=roi_key_map,
            )
            if isinstance(plans_cfg, list):
                definitions = _parse_plan_definitions(plans_cfg)
                if definitions:
                    widget.load_definitions(definitions)
            return widget, plan_editor_cfg.get("layout_slot", "plan_editor")

        extra_factories.append(make_plan_editor)

    if include_status:
        def make_status_widget() -> tuple[QWidget, str]:
            widget = QueueServerStatusWidget(indicators=indicator_config)
            controller = ensure_controller()
            widget.set_controller(controller)
            widget.connectRequested.connect(controller.request_connect)
            controller.statusUpdated.connect(
                lambda status: widget.update_status(status)
            )
            controller.start_polling()
            return widget, status_slot

        extra_factories.append(make_status_widget)

    console_cfg = viewer_cfg.get("console_output")
    if console_cfg is None:
        include_console = False
        console_slot = "console_output"
    elif isinstance(console_cfg, dict):
        include_console = console_cfg.get("enabled", True)
        console_slot = console_cfg.get("layout_slot", "console_output")
    else:
        include_console = bool(console_cfg)
        console_slot = "console_output"

    if include_console:
        console_title = "QServer Console"
        console_max_entries = 500
        console_auto_scroll = True
        if isinstance(console_cfg, dict):
            console_title = console_cfg.get("title", console_title)
            console_max_entries = int(console_cfg.get("max_entries", console_max_entries))
            console_auto_scroll = bool(console_cfg.get("auto_scroll", console_auto_scroll))

        def make_console_widget() -> tuple[QWidget, str]:
            controller = ensure_controller()
            widget = QServerConsoleWidget(
                title=console_title,
                max_entries=console_max_entries,
                auto_scroll=console_auto_scroll,
            )
            controller.consoleMessageReceived.connect(widget.append_message)
            controller.start_console_monitor()
            return widget, console_slot

        extra_factories.append(make_console_widget)

    if not loader_factories:
        def make_fallback_loader() -> tuple:
            controller = DataVisualizationController(
                loader=default_loader,
                search_paths=fallback_paths,
                file_patterns=("*.dat", "*.h5"),
            )
            loader_widget = XRFLoaderWidget(initial_folder=fallback_paths[0] if fallback_paths else None)
            return loader_widget, controller

        loader_factories.append(make_fallback_loader)

    def connect_scan_setup_widgets() -> DataViewerPane:
        loader_instances = [factory() for factory in loader_factories]
        extra_widgets = [factory() for factory in extra_factories]
        pane = DataViewerPane(
            loader_instances,
            extra_widgets=extra_widgets,
            layout_config=viewer_cfg.get("layout"),
        )

        plan_editor_widget: Optional[PlanEditorWidget] = None
        status_widget: Optional[QueueServerStatusWidget] = None

        for widget, _ in extra_widgets:
            if isinstance(widget, PlanEditorWidget):
                pane.canvasPointSelected.connect(widget.handle_point_drawn)
                pane.roiDrawn.connect(widget.handle_roi_drawn)
                plan_editor_widget = widget
            elif isinstance(widget, QueueServerStatusWidget):
                status_widget = widget

        if plan_editor_widget and status_widget:
            status_widget.clearPlansRequested.connect(plan_editor_widget.handle_plans_update)

        return pane

    target.register(
        WidgetDescriptor(
            key="scan_setup",
            title=viewer_cfg.get("title", "Microscopy Beamline Data Acquisition"),
            description=viewer_cfg.get(
                "description",
                "Load X-ray data and prepare data acquisition bluesky plans.",
            ),
            factory=connect_scan_setup_widgets,
        )
    )

    allowed_qserver_kwargs = {"poll_interval_ms", "roi_key_map", "columns"}
    q_kwargs = {
        key: value
        for key, value in (qserver_kwargs or {}).items()
        if key in allowed_qserver_kwargs
    }

    def make_qserver_monitor() -> QWidget:
        monitor_kwargs = dict(q_kwargs)
        monitor_kwargs.pop("poll_interval_ms", None)
        controller = ensure_controller()
        monitor_kwargs.setdefault("controller", controller)
        if roi_key_map and "roi_key_map" not in monitor_kwargs:
            monitor_kwargs["roi_key_map"] = roi_key_map
        widget = QueueMonitorWidget(**monitor_kwargs)
        controller.start_polling()
        return widget

    target.register(
        WidgetDescriptor(
            key="qserver_monitor",
            title="Queue Monitor",
            description="View Bluesky QServer queue, active plan, and history.",
            factory=make_qserver_monitor,
        )
    )
