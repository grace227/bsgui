"""Dedicated pipe-delimited log for QServer function calls."""

from __future__ import annotations

import logging
from pathlib import Path
from pprint import pformat
import threading
from typing import Any, Mapping
from contextlib import contextmanager
from contextvars import ContextVar


_logger = logging.getLogger("bsgui.qserver")
_configuration_lock = threading.Lock()
_configured_path: Path | None = None
_active_log_path: ContextVar[Path | None] = ContextVar("bsgui_qserver_log_path", default=None)


def _log_path(log_directory: str | Path | None = None) -> Path:
    if log_directory:
        return Path(log_directory).expanduser() / "qserver_calls.log"
    return Path.cwd() / "qserver_calls.log"


def _get_logger(log_directory: str | Path | None = None) -> logging.Logger:
    """Configure the dedicated file logger once for the current log path."""
    global _configured_path

    path = _log_path(log_directory).resolve()
    with _configuration_lock:
        # Check on every call so a file removed or rotated externally is
        # recreated rather than leaving the handler attached to a stale inode.
        active_path = _active_log_path.get()
        if _configured_path == path and _logger.handlers and (
            active_path == path or path.is_file()
        ):
            return _logger

        for handler in _logger.handlers[:]:
            _logger.removeHandler(handler)
            handler.close()

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if path.is_file() else "w"
            handler = logging.FileHandler(path, mode=mode, encoding="utf-8")
        except OSError:
            path = (Path.cwd() / "qserver_calls.log").resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if path.is_file() else "w"
            handler = logging.FileHandler(path, mode=mode, encoding="utf-8")
        formatter = logging.Formatter(
            "|%(asctime)s.%(msecs)03d|%(levelname)s|%(process)d|%(name)s|"
            "%(module)s|%(lineno)d|%(threadName)s| - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        _logger.addHandler(handler)
        _logger.setLevel(logging.INFO)
        _logger.propagate = False
        _configured_path = path
    return _logger


@contextmanager
def qserver_log_context(log_directory: str | Path | None = None):
    """Reuse one resolved log file for a group of QServer calls."""
    path = _log_path(log_directory).resolve()
    _get_logger(log_directory)
    token = _active_log_path.set(path)
    try:
        yield
    finally:
        _active_log_path.reset(token)


def get_active_log_directory() -> Path | None:
    """Return the log directory cached by the current logging context."""
    path = _active_log_path.get()
    return path.parent if path is not None else None


def _display(value: Any) -> str:
    """Format call data compactly while handling scientific-Python objects."""
    try:
        formatted = pformat(value, sort_dicts=False, compact=True, width=100_000)
        # Keep one QServer call on one physical log line.  ``pformat`` may
        # otherwise insert newlines for nested dictionaries.
        return " ".join(line.strip() for line in formatted.splitlines())
    except Exception:
        return repr(value)


def log_qserver_call(
    function_name: str,
    *,
    call_kwargs: Mapping[str, Any] | None = None,
    user_group: str = "root",
    timeout: float | None = None,
    status: str,
    return_value: Any = None,
    error: str | None = None,
    log_directory: str | Path | None = None,
) -> None:
    """Write one QServer call using the standard APS pipe-delimited format."""
    message = (
        "QServer call: function=%s parameters=%s user_group=%s timeout=%s "
        "status=%s return_value=%s"
        % (
            function_name,
            _display(dict(call_kwargs or {})),
            user_group,
            timeout,
            status,
            _display(return_value),
        )
    )
    if error:
        message += f" error={error}"

    try:
        _get_logger(log_directory).info(message)
    except Exception:
        # Diagnostic logging must never make an otherwise valid QServer call fail.
        return
