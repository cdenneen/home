import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name("peer_dispatch.py")
SPEC = importlib.util.spec_from_file_location("peer_dispatch", MODULE_PATH)
peer_dispatch = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(peer_dispatch)


class PeerDispatchTest(unittest.TestCase):
    def test_start_replays_an_isolated_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            connection = peer_dispatch.connect(Path(temporary))
            args = SimpleNamespace(
                target="nyx/ops",
                idempotency_key="task-1",
                board="work",
                task="t_1",
                message="inspect only",
            )
            accepted = {"run_id": "run_1", "status": "started", "replayed": False}
            markers = []

            def fake_comment(_config, _board, _task, marker, _message):
                if marker in markers:
                    return False
                markers.append(marker)
                return True

            with patch.object(peer_dispatch, "run_hermes", return_value="{}"), patch.object(
                peer_dispatch, "request", return_value=accepted
            ) as api_request, patch.object(
                peer_dispatch, "comment_once", side_effect=fake_comment
            ), redirect_stdout(io.StringIO()):
                peer_dispatch.start(args, {}, connection)
                accepted["replayed"] = True
                peer_dispatch.start(args, {}, connection)

            self.assertEqual(api_request.call_args.args[4], {"input": "inspect only"})
            self.assertNotIn("session_id", api_request.call_args.args[4])
            self.assertEqual(connection.execute("SELECT count(*) FROM runs").fetchone()[0], 1)
            self.assertEqual(markers, ["[peer-run:run_1:started]"])
            connection.close()

    def test_terminal_result_is_recorded_and_commented_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_dir = Path(temporary)
            connection = peer_dispatch.connect(state_dir)
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)",
                ("run_1", "nyx/ops", "task-1", "work", "t_1", "running", 1, 1),
            )
            connection.commit()
            status = {"run_id": "run_1", "status": "completed", "output": "done"}
            comments = []

            def fake_comment_once(_config, _board, _task, marker, message):
                if marker in comments:
                    return False
                comments.append(marker)
                return True

            with patch.object(peer_dispatch, "request", return_value=status), patch.object(
                peer_dispatch, "comment_once", side_effect=fake_comment_once
            ):
                peer_dispatch.reconcile({}, state_dir, connection)
                peer_dispatch.reconcile({}, state_dir, connection)

            row = connection.execute("SELECT * FROM runs WHERE run_id = 'run_1'").fetchone()
            self.assertEqual(row["status"], "completed")
            self.assertIsNotNone(row["delivered_at"])
            self.assertEqual(comments, ["[peer-run:run_1:terminal]"])
            payload = json.loads(Path(row["result_path"]).read_text())
            self.assertEqual(payload["output"], "done")
            connection.close()


if __name__ == "__main__":
    unittest.main()
