import datetime as dt
import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("assistant_automation.py")
SPEC = importlib.util.spec_from_file_location("assistant_automation", MODULE_PATH)
AUTOMATION = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(AUTOMATION)


class AssistantAutomationTests(unittest.TestCase):
    def test_child_environment_excludes_slack_credentials(self):
        environment = {
            "PATH": "/bin",
            "SLACK_BOT_TOKEN": "secret",
            "SLACK_APP_TOKEN": "secret",
            "HERMES_ASSISTANT_SLACK_CHANNEL": "D123",
        }
        with mock.patch.dict(AUTOMATION.os.environ, environment, clear=True):
            self.assertEqual(AUTOMATION.sanitized_child_env(), {"PATH": "/bin"})

    def test_runtime_token_requires_mode_0600(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token.json"
            token.write_text("{}")
            token.chmod(0o600)
            with mock.patch.object(AUTOMATION, "runtime_token", return_value=token):
                AUTOMATION.check_runtime_token("personal")
            token.chmod(0o644)
            with (
                mock.patch.object(AUTOMATION, "runtime_token", return_value=token),
                self.assertRaisesRegex(RuntimeError, "runtime-token-mode"),
            ):
                AUTOMATION.check_runtime_token("personal")

    def test_health_state_tracks_48_hour_soak(self):
        with tempfile.TemporaryDirectory() as directory:
            start = dt.datetime(2026, 9, 14, 12, tzinfo=dt.timezone.utc)
            with (
                mock.patch.object(AUTOMATION, "state_directory", return_value=Path(directory)),
                mock.patch.object(AUTOMATION, "slack_post") as slack_post,
                mock.patch.object(AUTOMATION, "now_utc", return_value=start),
            ):
                first = AUTOMATION.record_health("personal", {"mail": "ok"}, None)
            self.assertFalse(first["soak_complete"])
            self.assertEqual(first["scope"], "personal")
            slack_post.assert_called_once()
            status_path = Path(directory) / "status.json"
            self.assertEqual(stat.S_IMODE(status_path.stat().st_mode), 0o600)

            complete_at = start + dt.timedelta(hours=49)
            with (
                mock.patch.object(AUTOMATION, "state_directory", return_value=Path(directory)),
                mock.patch.object(AUTOMATION, "slack_post") as second_slack_post,
                mock.patch.object(AUTOMATION, "now_utc", return_value=complete_at),
            ):
                second = AUTOMATION.record_health("personal", {"mail": "ok"}, None)
            self.assertTrue(second["soak_complete"])
            second_slack_post.assert_not_called()
            self.assertEqual(json.loads(status_path.read_text())["status"], "ok")

    def test_clean_brief_removes_session_identifier(self):
        value = AUTOMATION.clean_brief("Calendar: none\nSession ID: abc123\nActions: none")
        self.assertEqual(value, "Calendar: none\nActions: none")

    def test_failed_health_is_recorded_once(self):
        recorded = {"status": "failed"}
        with (
            mock.patch.object(AUTOMATION, "health_checks", side_effect=RuntimeError("down")),
            mock.patch.object(AUTOMATION, "record_health", return_value=recorded) as record_health,
            mock.patch("builtins.print"),
        ):
            self.assertEqual(AUTOMATION.command_health("work"), 1)
        record_health.assert_called_once_with("work", None, "RuntimeError")


if __name__ == "__main__":
    unittest.main()
