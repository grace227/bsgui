"""Example application assembling beamline control widgets into tabs."""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import List, Optional

from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTabWidget

from bsgui.config.defaults import register_default_widgets
from bsgui.config.registry import WidgetRegistry, registry
from bsgui.ui.status_bus import get_status_bus, emit_status


DEFAULT_WIDGET_KEYS = ["scan_setup", "qserver_monitor"]


def load_config(path: pathlib.Path) -> dict:
    """Load YAML configuration from *path*."""

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError(
            "PyYAML is required to load configuration files. Install it with 'pip install PyYAML'."
        ) from exc

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Configuration file {path} must define a mapping at the top level")
    return data


def resolve_config_path(argument: Optional[pathlib.Path]) -> Optional[pathlib.Path]:
    if argument:
        return argument
    default_path = pathlib.Path("bsgui/config/widgets.yaml")
    if default_path.exists():
        return default_path
    return None


def extract_tab_configs(config: dict, widget_keys: List[str]) -> List[dict]:
    tabs = [tab for tab in config.get("tabs", []) if isinstance(tab, dict) and "key" in tab]
    if not tabs:
        return [{"key": key} for key in widget_keys]

    default_used = widget_keys == DEFAULT_WIDGET_KEYS
    tab_by_key = {tab["key"]: tab for tab in tabs}

    if default_used:
        return tabs

    ordered_tabs = []
    for key in widget_keys:
        tab = tab_by_key.get(key, {"key": key})
        if tab.get("key") != key:
            tab = dict(tab)
            tab["key"] = key
        ordered_tabs.append(tab)
    return ordered_tabs


def extract_widget_options(tab_configs: Sequence[dict], key: str) -> dict:
    for tab in tab_configs:
        if tab.get("key") == key:
            options = tab.get("options", {})
            if isinstance(options, dict):
                return dict(options)
    return {}


def parse_app_settings(config: dict) -> tuple[str, Sequence[int], dict]:
    app_config = config.get("app", {}) if isinstance(config, dict) else {}
    title = app_config.get("title", "Beamline Control")

    window_size = app_config.get("window_size", [1200, 800])
    if (
        isinstance(window_size, Sequence)
        and len(window_size) == 2
        and all(isinstance(dim, (int, float)) for dim in window_size)
    ):
        width, height = int(window_size[0]), int(window_size[1])
    else:
        width, height = 1200, 800

    layout_config = config.get("layout", {}) if isinstance(config, dict) else {}
    status_bar_config = layout_config.get("status_bar", {}) if isinstance(layout_config, dict) else {}
    messages = status_bar_config.get("messages", {}) if isinstance(status_bar_config, dict) else {}

    status_messages = {
        key: value for key, value in messages.items() if isinstance(key, str) and isinstance(value, str)
    }

    return title, (width, height), status_messages


class StatusBarLogHandler(logging.Handler):
    """Logging handler that forwards records to the status bus."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - defensive
            message = record.getMessage()
        emit_status(message)


class MainWindow(QMainWindow):
    """Main window that arranges registered widgets into tabs."""

    def __init__(
        self,
        tab_configs: Iterable[dict],
        registry: WidgetRegistry,
        *,
        window_title: str = "Beamline Control",
        status_messages: Optional[dict] = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(window_title)
        self.statusBar().showMessage((status_messages or {}).get("idle", "Ready."))
        get_status_bus().message.connect(self.statusBar().showMessage)

        tabs = QTabWidget()
        for tab_config in tab_configs:
            key = tab_config.get("key")
            if not isinstance(key, str):
                continue
            try:
                descriptor = registry.get(key)
            except KeyError:
                QMessageBox.warning(self, "Unknown Widget", f"Widget '{key}' not registered")
                continue

            tab = descriptor.factory()
            tab_title = tab_config.get("title") if isinstance(tab_config.get("title"), str) else None
            tab_description = (
                tab_config.get("description") if isinstance(tab_config.get("description"), str) else None
            )

            index = tabs.addTab(tab, tab_title or descriptor.title)
            tooltip = tab_description or descriptor.description
            if tooltip:
                tabs.setTabToolTip(index, tooltip)

        self.setCentralWidget(tabs)


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Beamline control GUI")
    parser.add_argument(
        "widgets",
        nargs="*",
        default=DEFAULT_WIDGET_KEYS,
        help=f"Widget keys to include (default: {' '.join(DEFAULT_WIDGET_KEYS)})",
    )
    parser.add_argument(
        "--data-path",
        action="append",
        dest="data_paths",
        default=None,
        type=pathlib.Path,
        help="Additional directory to scan for data files",
    )
    parser.add_argument(
        "--config",
        type=pathlib.Path,
        default=None,
        help="Path to a YAML configuration file (default: bsgui/config/widgets.yaml if present)",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    config_path = resolve_config_path(args.config)
    config = load_config(config_path) if config_path else {}

    logging.basicConfig(level=logging.INFO)
    status_handler = StatusBarLogHandler()
    status_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(status_handler)

    tab_configs = extract_tab_configs(config, list(args.widgets))

    data_paths = args.data_paths or None
    if data_paths is not None:
        data_paths = [pathlib.Path(path) for path in data_paths]

    scan_setup_options = extract_widget_options(tab_configs, "scan_setup") or {}

    if data_paths is not None and isinstance(scan_setup_options, dict):
        loaders_cfg = scan_setup_options.setdefault("loaders", {})
        if isinstance(loaders_cfg, dict):
            for key in ("xrf", "ptycho"):
                loader_cfg = loaders_cfg.setdefault(key, {})
                if isinstance(loader_cfg, dict) and "search_paths" not in loader_cfg:
                    loader_cfg["search_paths"] = data_paths

    qserver_options = extract_widget_options(tab_configs, "qserver_monitor")
    qserver_kwargs = {}
    poll_interval = qserver_options.get("poll_interval_ms")
    if isinstance(poll_interval, (int, float)):
        qserver_kwargs["poll_interval_ms"] = int(poll_interval)
    roi_key_map = qserver_options.get("roi_key_map")
    if isinstance(roi_key_map, Mapping):
        qserver_kwargs["roi_key_map"] = dict(roi_key_map)
    columns = qserver_options.get("columns")
    if isinstance(columns, Sequence):
        normalized_columns = []
        for entry in columns:
            if isinstance(entry, Mapping):
                normalized_columns.append(dict(entry))
        if normalized_columns:
            qserver_kwargs["columns"] = normalized_columns

    register_default_widgets(
        data_paths=data_paths,
        data_viewer_options=scan_setup_options,
        qserver_kwargs=qserver_kwargs,
    )

    title, window_size, status_messages = parse_app_settings(config)

    app = QApplication(sys.argv)
    window = MainWindow(
        tab_configs,
        registry,
        window_title=title,
        status_messages=status_messages,
    )
    window.resize(*window_size)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
