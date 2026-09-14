import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("backlog_sync.py")
SPEC = importlib.util.spec_from_file_location("backlog_sync", MODULE_PATH)
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class BacklogSyncTests(unittest.TestCase):
    def test_reads_only_requested_glab_host_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yml"
            config.write_text(
                "hosts:\n"
                "    gitlab.com:\n"
                "        token: personal-token\n"
                "    git.ap.org:\n"
                "        token: work-token\n"
                "api_protocol: https\n"
            )
            self.assertEqual(sync.glab_token("gitlab.com", config), "personal-token")
            self.assertEqual(sync.glab_token("git.ap.org", config), "work-token")

    def test_missing_glab_host_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yml"
            config.write_text("hosts:\n    gitlab.com:\n        token: token\n")
            with self.assertRaises(RuntimeError):
                sync.glab_token("git.ap.org", config)

    def test_personal_free_tier_uses_label_workflow(self) -> None:
        self.assertEqual(
            sync.workflow_for(
                {"state": "opened", "labels": ["stage::implementation-in-progress"]},
                "personal-labels",
            ),
            "active",
        )
        self.assertEqual(
            sync.workflow_for(
                {"state": "opened", "labels": ["gate::closure-blocked"]},
                "personal-labels",
            ),
            "blocked",
        )

    def test_work_premium_uses_state_label_workflow(self) -> None:
        self.assertEqual(
            sync.workflow_for(
                {"state": "opened", "labels": ["state::upstream-waiting"]},
                "work-native",
            ),
            "blocked",
        )
        self.assertEqual(
            sync.workflow_for(
                {"state": "opened", "labels": ["state::in-progress"]},
                "work-native",
            ),
            "active",
        )

    def test_normalized_personal_issue_keeps_native_milestone_informational(self) -> None:
        issue = {
            "iid": 7,
            "project_id": 42,
            "title": "Example",
            "description": "Body",
            "web_url": "https://gitlab.com/group/project/-/issues/7",
            "state": "opened",
            "labels": ["roadmap::AX-M1", "epic::kernel", "stage::backlog"],
            "assignees": [],
            "epic": None,
            "milestone": {"title": "Not authoritative"},
            "updated_at": "2026-09-14T00:00:00Z",
        }
        normalized = sync.normalize_issue(
            "gitlab.com",
            {"id": 42, "path": "group/project", "logical_project": "personal-axis"},
            issue,
            "labels",
            "personal-labels",
        )
        self.assertIsNone(normalized["milestone"])
        self.assertEqual(normalized["native_milestone_informational"]["title"], "Not authoritative")
        self.assertEqual(
            normalized["planning_labels"],
            ["epic::kernel", "roadmap::AX-M1", "stage::backlog"],
        )

    def test_priority_vocabularies_are_normalized(self) -> None:
        self.assertEqual(sync.priority_for(["priority::p0"]), 100)
        self.assertEqual(sync.priority_for(["prio::p2"]), 60)
        self.assertEqual(sync.priority_for(["prio::low"]), 40)

    def test_known_issue_inventory_is_scoped_by_source_and_project(self) -> None:
        source = {
            "id": "work",
            "projects": [
                {"path": "group/a"},
                {"path": "group/b"},
            ],
        }
        ledger = {
            "items": {
                "one": {
                    "source_id": "work",
                    "kind": "issue",
                    "project_path": "group/a",
                    "iid": 3,
                },
                "other-source": {
                    "source_id": "personal",
                    "kind": "issue",
                    "project_path": "group/a",
                    "iid": 4,
                },
            }
        }
        copied = sync.source_with_known(source, ledger)
        self.assertEqual(copied["projects"][0]["known_iids"], [3])
        self.assertEqual(copied["projects"][1]["known_iids"], [])

    def test_externally_active_card_gets_a_typed_sticky_block(self) -> None:
        item = {
            "key": "gitlab:git.ap.org:project:1:issue:2",
            "kind": "issue",
            "workflow": "active",
            "title": "Active elsewhere",
            "priority": 80,
            "host": "git.ap.org",
            "web_url": "https://git.ap.org/example/-/issues/2",
            "project_path": "example/project",
            "iid": 2,
            "planning_labels": [],
            "assignees": [],
            "description": "",
        }
        with mock.patch.object(
            sync,
            "hermes",
            side_effect=['{"id":"t_1234","status":"ready"}', ""],
        ) as hermes:
            self.assertEqual(sync.create_card(item, "work", False), "t_1234")
        block_command = hermes.call_args_list[1].args[0]
        self.assertIn("needs_input", block_command)
        self.assertEqual(block_command[-2], "t_1234")


if __name__ == "__main__":
    unittest.main()
