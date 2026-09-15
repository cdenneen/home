import sys
import tempfile
import types
import unittest
from pathlib import Path

try:
    import psycopg
except ImportError:
    psycopg = types.ModuleType("psycopg")
    psycopg.Error = Exception
    psycopg.rows = types.SimpleNamespace(dict_row=object())
    sys.modules["psycopg"] = psycopg

try:
    from mcp.server.fastmcp import FastMCP  # noqa: F401
except ImportError:

    class _FastMCP:
        def __init__(self, *_args, **_kwargs):
            pass

        def tool(self):
            return lambda function: function

    mcp_module = types.ModuleType("mcp")
    mcp_server_module = types.ModuleType("mcp.server")
    mcp_fastmcp_module = types.ModuleType("mcp.server.fastmcp")
    mcp_fastmcp_module.FastMCP = _FastMCP
    sys.modules.update(
        {
            "mcp": mcp_module,
            "mcp.server": mcp_server_module,
            "mcp.server.fastmcp": mcp_fastmcp_module,
        }
    )

import server


class BrokerPolicyTests(unittest.TestCase):
    def test_visible_domains_preserve_private_boundaries(self):
        self.assertEqual(server.visible_domains("shared"), ("shared",))
        self.assertEqual(server.visible_domains("personal"), ("shared", "personal"))
        self.assertEqual(server.visible_domains("work"), ("shared", "work"))
        with self.assertRaises(ValueError):
            server.visible_domains("invalid")

    def test_frontmatter(self):
        metadata, body = server.parse_frontmatter(
            "---\nname: demo\ndescription: 'Useful tool'\n---\nBody"
        )
        self.assertEqual(metadata["name"], "demo")
        self.assertEqual(metadata["description"], "Useful tool")
        self.assertEqual(body, "Body")

    def test_skill_discovery_ignores_plain_documentation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "docs" / "SKILL.md").write_text(
                "# Not a registered skill", encoding="utf-8"
            )
            (root / "demo").mkdir()
            (root / "demo" / "SKILL.md").write_text(
                "---\nname: demo\n---\nBody", encoding="utf-8"
            )
            original = server.SKILL_ROOTS
            server.SKILL_ROOTS = (root,)
            try:
                self.assertEqual(
                    [metadata["name"] for _, metadata, _ in server._iter_skills()],
                    ["demo"],
                )
            finally:
                server.SKILL_ROOTS = original

    def test_sse(self):
        value = server.parse_http_json(
            b'data: {"jsonrpc":"2.0","result":{"ok":true}}\n\n', "text/event-stream"
        )
        self.assertTrue(value["result"]["ok"])

    def test_empty_mcp_notification_response(self):
        self.assertEqual(server.parse_http_json(b"", "application/json"), {})

    def test_promotion_requires_independent_evidence(self):
        server.validate_promotion("test-passed", "file:///test", "abc", "sha256:1", [])
        for flag in server.INELIGIBLE_FLAGS:
            with self.assertRaises(ValueError):
                server.validate_promotion(
                    "test-passed", "file:///test", "abc", "sha256:1", [flag]
                )
        with self.assertRaises(ValueError):
            server.validate_promotion(
                "model-success", "file:///test", "abc", "sha256:1", []
            )

    def test_rank_merge_is_deduplicated_and_bounded(self):
        rows = server.merge_ranked(
            (
                [{"id": "a", "score": 0.2}, {"id": "b", "score": 0.4}],
                [{"payload": {"id": "a", "title": "better"}, "score": 0.8}],
            ),
            1,
        )
        self.assertEqual(rows, [{"id": "a", "title": "better", "score": 0.8}])

    def test_skill_load_is_rooted_digest_checked_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "demo" / "SKILL.md"
            skill.parent.mkdir()
            skill.write_text("demo skill", encoding="utf-8")
            digest = server.hashlib.sha256(b"demo skill").hexdigest()
            self.assertEqual(
                server.read_verified_skill(skill, digest, [root], 20), "demo skill"
            )
            with self.assertRaises(ValueError):
                server.read_verified_skill(skill, "bad", [root], 20)
            with self.assertRaises(ValueError):
                server.read_verified_skill(skill, digest, [root], 5)

    def test_soft_budget_is_advisory(self):
        self.assertEqual(server.soft_budget_level(81, 100), "warning")
        self.assertEqual(server.soft_budget_level(101, 100), "critical")
        self.assertEqual(server.soft_budget_level(10, None), "unconfigured")

    def test_tool_side_effects_fail_unknown(self):
        self.assertEqual(server.classify_tool_side_effect("gitlab.merge_mr"), "write")
        self.assertEqual(
            server.classify_tool_side_effect("aws.describe_instances"), "read"
        )
        self.assertEqual(server.classify_tool_side_effect("vendor_magic"), "unknown")

    def test_external_context_requires_exact_allowlist(self):
        original = server.EXTERNAL_PROJECTS
        server.EXTERNAL_PROJECTS = frozenset({"approved"})
        try:
            self.assertEqual(server._external_context("query", "unapproved"), [])
        finally:
            server.EXTERNAL_PROJECTS = original


if __name__ == "__main__":
    unittest.main()
