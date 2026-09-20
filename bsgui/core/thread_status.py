"""Lightweight runtime information for GUI worker threads."""

from __future__ import annotations

import threading
from typing import Any
import weakref


_lock = threading.Lock()
_pools: dict[str, tuple[weakref.ReferenceType[Any], str]] = {}
_qt_threads: dict[str, tuple[weakref.ReferenceType[Any], str]] = {}


def register_thread_pool(name: str, pool: Any, parent: str) -> None:
    """Register a Qt thread pool for display in the main-window status bar."""
    with _lock:
        _pools[name] = (weakref.ref(pool), parent)


def register_qt_thread(name: str, thread: Any, parent: str) -> None:
    """Register a QThread for display in the main-window status bar."""
    with _lock:
        _qt_threads[name] = (weakref.ref(thread), parent)


def _memory_usage_mb() -> float:
    """Return the current resident memory used by the GUI process."""
    try:
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def describe_workers() -> str:
    """Return a compact active-thread and memory summary."""
    threads = [thread for thread in threading.enumerate() if thread.is_alive()]

    active_pool_workers = 0
    active_qt_threads = 0
    with _lock:
        for name, (pool_ref, parent) in list(_pools.items()):
            pool = pool_ref()
            if pool is None:
                _pools.pop(name, None)
                continue
            active = pool.activeThreadCount()
            active_pool_workers += active
        for name, (thread_ref, parent) in list(_qt_threads.items()):
            thread = thread_ref()
            if thread is None:
                _qt_threads.pop(name, None)
                continue
            if thread.isRunning():
                active_qt_threads += 1

    active_thread_count = len(threads) + active_pool_workers + active_qt_threads
    return f"Threads: {active_thread_count} | Memory: {_memory_usage_mb():.1f} MB"
