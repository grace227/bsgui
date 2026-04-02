import atexit
import threading
import time

from bs_slack_bot.runtime import SlackbotRuntime


class MonitorService:
    def __init__(self, runtime: SlackbotRuntime) -> None:
        self._runtime = runtime
        self._stop = threading.Event()
        self._state_lock = threading.Lock()
        self._last_console_message_at = time.monotonic()
        self._stall_alert_sent = False
        self._console_thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None

    def start(self) -> None:
        self._console_thread = threading.Thread(
            target=self._console_target,
            name="SlackbotConsoleMonitor",
            daemon=True,
        )
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_target,
            name="SlackbotStallWatchdog",
            daemon=True,
        )
        self._console_thread.start()
        self._watchdog_thread.start()
        atexit.register(self.stop)

    def stop(self) -> None:
        self._stop.set()
        try:
            self._runtime.rm_api.stop_console_stream()
        except Exception:
            pass

    def _console_target(self) -> None:
        while not self._stop.is_set():
            message = self._runtime.rm_api.recv_console_message(timeout=0.2)
            if message:
                with self._state_lock:
                    self._last_console_message_at = time.monotonic()
                    self._stall_alert_sent = False

    def _watchdog_target(self) -> None:
        settings = self._runtime.settings
        while not self._stop.wait(settings.watchdog_poll_seconds):
            try:
                status = self._runtime.rm_api.status()
            except Exception as exc:
                print(f"Stall watchdog status error: {exc}")
                continue

            re_state = status.get("worker_environment_state")
            with self._state_lock:
                stalled = (time.monotonic() - self._last_console_message_at) >= settings.console_stall_seconds
                should_alert = re_state == "executing_plan" and stalled and not self._stall_alert_sent
                if re_state != "executing_plan":
                    self._stall_alert_sent = False

            if not should_alert:
                continue

            if not settings.slack_alert_channel:
                print("Console stall detected but SLACK_ALERT_CHANNEL is not configured.")
                with self._state_lock:
                    self._stall_alert_sent = True
                continue

            try:
                self._runtime.app.client.chat_postMessage(
                    channel=settings.slack_alert_channel,
                    text=(
                        f":warning: No Queue Server console update for "
                        f"{int(settings.console_stall_seconds)} seconds while RE state is `executing_plan`."
                    ),
                )
                with self._state_lock:
                    self._stall_alert_sent = True
            except Exception as exc:
                print(f"Failed to send console stall alert: {exc}")
