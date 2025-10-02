from __future__ import annotations

from typing import Any, Dict

from bluesky_queueserver_api.zmq import REManagerAPI


class QServerAPI(REManagerAPI):
    """API wrapper that handles connection state tracking for Bluesky QServer."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._status: Dict[str, Any] = {}
        self._connected: bool = False
        self.update_status()

    def update_status(self) -> Dict[str, Any]:
        try:
            status = self.status()
        except Exception as exc:  # pragma: no cover - network path
            print(f"Error fetching status: {exc}")
            self._connected = False
            self._status = {}
        else:
            self._status = status
            self._connected = True
        return self._status

    def get_status(self) -> Dict[str, Any]:
        return self.update_status()

    def get_queue(self) -> Dict[str, Any]:
        try:
            queue = self.queue_get()
        except Exception as exc:  # pragma: no cover - network path
            print(f"Error fetching queue: {exc}")
            self._connected = False
            return {}
        self._connected = True
        return queue
