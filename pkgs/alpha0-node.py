#!@python@
"""Forced-command transport and spool for bounded Alpha0 work packages."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GIT = "@git@"
REQUEST_SCHEMA = "alpha0.node-request.v1"
RESPONSE_SCHEMA = "alpha0.node-response.v1"
PACKAGE_SCHEMA = "alpha0.work-package.v1"
RESULT_SCHEMA = "alpha0.work-result.v1"
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
GIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class NodeError(Exception):
    def __init__(self, code: str, message: str, exit_code: int = 1):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise NodeError("invalid_json", "value is not canonical JSON") from exc


def digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical(value)).hexdigest()}"


def exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise NodeError("invalid_request", f"{label} has unsupported or missing fields")


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NodeError("invalid_request", f"{label} must be an object")
    return value


def sequence(value: Any, label: str, maximum: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum:
        raise NodeError("invalid_request", f"{label} must be a bounded list")
    return value


def identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise NodeError("invalid_request", f"{label} is not a safe identifier")
    return value


def bounded_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise NodeError("invalid_request", f"{label} is outside its safe range")
    return value


def safe_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise NodeError("invalid_request", f"{label} is not a SHA-256 digest")
    return value


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def open_lock(path: Path, *, blocking: bool) -> Any:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    stream = os.fdopen(descriptor, "r+")
    operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        fcntl.flock(stream, operation)
    except BlockingIOError:
        stream.close()
        raise
    return stream


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = mapping(json.loads(path.read_text(encoding="utf-8")), "config")
    except (OSError, json.JSONDecodeError) as exc:
        raise NodeError("invalid_config", "node config is unreadable or invalid") from exc
    exact_keys(
        config,
        {
            "node_id",
            "state_dir",
            "max_concurrent",
            "capabilities",
            "repositories",
            "workers",
            "aws_profiles",
        },
        "config",
    )
    identifier(config["node_id"], "config.node_id")
    state = Path(config["state_dir"])
    if not state.is_absolute():
        raise NodeError("invalid_config", "config.state_dir must be absolute")
    if config["max_concurrent"] != 2:
        raise NodeError("invalid_config", "config.max_concurrent must be exactly two")
    capabilities = sequence(config["capabilities"], "config.capabilities", 32)
    if not capabilities or len(capabilities) != len(set(capabilities)):
        raise NodeError("invalid_config", "node capabilities must be non-empty and unique")
    for capability in capabilities:
        identifier(capability, "node capability")
    repositories = mapping(config["repositories"], "config.repositories")
    for name, raw_path in repositories.items():
        identifier(name, "repository map key")
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise NodeError("invalid_config", "repository map paths must be absolute")
    workers = mapping(config["workers"], "config.workers")
    for name, argv in workers.items():
        identifier(name, "worker map key")
        values = sequence(argv, "worker argv", 16)
        if not values or any(not isinstance(item, str) or not item or not Path(item).is_absolute() for item in values):
            raise NodeError("invalid_config", "worker argv must contain only fixed absolute paths")
    profiles = sequence(config["aws_profiles"], "config.aws_profiles", 32)
    if len(profiles) != len(set(profiles)):
        raise NodeError("invalid_config", "AWS profile names must be unique")
    for profile in profiles:
        identifier(profile, "AWS profile name")
    return config


def validate_package(package: dict[str, Any], config: dict[str, Any]) -> tuple[str, str, str]:
    exact_keys(
        package,
        {
            "schema", "package_id", "execution", "route", "project", "repository",
            "worker", "goal", "context_refs", "deliverables", "acceptance_criteria",
            "authority", "budgets", "stop_conditions",
        },
        "work package",
    )
    if package["schema"] != PACKAGE_SCHEMA:
        raise NodeError("invalid_request", "unsupported work-package schema")
    package_id = identifier(package["package_id"], "package_id")
    execution = mapping(package["execution"], "execution")
    exact_keys(execution, {"id", "lease_id", "attempt_no", "expires_at", "idempotency_key"}, "execution")
    identifier(execution["id"], "execution.id")
    identifier(execution["lease_id"], "execution.lease_id")
    bounded_int(execution["attempt_no"], "execution.attempt_no", 1, 5)
    if not isinstance(execution["idempotency_key"], str) or not execution["idempotency_key"]:
        raise NodeError("invalid_request", "execution.idempotency_key is required")
    try:
        expires = datetime.fromisoformat(execution["expires_at"])
    except (TypeError, ValueError) as exc:
        raise NodeError("invalid_request", "execution.expires_at must be ISO-8601") from exc
    if expires.tzinfo is None or expires.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise NodeError("expired", "work package lease has expired")

    route = mapping(package["route"], "route")
    exact_keys(route, {"node_id", "required_capabilities"}, "route")
    if route["node_id"] != config["node_id"]:
        raise NodeError("wrong_node", "work package is bound to another node")
    capabilities = sequence(route["required_capabilities"], "route.required_capabilities", 16)
    if not capabilities:
        raise NodeError("invalid_request", "at least one node capability is required")
    for capability in capabilities:
        identifier(capability, "route capability")
    if not set(capabilities).issubset(config["capabilities"]):
        raise NodeError("capability_denied", "work package requests an unavailable node capability")

    repository = mapping(package["repository"], "repository")
    exact_keys(repository, {"id", "base_ref", "base_sha"}, "repository")
    repository_id = identifier(repository["id"], "repository.id")
    if repository_id not in config["repositories"]:
        raise NodeError("repository_denied", "repository is not in the exact node map")
    if not isinstance(repository["base_ref"], str) or not repository["base_ref"] or repository["base_ref"].startswith("-"):
        raise NodeError("invalid_request", "repository.base_ref is unsafe")
    base_sha = repository["base_sha"]
    if not isinstance(base_sha, str) or not GIT_SHA.fullmatch(base_sha):
        raise NodeError("invalid_request", "repository.base_sha is not an exact Git object")

    worker = mapping(package["worker"], "worker")
    exact_keys(worker, {"adapter"}, "worker")
    adapter = identifier(worker["adapter"], "worker.adapter")
    if adapter not in config["workers"]:
        raise NodeError("worker_denied", "worker adapter is not in the exact node map")

    authority = mapping(package["authority"], "authority")
    exact_keys(authority, {"external_mutations"}, "authority")
    if sequence(authority["external_mutations"], "authority.external_mutations", 16):
        raise NodeError("mutation_denied", "this node boundary is read-only")

    budgets = mapping(package["budgets"], "budgets")
    exact_keys(budgets, {"timeout_seconds", "max_output_bytes", "max_artifact_bytes", "max_turns"}, "budgets")
    bounded_int(budgets["timeout_seconds"], "budgets.timeout_seconds", 60, 14_400)
    bounded_int(budgets["max_output_bytes"], "budgets.max_output_bytes", 1_024, MAX_RESPONSE_BYTES)
    bounded_int(budgets["max_artifact_bytes"], "budgets.max_artifact_bytes", 1_024, 256 * 1024 * 1024)
    bounded_int(budgets["max_turns"], "budgets.max_turns", 1, 30)

    deliverables = sequence(package["deliverables"], "deliverables", 20)
    criteria = sequence(package["acceptance_criteria"], "acceptance_criteria", 50)
    if not deliverables or not criteria:
        raise NodeError("invalid_request", "deliverables and acceptance criteria are required")
    evidence_ids: set[str] = set()
    row_ids: set[str] = set()
    for label, rows in (("deliverable", deliverables), ("criterion", criteria)):
        for raw in rows:
            row = mapping(raw, label)
            expected = (
                {"id", "kind", "description", "required_evidence"}
                if label == "deliverable"
                else {"id", "description", "required_evidence", "independent_verifier"}
            )
            exact_keys(row, expected, label)
            row_id = identifier(row["id"], f"{label}.id")
            if row_id in row_ids:
                raise NodeError("invalid_request", "deliverable and criterion IDs must be unique")
            row_ids.add(row_id)
            required = sequence(row["required_evidence"], f"{label}.required_evidence", 16)
            if not required:
                raise NodeError("invalid_request", f"{label} requires evidence")
            for evidence_id in required:
                evidence_ids.add(identifier(evidence_id, "evidence ID"))
            if label == "deliverable" and row["kind"] not in {"artifact", "report"}:
                raise NodeError("worker_denied", "inspect cannot produce Git changes")
            if label == "criterion" and row["independent_verifier"] is not True:
                raise NodeError("invalid_request", "criteria require an independent verifier")
    if len(evidence_ids) > 128:
        raise NodeError("invalid_request", "work package declares too many evidence artifacts")
    if len(canonical(package)) > MAX_REQUEST_BYTES:
        raise NodeError("request_too_large", "work package exceeds 256 KiB")
    return package_id, repository_id, adapter


def run_git(*args: str, timeout: int = 120) -> str:
    result = subprocess.run(
        [GIT, "-c", "core.hooksPath=/dev/null", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode:
        raise NodeError("git_failed", result.stderr.strip()[:1000] or "Git command failed")
    return result.stdout.strip()


def prepare_worktree(source: Path, target: Path, base_sha: str) -> None:
    if run_git("--git-dir", str(source), "rev-parse", "--is-bare-repository") != "true":
        raise NodeError("repository_unavailable", "repository projection is not a bare cache")
    run_git("--git-dir", str(source), "cat-file", "-e", f"{base_sha}^{{commit}}")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    run_git("clone", "--shared", "--no-checkout", "--", str(source), str(target), timeout=300)
    run_git("-C", str(target), "checkout", "--detach", base_sha, timeout=300)
    observed = run_git("-C", str(target), "rev-parse", "HEAD")
    if observed != base_sha:
        raise NodeError("repository_mismatch", "detached worktree did not resolve to the exact base")


def worker_limits(max_output: int) -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (max_output, max_output))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


def run_worker(config: dict[str, Any], package: dict[str, Any], spool: Path, worktree: Path) -> tuple[int, dict[str, Any]]:
    budgets = package["budgets"]
    stdout_path = spool / "worker.stdout"
    stderr_path = spool / "worker.stderr"
    stdout_path.touch(mode=0o600)
    stderr_path.touch(mode=0o600)
    artifacts = spool / "artifacts"
    artifacts.mkdir(mode=0o700)
    runtime_home = spool / "home"
    runtime_tmp = spool / "tmp"
    runtime_home.mkdir(mode=0o700)
    runtime_tmp.mkdir(mode=0o700)
    argv = [
        *config["workers"][package["worker"]["adapter"]],
        "--package", str(spool / "package.json"),
        "--repository", str(worktree),
        "--artifacts", str(artifacts),
    ]
    environment = {
        "HOME": str(runtime_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/run/current-system/sw/bin",
        "TMPDIR": str(runtime_tmp),
        "ALPHA0_NODE_ID": config["node_id"],
    }
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            completed = subprocess.run(
                argv,
                check=False,
                env=environment,
                preexec_fn=lambda: worker_limits(budgets["max_output_bytes"]),
                stderr=stderr,
                stdout=stdout,
                timeout=budgets["timeout_seconds"],
            )
            exit_code = completed.returncode if 0 <= completed.returncode <= 255 else 255
        except subprocess.TimeoutExpired:
            exit_code = 124
    try:
        observation = mapping(json.loads(stdout_path.read_text(encoding="utf-8")), "worker observation")
        exact_keys(observation, {"schema", "head_sha", "clean", "summary"}, "worker observation")
        if observation["schema"] != "alpha0.node-worker-observation.v1":
            raise NodeError("worker_failed", "worker emitted an unsupported observation")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, NodeError) as exc:
        if exit_code == 0:
            raise NodeError("worker_failed", "worker did not emit a valid bounded observation") from exc
        observation = {
            "schema": "alpha0.node-worker-observation.v1",
            "head_sha": package["repository"]["base_sha"],
            "clean": False,
            "summary": "worker failed before producing a valid observation",
        }
    return exit_code, observation


def build_result(package: dict[str, Any], package_digest: str, spool: Path, exit_code: int, observation: dict[str, Any], node_id: str) -> dict[str, Any]:
    expected_head = package["repository"]["base_sha"]
    observed_head = observation.get("head_sha")
    verified = exit_code == 0 and observation.get("clean") is True and observed_head == expected_head
    criterion_evidence = {
        evidence_id
        for row in package["acceptance_criteria"]
        for evidence_id in row["required_evidence"]
    }
    evidence_ids = sorted(
        {
            evidence_id
            for rows in (package["deliverables"], package["acceptance_criteria"])
            for row in rows
            for evidence_id in row["required_evidence"]
        }
    )
    artifact_rows: list[dict[str, Any]] = []
    worker_evidence: list[str] = []
    for evidence_id in evidence_ids:
        independent = evidence_id in criterion_evidence
        content = canonical(
            {
                "schema": "alpha0.node-evidence.v1",
                "evidence_id": evidence_id,
                "package_id": package["package_id"],
                "package_digest": package_digest,
                "repository_id": package["repository"]["id"],
                "expected_head_sha": expected_head,
                "observed_head_sha": observed_head,
                "worktree_clean": observation.get("clean") is True,
                "worker_exit_code": exit_code,
                "verified": verified,
            }
        )
        artifact_path = spool / "artifacts" / f"{evidence_id}.json"
        atomic_write(artifact_path, content)
        producer = "node_verifier" if independent else "worker"
        if producer == "worker":
            worker_evidence.append(evidence_id)
        artifact_rows.append(
            {
                "id": evidence_id,
                "kind": "verification" if independent else "report",
                "ref": f"alpha0-node://{node_id}/{package['package_id']}/artifacts/{evidence_id}",
                "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
                "size_bytes": len(content),
                "producer": producer,
            }
        )
    return {
        "schema": RESULT_SCHEMA,
        "package_id": package["package_id"],
        "package_digest": package_digest,
        "execution_id": package["execution"]["id"],
        "node_id": node_id,
        "reported_status": "success" if verified else "failed",
        "worker": {
            "adapter": package["worker"]["adapter"],
            "exit_code": exit_code,
            "evidence_ids": worker_evidence,
        },
        "repository": {"base_sha": expected_head, "head_sha": observed_head or expected_head},
        "artifacts": artifact_rows,
        "deliverables": [
            {
                "id": row["id"],
                "status": "produced" if verified else "failed",
                "evidence_ids": row["required_evidence"],
                "summary": observation.get("summary", "bounded node observation"),
            }
            for row in package["deliverables"]
        ],
        "criteria": [
            {
                "id": row["id"],
                "status": "passed" if verified else "failed",
                "evidence_ids": row["required_evidence"],
                "summary": "node verifier observed the exact clean detached base" if verified else "node verification failed",
            }
            for row in package["acceptance_criteria"]
        ],
        "external_mutations": [],
    }


def acquire_slot(state: Path, maximum: int) -> Any:
    for index in range(maximum):
        try:
            return open_lock(state / "slots" / f"slot-{index}.lock", blocking=False)
        except BlockingIOError:
            continue
    raise NodeError("node_busy", "both Nyx execution slots are occupied", 75)


def stored_result(spool: Path) -> dict[str, Any]:
    try:
        return mapping(json.loads((spool / "result.json").read_text(encoding="utf-8")), "stored result")
    except (OSError, json.JSONDecodeError) as exc:
        raise NodeError("incomplete_spool", "package was admitted but has no replayable result") from exc


def execute(request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    exact_keys(request, {"schema", "operation", "package"}, "execute request")
    package = mapping(request["package"], "package")
    package_id, repository_id, _ = validate_package(package, config)
    package_digest = digest(package)
    state = Path(config["state_dir"])
    package_lock = open_lock(state / "locks" / f"{package_id}.lock", blocking=True)
    try:
        spool = state / "packages" / package_id
        if spool.exists():
            try:
                known_digest = (spool / "package.digest").read_text(encoding="ascii").strip()
            except OSError as exc:
                raise NodeError("incomplete_spool", "existing package spool is incomplete") from exc
            if known_digest != package_digest:
                raise NodeError("idempotency_conflict", "package ID is already bound to another digest")
            result = stored_result(spool)
            return {
                "schema": RESPONSE_SCHEMA,
                "operation": "execute",
                "status": "replayed",
                "package_id": package_id,
                "package_digest": package_digest,
                "result_digest": digest(result),
                "result": result,
            }
        slot = acquire_slot(state, config["max_concurrent"])
        try:
            spool.mkdir(mode=0o700, parents=True)
            atomic_write(spool / "package.json", canonical(package))
            atomic_write(spool / "package.digest", f"{package_digest}\n".encode())
            worktree = spool / "worktree" / "repository"
            prepare_worktree(Path(config["repositories"][repository_id]), worktree, package["repository"]["base_sha"])
            exit_code, observation = run_worker(config, package, spool, worktree)
            result = build_result(package, package_digest, spool, exit_code, observation, config["node_id"])
            atomic_write(spool / "result.json", canonical(result))
        finally:
            slot.close()
        return {
            "schema": RESPONSE_SCHEMA,
            "operation": "execute",
            "status": "completed",
            "package_id": package_id,
            "package_digest": package_digest,
            "result_digest": digest(result),
            "result": result,
        }
    finally:
        package_lock.close()


def load_bound_spool(config: dict[str, Any], package_id: str, package_digest: str) -> tuple[Path, dict[str, Any]]:
    state = Path(config["state_dir"])
    spool = state / "packages" / identifier(package_id, "package_id")
    try:
        known_digest = (spool / "package.digest").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise NodeError("not_found", "package spool does not exist") from exc
    if known_digest != safe_digest(package_digest, "package_digest"):
        raise NodeError("idempotency_conflict", "package digest does not match the spool")
    return spool, stored_result(spool)


def fetch(request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    exact_keys(request, {"schema", "operation", "package_id", "package_digest", "artifact_id"}, "fetch request")
    artifact_id = identifier(request["artifact_id"], "artifact_id")
    spool, result = load_bound_spool(config, request["package_id"], request["package_digest"])
    metadata = next((row for row in result["artifacts"] if row["id"] == artifact_id), None)
    if metadata is None:
        raise NodeError("not_found", "artifact is not declared by the stored result")
    path = spool / "artifacts" / f"{artifact_id}.json"
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise NodeError("not_found", "artifact content is unavailable") from exc
    observed_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if len(content) != metadata["size_bytes"] or observed_digest != metadata["digest"]:
        raise NodeError("artifact_mismatch", "artifact content does not match its manifest")
    if len(content) > 3 * 1024 * 1024:
        raise NodeError("artifact_too_large", "artifact exceeds the bounded SSH response")
    return {
        "schema": RESPONSE_SCHEMA,
        "operation": "fetch",
        "status": "completed",
        "package_id": request["package_id"],
        "package_digest": request["package_digest"],
        "artifact": metadata,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def ack(request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    exact_keys(request, {"schema", "operation", "package_id", "package_digest", "result_digest"}, "ack request")
    result_digest = safe_digest(request["result_digest"], "result_digest")
    state = Path(config["state_dir"])
    package_id = identifier(request["package_id"], "package_id")
    package_lock = open_lock(state / "locks" / f"{package_id}.lock", blocking=True)
    try:
        spool, result = load_bound_spool(config, package_id, request["package_digest"])
        if digest(result) != result_digest:
            raise NodeError("result_mismatch", "acknowledgement is not bound to the stored result")
        ack_path = spool / "ack.json"
        receipt = {
            "schema": "alpha0.node-ack.v1",
            "package_id": package_id,
            "package_digest": request["package_digest"],
            "result_digest": result_digest,
        }
        status = "already_acknowledged" if ack_path.exists() else "acknowledged"
        if ack_path.exists():
            existing = json.loads(ack_path.read_text(encoding="utf-8"))
            if existing != receipt:
                raise NodeError("idempotency_conflict", "stored acknowledgement differs")
        else:
            atomic_write(ack_path, canonical(receipt))
            worktree = spool / "worktree"
            if worktree.exists() and not worktree.is_symlink():
                shutil.rmtree(worktree)
        return {
            "schema": RESPONSE_SCHEMA,
            "operation": "ack",
            "status": status,
            "package_id": package_id,
            "package_digest": request["package_digest"],
            "result_digest": result_digest,
        }
    finally:
        package_lock.close()


def read_request() -> dict[str, Any]:
    content = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(content) > MAX_REQUEST_BYTES:
        raise NodeError("request_too_large", "request exceeds 256 KiB")
    try:
        return mapping(json.loads(content), "request")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NodeError("invalid_json", "stdin must contain one JSON request") from exc


def write_response(response: dict[str, Any]) -> None:
    content = canonical(response) + b"\n"
    if len(content) > MAX_RESPONSE_BYTES:
        raise NodeError("response_too_large", "response exceeds 4 MiB")
    sys.stdout.buffer.write(content)
    sys.stdout.buffer.flush()


def self_test() -> None:
    assert digest({"a": 1}) == "sha256:015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
    assert identifier("eks-platform-governance", "test") == "eks-platform-governance"
    try:
        identifier("../escape", "test")
    except NodeError:
        return
    raise AssertionError("unsafe identifier accepted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if args.config is None:
        parser.error("--config is required")
    try:
        if os.environ.get("SSH_ORIGINAL_COMMAND"):
            raise NodeError("command_denied", "SSH commands are not accepted")
        config = load_config(args.config)
        request = read_request()
        if request.get("schema") != REQUEST_SCHEMA:
            raise NodeError("invalid_request", "unsupported node-request schema")
        operation = request.get("operation")
        if operation == "execute":
            response = execute(request, config)
        elif operation == "fetch":
            response = fetch(request, config)
        elif operation == "ack":
            response = ack(request, config)
        else:
            raise NodeError("invalid_request", "unsupported node operation")
        write_response(response)
        return 0
    except NodeError as exc:
        try:
            write_response({"schema": RESPONSE_SCHEMA, "status": "error", "error": exc.code, "message": exc.message})
        except NodeError:
            pass
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
