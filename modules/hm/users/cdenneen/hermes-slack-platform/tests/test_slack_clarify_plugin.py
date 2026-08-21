import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("slack_platform_override", PLUGIN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeApp:
    def __init__(self):
        self.handlers = []

    def action(self, action_id):
        def register(handler):
            self.handlers.append((action_id, handler))
            return handler

        return register


class FakeSlackAdapter:
    def __init__(self):
        self._app = None
        self.starts = 0

    def _start_socket_mode_handler(self):
        self.starts += 1
        return "started"

    async def send_clarify(self, *args, **kwargs):
        return None

    async def _handle_clarify_action(self, ack, body, action):
        return None


def fake_bundled_module():
    return SimpleNamespace(
        SlackAdapter=FakeSlackAdapter,
        SendResult=SimpleNamespace,
        sanitize_blocks=lambda blocks: blocks,
    )


def test_select_handler_is_registered_before_socket_start():
    plugin = load_plugin()
    bundled = fake_bundled_module()
    plugin._patch_bundled_adapter(bundled)
    adapter = bundled.SlackAdapter()
    adapter._app = FakeApp()

    assert adapter._start_socket_mode_handler() == "started"
    assert [action_id for action_id, _handler in adapter._app.handlers] == [
        "hermes_clarify_select"
    ]
    assert adapter.starts == 1


def test_select_handler_is_registered_once_per_reconnected_app():
    plugin = load_plugin()
    bundled = fake_bundled_module()
    plugin._patch_bundled_adapter(bundled)
    adapter = bundled.SlackAdapter()
    first_app = FakeApp()
    adapter._app = first_app

    adapter._start_socket_mode_handler()
    adapter._start_socket_mode_handler()
    assert len(first_app.handlers) == 1

    second_app = FakeApp()
    adapter._app = second_app
    adapter._start_socket_mode_handler()
    assert len(second_app.handlers) == 1


def test_send_clarify_renders_readable_choices_and_compact_selector():
    plugin = load_plugin()
    bundled = fake_bundled_module()
    plugin._patch_bundled_adapter(bundled)
    adapter = bundled.SlackAdapter()
    adapter._app = True
    adapter._clarify_resolved = {}
    adapter._CLARIFY_RESOLVED_MAX = 100
    adapter._metadata_team_id = lambda _metadata: "team"
    adapter._resolve_thread_ts = lambda _message, _metadata: "thread"
    adapter._ensure_dm_conversation = AsyncMock(return_value="channel")
    adapter._trim_oldest_dict_entries = Mock()
    client = SimpleNamespace(chat_postMessage=AsyncMock(return_value={"ts": "message"}))
    adapter._get_client = lambda _chat_id: client

    result = asyncio.run(
        adapter.send_clarify(
            chat_id="channel",
            question="Which environment should be changed?",
            choices=["Production with the full descriptive label", "Staging"],
            clarify_id="clarify-1",
            session_key="session",
        )
    )

    assert result.success is True
    blocks = client.chat_postMessage.await_args.kwargs["blocks"]
    assert blocks[1]["text"]["text"] == "*1.* Production with the full descriptive label"
    assert blocks[2]["text"]["text"] == "*2.* Staging"
    selector = blocks[-1]["accessory"]
    assert selector["action_id"] == "hermes_clarify_select"
    assert [option["text"]["text"] for option in selector["options"]] == [
        "1",
        "2",
        "Other (type answer)",
    ]
    assert [option["value"] for option in selector["options"]] == [
        "clarify-1|0",
        "clarify-1|1",
        "clarify-1|other",
    ]


def test_static_select_resolves_original_choice(monkeypatch):
    plugin = load_plugin()
    bundled = fake_bundled_module()
    plugin._patch_bundled_adapter(bundled)
    adapter = bundled.SlackAdapter()
    adapter._clarify_resolved = {"message": False}
    adapter._event_team_id = lambda _event, _body: "team"
    adapter._is_interactive_user_authorized = lambda *_args, **_kwargs: True
    adapter._update_clarify_message = AsyncMock()
    clarify = SimpleNamespace(
        _entries={
            "clarify-1": SimpleNamespace(
                choices=["Production with the full descriptive label", "Staging"]
            )
        },
        resolve_gateway_clarify=Mock(return_value=True),
    )
    monkeypatch.setattr(plugin.importlib, "import_module", lambda _name: clarify)
    ack = AsyncMock()

    asyncio.run(
        adapter._handle_clarify_action(
            ack,
            {
                "channel": {"id": "channel"},
                "message": {
                    "ts": "message",
                    "blocks": [
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": "Question"},
                        }
                    ],
                },
                "user": {"id": "user", "name": "Chris"},
            },
            {
                "action_id": "hermes_clarify_select",
                "selected_option": {"value": "clarify-1|0"},
            },
        )
    )

    ack.assert_awaited_once()
    clarify.resolve_gateway_clarify.assert_called_once_with(
        "clarify-1", "Production with the full descriptive label"
    )
    adapter._update_clarify_message.assert_awaited_once_with(
        "channel",
        "message",
        "Question",
        "✅ Chris: Production with the full descriptive label",
    )
