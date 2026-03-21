import sys
from pathlib import Path

from slack_bolt.adapter.socket_mode import SocketModeHandler

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bs_slack_bot.handlers.qhistory import register_qhistory_command  # noqa: E402
from bs_slack_bot.handlers.qstatus import register_qstatus_command  # noqa: E402
from bs_slack_bot.handlers.scan_progress import register_scan_progress_command  # noqa: E402
from bs_slack_bot.monitor import MonitorService  # noqa: E402
from bs_slack_bot.runtime import build_runtime  # noqa: E402
from bs_slack_bot.settings import load_settings  # noqa: E402


def main() -> None:
    settings = load_settings()
    if not settings.slack_bot_token or not settings.slack_app_token:
        print("Error: SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be configured.")
        raise SystemExit(1)

    runtime = build_runtime(settings)
    register_scan_progress_command(runtime)
    register_qhistory_command(runtime)
    register_qstatus_command(runtime)

    monitor_service = MonitorService(runtime)
    monitor_service.start()

    print("Starting Bluesky QServer Slackbot...")
    print(
        "Alert config:"
        f" channel={settings.slack_alert_channel!r},"
        f" stall_timeout={settings.console_stall_seconds}s,"
        f" watchdog_poll={settings.watchdog_poll_seconds}s"
    )
    handler = SocketModeHandler(runtime.app, settings.slack_app_token)
    handler.start()


if __name__ == "__main__":
    main()
