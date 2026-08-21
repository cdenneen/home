from __future__ import annotations

import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
_PATCH_FLAG = "_hermes_slack_clarify_select_patch_v2"
_SELECT_HANDLER_FLAG = "_hermes_clarify_select_handler_registered"


def _load_bundled_slack_module():
    cached = sys.modules.get("hermes_bundled_platforms_slack_adapter")
    if cached is not None:
        return cached

    plugins_root = os.getenv("HERMES_BUNDLED_PLUGINS")
    if not plugins_root:
        raise RuntimeError("HERMES_BUNDLED_PLUGINS is not set")

    plugin_dir = Path(plugins_root) / "platforms" / "slack"
    adapter_path = plugin_dir / "adapter.py"
    if not adapter_path.exists():
        raise RuntimeError(f"Bundled Slack adapter not found: {adapter_path}")

    plugin_dir_str = str(plugin_dir)
    if plugin_dir_str not in sys.path:
        sys.path.insert(0, plugin_dir_str)

    spec = importlib.util.spec_from_file_location(
        "hermes_bundled_platforms_slack_adapter",
        adapter_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load spec for {adapter_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _escape_mrkdwn(text: Any) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _patch_bundled_adapter(module) -> None:
    SlackAdapter = module.SlackAdapter
    if getattr(SlackAdapter, _PATCH_FLAG, False):
        return

    SendResult = module.SendResult
    original_start_socket_mode_handler = SlackAdapter._start_socket_mode_handler

    def patched_start_socket_mode_handler(self):
        app = getattr(self, "_app", None)
        if app is not None and not getattr(app, _SELECT_HANDLER_FLAG, False):
            app.action("hermes_clarify_select")(self._handle_clarify_action)
            setattr(app, _SELECT_HANDLER_FLAG, True)
            logger.info("Slack clarify select action handler registered")
        return original_start_socket_mode_handler(self)

    async def patched_send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: Optional[list],
        clarify_id: str,
        session_key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        if not choices:
            return await super(SlackAdapter, self).send_clarify(
                chat_id=chat_id,
                question=question,
                choices=choices,
                clarify_id=clarify_id,
                session_key=session_key,
                metadata=metadata,
            )

        if not self._app:
            return SendResult(success=False, error="Not connected")

        chat_id = await self._ensure_dm_conversation(
            chat_id,
            team_id=self._metadata_team_id(metadata),
        )
        try:
            thread_ts = self._resolve_thread_ts(None, metadata)
            header = _truncate(f"❓ {_escape_mrkdwn(question)}", 3000)
            blocks: list[dict[str, Any]] = [
                {"type": "section", "text": {"type": "mrkdwn", "text": header}}
            ]
            fallback_lines = [question or "Clarification requested:"]
            options = []

            for choice_index, choice in enumerate(choices):
                raw_label = str(choice).strip() or f"Option {choice_index + 1}"
                option_line = _truncate(
                    f"*{choice_index + 1}.* {_escape_mrkdwn(raw_label)}",
                    3000,
                )
                blocks.append(
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": option_line},
                    }
                )
                fallback_lines.append(f"{choice_index + 1}. {raw_label}")
                options.append(
                    {
                        "text": {
                            "type": "plain_text",
                            "text": f"{choice_index + 1}",
                            "emoji": True,
                        },
                        "value": f"{clarify_id}|{choice_index}",
                    }
                )

            options.append(
                {
                    "text": {
                        "type": "plain_text",
                        "text": "Other (type answer)",
                        "emoji": True,
                    },
                    "value": f"{clarify_id}|other",
                }
            )
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Choose an option:*"},
                    "accessory": {
                        "type": "static_select",
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Select an option...",
                            "emoji": True,
                        },
                        "action_id": "hermes_clarify_select",
                        "options": options,
                    },
                }
            )

            sanitized = module.sanitize_blocks(blocks)
            if sanitized:
                blocks = sanitized

            fallback_text = _truncate(
                "\n".join(
                    fallback_lines
                    + ["Reply with the number above or use the selector."]
                ),
                4000,
            )
            message_args: Dict[str, Any] = {
                "channel": chat_id,
                "text": fallback_text,
                "blocks": blocks,
            }
            if thread_ts:
                message_args["thread_ts"] = thread_ts

            result = await self._get_client(chat_id).chat_postMessage(**message_args)
            message_ts = result.get("ts", "")
            if message_ts:
                self._clarify_resolved[message_ts] = False
                self._trim_oldest_dict_entries(
                    self._clarify_resolved,
                    self._CLARIFY_RESOLVED_MAX,
                )
            return SendResult(
                success=True,
                message_id=message_ts,
                raw_response=result,
            )
        except Exception as exception:
            logger.error(
                "[Slack override] send_clarify failed: %s",
                exception,
                exc_info=True,
            )
            return SendResult(success=False, error=str(exception))

    async def patched_handle_clarify_action(self, ack, body, action) -> None:
        await ack()

        action_id = action.get("action_id", "")
        if action_id == "hermes_clarify_select":
            value = ((action.get("selected_option") or {}).get("value") or "")
        else:
            value = action.get("value", "")
        message = body.get("message", {})
        message_ts = message.get("ts", "")
        channel_id = body.get("channel", {}).get("id", "")
        user_name = body.get("user", {}).get("name", "unknown")
        user_id = body.get("user", {}).get("id", "")
        try:
            team_id = self._event_team_id({}, body)
        except Exception:
            team_id = ""

        if not self._is_interactive_user_authorized(
            user_id,
            channel_id=channel_id,
            user_name=user_name,
            team_id=team_id,
        ):
            logger.warning(
                "[Slack override] Unauthorized clarify click by %s (%s) - ignoring",
                user_name,
                user_id,
            )
            return

        if "|" not in value:
            logger.warning("[Slack override] Malformed clarify value: %s", value)
            return
        clarify_id, token = value.split("|", 1)

        if self._clarify_resolved.pop(message_ts, True):
            return

        original_text = ""
        for block in message.get("blocks", []):
            if block.get("type") == "section":
                original_text = ((block.get("text") or {}).get("text") or "")
                break

        clarify_module = importlib.import_module("tools.clarify_gateway")

        if action_id == "hermes_clarify_other" or token == "other":
            if not clarify_module.mark_awaiting_text(clarify_id):
                await self._update_clarify_message(
                    channel_id,
                    message_ts,
                    original_text,
                    f"⏳ This prompt expired — please send a new request. (by {user_name})",
                )
                return
            await self._update_clarify_message(
                channel_id,
                message_ts,
                original_text,
                f"✏️ Awaiting typed answer from {user_name}...",
            )
            return

        try:
            choice_index = int(token)
        except (TypeError, ValueError):
            logger.warning("[Slack override] Invalid clarify choice token: %s", token)
            return

        resolved_text: Optional[str] = None
        try:
            entry = clarify_module._entries.get(clarify_id)
            if entry and entry.choices and 0 <= choice_index < len(entry.choices):
                resolved_text = str(entry.choices[choice_index])
        except Exception:
            resolved_text = None
        if resolved_text is None:
            resolved_text = f"choice {choice_index + 1}"

        if clarify_module.resolve_gateway_clarify(clarify_id, resolved_text):
            await self._update_clarify_message(
                channel_id,
                message_ts,
                original_text,
                f"✅ {user_name}: {resolved_text}",
            )
            logger.info(
                "Slack clarify select resolved (id=%s, choice_index=%d, user=%s)",
                clarify_id,
                choice_index,
                user_name,
            )
        else:
            await self._update_clarify_message(
                channel_id,
                message_ts,
                original_text,
                f"⏳ This prompt expired — please send a new request. (by {user_name})",
            )
            logger.warning(
                "[Slack override] clarify resolve returned False (id=%s)",
                clarify_id,
            )

    SlackAdapter._start_socket_mode_handler = patched_start_socket_mode_handler
    SlackAdapter.send_clarify = patched_send_clarify
    SlackAdapter._handle_clarify_action = patched_handle_clarify_action
    setattr(SlackAdapter, _PATCH_FLAG, True)


def register(ctx) -> None:
    module = _load_bundled_slack_module()
    _patch_bundled_adapter(module)
    module.register(ctx)
