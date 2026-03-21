import os
from dataclasses import dataclass
from pathlib import Path


def read_env_from_run_sh(name: str) -> str | None:
    run_sh = Path(__file__).with_name("run.sh")
    if not run_sh.exists():
        return None

    prefix = f"export {name}="
    for raw_line in run_sh.read_text().splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            value = line[len(prefix):].strip().strip('"').strip("'")
            return value or None
    return None


def get_setting(name: str) -> str | None:
    return read_env_from_run_sh(name) or os.environ.get(name)


@dataclass(frozen=True)
class SlackbotSettings:
    slack_bot_token: str | None
    slack_app_token: str | None
    slack_alert_channel: str | None
    qserver_zmq_info_address: str | None
    qserver_zmq_control_address: str | None
    console_stall_seconds: float
    watchdog_poll_seconds: float


def load_settings() -> SlackbotSettings:
    return SlackbotSettings(
        slack_bot_token=get_setting("SLACK_BOT_TOKEN"),
        slack_app_token=get_setting("SLACK_APP_TOKEN"),
        slack_alert_channel=get_setting("SLACK_ALERT_CHANNEL"),
        qserver_zmq_info_address=get_setting("QSERVER_ZMQ_INFO_ADDRESS"),
        qserver_zmq_control_address=get_setting("QSERVER_ZMQ_CONTROL_ADDRESS"),
        console_stall_seconds=float(get_setting("SLACK_CONSOLE_STALL_SECONDS") or "1800"),
        watchdog_poll_seconds=float(get_setting("SLACK_CONSOLE_WATCHDOG_POLL_SECONDS") or "15"),
    )
