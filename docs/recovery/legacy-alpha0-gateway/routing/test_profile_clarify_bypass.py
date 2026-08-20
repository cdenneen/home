"""Regression coverage for ordinary Slack replies to Alpha0 clarifications."""

import asyncio
import unittest
from unittest.mock import AsyncMock

from gateway.config import Platform, PlatformConfig
from gateway.platforms import base
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.session import SessionSource, build_session_key
from tools import clarify_gateway


class _SlackAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="test"), Platform.SLACK)

    async def connect(self):
        return True

    async def disconnect(self):
        pass

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id="text")

    async def get_chat_info(self, chat_id):
        return {"id": chat_id, "type": "private"}


class ProfileClarifyBypassTest(unittest.IsolatedAsyncioTestCase):
    async def test_plain_reply_reaches_profiled_clarify_without_busy_control(self):
        adapter = _SlackAdapter()
        adapter._message_handler = AsyncMock(return_value="")
        adapter._busy_session_handler = AsyncMock(return_value=True)
        event = MessageEvent(
            text="The Catskill location, for four people",
            message_type=MessageType.TEXT,
            source=SessionSource(
                platform=Platform.SLACK,
                scope_id="TALPHA0",
                chat_id="DALPHA0",
                chat_type="dm",
                user_id="UALPHA0",
                thread_id="1234567890.123456",
                profile="alpha0",
            ),
            message_id="message-2",
        )
        options = {
            "group_sessions_per_user": adapter.config.extra.get(
                "group_sessions_per_user", True
            ),
            "thread_sessions_per_user": adapter.config.extra.get(
                "thread_sessions_per_user", False
            ),
        }
        legacy_key = build_session_key(event.source, **options)
        profiled_key = build_session_key(
            event.source, profile=event.source.profile, **options
        )

        self.assertNotEqual(legacy_key, profiled_key)
        self.assertEqual(base.build_session_key(event.source, **options), profiled_key)

        # Reproduce both possible adapter guards. The pending clarify belongs
        # only to Alpha0's real profile namespace.
        adapter._active_sessions[legacy_key] = asyncio.Event()
        adapter._active_sessions[profiled_key] = asyncio.Event()
        clarify_gateway.register(
            "alpha0-open-ended-clarify",
            profiled_key,
            "Which location and how many people?",
            None,
        )
        try:
            await adapter.handle_message(event)
        finally:
            clarify_gateway.clear_session(profiled_key)

        adapter._message_handler.assert_awaited_once_with(event)
        adapter._busy_session_handler.assert_not_awaited()
        self.assertEqual(adapter._pending_messages, {})


if __name__ == "__main__":
    unittest.main()
