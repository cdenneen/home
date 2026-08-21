import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "slack-platform" / "__init__.py"


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
