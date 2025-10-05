"""Shared status bus for routing messages to the main window."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class _StatusBus(QObject):
    message = Signal(str)


_status_bus: _StatusBus | None = None


def get_status_bus() -> _StatusBus:
    global _status_bus
    if _status_bus is None:
        _status_bus = _StatusBus()
    return _status_bus


def emit_status(message: str) -> None:
    if not message:
        return
    get_status_bus().message.emit(str(message))
