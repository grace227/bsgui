from bsgui.core.scan_progress import (
    extract_inner_status,
    extract_outer_progress,
    extract_progress,
    extract_status,
    format_eta,
    render_progress_bar,
    update_scan_timing,
)

from bs_slack_bot.runtime import SlackbotRuntime

DEFAULT_CONSOLE_LINES = 10
MAX_CONSOLE_LINES = 20
MAX_CONSOLE_CHARS = 1200


def register_scan_progress_command(runtime: SlackbotRuntime) -> None:
    @runtime.app.command("/scan-progress")
    def handle_scan_progress_command(ack, respond, command):
        ack()

        try:
            full_text = runtime.rm_api.console_monitor.text()

            if not full_text.strip():
                respond("ℹ️ No recent scan progress or console messages found (or the buffer is still empty).")
                return

            update_scan_timing(full_text, runtime.scan_timing_state)

            status_text = extract_status(full_text) or "Waiting for console output"
            eta_text = format_eta(full_text, runtime.scan_timing_state)
            outer_status, outer_progress = extract_outer_progress(full_text)
            inner_status = extract_inner_status(full_text) or "Scan Progress"
            inner_progress = extract_progress(full_text)

            console_lines = _parse_console_line_limit(command)
            formatted_text = _format_console_output(full_text, console_lines)
            status_line = f"{status_text} | {eta_text}" if eta_text else status_text

            respond(
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "*Scan Progress*"},
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*Status:*\n{status_line}"},
                    },
                    {
                        "type": "section",
                        "fields": [
                            {
                                "type": "mrkdwn",
                                "text": (
                                    "*3D Scan*\n"
                                    f"{outer_status or 'Waiting for angle info'}\n"
                                    f"`{render_progress_bar(outer_progress)}`"
                                ),
                            },
                            {
                                "type": "mrkdwn",
                                "text": (
                                    "*Current Scan*\n"
                                    f"{inner_status}\n"
                                    f"`{render_progress_bar(inner_progress)}`"
                                ),
                            },
                        ],
                    },
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*Recent Console Output*",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"```{formatted_text}```",
                        },
                    },
                ],
                response_type="in_channel",
            )
        except Exception as exc:
            respond(f"⚠️ Error fetching scan progress: `{str(exc)}`")


def _parse_console_line_limit(command: dict) -> int:
    text = str(command.get("text", "")).strip()
    if not text:
        return DEFAULT_CONSOLE_LINES
    try:
        requested = int(text)
    except (TypeError, ValueError):
        return DEFAULT_CONSOLE_LINES
    return max(1, min(MAX_CONSOLE_LINES, requested))


def _format_console_output(full_text: str, max_lines: int) -> str:
    lines = full_text.strip().splitlines()
    limited = "\n".join(lines[-max_lines:])
    if len(limited) <= MAX_CONSOLE_CHARS:
        return limited
    return limited[-MAX_CONSOLE_CHARS:]
