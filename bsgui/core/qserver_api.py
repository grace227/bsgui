from bluesky_queueserver_api.zmq import REManagerAPI

class QServerAPI(REManagerAPI):
    """API for Bluesky QServer."""
    _client: REManagerAPI = None
    _status: dict = {}
    _connected: bool = False

    def __init__(self, *args, **kwargs):
        self._client = REManagerAPI(*args, **kwargs)
        self._status = self.update_status()

    def update_status(self):
        if self._client is not None:
            try:
                status = self._client.status()
                self._status = status
                self._connected = True
            except Exception as e:
                print(f"Error fetching status: {e}")
                self._connected = False
                self._status = None
    
