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
import secrets
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
REFERENCE = re.compile(r"^[a-z][a-z0-9+.-]{1,31}://\S+$")
RESERVED_CLOSURE_IDS = frozenset({"worker-learning", "memory-closure"})
DEVICE_CODE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")
SSO_EXPIRED = re.compile(
    r"(?:sso session.*(?:expired|invalid)|token.*(?:expired|does not exist)|"
    r"error loading sso token|run aws sso login)",
    re.IGNORECASE,
)
AUTH_REQUEST_SCHEMA = "alpha0.aws-sso-request.v1"
AUTH_SESSION_SCHEMA = "alpha0.aws-sso-node-session.v1"
AUTH_CONTINUATION_SCHEMA = "alpha0.aws-sso-continuation.v1"
AUTH_EVENT_SCHEMA = "alpha0.aws-sso-device-event.v1"


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
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
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
        raise NodeError(
            "invalid_config", "node config is unreadable or invalid"
        ) from exc
    exact_keys(
        config,
        {
            "node_id",
            "state_dir",
            "max_concurrent",
            "capabilities",
            "repositories",
            "workers",
            "worker_context_refs",
            "worker_secret_files",
            "aws_cli",
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
        raise NodeError(
            "invalid_config", "node capabilities must be non-empty and unique"
        )
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
        if not values or any(
            not isinstance(item, str) or not item or not Path(item).is_absolute()
            for item in values
        ):
            raise NodeError(
                "invalid_config", "worker argv must contain only fixed absolute paths"
            )
    worker_context_refs = mapping(
        config["worker_context_refs"], "config.worker_context_refs"
    )
    if set(worker_context_refs) != set(workers):
        raise NodeError(
            "invalid_config", "worker context map must exactly match worker adapters"
        )
    for name, raw_refs in worker_context_refs.items():
        identifier(name, "worker context map key")
        refs = sequence(raw_refs, "worker context refs", 16)
        if not refs or len(refs) != len(set(refs)):
            raise NodeError(
                "invalid_config", "worker context refs must be non-empty and unique"
            )
        for ref in refs:
            if (
                not isinstance(ref, str)
                or len(ref) > 1024
                or not ref.startswith("repo://")
                or "#sha256:" not in ref
            ):
                raise NodeError(
                    "invalid_config", "worker context ref is not exact and digest-bound"
                )
    worker_secret_files = mapping(
        config["worker_secret_files"], "config.worker_secret_files"
    )
    if set(worker_secret_files) != set(workers):
        raise NodeError(
            "invalid_config", "worker secret map must exactly match worker adapters"
        )
    for name, raw_secrets in worker_secret_files.items():
        identifier(name, "worker secret map key")
        secrets = mapping(raw_secrets, "worker secret files")
        for environment_name, raw_path in secrets.items():
            if environment_name not in {"OPENAI_API_KEY"}:
                raise NodeError(
                    "invalid_config", "worker secret environment name is unsupported"
                )
            if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
                raise NodeError(
                    "invalid_config", "worker secret paths must be absolute"
                )
    if (
        not isinstance(config["aws_cli"], str)
        or not Path(config["aws_cli"]).is_absolute()
    ):
        raise NodeError("invalid_config", "config.aws_cli must be an absolute path")
    profiles = sequence(config["aws_profiles"], "config.aws_profiles", 32)
    if len(profiles) != len(set(profiles)):
        raise NodeError("invalid_config", "AWS profile names must be unique")
    for profile in profiles:
        identifier(profile, "AWS profile name")
    if profiles and (
        not Path(config["aws_cli"]).is_file()
        or not os.access(config["aws_cli"], os.X_OK)
    ):
        raise NodeError("invalid_config", "configured AWS CLI is unavailable")
    return config


def validate_package(
    package: dict[str, Any], config: dict[str, Any]
) -> tuple[str, str, str]:
    exact_keys(
        package,
        {
            "schema",
            "package_id",
            "execution",
            "route",
            "project",
            "repository",
            "worker",
            "goal",
            "context_refs",
            "deliverables",
            "acceptance_criteria",
            "authority",
            "budgets",
            "stop_conditions",
        },
        "work package",
    )
    if package["schema"] != PACKAGE_SCHEMA:
        raise NodeError("invalid_request", "unsupported work-package schema")
    package_id = identifier(package["package_id"], "package_id")
    execution = mapping(package["execution"], "execution")
    exact_keys(
        execution,
        {"id", "lease_id", "attempt_no", "expires_at", "idempotency_key"},
        "execution",
    )
    identifier(execution["id"], "execution.id")
    identifier(execution["lease_id"], "execution.lease_id")
    bounded_int(execution["attempt_no"], "execution.attempt_no", 1, 5)
    if (
        not isinstance(execution["idempotency_key"], str)
        or not execution["idempotency_key"]
    ):
        raise NodeError("invalid_request", "execution.idempotency_key is required")
    try:
        expires = datetime.fromisoformat(execution["expires_at"])
    except (TypeError, ValueError) as exc:
        raise NodeError(
            "invalid_request", "execution.expires_at must be ISO-8601"
        ) from exc
    if expires.tzinfo is None or expires.astimezone(timezone.utc) <= datetime.now(
        timezone.utc
    ):
        raise NodeError("expired", "work package lease has expired")

    route = mapping(package["route"], "route")
    exact_keys(route, {"node_id", "required_capabilities"}, "route")
    if route["node_id"] != config["node_id"]:
        raise NodeError("wrong_node", "work package is bound to another node")
    capabilities = sequence(
        route["required_capabilities"], "route.required_capabilities", 16
    )
    if not capabilities:
        raise NodeError("invalid_request", "at least one node capability is required")
    for capability in capabilities:
        identifier(capability, "route capability")
    if not set(capabilities).issubset(config["capabilities"]):
        raise NodeError(
            "capability_denied", "work package requests an unavailable node capability"
        )

    repository = mapping(package["repository"], "repository")
    exact_keys(repository, {"id", "base_ref", "base_sha"}, "repository")
    repository_id = identifier(repository["id"], "repository.id")
    if repository_id not in config["repositories"]:
        raise NodeError("repository_denied", "repository is not in the exact node map")
    if (
        not isinstance(repository["base_ref"], str)
        or not repository["base_ref"]
        or repository["base_ref"].startswith("-")
    ):
        raise NodeError("invalid_request", "repository.base_ref is unsafe")
    base_sha = repository["base_sha"]
    if not isinstance(base_sha, str) or not GIT_SHA.fullmatch(base_sha):
        raise NodeError(
            "invalid_request", "repository.base_sha is not an exact Git object"
        )

    project = mapping(package["project"], "project")
    exact_keys(project, {"id", "source_ref"}, "project")
    identifier(project["id"], "project.id")
    if (
        not isinstance(project["source_ref"], str)
        or len(project["source_ref"]) > 1024
        or not REFERENCE.fullmatch(project["source_ref"])
        or project["source_ref"].startswith("file://")
    ):
        raise NodeError(
            "invalid_request", "project.source_ref must be a stable authority URI"
        )

    worker = mapping(package["worker"], "worker")
    exact_keys(worker, {"adapter"}, "worker")
    adapter = identifier(worker["adapter"], "worker.adapter")
    if adapter not in config["workers"]:
        raise NodeError("worker_denied", "worker adapter is not in the exact node map")
    context_refs = sequence(package["context_refs"], "context_refs", 32)
    if any(
        not isinstance(ref, str) or not ref or len(ref) > 1024 for ref in context_refs
    ):
        raise NodeError("invalid_request", "context refs must be bounded strings")
    if not set(config["worker_context_refs"][adapter]).issubset(context_refs):
        raise NodeError("context_denied", "work package omits an exact worker context")

    authority = mapping(package["authority"], "authority")
    exact_keys(authority, {"external_mutations"}, "authority")
    if sequence(authority["external_mutations"], "authority.external_mutations", 16):
        raise NodeError("mutation_denied", "this node boundary is read-only")

    budgets = mapping(package["budgets"], "budgets")
    exact_keys(
        budgets,
        {"timeout_seconds", "max_output_bytes", "max_artifact_bytes", "max_turns"},
        "budgets",
    )
    bounded_int(budgets["timeout_seconds"], "budgets.timeout_seconds", 60, 14_400)
    bounded_int(
        budgets["max_output_bytes"],
        "budgets.max_output_bytes",
        1_024,
        MAX_RESPONSE_BYTES,
    )
    bounded_int(
        budgets["max_artifact_bytes"],
        "budgets.max_artifact_bytes",
        1_024,
        256 * 1024 * 1024,
    )
    bounded_int(budgets["max_turns"], "budgets.max_turns", 1, 30)

    deliverables = sequence(package["deliverables"], "deliverables", 20)
    criteria = sequence(package["acceptance_criteria"], "acceptance_criteria", 50)
    if not deliverables or not criteria:
        raise NodeError(
            "invalid_request", "deliverables and acceptance criteria are required"
        )
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
                raise NodeError(
                    "invalid_request", "deliverable and criterion IDs must be unique"
                )
            row_ids.add(row_id)
            required = sequence(
                row["required_evidence"], f"{label}.required_evidence", 16
            )
            if not required:
                raise NodeError("invalid_request", f"{label} requires evidence")
            for evidence_id in required:
                evidence_ids.add(identifier(evidence_id, "evidence ID"))
            if label == "deliverable" and row["kind"] not in {"artifact", "report"}:
                raise NodeError("worker_denied", "inspect cannot produce Git changes")
            if label == "criterion" and row["independent_verifier"] is not True:
                raise NodeError(
                    "invalid_request", "criteria require an independent verifier"
                )
    if len(evidence_ids) > 128:
        raise NodeError(
            "invalid_request", "work package declares too many evidence artifacts"
        )
    if evidence_ids.intersection(RESERVED_CLOSURE_IDS):
        raise NodeError(
            "invalid_request", "evidence IDs collide with mandatory worker closures"
        )
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
        raise NodeError(
            "git_failed", result.stderr.strip()[:1000] or "Git command failed"
        )
    return result.stdout.strip()


def prepare_worktree(source: Path, target: Path, base_sha: str) -> None:
    if run_git("--git-dir", str(source), "rev-parse", "--is-bare-repository") != "true":
        raise NodeError(
            "repository_unavailable", "repository projection is not a bare cache"
        )
    run_git("--git-dir", str(source), "cat-file", "-e", f"{base_sha}^{{commit}}")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    run_git(
        "clone",
        "--shared",
        "--no-checkout",
        "--",
        str(source),
        str(target),
        timeout=300,
    )
    run_git("-C", str(target), "checkout", "--detach", base_sha, timeout=300)
    observed = run_git("-C", str(target), "rev-parse", "HEAD")
    if observed != base_sha:
        raise NodeError(
            "repository_mismatch", "detached worktree did not resolve to the exact base"
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def auth_environment(config: dict[str, Any]) -> dict[str, str]:
    aws_cli = config["aws_cli"]
    return {
        "AWS_EC2_METADATA_DISABLED": "true",
        "HOME": config["state_dir"],
        "LC_ALL": "C",
        "PATH": str(Path(aws_cli).parent),
        "PYTHONNOUSERSITE": "1",
    }


def identity_preflight(
    config: dict[str, Any], profile: str
) -> tuple[str, dict[str, Any] | None]:
    try:
        completed = subprocess.run(
            [
                config["aws_cli"],
                "sts",
                "get-caller-identity",
                "--profile",
                profile,
                "--output",
                "json",
                "--no-cli-pager",
            ],
            check=False,
            capture_output=True,
            env=auth_environment(config),
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NodeError(
            "auth_unavailable", "AWS identity preflight is unavailable"
        ) from exc
    if completed.returncode != 0:
        error = (completed.stderr or b"")[:16_384].decode("utf-8", errors="replace")
        return ("expired" if SSO_EXPIRED.search(error) else "failed"), None
    try:
        identity = mapping(json.loads(completed.stdout), "AWS identity")
        account = identity["Account"]
        arn = identity["Arn"]
        user_id = identity["UserId"]
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError, NodeError) as exc:
        raise NodeError(
            "auth_unavailable", "AWS identity preflight returned invalid data"
        ) from exc
    if any(
        not isinstance(value, str) or not value for value in (account, arn, user_id)
    ):
        raise NodeError(
            "auth_unavailable", "AWS identity preflight returned invalid data"
        )
    observed_at = utc_now().isoformat()
    identity_digest = digest({"Account": account, "Arn": arn, "UserId": user_id})
    return "authenticated", {
        "schema": "alpha0.aws-identity-preflight.v1",
        "node_id": config["node_id"],
        "profile": profile,
        "authenticated": True,
        "source_ref": f"aws://sts/{profile}/{identity_digest.split(':', 1)[1]}",
        "observed_at": observed_at,
    }


def build_auth_link(url: str, code: str) -> str:
    if not DEVICE_CODE.fullmatch(code):
        raise NodeError("auth_unavailable", "AWS SSO device code is invalid")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.port not in {None, 443}
        or not (
            parts.hostname.endswith(".awsapps.com")
            or parts.hostname.endswith(".amazonaws.com")
        )
    ):
        raise NodeError("auth_unavailable", "AWS SSO device URL is invalid")
    if parts.fragment:
        fragment_path, separator, fragment_query = parts.fragment.partition("?")
        query = dict(parse_qsl(fragment_query)) if separator else {}
        query["user_code"] = code
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                parts.query,
                f"{fragment_path}?{urlencode(query)}",
            )
        )
    query = dict(parse_qsl(parts.query))
    query["user_code"] = code
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def auth_session_path(config: dict[str, Any], profile: str) -> Path:
    return Path(config["state_dir"]) / "auth" / f"{profile}.json"


def auth_session_lock(config: dict[str, Any], profile: str) -> Any:
    return open_lock(
        Path(config["state_dir"]) / "auth" / f"{profile}.lock", blocking=True
    )


def read_auth_session(config: dict[str, Any], profile: str) -> dict[str, Any] | None:
    path = auth_session_path(config, profile)
    if not path.exists():
        return None
    try:
        session = mapping(json.loads(path.read_text(encoding="utf-8")), "auth session")
    except (OSError, json.JSONDecodeError, NodeError) as exc:
        raise NodeError(
            "auth_state_invalid", "AWS authentication state is invalid"
        ) from exc
    if (
        session.get("schema") != AUTH_SESSION_SCHEMA
        or session.get("profile") != profile
        or session.get("node_id") != config["node_id"]
        or session.get("state")
        not in {"waiting_for_auth", "authenticated", "verified", "needs_input"}
        or not isinstance(session.get("waiters"), list)
    ):
        raise NodeError("auth_state_invalid", "AWS authentication state is invalid")
    return session


def write_auth_session(config: dict[str, Any], session: dict[str, Any]) -> None:
    atomic_write(auth_session_path(config, session["profile"]), canonical(session))


def auth_session_expired(session: dict[str, Any]) -> bool:
    try:
        expires = datetime.fromisoformat(session["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NodeError(
            "auth_state_invalid", "AWS authentication state is invalid"
        ) from exc
    if expires.tzinfo is None:
        raise NodeError("auth_state_invalid", "AWS authentication state is invalid")
    return utc_now() > expires.astimezone(timezone.utc)


def patch_auth_session(
    config: dict[str, Any], profile: str, challenge_id: str, **changes: Any
) -> None:
    lock = auth_session_lock(config, profile)
    try:
        session = read_auth_session(config, profile)
        if session is None or session.get("challenge_id") != challenge_id:
            return
        session.update(changes)
        write_auth_session(config, session)
    finally:
        lock.close()


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def monitor_device_login(
    config: dict[str, Any], profile: str, challenge_id: str, event_fd: int
) -> None:
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + 600
    try:
        patch_auth_session(config, profile, challenge_id, monitor_pid=os.getpid())
        process = subprocess.Popen(
            [
                config["aws_cli"],
                "sso",
                "login",
                "--profile",
                profile,
                "--use-device-code",
                "--no-browser",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=auth_environment(config),
        )
        if process.stdout is None:
            raise NodeError("auth_unavailable", "AWS SSO login output is unavailable")
        selector.register(process.stdout, selectors.EVENT_READ)
        url: str | None = None
        code: str | None = None
        while time.monotonic() < deadline and (url is None or code is None):
            if process.poll() is not None:
                break
            remaining = max(0.0, deadline - time.monotonic())
            for key, _ in selector.select(timeout=min(1.0, remaining)):
                line = key.fileobj.readline(4097)
                if len(line) > 4096:
                    raise NodeError(
                        "auth_unavailable", "AWS SSO login output is invalid"
                    )
                value = line.decode("utf-8", errors="replace").strip()
                if value.startswith("https://"):
                    url = value
                if DEVICE_CODE.fullmatch(value):
                    code = value
        if not url or not code:
            raise NodeError(
                "auth_unavailable", "AWS SSO login produced no device challenge"
            )
        event = {
            "profile": profile,
            "schema": AUTH_EVENT_SCHEMA,
            "sensitive": True,
            "status": "authorization_required",
            "verification_url": build_auth_link(url, code),
        }
        os.write(event_fd, canonical(event))
        os.close(event_fd)
        event_fd = -1
        try:
            status = process.wait(timeout=max(1, int(deadline - time.monotonic())))
        except subprocess.TimeoutExpired as exc:
            raise NodeError("auth_unavailable", "AWS SSO login timed out") from exc
        if status != 0:
            raise NodeError("auth_unavailable", "AWS SSO login failed")
        patch_auth_session(
            config,
            profile,
            challenge_id,
            state="authenticated",
            authenticated_at=utc_now().isoformat(),
            monitor_pid=None,
        )
    except (OSError, NodeError):
        patch_auth_session(
            config,
            profile,
            challenge_id,
            state="needs_input",
            failure="aws_sso_device_login_failed",
            monitor_pid=None,
        )
    finally:
        if event_fd >= 0:
            os.close(event_fd)
        selector.close()
        if process is not None:
            _stop_process(process)


def start_device_login(
    config: dict[str, Any], profile: str, challenge_id: str
) -> dict[str, Any]:
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        try:
            os.setsid()
            with (
                open(os.devnull, "rb", buffering=0) as stdin,
                open(os.devnull, "ab", buffering=0) as output,
            ):
                os.dup2(stdin.fileno(), 0)
                os.dup2(output.fileno(), 1)
                os.dup2(output.fileno(), 2)
            for raw_descriptor in os.listdir("/proc/self/fd"):
                descriptor = int(raw_descriptor)
                if descriptor > 2 and descriptor != write_fd:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            monitor_device_login(config, profile, challenge_id, write_fd)
        finally:
            os._exit(0)
    os.close(write_fd)
    selector = selectors.DefaultSelector()
    try:
        selector.register(read_fd, selectors.EVENT_READ)
        ready = selector.select(timeout=30)
        if not ready:
            raise NodeError("auth_unavailable", "AWS SSO challenge timed out")
        event_bytes = os.read(read_fd, 16_385)
    finally:
        selector.close()
        os.close(read_fd)
    if not event_bytes or len(event_bytes) > 16_384:
        raise NodeError("auth_unavailable", "AWS SSO challenge is unavailable")
    try:
        event = mapping(json.loads(event_bytes), "AWS SSO event")
    except (UnicodeDecodeError, json.JSONDecodeError, NodeError) as exc:
        raise NodeError("auth_unavailable", "AWS SSO challenge is invalid") from exc
    if (
        set(event) != {"profile", "schema", "sensitive", "status", "verification_url"}
        or event.get("schema") != AUTH_EVENT_SCHEMA
        or event.get("profile") != profile
        or event.get("status") != "authorization_required"
        or event.get("sensitive") is not True
    ):
        raise NodeError("auth_unavailable", "AWS SSO challenge is invalid")
    return event


def worker_limits(max_output: int) -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (max_output, max_output))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))


def load_auth_request(
    config: dict[str, Any], package: dict[str, Any], artifacts: Path, exit_code: int
) -> dict[str, Any] | None:
    path = artifacts / "aws-sso-request.json"
    if not path.exists():
        return None
    try:
        content = path.read_bytes()
        request = mapping(json.loads(content), "AWS SSO request")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, NodeError) as exc:
        raise NodeError(
            "auth_request_invalid", "worker AWS SSO request is invalid"
        ) from exc
    if canonical(request) != content:
        raise NodeError(
            "auth_request_invalid", "worker AWS SSO request is not canonical"
        )
    exact_keys(request, {"schema", "profile", "reason"}, "AWS SSO request")
    profile = identifier(request["profile"], "AWS profile")
    if (
        request["schema"] != AUTH_REQUEST_SCHEMA
        or request["reason"] != "qualified_sso_expiry"
        or exit_code != 75
        or profile not in config["aws_profiles"]
        or "aws.sso-device-login" not in package["route"]["required_capabilities"]
    ):
        raise NodeError(
            "auth_request_denied", "worker AWS SSO request is not authorized"
        )
    return request


def run_worker(
    config: dict[str, Any],
    package: dict[str, Any],
    spool: Path,
    worktree: Path,
    *,
    resumed: bool = False,
) -> tuple[int, dict[str, Any], dict[str, Any] | None]:
    budgets = package["budgets"]
    stdout_path = spool / "worker.stdout"
    stderr_path = spool / "worker.stderr"
    stdout_path.touch(mode=0o600)
    stderr_path.touch(mode=0o600)
    artifacts = spool / "artifacts"
    artifacts.mkdir(mode=0o700, exist_ok=True)
    if package["worker"]["adapter"] == "inspect":
        head = run_git("-C", str(worktree), "rev-parse", "HEAD")
        status = run_git(
            "-C", str(worktree), "status", "--porcelain=v1", "--untracked-files=all"
        )
        clean = not status
        matches = head == package["repository"]["base_sha"]
        observation = {
            "schema": "alpha0.node-worker-observation.v1",
            "head_sha": head,
            "clean": clean,
            "summary": (
                "exact detached base inspected; worktree clean"
                if clean and matches
                else "repository observation did not match the requested clean base"
            ),
        }
        atomic_write(stdout_path, canonical(observation))
        return (0 if clean and matches else 1), observation, None
    runtime_home = spool / "home"
    runtime_tmp = spool / "tmp"
    runtime_home.mkdir(mode=0o700, exist_ok=True)
    runtime_tmp.mkdir(mode=0o700, exist_ok=True)
    argv = [
        *config["workers"][package["worker"]["adapter"]],
        "--package",
        str(spool / "package.json"),
        "--repository",
        str(worktree),
        "--artifacts",
        str(artifacts),
    ]
    environment = {
        "HOME": str(runtime_home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/run/current-system/sw/bin",
        "TMPDIR": str(runtime_tmp),
        "ALPHA0_NODE_ID": config["node_id"],
    }
    if resumed:
        environment["ALPHA0_AUTH_RESUMED"] = "1"
    for name, raw_path in config["worker_secret_files"][
        package["worker"]["adapter"]
    ].items():
        try:
            secret = Path(raw_path).read_bytes()
        except OSError as exc:
            raise NodeError(
                "worker_unavailable", "worker runtime secret is unavailable"
            ) from exc
        secret = secret.rstrip(b"\r\n")
        if not secret or len(secret) > 16 * 1024 or b"\x00" in secret:
            raise NodeError("worker_unavailable", "worker runtime secret is invalid")
        try:
            environment[name] = secret.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NodeError(
                "worker_unavailable", "worker runtime secret is invalid"
            ) from exc
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
            exit_code = (
                completed.returncode if 0 <= completed.returncode <= 255 else 255
            )
        except subprocess.TimeoutExpired:
            exit_code = 124
    auth_request = load_auth_request(config, package, artifacts, exit_code)
    if auth_request is not None:
        return (
            exit_code,
            {
                "schema": "alpha0.node-worker-observation.v1",
                "head_sha": package["repository"]["base_sha"],
                "clean": False,
                "summary": "worker paused at a qualified authentication boundary",
            },
            auth_request,
        )
    try:
        observation = mapping(
            json.loads(stdout_path.read_text(encoding="utf-8")), "worker observation"
        )
        exact_keys(
            observation,
            {"schema", "head_sha", "clean", "summary"},
            "worker observation",
        )
        if observation["schema"] != "alpha0.node-worker-observation.v1":
            raise NodeError(
                "worker_failed", "worker emitted an unsupported observation"
            )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, NodeError) as exc:
        if exit_code == 0:
            raise NodeError(
                "worker_failed", "worker did not emit a valid bounded observation"
            ) from exc
        observation = {
            "schema": "alpha0.node-worker-observation.v1",
            "head_sha": package["repository"]["base_sha"],
            "clean": False,
            "summary": "worker failed before producing a valid observation",
        }
    return exit_code, observation, None


def build_result(
    package: dict[str, Any],
    package_digest: str,
    spool: Path,
    exit_code: int,
    observation: dict[str, Any],
    node_id: str,
) -> dict[str, Any]:
    expected_head = package["repository"]["base_sha"]
    observed_head = observation.get("head_sha")
    verified = (
        exit_code == 0
        and observation.get("clean") is True
        and observed_head == expected_head
    )
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
        worker_path = spool / "artifacts" / f"{evidence_id}.json"
        worker_supplied = not independent and worker_path.is_file()
        worker_value: dict[str, Any] = {}
        if worker_supplied:
            content = worker_path.read_bytes()
            if not content or len(content) > package["budgets"]["max_artifact_bytes"]:
                raise NodeError("worker_failed", "worker artifact exceeds its bound")
            try:
                worker_value = mapping(json.loads(content), "worker artifact")
            except (UnicodeDecodeError, json.JSONDecodeError, NodeError) as exc:
                raise NodeError(
                    "worker_failed", "worker artifact is not bounded JSON"
                ) from exc
            if canonical(worker_value) != content:
                raise NodeError(
                    "worker_failed", "worker artifact is not canonical JSON"
                )
        else:
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
                "kind": (
                    "verification"
                    if independent
                    else (
                        "implementation_plan"
                        if worker_supplied
                        and worker_value.get("schema") == "alpha0.worker-plan.v1"
                        else "report"
                    )
                ),
                "ref": f"alpha0-node://{node_id}/{package['package_id']}/artifacts/{evidence_id}",
                "digest": f"sha256:{hashlib.sha256(content).hexdigest()}",
                "size_bytes": len(content),
                "producer": producer,
            }
        )
    captured_at = datetime.now(timezone.utc).isoformat()
    learning = {
        "schema": "alpha0.worker-learning.v1",
        "package_id": package["package_id"],
        "package_digest": package_digest,
        "execution_id": package["execution"]["id"],
        "node_id": node_id,
        "worker_adapter": package["worker"]["adapter"],
        "candidates": [],
    }
    learning_content = canonical(learning)
    atomic_write(spool / "artifacts" / "worker-learning.json", learning_content)
    artifact_rows.append(
        {
            "id": "worker-learning",
            "kind": "learning_candidates",
            "ref": f"alpha0-node://{node_id}/{package['package_id']}/artifacts/worker-learning",
            "digest": f"sha256:{hashlib.sha256(learning_content).hexdigest()}",
            "size_bytes": len(learning_content),
            "producer": "worker",
        }
    )
    verifier_artifact = next(
        row for row in artifact_rows if row["id"] in criterion_evidence
    )
    memory = {
        "schema": "alpha0.memory-closure.v1",
        "source": {
            "actor_id": package["worker"]["adapter"],
            "kind": "worker",
            "ref": f"alpha0-node://{node_id}/{package['package_id']}",
        },
        "project_id": package["project"]["id"],
        "captured_at": captured_at,
        "records": [
            {
                "id": "worker-outcome",
                "kind": "episode",
                "subject": "Bounded worker outcome",
                "summary": observation.get("summary", "bounded node observation"),
                "source_ref": verifier_artifact["ref"],
                "authority_ref": package["project"]["source_ref"],
                "observed_at": captured_at,
                "valid_until": None,
                "evidence_refs": [verifier_artifact["ref"]],
                "details": {
                    "event": "outcome" if verified else "failure",
                    "outcome": "succeeded" if verified else "failed",
                    "repository": {
                        "id": package["repository"]["id"],
                        "base_ref": package["repository"]["base_ref"],
                        "base_sha": expected_head,
                        "head_sha": observed_head or expected_head,
                        "workspace_ref": (
                            f"git-worktree://{package['repository']['id']}/"
                            f"{package['package_id']}"
                        ),
                    },
                    "artifact_refs": [verifier_artifact["ref"]],
                },
            }
        ],
    }
    memory_content = canonical(memory)
    atomic_write(spool / "artifacts" / "memory-closure.json", memory_content)
    artifact_rows.append(
        {
            "id": "memory-closure",
            "kind": "memory_closure",
            "ref": f"alpha0-node://{node_id}/{package['package_id']}/artifacts/memory-closure",
            "digest": f"sha256:{hashlib.sha256(memory_content).hexdigest()}",
            "size_bytes": len(memory_content),
            "producer": "worker",
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
        "repository": {
            "base_sha": expected_head,
            "head_sha": observed_head or expected_head,
        },
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
                "summary": "node verifier observed the exact clean detached base"
                if verified
                else "node verification failed",
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
        return mapping(
            json.loads((spool / "result.json").read_text(encoding="utf-8")),
            "stored result",
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise NodeError(
            "incomplete_spool", "package was admitted but has no replayable result"
        ) from exc


def load_spool_identity(
    config: dict[str, Any], package_id: Any, package_digest: Any
) -> tuple[Path, dict[str, Any]]:
    state = Path(config["state_dir"])
    safe_package_id = identifier(package_id, "package_id")
    safe_package_digest = safe_digest(package_digest, "package_digest")
    spool = state / "packages" / safe_package_id
    try:
        known_digest = (spool / "package.digest").read_text(encoding="ascii").strip()
        package = mapping(
            json.loads((spool / "package.json").read_text(encoding="utf-8")),
            "stored package",
        )
    except (OSError, json.JSONDecodeError, NodeError) as exc:
        raise NodeError("not_found", "package spool does not exist") from exc
    if known_digest != safe_package_digest or digest(package) != safe_package_digest:
        raise NodeError(
            "idempotency_conflict", "package digest does not match the spool"
        )
    return spool, package


def auth_waiter(
    config: dict[str, Any], package: dict[str, Any], package_digest: str
) -> dict[str, Any]:
    return {
        "package_id": package["package_id"],
        "package_digest": package_digest,
        "continuation_ref": (
            f"alpha0-node://{config['node_id']}/{package['package_id']}/last-safe-step"
        ),
        "resumed": False,
    }


def write_auth_continuation(
    spool: Path,
    *,
    challenge_id: str,
    profile: str,
    waiter: dict[str, Any],
) -> None:
    atomic_write(
        spool / "auth-continuation.json",
        canonical(
            {
                "schema": AUTH_CONTINUATION_SCHEMA,
                "challenge_id": challenge_id,
                "profile": profile,
                "package_id": waiter["package_id"],
                "package_digest": waiter["package_digest"],
                "continuation_ref": waiter["continuation_ref"],
            }
        ),
    )


def read_auth_continuation(spool: Path) -> dict[str, Any]:
    try:
        continuation = mapping(
            json.loads((spool / "auth-continuation.json").read_text(encoding="utf-8")),
            "auth continuation",
        )
    except (OSError, json.JSONDecodeError, NodeError) as exc:
        raise NodeError(
            "auth_state_invalid", "package authentication state is invalid"
        ) from exc
    exact_keys(
        continuation,
        {
            "schema",
            "challenge_id",
            "profile",
            "package_id",
            "package_digest",
            "continuation_ref",
        },
        "auth continuation",
    )
    if continuation["schema"] != AUTH_CONTINUATION_SCHEMA:
        raise NodeError("auth_state_invalid", "package authentication state is invalid")
    identifier(continuation["challenge_id"], "challenge_id")
    identifier(continuation["profile"], "profile")
    identifier(continuation["package_id"], "package_id")
    safe_digest(continuation["package_digest"], "package_digest")
    if not isinstance(continuation["continuation_ref"], str) or not REFERENCE.fullmatch(
        continuation["continuation_ref"]
    ):
        raise NodeError("auth_state_invalid", "package authentication state is invalid")
    return continuation


def auth_response(
    operation: str,
    continuation: dict[str, Any],
    session: dict[str, Any] | None,
    *,
    event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = "needs_input"
    if (
        session is not None
        and session.get("challenge_id") == continuation["challenge_id"]
    ):
        state = {
            "waiting_for_auth": "waiting_for_auth",
            "authenticated": "authentication_ready",
            "verified": "authentication_verified",
            "needs_input": "needs_input",
        }[session["state"]]
        if auth_session_expired(session):
            state = "needs_input"
    response = {
        "schema": RESPONSE_SCHEMA,
        "operation": operation,
        "status": state,
        "package_id": continuation["package_id"],
        "package_digest": continuation["package_digest"],
        "challenge_id": continuation["challenge_id"],
        "profile": continuation["profile"],
        "continuation_ref": continuation["continuation_ref"],
    }
    if event is not None:
        response["auth_event"] = event
    return response


def begin_authentication(
    config: dict[str, Any],
    package: dict[str, Any],
    package_digest: str,
    spool: Path,
    auth_request: dict[str, Any],
) -> dict[str, Any]:
    profile = auth_request["profile"]
    waiter = auth_waiter(config, package, package_digest)
    start_challenge = False
    lock = auth_session_lock(config, profile)
    try:
        session = read_auth_session(config, profile)
        if session is not None and session["state"] in {
            "waiting_for_auth",
            "authenticated",
        }:
            active = not auth_session_expired(session)
        else:
            active = False
        if not active:
            preflight_status, _ = identity_preflight(config, profile)
            if preflight_status == "authenticated":
                raise NodeError(
                    "auth_not_required", "AWS profile is already authenticated"
                )
            if preflight_status != "expired":
                raise NodeError(
                    "auth_not_qualified",
                    "worker failure is not a qualified AWS SSO expiry",
                )
            created = utc_now()
            session = {
                "schema": AUTH_SESSION_SCHEMA,
                "challenge_id": f"auth_{secrets.token_hex(16)}",
                "node_id": config["node_id"],
                "profile": profile,
                "state": "waiting_for_auth",
                "created_at": created.isoformat(),
                "expires_at": (created + timedelta(seconds=600)).isoformat(),
                "authenticated_at": None,
                "monitor_pid": None,
                "failure": None,
                "preflight": None,
                "waiters": [],
            }
            start_challenge = True
        if len(session["waiters"]) >= 32:
            raise NodeError("auth_waiter_limit", "AWS SSO waiter limit reached")
        existing = next(
            (
                row
                for row in session["waiters"]
                if row.get("package_id") == waiter["package_id"]
            ),
            None,
        )
        if existing is not None and existing != waiter:
            raise NodeError("idempotency_conflict", "AWS SSO waiter identity differs")
        if existing is None:
            session["waiters"].append(waiter)
        write_auth_session(config, session)
        write_auth_continuation(
            spool,
            challenge_id=session["challenge_id"],
            profile=profile,
            waiter=waiter,
        )
    finally:
        lock.close()
    event = (
        start_device_login(config, profile, session["challenge_id"])
        if start_challenge
        else None
    )
    continuation = read_auth_continuation(spool)
    current_session = read_auth_session(config, profile)
    return auth_response("execute", continuation, current_session, event=event)


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
                known_digest = (
                    (spool / "package.digest").read_text(encoding="ascii").strip()
                )
            except OSError as exc:
                raise NodeError(
                    "incomplete_spool", "existing package spool is incomplete"
                ) from exc
            if known_digest != package_digest:
                raise NodeError(
                    "idempotency_conflict",
                    "package ID is already bound to another digest",
                )
            if not (spool / "result.json").exists():
                continuation = read_auth_continuation(spool)
                session = read_auth_session(config, continuation["profile"])
                return auth_response("execute", continuation, session)
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
            prepare_worktree(
                Path(config["repositories"][repository_id]),
                worktree,
                package["repository"]["base_sha"],
            )
            exit_code, observation, auth_request = run_worker(
                config, package, spool, worktree
            )
            if auth_request is not None:
                return begin_authentication(
                    config, package, package_digest, spool, auth_request
                )
            result = build_result(
                package,
                package_digest,
                spool,
                exit_code,
                observation,
                config["node_id"],
            )
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


def load_bound_spool(
    config: dict[str, Any], package_id: str, package_digest: str
) -> tuple[Path, dict[str, Any]]:
    spool, _ = load_spool_identity(config, package_id, package_digest)
    return spool, stored_result(spool)


def bound_auth_state(
    request: dict[str, Any], config: dict[str, Any]
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    spool, package = load_spool_identity(
        config, request["package_id"], request["package_digest"]
    )
    continuation = read_auth_continuation(spool)
    challenge_id = identifier(request["challenge_id"], "challenge_id")
    if (
        continuation["package_id"] != request["package_id"]
        or continuation["package_digest"] != request["package_digest"]
        or continuation["challenge_id"] != challenge_id
    ):
        raise NodeError("idempotency_conflict", "authentication continuation differs")
    session = read_auth_session(config, continuation["profile"])
    if session is None or session.get("challenge_id") != challenge_id:
        raise NodeError(
            "auth_state_invalid", "AWS authentication session is unavailable"
        )
    waiter = next(
        (
            row
            for row in session["waiters"]
            if row.get("package_id") == continuation["package_id"]
        ),
        None,
    )
    expected = auth_waiter(config, package, request["package_digest"])
    if waiter != expected and not (
        isinstance(waiter, dict)
        and waiter.get("package_id") == expected["package_id"]
        and waiter.get("package_digest") == expected["package_digest"]
        and waiter.get("continuation_ref") == expected["continuation_ref"]
        and waiter.get("resumed") is True
    ):
        raise NodeError("auth_state_invalid", "AWS authentication waiter is misbound")
    return spool, continuation, session


def auth_status(request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    exact_keys(
        request,
        {"schema", "operation", "package_id", "package_digest", "challenge_id"},
        "auth-status request",
    )
    _, continuation, session = bound_auth_state(request, config)
    return auth_response("auth-status", continuation, session)


def auth_verify(request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    exact_keys(
        request,
        {"schema", "operation", "package_id", "package_digest", "challenge_id"},
        "auth-verify request",
    )
    _, continuation, session = bound_auth_state(request, config)
    profile = continuation["profile"]
    lock = auth_session_lock(config, profile)
    try:
        session = read_auth_session(config, profile)
        if (
            session is None
            or session.get("challenge_id") != continuation["challenge_id"]
        ):
            raise NodeError(
                "auth_state_invalid", "AWS authentication session is unavailable"
            )
        if auth_session_expired(session):
            session.update(
                state="needs_input",
                failure="aws_sso_device_challenge_expired",
                preflight=None,
            )
            write_auth_session(config, session)
            return auth_response("auth-verify", continuation, session)
        if session["state"] == "verified":
            preflight = session.get("preflight")
        elif session["state"] == "authenticated":
            status, preflight = identity_preflight(config, profile)
            if status != "authenticated" or preflight is None:
                session.update(
                    state="needs_input",
                    failure="aws_identity_preflight_failed",
                    preflight=None,
                )
                write_auth_session(config, session)
                return auth_response("auth-verify", continuation, session)
            session.update(state="verified", preflight=preflight)
            write_auth_session(config, session)
        else:
            return auth_response("auth-verify", continuation, session)
    finally:
        lock.close()
    if not isinstance(preflight, dict):
        raise NodeError("auth_state_invalid", "AWS identity preflight is unavailable")
    return {
        **auth_response("auth-verify", continuation, session),
        "auth_event": {
            "schema": AUTH_EVENT_SCHEMA,
            "profile": profile,
            "sensitive": False,
            "status": "authenticated",
        },
        "preflight": preflight,
    }


def resume(request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    exact_keys(
        request,
        {"schema", "operation", "package_id", "package_digest", "challenge_id"},
        "resume request",
    )
    state = Path(config["state_dir"])
    package_id = identifier(request["package_id"], "package_id")
    package_lock = open_lock(state / "locks" / f"{package_id}.lock", blocking=True)
    try:
        spool, package = load_spool_identity(
            config, package_id, request["package_digest"]
        )
        if (spool / "result.json").exists():
            result = stored_result(spool)
            return {
                "schema": RESPONSE_SCHEMA,
                "operation": "resume",
                "status": "replayed",
                "package_id": package_id,
                "package_digest": request["package_digest"],
                "result_digest": digest(result),
                "result": result,
            }
        _, continuation, session = bound_auth_state(request, config)
        if auth_session_expired(session):
            return auth_response("resume", continuation, session)
        if session["state"] != "verified" or not isinstance(
            session.get("preflight"), dict
        ):
            return auth_response("resume", continuation, session)
        validate_package(package, config)
        worktree = spool / "worktree" / "repository"
        if (
            not worktree.is_dir()
            or run_git("-C", str(worktree), "rev-parse", "HEAD")
            != package["repository"]["base_sha"]
            or run_git(
                "-C",
                str(worktree),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            )
        ):
            raise NodeError(
                "continuation_mismatch",
                "preserved worktree does not match the last safe step",
            )
        auth_request_path = spool / "artifacts" / "aws-sso-request.json"
        if auth_request_path.exists():
            auth_request_path.unlink()
        slot = acquire_slot(state, config["max_concurrent"])
        try:
            exit_code, observation, repeated_auth = run_worker(
                config, package, spool, worktree, resumed=True
            )
            if repeated_auth is not None:
                observation["summary"] = (
                    "worker requested authentication again after the one allowed resume"
                )
            result = build_result(
                package,
                request["package_digest"],
                spool,
                exit_code,
                observation,
                config["node_id"],
            )
            atomic_write(spool / "result.json", canonical(result))
        finally:
            slot.close()
        session_lock = auth_session_lock(config, continuation["profile"])
        try:
            current = read_auth_session(config, continuation["profile"])
            if (
                current is not None
                and current.get("challenge_id") == continuation["challenge_id"]
            ):
                for waiter in current["waiters"]:
                    if waiter.get("package_id") == package_id:
                        waiter["resumed"] = True
                write_auth_session(config, current)
        finally:
            session_lock.close()
        return {
            "schema": RESPONSE_SCHEMA,
            "operation": "resume",
            "status": "completed",
            "package_id": package_id,
            "package_digest": request["package_digest"],
            "result_digest": digest(result),
            "result": result,
        }
    finally:
        package_lock.close()


def fetch(request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    exact_keys(
        request,
        {"schema", "operation", "package_id", "package_digest", "artifact_id"},
        "fetch request",
    )
    artifact_id = identifier(request["artifact_id"], "artifact_id")
    spool, result = load_bound_spool(
        config, request["package_id"], request["package_digest"]
    )
    metadata = next(
        (row for row in result["artifacts"] if row["id"] == artifact_id), None
    )
    if metadata is None:
        raise NodeError("not_found", "artifact is not declared by the stored result")
    path = spool / "artifacts" / f"{artifact_id}.json"
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise NodeError("not_found", "artifact content is unavailable") from exc
    observed_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    if len(content) != metadata["size_bytes"] or observed_digest != metadata["digest"]:
        raise NodeError(
            "artifact_mismatch", "artifact content does not match its manifest"
        )
    if len(content) > 3 * 1024 * 1024:
        raise NodeError(
            "artifact_too_large", "artifact exceeds the bounded SSH response"
        )
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
    exact_keys(
        request,
        {"schema", "operation", "package_id", "package_digest", "result_digest"},
        "ack request",
    )
    result_digest = safe_digest(request["result_digest"], "result_digest")
    state = Path(config["state_dir"])
    package_id = identifier(request["package_id"], "package_id")
    package_lock = open_lock(state / "locks" / f"{package_id}.lock", blocking=True)
    try:
        spool, result = load_bound_spool(config, package_id, request["package_digest"])
        if digest(result) != result_digest:
            raise NodeError(
                "result_mismatch", "acknowledgement is not bound to the stored result"
            )
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
                raise NodeError(
                    "idempotency_conflict", "stored acknowledgement differs"
                )
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
    assert (
        digest({"a": 1})
        == "sha256:015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
    )
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
        elif operation == "auth-status":
            response = auth_status(request, config)
        elif operation == "auth-verify":
            response = auth_verify(request, config)
        elif operation == "resume":
            response = resume(request, config)
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
            write_response(
                {
                    "schema": RESPONSE_SCHEMA,
                    "status": "error",
                    "error": exc.code,
                    "message": exc.message,
                }
            )
        except NodeError:
            pass
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
