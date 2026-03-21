from dataclasses import dataclass

from slack_bolt import App

from bsgui.core.qserver_api import QServerAPI
from bsgui.core.scan_progress import ScanTimingState

from bs_slack_bot.settings import SlackbotSettings


@dataclass
class SlackbotRuntime:
    app: App
    rm_api: QServerAPI
    scan_timing_state: ScanTimingState
    settings: SlackbotSettings


def build_runtime(settings: SlackbotSettings) -> SlackbotRuntime:
    rm_api = QServerAPI(
        zmq_info_addr=settings.qserver_zmq_info_address,
        zmq_control_addr=settings.qserver_zmq_control_address,
    )
    app = App(token=settings.slack_bot_token)
    return SlackbotRuntime(
        app=app,
        rm_api=rm_api,
        scan_timing_state=ScanTimingState(),
        settings=settings,
    )
