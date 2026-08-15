from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import mock


def load(path: str):
    loader = importlib.machinery.SourceFileLoader("alpha0_node_codex_plan_test", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def main() -> None:
    import sys

    wrapper, git = sys.argv[1:]
    module = load(wrapper)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        repository = root / "repository"
        artifacts = root / "artifacts"
        repository.mkdir()
        subprocess.run([git, "-C", repository, "init", "-q"], check=True)
        subprocess.run(
            [git, "-C", repository, "config", "user.name", "Alpha0-Test"], check=True
        )
        subprocess.run(
            [git, "-C", repository, "config", "user.email", "alpha0@example.invalid"],
            check=True,
        )
        (repository / "README.md").write_text("bounded plan\n", encoding="utf-8")
        subprocess.run([git, "-C", repository, "add", "README.md"], check=True)
        subprocess.run(
            [git, "-C", repository, "commit", "-q", "-m", "initial"], check=True
        )
        head = subprocess.run(
            [git, "-C", repository, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        package = {
            "schema": "alpha0.work-package.v1",
            "package_id": "wp-plan-test-001",
            "execution": {},
            "route": {},
            "project": {"id": "eks", "source_ref": "gitlab://example/eks"},
            "repository": {"id": "eks", "base_ref": "main", "base_sha": head},
            "worker": {"adapter": "codex-plan"},
            "goal": "Plan a bounded repair using only exact repository evidence.",
            "context_refs": ["gitlab://example/eks/pipelines/1#sha256:" + "a" * 64],
            "deliverables": [
                {
                    "id": "plan",
                    "kind": "report",
                    "description": "One plan",
                    "required_evidence": ["worker-plan"],
                }
            ],
            "acceptance_criteria": [
                {
                    "id": "exact-base",
                    "description": "Exact base inspected",
                    "required_evidence": ["verify-base"],
                    "independent_verifier": True,
                }
            ],
            "authority": {"external_mutations": []},
            "budgets": {
                "timeout_seconds": 60,
                "max_output_bytes": 262144,
                "max_artifact_bytes": 1048576,
                "max_turns": 1,
            },
            "stop_conditions": [],
        }
        package_path = root / "package.json"
        package_path.write_bytes(module.canonical(package))
        schema = module.output_schema()
        assert schema["properties"]["schema"]["type"] == "string"
        assert (
            schema["properties"]["acceptance_criteria"]["items"]["properties"]
            ["independent_verifier"]["type"]
            == "boolean"
        )
        plan = {
            "schema": "alpha0.worker-plan.v1",
            "plan_id": "plan-wp-plan-test-001",
            "planner_actor_id": "codex-plan",
            "project": package["project"],
            "repository": package["repository"],
            "goal": package["goal"],
            "scope": {"included": ["Inspect one failure path"], "excluded": ["Any mutation"]},
            "dependencies": [],
            "deliverables": [
                {"id": "repair", "description": "A reviewable repair", "evidence_ids": ["diff"]}
            ],
            "acceptance_criteria": [
                {
                    "id": "behavior",
                    "outcome": "The failing path produces the expected bounded result.",
                    "evidence_ids": ["runtime-proof"],
                    "independent_verifier": True,
                }
            ],
            "required_artifacts": [
                {"id": "diff", "kind": "diff", "description": "Exact diff", "producer": "worker"},
                {
                    "id": "runtime-proof",
                    "kind": "verification",
                    "description": "Independent runtime proof",
                    "producer": "node_verifier",
                },
            ],
            "external_mutations": [],
            "risks": ["The observed symptom may have more than one cause."],
            "rollback": ["Revert the exact implementation commit."],
            "budgets": {"max_attempts": 2, "timeout_seconds": 1800},
            "unknowns": ["The root cause remains to be independently verified."],
            "evidence_refs": package["context_refs"],
        }

        real_run = module.subprocess.run
        git_attempts = 0

        def fake_run(argv, **kwargs):
            nonlocal git_attempts
            if argv[0] == module.GIT:
                git_attempts += 1
                if git_attempts == 1:
                    raise BlockingIOError(11, "resource temporarily unavailable")
                return real_run(argv, **kwargs)
            if argv[1:] == ["login", "--with-api-key"]:
                assert kwargs["input"] == b"test-only-not-real"
                assert kwargs["env"]["HOME"] == kwargs["env"]["CODEX_HOME"]
                return type("Completed", (), {"returncode": 0})()
            assert "--sandbox" in argv and argv[argv.index("--sandbox") + 1] == "read-only"
            assert "--ephemeral" not in argv and "--ignore-user-config" not in argv
            assert kwargs["env"]["HOME"] == kwargs["env"]["CODEX_HOME"]
            assert kwargs["env"]["HOME"] != os.environ.get("HOME")
            assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
            prompt = json.loads(kwargs["input"])
            assert prompt["repository"]["base_sha"] == head
            output = Path(argv[argv.index("--output-last-message") + 1])
            output.write_bytes(module.canonical(plan))
            return type("Completed", (), {"returncode": 0})()

        with (
            mock.patch.object(module.subprocess, "run", side_effect=fake_run),
            mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-only-not-real"}, clear=True),
        ):
            assert (
                module.main(
                    [
                        "--package",
                        str(package_path),
                        "--repository",
                        str(repository),
                        "--artifacts",
                        str(artifacts),
                    ]
                )
                == 0
            )
        assert json.loads((artifacts / "worker-plan.json").read_text()) == plan


if __name__ == "__main__":
    main()
