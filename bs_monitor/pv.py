"""Low-level EPICS PV polling utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


try:  # pragma: no cover - optional runtime dependency
    import epics
except Exception:  # pragma: no cover - optional runtime dependency
    epics = None


@dataclass
class PVSnapshot:
    pvname: str
    connected: bool
    value: Any = None
    char_value: str | None = None
    severity: Any = None
    status: Any = None
    timestamp: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pvname": self.pvname,
            "connected": self.connected,
            "value": self.value,
            "char_value": self.char_value,
            "severity": self.severity,
            "status": self.status,
            "timestamp": self.timestamp,
            "error": self.error,
        }


class PVCache:
    """Cache pyepics PV objects for periodic polling."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    @property
    def available(self) -> bool:
        return epics is not None

    def get_pv(self, pvname: str):
        if epics is None:
            return None
        pv = self._cache.get(pvname)
        if pv is None:
            pv = epics.PV(pvname, auto_monitor=False)
            self._cache[pvname] = pv
        return pv

    def snapshot(self, pvname: str) -> PVSnapshot:
        if epics is None:
            return PVSnapshot(
                pvname=pvname,
                connected=False,
                error="pyepics is not installed in this environment",
            )

        pv = self.get_pv(pvname)
        if pv is None:
            return PVSnapshot(
                pvname=pvname,
                connected=False,
                error="Unable to create PV handle",
            )

        try:
            connected = bool(pv.wait_for_connection(timeout=0.2))
        except Exception as exc:
            return PVSnapshot(
                pvname=pvname,
                connected=False,
                error=str(exc),
            )

        if not connected:
            return PVSnapshot(
                pvname=pvname,
                connected=False,
                error="Connection timeout",
            )

        try:
            value = pv.get(use_monitor=False)
        except Exception as exc:
            return PVSnapshot(
                pvname=pvname,
                connected=True,
                error=str(exc),
            )

        timestamp = None
        if getattr(pv, "timestamp", None):
            try:
                timestamp = datetime.fromtimestamp(float(pv.timestamp)).isoformat()
            except Exception:
                timestamp = None

        return PVSnapshot(
            pvname=pvname,
            connected=True,
            value=value,
            char_value=getattr(pv, "char_value", None),
            severity=getattr(pv, "severity", None),
            status=getattr(pv, "status", None),
            timestamp=timestamp,
        )

    def put(self, pvname: str, value: Any, *, wait: bool = False, timeout: float = 1.0) -> dict[str, Any]:
        if epics is None:
            return {
                "pvname": pvname,
                "value": value,
                "ok": False,
                "error": "pyepics is not installed in this environment",
            }

        pv = self.get_pv(pvname)
        if pv is None:
            return {
                "pvname": pvname,
                "value": value,
                "ok": False,
                "error": "Unable to create PV handle",
            }

        try:
            connected = bool(pv.wait_for_connection(timeout=timeout))
        except Exception as exc:
            return {
                "pvname": pvname,
                "value": value,
                "ok": False,
                "error": str(exc),
            }

        if not connected:
            return {
                "pvname": pvname,
                "value": value,
                "ok": False,
                "error": "Connection timeout",
            }

        try:
            result = pv.put(value, wait=wait, timeout=timeout)
        except Exception as exc:
            return {
                "pvname": pvname,
                "value": value,
                "ok": False,
                "error": str(exc),
            }

        return {
            "pvname": pvname,
            "value": value,
            "ok": bool(result is None or result == 1),
            "error": None,
        }
