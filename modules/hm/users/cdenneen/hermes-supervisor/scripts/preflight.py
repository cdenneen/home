#!/usr/bin/env python3
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from axis_supervisor.accounting import AccountingLedger
from axis_supervisor.lifecycle import is_terminal
from axis_supervisor.mutation import MutationGate, OperationClass
from axis_supervisor.missions import (
    has_runnable_action,
    mission_summary,
    read_mission_record,
)
from axis_supervisor.observability import record_event
from axis_supervisor.schema_registry import read_record, write_record

ROOT = Path(os.environ.get("AXIS_SUPERVISOR_ROOT", Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor"))
CONTROL = ROOT / "control.json"
RUNS = ROOT / "runs"
ASSIGNMENTS = ROOT / "assignments"
RECONCILE = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_RECONCILE",
        Path.home() / ".hermes" / "scripts" / "axis-development-supervisor-reconcile.py",
    )
)
SUPERVISORCTL = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_CTL",
        Path.home() / ".hermes" / "scripts" / "axis-development-supervisorctl.py",
    )
)
CYCLE = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_CYCLE",
        Path.home() / ".hermes" / "scripts" / "axis_supervisor" / "cycle.py",
    )
)
INVENTORY_LOCK = ROOT / "inventory.lock"
INVENTORY_LOCK_OWNER = INVENTORY_LOCK / "owner.json"


def load_control() -> dict:
    return read_record(CONTROL, "axis.external-development-supervisor.control")


def emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def process_start_time(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21]
    except (OSError, IndexError):
        return None


def process_alive(pid: int, expected_start_time: str | None = None) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return expected_start_time in {None, process_start_time(pid)}
    except OSError:
        return False


def child_diagnostic(exc: BaseException) -> str:
    output = []
    for name in ("stdout", "stderr", "output"):
        value = getattr(exc, name, None)
        if value:
            if isinstance(value, bytes):
                value = value.decode(errors="replace")
            output.append(f"{name}: {value}")
    return "\n".join(output)[-1200:]


def skip(
    run_id: str,
    mode: str | None,
    reason: str,
    *,
    diagnostic: str | None = None,
) -> int:
    diagnostic_id = uuid.uuid4().hex
    details = {
        "run_id": run_id,
        "mode": mode,
        "reason": reason,
        "diagnostic_id": diagnostic_id,
        "diagnostic": diagnostic,
    }
    record_event(ROOT, "preflight_skip", details=details, source="preflight", notify=False)
    emit({
        "wakeAgent": False,
        "run_id": run_id,
        "skip_agent": True,
        "reason": reason,
        "mode": mode,
        "diagnostic_id": diagnostic_id,
        "diagnostic": diagnostic,
    })
    return 0


def reconcile_prior_runs(control: dict, now: int, gate: MutationGate) -> None:
    job_id = str(control.get("cron_job_id") or "").strip()
    output_dir = Path.home() / ".hermes" / "cron" / "output" / job_id
    outputs = list(output_dir.glob("*.md")) if job_id else []

    for run_path in RUNS.glob("*.json"):
        try:
            record = read_record(
                run_path, "axis.external-development-supervisor.run"
            )
        except Exception:
            record = json.loads(run_path.read_text(encoding="utf-8"))
            if record.get("schema") or record.get("status") != "started":
                continue
            record = {
                "schema": "axis.external-development-supervisor.run",
                "schema_version": "1.0.0",
                "run_id": str(record.get("run_id") or run_path.stem),
                "status": "started",
                "host": str(record.get("host") or "legacy"),
                "started_at_epoch": int(record.get("started_at_epoch") or 1),
                "mode": str(record.get("mode") or "observing"),
                "allow_repository_mutation": bool(
                    record.get("allow_repository_mutation")
                ),
                "inventory_generation_id": record.get("inventory_generation_id"),
                "model_calls_remaining": int(record.get("model_calls_remaining") or 0),
            }
        if record.get("status") != "started":
            continue

        run_id = str(record.get("run_id") or "")
        matched = None
        for output in reversed(outputs):
            try:
                if run_id and run_id in output.read_text(encoding="utf-8"):
                    matched = output
                    break
            except OSError:
                continue

        if matched is not None:
            record["status"] = "completed"
            record["completion_output"] = str(matched)
            record["reconciled_at_epoch"] = now
        elif now - int(record.get("started_at_epoch", now)) > 1800:
            record["status"] = "abandoned"
            record["reconciled_at_epoch"] = now
        else:
            continue

        decision = gate.decide(OperationClass.RECONCILIATION)
        gate.require(decision, OperationClass.RECONCILIATION)
        write_record(
            run_path, record, "axis.external-development-supervisor.run"
        )


def main() -> int:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    RUNS.mkdir(mode=0o700, parents=True, exist_ok=True)
    ASSIGNMENTS.mkdir(mode=0o700, parents=True, exist_ok=True)
    control = load_control()
    gate = MutationGate(ROOT, source="preflight")
    accounting = AccountingLedger(ROOT)
    now = int(time.time())
    run_id = f"axis-supervisor-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    tool_fallbacks = {
        "ssh": "/run/current-system/sw/bin/ssh",
    }
    tool_paths = {
        name: shutil.which(name)
        or tool_fallbacks.get(name)
        or f"/etc/profiles/per-user/cdenneen/bin/{name}"
        for name in ("bash", "git", "ssh", "glab", "python3", "uv", "jq")
    }

    mode = str(control.get("mode") or "disabled")
    if control.get("kill_switch") or mode in {"paused", "disabled", "stopped", "decommissioned"}:
        return skip(run_id, control.get("mode"), "supervisor is paused or stopped")

    free_gib = shutil.disk_usage(ROOT).free // (1024 ** 3)
    minimum = int(control.get("minimum_free_disk_gib", 15))
    if free_gib < minimum:
        return skip(
            run_id,
            control.get("mode"),
            f"disk guard: {free_gib} GiB free, {minimum} GiB required",
        )

    daily_limit = int(control.get("daily_worker_cycle_limit", 24))
    cycles_today = accounting.worker_cycles_today(now)
    model_limit = int(control.get("daily_model_call_limit", daily_limit))
    calls_today = accounting.model_attempts_today(now)

    reconcile_prior_runs(control, now, gate)
    try:
        subprocess.run(
            [sys.executable, str(SUPERVISORCTL), "recover"],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        )
    except Exception as exc:
        diagnostic = child_diagnostic(exc)
        return skip(
            run_id,
            control.get("mode"),
            f"supervisor recovery failed closed: {type(exc).__name__}: {exc}; {diagnostic}"[-1800:],
            diagnostic=diagnostic,
        )
    if INVENTORY_LOCK.exists() and now - int(INVENTORY_LOCK.stat().st_mtime) > 300:
        try:
            owner = json.loads(INVENTORY_LOCK_OWNER.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            owner = {}
        if process_alive(
            int(owner.get("pid") or 0), owner.get("process_start_time")
        ):
            return skip(run_id, mode, "inventory lock owner is still alive")
        gate.require(
            gate.decide(OperationClass.RECONCILIATION),
            OperationClass.RECONCILIATION,
        )
        stale_lock = ROOT / f"inventory.lock.stale.{now}"
        INVENTORY_LOCK.rename(stale_lock)
    try:
        gate.require(
            gate.decide(OperationClass.RECONCILIATION),
            OperationClass.RECONCILIATION,
        )
        INVENTORY_LOCK.mkdir(mode=0o700)
        INVENTORY_LOCK_OWNER.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "process_start_time": process_start_time(os.getpid()),
                    "run_id": run_id,
                    "acquired_at_epoch": now,
                }
            ),
            encoding="utf-8",
        )
        INVENTORY_LOCK_OWNER.chmod(0o600)
    except FileExistsError:
        return skip(run_id, control.get("mode"), "inventory generation already in progress")
    try:
        subprocess.run(
            [sys.executable, str(RECONCILE)],
            check=True,
            text=True,
            capture_output=True,
            timeout=240,
        )
        subprocess.run(
            [sys.executable, str(CYCLE), "rebuild"],
            check=True,
            text=True,
            capture_output=True,
            timeout=60,
        )
    except Exception as exc:
        diagnostic = child_diagnostic(exc)
        return skip(
            run_id,
            control.get("mode"),
            f"live reconciliation failed closed: {type(exc).__name__}: {exc}; {diagnostic}"[-1800:],
            diagnostic=diagnostic,
        )
    finally:
        try:
            owner = json.loads(INVENTORY_LOCK_OWNER.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            owner = {}
        if int(owner.get("pid") or 0) == os.getpid():
            shutil.rmtree(INVENTORY_LOCK, ignore_errors=True)

    inventory = read_record(
        ROOT / "inventory.json", "axis.external-development-supervisor.inventory"
    )
    execution_graph = read_record(
        ROOT / "execution-graph.json",
        "axis.external-development-supervisor.execution-graph",
    )
    active_mission = read_mission_record(ROOT / "active-mission.json")
    record_event(
        ROOT,
        "reconciliation_completed",
        details={
            "run_id": run_id,
            "mode": mode,
            "inventory_generation_id": inventory.get("generation_id"),
            "graph_generation_id": execution_graph.get("generation_id"),
            "queue_depth": execution_graph.get("queue_depth"),
            "selected_batch": (execution_graph.get("scheduler_state") or {}).get(
                "selected_batch"
            )
            or [],
            "expected_next_phase": "observing-status"
            if mode == "observing"
            else "semantic-supervisor-cycle",
        },
        source="preflight",
        notify=False,
    )
    collection = inventory.get("collection_status") or {}
    if not collection.get("all_configured_repositories_inspected"):
        return skip(run_id, mode, "configured repository discovery is incomplete")
    if int(collection.get("retrieval_error_count", 1)) != 0:
        return skip(run_id, mode, "source retrieval is incomplete")
    if int(collection.get("stale_repository_count", 1)) != 0:
        return skip(run_id, mode, "local repository refs are stale relative to origin")
    if int(collection.get("dependency_query_failures", 1)) != 0:
        return skip(run_id, mode, "dependency retrieval is incomplete")
    active_assignments = [
        item
        for item in (inventory.get("supervisor_assignments") or [])
        if not is_terminal(item)
    ]
    if mode == "observing":
        return skip(run_id, mode, "observing mode performs reconciliation without model execution")
    if mode == "draining" and not active_assignments:
        return skip(run_id, mode, "draining mode has no active assignment")
    if cycles_today >= daily_limit:
        return skip(
            run_id,
            control.get("mode"),
            f"daily worker cycle limit reached: {cycles_today}/{daily_limit}",
        )
    selected_batch = (execution_graph.get("scheduler_state") or {}).get(
        "selected_batch"
    ) or []
    model_work_selected = any(
        item.get("kind") != "repository-convergence" for item in selected_batch
    )
    if calls_today >= model_limit and model_work_selected:
        return skip(
            run_id,
            control.get("mode"),
            f"daily model call limit reached: {calls_today}/{model_limit}",
        )
    termination = active_mission.get("termination_condition") or {}
    if termination.get("should_terminate"):
        return skip(
            run_id,
            control.get("mode"),
            f"mission terminated: {termination.get('reason')}",
        )
    if (
        not execution_graph.get("executable_queue")
        and not active_assignments
        and not has_runnable_action(active_mission)
    ):
        return skip(
            run_id,
            control.get("mode"),
            "mission remains active with no currently executable bounded action",
        )

    run_record = {
        "schema": "axis.external-development-supervisor.run",
        "schema_version": "1.0.0",
        "run_id": run_id,
        "status": "started",
        "host": socket.gethostname(),
        "started_at_epoch": now,
        "mode": control.get("mode"),
        "allow_repository_mutation": bool(control.get("allow_repository_mutation")),
        "inventory_generation_id": inventory.get("generation_id"),
        "model_calls_remaining": max(0, model_limit - calls_today),
    }
    run_path = RUNS / f"{run_id}.json"
    gate.require(
        gate.decide(OperationClass.RECONCILIATION),
        OperationClass.RECONCILIATION,
    )
    write_record(run_path, run_record, "axis.external-development-supervisor.run")

    emit({
        "run_id": run_id,
        "wakeAgent": True,
        "skip_agent": False,
        "mode": control.get("mode"),
        "allow_repository_mutation": bool(control.get("allow_repository_mutation")),
        "tool_paths": tool_paths,
        "supervisorctl": str(SUPERVISORCTL),
        "control_path": str(CONTROL),
        "inventory_path": str(ROOT / "inventory.json"),
        "inventory_summary": {
            "repositories_inspected": inventory.get("repositories_inspected"),
            "work_items_discovered": inventory.get("work_items_discovered"),
            "collection_status": collection,
            "reconcile_error": None,
        },
        "execution_graph_summary": {
            "generation_id": execution_graph.get("generation_id"),
            "queue_depth": execution_graph.get("queue_depth"),
            "semantic_decomposition_pending": execution_graph.get(
                "semantic_decomposition_pending"
            ),
            "governed_queue_zero_proven": execution_graph.get(
                "governed_queue_zero_proven"
            ),
            "classification_counts": execution_graph.get("classification_counts"),
            "waiting_reason_counts": execution_graph.get("waiting_reason_counts"),
            "scheduler_state": execution_graph.get("scheduler_state"),
            "top_executable": (execution_graph.get("executable_queue") or [])[:5],
        },
        "active_mission": mission_summary(active_mission),
        "cycle_command": [
            sys.executable,
            str(CYCLE),
            "run-next",
            "--run-id",
            run_id,
            "--hermes",
            shutil.which("hermes") or "/home/cdenneen/.nix-profile/bin/hermes",
            "--supervisorctl",
            str(SUPERVISORCTL),
        ],
        "runs_path": str(RUNS),
        "run_record": str(run_path),
        "instruction": "Run one bounded cycle using axis-development-supervisor. Hermes cron owns singleton execution and durable completion output.",
    })
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        diagnostic = child_diagnostic(exc)
        try:
            skip(
                f"axis-supervisor-failure-{uuid.uuid4().hex[:8]}",
                None,
                f"preflight failure: {type(exc).__name__}: {exc}; {diagnostic}"[-1800:],
                diagnostic=diagnostic,
            )
        except Exception:
            emit({
                "wakeAgent": False,
                "skip_agent": True,
                "reason": f"preflight failure: {type(exc).__name__}: {exc}",
                "diagnostic": diagnostic,
            })
        raise SystemExit(0)
