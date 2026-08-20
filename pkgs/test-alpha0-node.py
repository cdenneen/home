from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path


def run(
    *args: str, input_value: dict[str, object] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        input=None if input_value is None else json.dumps(input_value),
        text=True,
    )


def request(runner: str, config: Path, value: dict[str, object]) -> dict[str, object]:
    result = run(runner, "--config", str(config), input_value=value)
    if not result.stdout:
        raise AssertionError(result.stderr or "alpha0-node returned no response")
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
                    "worker_context_refs": {
                        "inspect": [
                            "repo://alpha0/skills/agent-tool-usage/SKILL.md#sha256:"
                            + "a" * 64,
                            "repo://alpha0/skills/project-sdlc/SKILL.md#sha256:"
                            + "b" * 64,
                        ]
                    },
                    "worker_secret_files": {"inspect": {}},
                    "aws_cli": "/run/current-system/sw/bin/aws",
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
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
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
            "context_refs": [
                "repo://alpha0/skills/agent-tool-usage/SKILL.md#sha256:" + "a" * 64,
                "repo://alpha0/skills/project-sdlc/SKILL.md#sha256:" + "b" * 64,
            ],
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
        execute = {
            "schema": "alpha0.node-request.v1",
            "operation": "execute",
            "package": package,
        }
        first = request(runner, config, execute)
        assert (
            first["status"] == "completed"
            and first["result"]["reported_status"] == "success"
        )
        assert {row["kind"] for row in first["result"]["artifacts"]}.issuperset(
            {"learning_candidates", "memory_closure"}
        )
        replay = request(runner, config, execute)
        assert (
            replay["status"] == "replayed"
            and replay["result_digest"] == first["result_digest"]
        )
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
        assert (
            fetch["artifact"]["digest"]
            == f"sha256:{hashlib.sha256(content).hexdigest()}"
        )
        memory_fetch = request(
            runner,
            config,
            {
                "schema": "alpha0.node-request.v1",
                "operation": "fetch",
                "package_id": package["package_id"],
                "package_digest": first["package_digest"],
                "artifact_id": "memory-closure",
            },
        )
        memory = json.loads(base64.b64decode(memory_fetch["content_base64"]))
        assert memory["source"]["ref"] == f"alpha0-node://nyx/{package['package_id']}"
        assert memory["records"][0]["details"]["repository"]["base_sha"] == base_sha
        acknowledgement = {
            "schema": "alpha0.node-request.v1",
            "operation": "ack",
            "package_id": package["package_id"],
            "package_digest": first["package_digest"],
            "result_digest": first["result_digest"],
        }
        assert request(runner, config, acknowledgement)["status"] == "acknowledged"
        assert (
            request(runner, config, acknowledgement)["status"] == "already_acknowledged"
        )
        assert not (
            state / "packages" / str(package["package_id"]) / "worktree"
        ).exists()

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
        capability_denied["package"]["route"]["required_capabilities"] = [
            "aws.direct-connect"
        ]
        assert (
            request(runner, config, capability_denied)["error"] == "capability_denied"
        )

        context_denied = deepcopy(execute)
        context_denied["package"]["package_id"] = "wp-nix-context-denied-001"
        context_denied["package"]["context_refs"] = []
        assert request(runner, config, context_denied)["error"] == "context_denied"

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

        auth_worker = root / "auth-worker.py"
        auth_worker.write_text(
            f"""#!{sys.executable}
import argparse
import json
import os
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--package", type=Path, required=True)
parser.add_argument("--repository", type=Path, required=True)
parser.add_argument("--artifacts", type=Path, required=True)
args = parser.parse_args()
package = json.loads(args.package.read_text())
if os.environ.get("ALPHA0_AUTH_RESUMED") != "1":
    value = {{
        "profile": "alpha0-apss-read",
        "reason": "qualified_sso_expiry",
        "schema": "alpha0.aws-sso-request.v1",
    }}
    (args.artifacts / "aws-sso-request.json").write_text(
        json.dumps(value, separators=(",", ":"), sort_keys=True)
    )
    raise SystemExit(75)
head = subprocess.run(
    [{git!r}, "-C", str(args.repository), "rev-parse", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
print(json.dumps({{
    "clean": True,
    "head_sha": head,
    "schema": "alpha0.node-worker-observation.v1",
    "summary": "same worktree resumed after exact-profile authentication",
}}, separators=(",", ":"), sort_keys=True))
""",
            encoding="utf-8",
        )
        auth_worker.chmod(0o755)
        fake_aws = root / "aws"
        fake_aws.write_text(
            f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path

home = Path(os.environ["HOME"])
authenticated = home / "fake-authenticated"
login_count = home / "fake-login-count"
if any(name in os.environ for name in (
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "OPENAI_API_KEY"
)):
    raise SystemExit(99)
if sys.argv[1:3] == ["sso", "login"]:
    count = int(login_count.read_text()) if login_count.exists() else 0
    login_count.write_text(str(count + 1))
    print("https://device.sso.us-east-1.amazonaws.com/start", flush=True)
    print("ABCD-EFGH", flush=True)
    authenticated.write_text("yes")
    raise SystemExit(0)
if sys.argv[1:3] == ["sts", "get-caller-identity"]:
    if (home / "force-provider-error").exists():
        print("AccessDenied: role policy denied the request", file=sys.stderr)
        raise SystemExit(1)
    if not authenticated.exists():
        print("The SSO session has expired; run aws sso login", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({{
        "Account": "123456789012",
        "Arn": "arn:aws:sts::123456789012:assumed-role/alpha0/read",
        "UserId": "AROATEST:alpha0",
    }}))
    raise SystemExit(0)
raise SystemExit(2)
""",
            encoding="utf-8",
        )
        fake_aws.chmod(0o755)
        auth_config_value = json.loads(config.read_text(encoding="utf-8"))
        auth_config_value["capabilities"].append("aws.sso-device-login")
        auth_config_value["workers"]["auth-test"] = [str(auth_worker)]
        auth_config_value["worker_context_refs"]["auth-test"] = list(
            auth_config_value["worker_context_refs"]["inspect"]
        )
        auth_config_value["worker_secret_files"]["auth-test"] = {}
        auth_config_value["aws_cli"] = str(fake_aws)
        auth_config_value["aws_profiles"] = ["alpha0-apss-read"]
        auth_config = root / "auth-config.json"
        auth_config.write_text(json.dumps(auth_config_value), encoding="utf-8")

        auth_package = deepcopy(package)
        auth_package["package_id"] = "wp-nix-auth-001"
        auth_package["execution"]["id"] = "exec-auth-001"
        auth_package["execution"]["lease_id"] = "lease-auth-001"
        auth_package["execution"]["idempotency_key"] = "wp-nix-auth-001-attempt-1"
        auth_package["execution"]["expires_at"] = (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat()
        auth_package["route"]["required_capabilities"] = [
            "git.ap.org",
            "aws.sso-device-login",
        ]
        auth_package["worker"]["adapter"] = "auth-test"
        auth_execute = {
            "schema": "alpha0.node-request.v1",
            "operation": "execute",
            "package": auth_package,
        }
        waiting = request(runner, auth_config, auth_execute)
        assert waiting["status"] == "waiting_for_auth"
        assert waiting["auth_event"]["status"] == "authorization_required"
        assert "user_code=ABCD-EFGH" in waiting["auth_event"]["verification_url"]
        challenge_id = waiting["challenge_id"]
        package_digest = waiting["package_digest"]
        durable_content = b"\n".join(
            path.read_bytes()
            for path in state.rglob("*")
            if path.is_file() and not path.name.endswith(".lock")
        )
        assert b"ABCD-EFGH" not in durable_content
        assert b"device.sso" not in durable_content
        replay_waiting = request(runner, auth_config, auth_execute)
        assert replay_waiting["challenge_id"] == challenge_id
        assert "auth_event" not in replay_waiting

        joined_package = deepcopy(auth_package)
        joined_package["package_id"] = "wp-nix-auth-002"
        joined_package["execution"]["id"] = "exec-auth-002"
        joined_package["execution"]["lease_id"] = "lease-auth-002"
        joined_package["execution"]["idempotency_key"] = "wp-nix-auth-002-attempt-1"
        joined = request(
            runner,
            auth_config,
            {
                "schema": "alpha0.node-request.v1",
                "operation": "execute",
                "package": joined_package,
            },
        )
        assert joined["challenge_id"] == challenge_id
        assert joined["status"] in {"waiting_for_auth", "authentication_ready"}
        assert "auth_event" not in joined

        auth_bound = {
            "schema": "alpha0.node-request.v1",
            "package_id": auth_package["package_id"],
            "package_digest": package_digest,
            "challenge_id": challenge_id,
        }
        premature_ack = request(
            runner,
            auth_config,
            {
                "schema": "alpha0.node-request.v1",
                "operation": "ack",
                "package_id": auth_package["package_id"],
                "package_digest": package_digest,
                "result_digest": "sha256:" + "0" * 64,
            },
        )
        assert premature_ack["error"] == "incomplete_spool"
        wrong_challenge = request(
            runner,
            auth_config,
            {**auth_bound, "operation": "resume", "challenge_id": "auth_wrong"},
        )
        assert wrong_challenge["error"] == "idempotency_conflict"

        for _ in range(50):
            status = request(
                runner, auth_config, {**auth_bound, "operation": "auth-status"}
            )
            if status["status"] == "authentication_ready":
                break
            time.sleep(0.02)
        assert status["status"] == "authentication_ready"
        verified = request(
            runner, auth_config, {**auth_bound, "operation": "auth-verify"}
        )
        assert verified["status"] == "authentication_verified"
        assert verified["auth_event"] == {
            "schema": "alpha0.aws-sso-device-event.v1",
            "profile": "alpha0-apss-read",
            "sensitive": False,
            "status": "authenticated",
        }
        assert verified["preflight"]["profile"] == "alpha0-apss-read"
        resumed = request(runner, auth_config, {**auth_bound, "operation": "resume"})
        assert resumed["status"] == "completed"
        assert resumed["result"]["reported_status"] == "success"
        resumed_replay = request(
            runner, auth_config, {**auth_bound, "operation": "resume"}
        )
        assert resumed_replay["status"] == "replayed"
        assert resumed_replay["result_digest"] == resumed["result_digest"]
        joined_bound = {
            "schema": "alpha0.node-request.v1",
            "operation": "resume",
            "package_id": joined_package["package_id"],
            "package_digest": joined["package_digest"],
            "challenge_id": challenge_id,
        }
        joined_resumed = request(runner, auth_config, joined_bound)
        assert joined_resumed["status"] == "completed"
        assert joined_resumed["result"]["reported_status"] == "success"
        assert (state / "fake-login-count").read_text() == "1"
        session_path = state / "auth" / "alpha0-apss-read.json"
        expired_session = json.loads(session_path.read_text())
        expired_session["expires_at"] = "2000-01-01T00:00:00+00:00"
        session_path.write_text(
            json.dumps(expired_session, separators=(",", ":"), sort_keys=True)
        )
        expired_status = request(
            runner, auth_config, {**auth_bound, "operation": "auth-status"}
        )
        assert expired_status["status"] == "needs_input"

        bad_state = root / "bad-state"
        bad_config_value = deepcopy(auth_config_value)
        bad_config_value["state_dir"] = str(bad_state)
        bad_config_value["repositories"] = {"eks-platform-governance": str(cache)}
        bad_config = root / "bad-config.json"
        bad_config.write_text(json.dumps(bad_config_value), encoding="utf-8")
        bad_state.mkdir()
        (bad_state / "force-provider-error").write_text("yes")
        denied_package = deepcopy(auth_package)
        denied_package["package_id"] = "wp-nix-auth-denied-001"
        denied_package["execution"]["id"] = "exec-auth-denied-001"
        denied_package["execution"]["lease_id"] = "lease-auth-denied-001"
        denied_package["execution"]["idempotency_key"] = "auth-denied-attempt-1"
        denied = request(
            runner,
            bad_config,
            {
                "schema": "alpha0.node-request.v1",
                "operation": "execute",
                "package": denied_package,
            },
        )
        assert denied["error"] == "auth_not_qualified"
        assert not (bad_state / "fake-login-count").exists()


if __name__ == "__main__":
    main()
