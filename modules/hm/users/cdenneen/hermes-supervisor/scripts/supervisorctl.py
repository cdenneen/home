#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from axis_supervisor.schema_registry import read_record, write_record
from axis_supervisor.mutation import MutationGate, OperationClass
from axis_supervisor.lifecycle import (
    adapt_assignment,
    is_read_only_work,
    is_terminal,
    set_lifecycle,
)
from axis_supervisor.models import validate_assignment
from axis_supervisor.canary import CanaryDenied, validate_canary
from axis_supervisor.assignment_grants import AssignmentGrantDenied, validate_grant
from axis_supervisor.canonical_work_item import authority_lineage_for, projection_for
from axis_supervisor.finding_ingestion import normalize_gitlab_findings
from axis_supervisor.canonical_work_item import reconstruct_work_item

ROOT = Path(os.environ.get("AXIS_SUPERVISOR_ROOT", Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor"))
CONTROL = ROOT / "control.json"
LEASES = ROOT / "leases"
CONTROLLER_LOCK = LEASES / ".controller.lock"
CONTROLLER_OWNER = CONTROLLER_LOCK / "owner.json"
RESOURCE_PATTERN = re.compile(r"^(repo|path|branch|worktree):([^:]+/[^:]+)(?::.*)?$")


def load_control() -> dict:
    return read_record(CONTROL, "axis.external-development-supervisor.control")


def process_start_time(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21]
    except (OSError, IndexError):
        return None


def authorize_write() -> None:
    gate = MutationGate(ROOT, source="lease-controller")
    decision = gate.decide(OperationClass.RECONCILIATION)
    gate.require(decision, OperationClass.RECONCILIATION)


def acquire_controller(now: int) -> None:
    if CONTROLLER_LOCK.exists() and now - int(CONTROLLER_LOCK.stat().st_mtime) > 60:
        try:
            owner = json.loads(CONTROLLER_OWNER.read_text(encoding="utf-8"))
            owner_pid = int(owner.get("pid") or 0)
            if owner_pid <= 0:
                raise ValueError("invalid lock owner pid")
            os.kill(owner_pid, 0)
            if owner.get("process_start_time") != process_start_time(owner_pid):
                raise ValueError("lease controller pid was reused")
        except (OSError, ValueError, json.JSONDecodeError):
            shutil.rmtree(CONTROLLER_LOCK, ignore_errors=True)
        else:
            raise RuntimeError("lease controller lock owner is still alive")
    try:
        CONTROLLER_LOCK.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise RuntimeError("another lease operation is in progress") from exc
    CONTROLLER_OWNER.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "process_start_time": process_start_time(os.getpid()),
                "acquired_at_epoch": now,
            }
        ),
    )
    CONTROLLER_OWNER.chmod(0o600)


def release_controller() -> None:
    try:
        owner = json.loads(CONTROLLER_OWNER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if int(owner.get("pid") or 0) == os.getpid():
        shutil.rmtree(CONTROLLER_LOCK, ignore_errors=True)


def active_leases(now: int) -> list[dict]:
    values = []
    for path in LEASES.glob("*/lease.json"):
        lease = read_record(path, "axis.external-development-supervisor.lease")
        if int(lease.get("expires_at_epoch", 0)) > now:
            lease["path"] = str(path)
            values.append(lease)
    return values


def all_leases() -> list[dict]:
    values = []
    for path in LEASES.glob("*/lease.json"):
        if path.parent.name.startswith("stale-"):
            continue
        lease = read_record(path, "axis.external-development-supervisor.lease")
        lease["path"] = str(path)
        values.append(lease)
    return values


def recover(now: int) -> list[str]:
    authorize_write()
    recovered = []
    for directory in LEASES.iterdir() if LEASES.exists() else []:
        if not directory.is_dir() or directory.name.startswith(("stale-", ".")):
            continue
        lease_path = directory / "lease.json"
        try:
            lease = read_record(
                lease_path, "axis.external-development-supervisor.lease"
            )
            expired = int(lease.get("expires_at_epoch", 0)) <= now
        except Exception:
            expired = True
        if expired:
            stale = LEASES / f"stale-{now}-{directory.name}"
            suffix = 0
            while stale.exists():
                suffix += 1
                stale = LEASES / f"stale-{now}-{directory.name}-{suffix}"
            directory.rename(stale)
            recovered.append(str(stale / "lease.json"))
            assignment_path = ROOT / "assignments" / f"{directory.name}.json"
            if assignment_path.exists():
                assignment = validate_assignment(
                    adapt_assignment(
                        json.loads(assignment_path.read_text(encoding="utf-8")), ROOT
                    )
                )
                if not is_terminal(assignment):
                    set_lifecycle(assignment, "recovery-required")
                    assignment["lease_id"] = None
                    assignment["lease_uri"] = None
                    assignment["recovery_lease_uri"] = (stale / "lease.json").as_uri()
                    write_record(
                        assignment_path,
                        assignment,
                        "axis.external-development-supervisor.assignment",
                    )
    return recovered


def claim(args: argparse.Namespace) -> int:
    now = int(time.time())
    control = load_control()
    if control.get("kill_switch") or control.get("mode") != "enabled":
        raise RuntimeError("supervisor is not enabled")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.assignment_id):
        raise RuntimeError("invalid assignment_id")
    assignment_path = ROOT / "assignments" / f"{args.assignment_id}.json"
    if not assignment_path.is_file():
        raise RuntimeError("lease claim requires an existing assignment")
    assignment = validate_assignment(
        json.loads(assignment_path.read_text(encoding="utf-8")), ROOT
    )
    if is_terminal(assignment):
        raise RuntimeError("terminal assignment cannot acquire a lease")
    if assignment.get("created_by_run") != args.run_id:
        raise RuntimeError("lease owner run does not match assignment")
    if bool(args.read_only) != is_read_only_work(assignment):
        raise RuntimeError("lease read-only mode does not match assignment kind")
    if not args.read_only:
        authority_state = (assignment.get("authority") or {}).get("state")
        bounded_grant = False
        if assignment.get("assignment_type") == "capability-deployment":
            repository_convergence = read_record(
                ROOT / "repository-convergence.json",
                "axis.external-development-supervisor.repository-convergence",
            )
            capability_convergence = read_record(
                ROOT / "capability-convergence.json",
                "axis.external-development-supervisor.capability-convergence",
            )
            plan = assignment.get("deployment_plan") or {}
            expected = next(
                (
                    value
                    for value in capability_convergence.get(
                        "deployment_assignments"
                    )
                    or []
                    if value.get("assignment_id") == plan.get("assignment_id")
                ),
                None,
            )
            if repository_convergence.get("status") != "green" or expected != plan:
                raise RuntimeError(
                    "capability deployment is not authorized by current convergence state"
                )
            if args.resource != [f"runtime:{plan.get('target_runtime')}"]:
                raise RuntimeError("capability deployment runtime resource mismatch")
            bounded_grant = True
        elif authority_state == "canary":
            merged_mr = None
            if args.merged_mr_json:
                merged_mr = json.loads(args.merged_mr_json)
                if not isinstance(merged_mr, dict):
                    raise RuntimeError("merged MR recovery evidence must be an object")
            try:
                validate_canary(
                    ROOT,
                    assignment,
                    "repository-mutation",
                    assignment.get("project"),
                    merged_mr=merged_mr,
                )
            except CanaryDenied as exc:
                raise RuntimeError(str(exc)) from exc
        elif assignment.get("mutation_grant_id"):
            merged_mr = None
            if args.merged_mr_json:
                merged_mr = json.loads(args.merged_mr_json)
                if not isinstance(merged_mr, dict):
                    raise RuntimeError("merged MR recovery evidence must be an object")
            try:
                validate_grant(
                    ROOT,
                    assignment,
                    "repository-mutation",
                    assignment.get("project"),
                    effect="clone",
                    merged_mr=merged_mr,
                )
                bounded_grant = True
            except AssignmentGrantDenied as exc:
                raise RuntimeError(str(exc)) from exc
        elif authority_state not in {"direct", "inherited"}:
            raise RuntimeError("mutating lease requires direct or inherited authority")
        if (
            not control.get("allow_repository_mutation")
            and authority_state != "canary"
            and not bounded_grant
        ):
            raise RuntimeError("repository mutation is disabled")
        if assignment.get("governance_state") not in {"Executable", "Running"}:
            raise RuntimeError("mutating lease requires executable governance state")
    ttl = int(args.ttl or control.get("lease_seconds", 1200))
    if ttl <= 0:
        raise RuntimeError("lease TTL must be positive")
    resources = sorted(set(args.resource))
    if assignment.get("assignment_type") == "capability-deployment":
        expected_resource = f"runtime:{(assignment.get('deployment_plan') or {}).get('target_runtime')}"
        if resources != [expected_resource]:
            raise RuntimeError("deployment lease must identify the exact runtime")
    else:
        allowlist = set(control.get("repository_allowlist") or [])
        parsed_resources = [RESOURCE_PATTERN.fullmatch(resource) for resource in resources]
        if (
            not resources
            or any(match is None for match in parsed_resources)
            or any(match.group(2) not in allowlist for match in parsed_resources if match)
        ):
            raise RuntimeError("lease resources must identify an allowlisted repository")
        if any(
            match.group(2) != assignment.get("project")
            for match in parsed_resources
            if match
        ):
            raise RuntimeError("lease resources must match the assignment project")

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
            "lease_id": args.assignment_id,
            "assignment_id": args.assignment_id,
            "owner_run_id": args.run_id,
            "fencing_token": token,
            "resources": resources,
            "read_only": bool(args.read_only),
            "acquired_at_epoch": now,
            "heartbeat_at_epoch": now,
            "expires_at_epoch": now + ttl,
        }
        write_record(
            directory / "lease.json",
            lease,
            "axis.external-development-supervisor.lease",
        )
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
        lease = read_record(path, "axis.external-development-supervisor.lease")
        if lease.get("fencing_token") != args.token:
            raise RuntimeError("fencing token mismatch")
        if int(lease.get("expires_at_epoch", 0)) <= now:
            raise RuntimeError("expired or recovery-required lease cannot be renewed")
        ttl = int(args.ttl or load_control().get("lease_seconds", 1200))
        if ttl <= 0:
            raise RuntimeError("lease TTL must be positive")
        lease["heartbeat_at_epoch"] = now
        lease["expires_at_epoch"] = now + ttl
        authorize_write()
        write_record(path, lease, "axis.external-development-supervisor.lease")
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
        lease = read_record(
            directory / "lease.json", "axis.external-development-supervisor.lease"
        )
        if lease.get("fencing_token") != args.token:
            raise RuntimeError("fencing token mismatch")
        authorize_write()
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


def canonical_work_items(args: argparse.Namespace) -> int:
    """Read-only projection inspector for collection/restart/migration diagnostics."""
    inventory_path = Path(args.inventory or ROOT / "inventory.json")
    value = read_record(inventory_path, "axis.external-development-supervisor.inventory")
    items = []
    for item in value.get("work_items") or []:
        projection = projection_for(item)
        items.append(
            {
                "ref": item.get("ref"),
                "migration_state": "canonical" if item.get("canonical_work_item") else "n-1-legacy",
                "collection_complete_for_authority": projection.get("collection_complete_for_authority", False),
                "current_planning_record": projection.get("current_planning_record"),
                "slice_inventory": projection.get("slice_inventory", []),
                "authority_facts": projection.get("authority_facts", {}),
            }
        )
    print(json.dumps({"dry_run": True, "inventory": str(inventory_path), "work_items": items}, sort_keys=True))
    return 0


def authority_lineage_probe(_args: argparse.Namespace) -> int:
    """Print a no-write, production-shaped authority chain for axis#29."""
    digest = "sha256:" + "2" * 64
    source_sha = "a" * 40
    issue_url = "https://gitlab.test/ghostspace/axis/-/issues/29"
    def note(note_id: int, body: str) -> dict:
        return {"id": note_id, "author": {"id": 117046}, "created_at": "t", "updated_at": "t", "body": body}
    description = """Immutable PlanningRecord v1
Digest: `sha256:1111111111111111111111111111111111111111111111111111111111111111`
Assignment type: code-implementation
Repository: ghostspace/axis
Authorized slices:
- historical: src/retired.py
Required tests:
- pytest -q tests/test_retired.py"""
    record = f"""Immutable PlanningRecord v2
Digest: `{digest}`
Assignment type: code-implementation
Repository: ghostspace/axis
Authorized slices:
- repair: src/axis_runtime/mcp_tasks.py, tests/test_mcp_task_handles.py
Required tests:
- pytest -q tests/test_mcp_task_handles.py"""
    finding = f"""Current-main regression finding
Finding ID: task-handles-timeout
Affected tests:
- test_task_handles
Expected: task handles terminate.
Actual: task handles time out.
Classification: PRODUCT_DEFECT
Capability: MCP
Affected gates: current-main verification
Authority: bounded repair `{digest}`.
Replay: pytest -q tests/test_mcp_task_handles.py"""
    amendment = f"""Finding amendment v2
Finding ID: task-handles-timeout
Finding class: PRODUCT_DEFECT
Owner work item: ghostspace/axis#29
Approved slice_id: repair
PlanningRecord revision: 2
PlanningRecord digest: `{digest}`
Repository: ghostspace/axis
Affected gate: current-main verification
Affected tests:
- test_task_handles
Expected behavior: task handles terminate.
Observed behavior: task handles time out.
Source evidence: note 30 on current main.
Replay: pytest -q tests/test_mcp_task_handles.py
Scope: exact repair slice only.
Supersession: this metadata amendment preserves original finding provenance and supplies exact scope."""
    notes = [
        note(20, record),
        note(21, f"**Approve** PlanningRecord v2 {digest}"),
        note(30, finding),
        note(31, amendment),
    ]
    projection = reconstruct_work_item(description, notes, {117046}, notes_state="NOTES_OK", issue_url=issue_url)
    findings = normalize_gitlab_findings(notes, "ghostspace/axis#29", source_sha, {117046}, projection)
    candidate = (findings[0].get("repair_candidate") if findings else None) or {
        "slice_id": "repair",
        "allowed_paths": ["src/axis_runtime/mcp_tasks.py", "tests/test_mcp_task_handles.py"],
        "required_tests": ["pytest -q tests/test_mcp_task_handles.py"],
    }
    source = {"ref": "ghostspace/axis#29", "canonical_work_item": projection}
    lineage = authority_lineage_for(source, candidate)
    if lineage is None:
        raise RuntimeError("probe fixture did not produce canonical authority lineage")
    print(json.dumps({
        "dry_run": True,
        "axis": 29,
        "description_history": projection["description_history"],
        "authority_lineage": lineage,
        "chain": {
            "graph_candidate": candidate | {"authority_lineage": lineage},
            "frontier_entry": {"entry_id": "finding:axis29-task-handles-timeout", "authority_lineage": lineage},
            "scheduler_selection": {"source_ref": source["ref"], "authority_lineage": lineage},
            "assignment": {"planning_record": {"digest": lineage["record_digest"], "revision": lineage["record_revision"], "approval_note": lineage["approval_note"]}, "authority_lineage": lineage},
            "grant": {"approval_source": {"planning_digest": lineage["record_digest"], "authority_lineage": lineage}},
            "worker": {"required_authority_lineage": lineage},
            "handoff": {"authority_lineage": lineage},
        },
    }, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    claim_parser = subparsers.add_parser("claim")
    claim_parser.add_argument("assignment_id")
    claim_parser.add_argument("--run-id", required=True)
    claim_parser.add_argument("--resource", action="append", default=[], required=True)
    claim_parser.add_argument("--ttl", type=int)
    claim_parser.add_argument("--read-only", action="store_true")
    claim_parser.add_argument("--merged-mr-json")
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

    canonical_parser = subparsers.add_parser("canonical-work-items")
    canonical_parser.add_argument("--inventory")
    canonical_parser.set_defaults(handler=canonical_work_items)

    lineage_probe_parser = subparsers.add_parser("authority-lineage-probe")
    lineage_probe_parser.set_defaults(handler=authority_lineage_probe)

    status_parser = subparsers.add_parser("status")
    status_parser.set_defaults(handler=lambda _args: (print(json.dumps({"leases": active_leases(int(time.time()))}, sort_keys=True)), 0)[1])

    args = parser.parse_args()
    if args.command != "authority-lineage-probe":
        LEASES.mkdir(mode=0o700, parents=True, exist_ok=True)
    return args.handler(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
