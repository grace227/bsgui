from bs_slack_bot.runtime import SlackbotRuntime


def register_qstatus_command(runtime: SlackbotRuntime) -> None:
    @runtime.app.command("/qstatus")
    def handle_status_command(ack, respond, command):
        ack()

        try:
            status = runtime.rm_api.status()
            worker_environment_exists = status.get("worker_environment_exists", False)
            manager_state = status.get("manager_state", "unknown")
            items_in_queue = status.get("items_in_queue", 0)
            items_in_history = status.get("items_in_history", 0)
            running_item_uid = status.get("running_item_uid", None)

            reply_blocks = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Queue Status*"},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Manager State:*\n{manager_state.capitalize()}"},
                        {
                            "type": "mrkdwn",
                            "text": f"*Worker Environment:*\n{'✅ Exists' if worker_environment_exists else '❌ Does not exist'}",
                        },
                        {"type": "mrkdwn", "text": f"*Items in Queue:*\n{items_in_queue}"},
                        {"type": "mrkdwn", "text": f"*Items in History:*\n{items_in_history}"},
                    ],
                }
            ]

            if running_item_uid:
                reply_blocks.append(
                    {"type": "divider"}
                )
                reply_blocks.append(
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Currently Running Item UID:*\n`{running_item_uid}`",
                        },
                    }
                )

            respond(blocks=reply_blocks, response_type="in_channel")
        except Exception as exc:
            respond(f"⚠️ Error reaching Bluesky Queue Server: `{str(exc)}`")
