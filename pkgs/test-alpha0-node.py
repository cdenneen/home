from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import subprocess
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path


def run(*args: str, input_value: dict[str, object] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        input=None if input_value is None else json.dumps(input_value),
        text=True,
    )


def request(runner: str, config: Path, value: dict[str, object]) -> dict[str, object]:
    result = run(runner, "--config", str(config), input_value=value)
    response = json.loads(result.stdout)
    response["_exit_code"] = result.returncode
    return response


def main() -> None:
    runner, inspector, git = sys.argv[1:]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source"
        cache = root / "cache.git"
        state = root / "state"
        run(git, "-c", "init.defaultBranch=main", "init", str(source))
        run(git, "-C", str(source), "config", "user.name", "Alpha0 Test")
        run(git, "-C", str(source), "config", "user.email", "alpha0@example.invalid")
        (source / "README.md").write_text("governance\n", encoding="utf-8")
        run(git, "-C", str(source), "add", "README.md")
        assert run(git, "-C", str(source), "commit", "-m", "initial").returncode == 0
        assert run(git, "clone", "--bare", str(source), str(cache)).returncode == 0
        base_sha = run(git, "--git-dir", str(cache), "rev-parse", "HEAD").stdout.strip()
        config = root / "config.json"
        config.write_text(
            json.dumps(
                {
                    "node_id": "nyx",
                    "state_dir": str(state),
                    "max_concurrent": 2,
                    "capabilities": ["git.ap.org"],
                    "repositories": {"eks-platform-governance": str(cache)},
                    "workers": {"inspect": [inspector]},
                    "aws_profiles": [],
                }
            ),
            encoding="utf-8",
        )
        package = {
            "schema": "alpha0.work-package.v1",
            "package_id": "wp-nix-e2e-001",
            "execution": {
                "id": "exec-001",
                "lease_id": "lease-001",
                "attempt_no": 1,
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                "idempotency_key": "wp-nix-e2e-001-attempt-1",
            },
            "route": {"node_id": "nyx", "required_capabilities": ["git.ap.org"]},
            "project": {"id": "work-ops", "source_ref": "gitlab://example/1"},
            "repository": {
                "id": "eks-platform-governance",
                "base_ref": "main",
                "base_sha": base_sha,
            },
            "worker": {"adapter": "inspect"},
            "goal": "Inspect the exact governance base without mutation.",
            "context_refs": [],
            "deliverables": [
                {
                    "id": "governance-report",
                    "kind": "report",
                    "description": "Bounded source observation.",
                    "required_evidence": ["inspect-report"],
                }
            ],
            "acceptance_criteria": [
                {
                    "id": "exact-base",
                    "description": "The detached checkout equals the authorized base.",
                    "required_evidence": ["verify-base"],
                    "independent_verifier": True,
                }
            ],
            "authority": {"external_mutations": []},
            "budgets": {
                "timeout_seconds": 60,
                "max_output_bytes": 262_144,
                "max_artifact_bytes": 1_048_576,
                "max_turns": 1,
            },
            "stop_conditions": ["Any mutation would be required."],
        }
        execute = {"schema": "alpha0.node-request.v1", "operation": "execute", "package": package}
        first = request(runner, config, execute)
        assert first["status"] == "completed" and first["result"]["reported_status"] == "success"
        replay = request(runner, config, execute)
        assert replay["status"] == "replayed" and replay["result_digest"] == first["result_digest"]
        fetch = request(
            runner,
            config,
            {
                "schema": "alpha0.node-request.v1",
                "operation": "fetch",
                "package_id": package["package_id"],
                "package_digest": first["package_digest"],
                "artifact_id": "verify-base",
            },
        )
        assert fetch["status"] == "completed"
        content = base64.b64decode(fetch["content_base64"])
        assert fetch["artifact"]["digest"] == f"sha256:{hashlib.sha256(content).hexdigest()}"
        acknowledgement = {
            "schema": "alpha0.node-request.v1",
            "operation": "ack",
            "package_id": package["package_id"],
            "package_digest": first["package_digest"],
            "result_digest": first["result_digest"],
        }
        assert request(runner, config, acknowledgement)["status"] == "acknowledged"
        assert request(runner, config, acknowledgement)["status"] == "already_acknowledged"
        assert not (state / "packages" / str(package["package_id"]) / "worktree").exists()

        conflict = deepcopy(execute)
        conflict["package"]["goal"] = "A conflicting package body."
        assert request(runner, config, conflict)["error"] == "idempotency_conflict"

        denied = deepcopy(execute)
        denied["package"]["package_id"] = "wp-nix-denied-001"
        denied["package"]["authority"]["external_mutations"] = [
            {"capability": "aws.write", "target_ref": "aws://denied"}
        ]
        assert request(runner, config, denied)["error"] == "mutation_denied"

        capability_denied = deepcopy(execute)
        capability_denied["package"]["package_id"] = "wp-nix-capability-denied-001"
        capability_denied["package"]["route"]["required_capabilities"] = ["aws.direct-connect"]
        assert request(runner, config, capability_denied)["error"] == "capability_denied"

        slot_handles = []
        for index in range(2):
            path = state / "slots" / f"slot-{index}.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a+")
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            slot_handles.append(handle)
        busy = deepcopy(execute)
        busy["package"]["package_id"] = "wp-nix-busy-001"
        busy["package"]["execution"]["id"] = "exec-busy-001"
        assert request(runner, config, busy)["error"] == "node_busy"
        for handle in slot_handles:
            handle.close()


if __name__ == "__main__":
    main()
