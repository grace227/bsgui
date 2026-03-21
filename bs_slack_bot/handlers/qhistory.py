from collections.abc import Mapping
from typing import Any

from bsgui.core.queue_item_utils import prepare_display_item, resolve_queue_value

from bs_slack_bot.runtime import SlackbotRuntime

BASE_COLUMNS: list[tuple[str, str]] = [
    ("status", "Status"),
    ("name", "Plan"),
    ("time_start", "Start Time"),
    ("scan_ids", "Scan ID"),
]
DEFAULT_MAX_ROWS = 15
MAX_ROWS = 40
MAX_CELL_WIDTH = 80
MAX_DETAIL_FIELDS = 8
DETAILS_PER_ROW = 3
MAX_SLACK_BLOCKS = 50


def register_qhistory_command(runtime: SlackbotRuntime) -> None:
    @runtime.app.command("/qhistory")
    def handle_qhistory_command(ack, respond, command):
        ack()

        try:
            row_limit = _parse_row_limit(command)
            queue_response = runtime.rm_api.queue_get()
            history_response = runtime.rm_api.history_get()

            pending = _extract_items(queue_response.get("items"))
            running_raw = queue_response.get("running_item")
            running = dict(running_raw) if isinstance(running_raw, Mapping) else None
            completed = _extract_items(history_response.get("items"))

            display_rows = _build_display_rows(
                pending=pending,
                running=running,
                completed=completed,
                row_limit=row_limit,
            )
            if not display_rows:
                respond("ℹ️ No queue or history items found.")
                return

            columns = _build_columns([row["item"] for row in display_rows])
            blocks = _build_history_blocks(
                display_rows,
                columns,
                running_count=1 if running else 0,
                pending_count=len(pending),
                history_count=len(completed),
                row_limit=row_limit,
            )
            respond(
                blocks=blocks,
                response_type="in_channel",
            )
        except Exception as exc:
            respond(f"⚠️ Error fetching queue history: `{str(exc)}`")


def _extract_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, Mapping)]


def _build_display_rows(
    *,
    pending: list[dict[str, Any]],
    running: dict[str, Any] | None,
    completed: list[dict[str, Any]],
    row_limit: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if running:
        rows.append({"item": prepare_display_item(running), "running": True})

    for item in pending:
        rows.append({"item": prepare_display_item(item), "running": False})

    for item in reversed(completed):
        rows.append({"item": prepare_display_item(item, completed=True), "running": False})

    return rows[:row_limit]


def _build_columns(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    columns = list(BASE_COLUMNS)
    seen = {column_id for column_id, _label in columns}

    for item in items:
        kwargs_sources: list[Mapping[str, Any]] = []
        kwargs = item.get("kwargs")
        if isinstance(kwargs, Mapping):
            kwargs_sources.append(kwargs)
        nested_item = item.get("item")
        if isinstance(nested_item, Mapping):
            nested_kwargs = nested_item.get("kwargs")
            if isinstance(nested_kwargs, Mapping):
                kwargs_sources.append(nested_kwargs)
        for mapping in kwargs_sources:
            for key in mapping.keys():
                key_str = str(key)
                if key_str in seen:
                    continue
                seen.add(key_str)
                columns.append((key_str, key_str.replace("_", " ").title()))

    return columns


def _build_history_blocks(
    rows: list[dict[str, Any]],
    columns: list[tuple[str, str]],
    *,
    running_count: int,
    pending_count: int,
    history_count: int,
    row_limit: int,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "*Queue History*\n"
                    f"Running: {running_count} | Pending: {pending_count} | History: {history_count} | "
                    f"Showing: {len(rows)}/{running_count + pending_count + history_count}"
                ),
            },
        }
    ]

    for row_index, row_info in enumerate(rows):
        item = row_info["item"]
        running = bool(row_info["running"])
        values: dict[str, str] = {}
        for column_id, label in columns:
            value, _source_key = resolve_queue_value(
                column_id,
                item,
                row_index,
                roi_key_map={},
                roi_value_aliases=set(),
                running=running,
            )
            values[label] = _trim_cell(value)

        title_bits = [f"*{row_index + 1}. {values.get('Plan') or 'Unknown'}*"]
        if running:
            title_bits.append("`RUNNING`")
        elif row_index < pending_count + running_count:
            title_bits.append("`PENDING`")
        else:
            title_bits.append("`HISTORY`")

        summary_parts = []
        for key in ("Status", "Start Time", "Scan ID"):
            value = values.get(key)
            if value:
                summary_parts.append(f"*{key}:* {value}")
        summary_text = " | ".join(summary_parts) if summary_parts else "_No summary fields_"

        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "\n".join([" ".join(title_bits), summary_text]),
                },
            }
        )

        detail_entries = []
        for label, value in values.items():
            if label in {"Status", "Plan", "Start Time", "Scan ID"} or not value:
                continue
            detail_entries.append(f"*{label}:* {value}")
            if len(detail_entries) >= MAX_DETAIL_FIELDS:
                break
        if detail_entries:
            detail_lines = [
                " | ".join(detail_entries[idx : idx + DETAILS_PER_ROW])
                for idx in range(0, len(detail_entries), DETAILS_PER_ROW)
            ]
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "\n".join(detail_lines),
                    },
                }
            )

        if row_index < len(rows) - 1:
            blocks.append({"type": "divider"})

    if len(rows) < (running_count + pending_count + history_count):
        remaining = (running_count + pending_count + history_count) - len(rows)
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"Showing {len(rows)} items. {remaining} more not shown. "
                            f"Use `/qhistory {min(MAX_ROWS, max(row_limit + 10, DEFAULT_MAX_ROWS))}` to request more."
                        ),
                    }
                ],
            }
        )

    return blocks


def _trim_cell(value: Any) -> str:
    text = "" if value is None else str(value).replace("\n", " ")
    if len(text) <= MAX_CELL_WIDTH:
        return text
    return f"{text[:MAX_CELL_WIDTH - 3]}..."


def _parse_row_limit(command: dict[str, Any]) -> int:
    text = str(command.get("text", "")).strip()
    if not text:
        return DEFAULT_MAX_ROWS
    try:
        requested = int(text)
    except (TypeError, ValueError):
        return DEFAULT_MAX_ROWS
    return max(1, min(_max_rows_from_slack_blocks(), requested))


def _max_rows_from_slack_blocks() -> int:
    # Each item uses 2 blocks minimum (summary + divider) and usually 3 with details.
    # Keep a conservative cap so the response stays under Slack's 50-block limit.
    return min(MAX_ROWS, max(1, (MAX_SLACK_BLOCKS - 2) // 3))
