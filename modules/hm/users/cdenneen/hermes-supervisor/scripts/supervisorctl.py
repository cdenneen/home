#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

ROOT = Path(os.environ.get("AXIS_SUPERVISOR_ROOT", Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor"))
CONTROL = ROOT / "control.json"
LEASES = ROOT / "leases"
CONTROLLER_LOCK = LEASES / ".controller.lock"
RESOURCE_PATTERN = re.compile(r"^(repo|path|branch|worktree):([^:]+/[^:]+)(?::.*)?$")


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def load_control() -> dict:
    value = load(CONTROL)
    if value.get("schema") != "axis.external-development-supervisor.control":
        raise ValueError("unsupported control schema")
    if value.get("schema_version") != "1.0.0":
        raise ValueError("unsupported control schema_version")
    return value


def write_atomic(path: Path, value: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def acquire_controller(now: int) -> None:
    if CONTROLLER_LOCK.exists() and now - int(CONTROLLER_LOCK.stat().st_mtime) > 60:
        CONTROLLER_LOCK.rmdir()
    try:
        CONTROLLER_LOCK.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise RuntimeError("another lease operation is in progress") from exc


def release_controller() -> None:
    CONTROLLER_LOCK.rmdir()


def active_leases(now: int) -> list[dict]:
    values = []
    for path in LEASES.glob("*/lease.json"):
        try:
            lease = load(path)
        except Exception:
            continue
        if int(lease.get("expires_at_epoch", 0)) > now:
            lease["path"] = str(path)
            values.append(lease)
    return values


def all_leases() -> list[dict]:
    values = []
    for path in LEASES.glob("*/lease.json"):
        try:
            lease = load(path)
        except Exception:
            continue
        lease["path"] = str(path)
        values.append(lease)
    return values


def recover(now: int) -> list[str]:
    recovered = []
    for directory in LEASES.iterdir() if LEASES.exists() else []:
        if not directory.is_dir() or directory.name.startswith(("stale-", ".")):
            continue
        lease_path = directory / "lease.json"
        try:
            lease = load(lease_path)
            expired = int(lease.get("expires_at_epoch", 0)) <= now
        except Exception:
            lease = {"assignment_id": directory.name}
            expired = True
        if expired:
            lease["recovery_required"] = True
            lease["recovery_detected_at_epoch"] = now
            write_atomic(lease_path, lease)
            recovered.append(str(lease_path))
    return recovered


def claim(args: argparse.Namespace) -> int:
    now = int(time.time())
    control = load_control()
    if control.get("kill_switch") or control.get("mode") != "enabled":
        raise RuntimeError("supervisor is not enabled")
    if not control.get("allow_repository_mutation") and not args.read_only:
        raise RuntimeError("repository mutation is disabled")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.assignment_id):
        raise RuntimeError("invalid assignment_id")
    ttl = int(args.ttl or control.get("lease_seconds", 1200))
    if ttl <= 0:
        raise RuntimeError("lease TTL must be positive")
    resources = sorted(set(args.resource))
    allowlist = set(control.get("repository_allowlist") or [])
    parsed_resources = [RESOURCE_PATTERN.fullmatch(resource) for resource in resources]
    if (
        not resources
        or any(match is None for match in parsed_resources)
        or any(match.group(2) not in allowlist for match in parsed_resources if match)
    ):
        raise RuntimeError("lease resources must identify an allowlisted repository")

    acquire_controller(now)
    try:
        recover(now)
        leases = all_leases()
        maximum = int(control.get("max_active_assignments", 1))
        if len(leases) >= maximum:
            raise RuntimeError(f"active/recovery assignment limit reached: {len(leases)}/{maximum}")
        for existing in leases:
            overlap = sorted(set(resources) & set(existing.get("resources") or []))
            if overlap:
                raise RuntimeError(
                    f"resource conflict with {existing.get('assignment_id')}: {overlap}"
                )

        directory = LEASES / args.assignment_id
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        token = uuid.uuid4().hex
        lease = {
            "schema": "axis.external-development-supervisor.lease",
            "schema_version": "1.0.0",
            "assignment_id": args.assignment_id,
            "owner_run_id": args.run_id,
            "fencing_token": token,
            "resources": resources,
            "phase": args.phase,
            "read_only": bool(args.read_only),
            "acquired_at_epoch": now,
            "heartbeat_at_epoch": now,
            "expires_at_epoch": now + ttl,
        }
        write_atomic(directory / "lease.json", lease)
    finally:
        release_controller()
    print(json.dumps(lease, sort_keys=True))
    return 0


def heartbeat(args: argparse.Namespace) -> int:
    now = int(time.time())
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.assignment_id):
        raise RuntimeError("invalid assignment_id")
    acquire_controller(now)
    try:
        path = LEASES / args.assignment_id / "lease.json"
        lease = load(path)
        if lease.get("fencing_token") != args.token:
            raise RuntimeError("fencing token mismatch")
        if lease.get("recovery_required") or int(lease.get("expires_at_epoch", 0)) <= now:
            raise RuntimeError("expired or recovery-required lease cannot be renewed")
        ttl = int(args.ttl or load_control().get("lease_seconds", 1200))
        if ttl <= 0:
            raise RuntimeError("lease TTL must be positive")
        lease["heartbeat_at_epoch"] = now
        lease["expires_at_epoch"] = now + ttl
        write_atomic(path, lease)
    finally:
        release_controller()
    print(json.dumps(lease, sort_keys=True))
    return 0


def release(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.assignment_id):
        raise RuntimeError("invalid assignment_id")
    acquire_controller(int(time.time()))
    try:
        directory = LEASES / args.assignment_id
        lease = load(directory / "lease.json")
        if lease.get("fencing_token") != args.token:
            raise RuntimeError("fencing token mismatch")
        shutil.rmtree(directory)
    finally:
        release_controller()
    print(json.dumps({"released": args.assignment_id}, sort_keys=True))
    return 0


def recover_command(_args: argparse.Namespace) -> int:
    now = int(time.time())
    acquire_controller(now)
    try:
        recovered = recover(now)
    finally:
        release_controller()
    print(json.dumps({"recovered": recovered}, sort_keys=True))
    return 0


def main() -> int:
    LEASES.mkdir(mode=0o700, parents=True, exist_ok=True)
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    claim_parser = subparsers.add_parser("claim")
    claim_parser.add_argument("assignment_id")
    claim_parser.add_argument("--run-id", required=True)
    claim_parser.add_argument("--phase", default="implementation")
    claim_parser.add_argument("--resource", action="append", default=[], required=True)
    claim_parser.add_argument("--ttl", type=int)
    claim_parser.add_argument("--read-only", action="store_true")
    claim_parser.set_defaults(handler=claim)

    heartbeat_parser = subparsers.add_parser("heartbeat")
    heartbeat_parser.add_argument("assignment_id")
    heartbeat_parser.add_argument("--token", required=True)
    heartbeat_parser.add_argument("--ttl", type=int)
    heartbeat_parser.set_defaults(handler=heartbeat)

    release_parser = subparsers.add_parser("release")
    release_parser.add_argument("assignment_id")
    release_parser.add_argument("--token", required=True)
    release_parser.set_defaults(handler=release)

    recover_parser = subparsers.add_parser("recover")
    recover_parser.set_defaults(handler=recover_command)

    status_parser = subparsers.add_parser("status")
    status_parser.set_defaults(handler=lambda _args: (print(json.dumps({"leases": active_leases(int(time.time()))}, sort_keys=True)), 0)[1])

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
