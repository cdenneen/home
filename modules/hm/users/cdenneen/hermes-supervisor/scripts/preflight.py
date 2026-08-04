#!/usr/bin/env python3
import hashlib
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
from urllib.parse import quote

ROOT = Path(os.environ.get("AXIS_SUPERVISOR_ROOT", Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor"))
CONTROL = ROOT / "control.json"
BASELINE = ROOT / "baseline.json"
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
CYCLE = Path.home() / ".hermes" / "scripts" / "axis_supervisor" / "cycle.py"
INVENTORY_LOCK = ROOT / "inventory.lock"


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def load_control() -> dict:
    value = load(CONTROL)
    if value.get("schema") != "axis.external-development-supervisor.control":
        raise ValueError("unsupported control schema")
    if value.get("schema_version") != "1.0.0":
        raise ValueError("unsupported control schema_version")
    return value


def emit(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def skip(run_id: str, mode: str | None, reason: str) -> int:
    emit({
        "wakeAgent": False,
        "run_id": run_id,
        "skip_agent": True,
        "reason": reason,
        "mode": mode,
    })
    return 0


def model_calls_today(now: int) -> int:
    day = datetime.fromtimestamp(now, timezone.utc).date()
    count = 0
    for path in RUNS.glob("*.json"):
        try:
            record = load(path)
            started = int(record.get("started_at_epoch", 0))
            status = str(record.get("status") or "")
        except Exception:
            continue
        if status == "preflight-test":
            continue
        if datetime.fromtimestamp(started, timezone.utc).date() == day:
            count += 1
    return count


def reconcile_prior_runs(control: dict, now: int) -> None:
    job_id = str(control.get("cron_job_id") or "").strip()
    output_dir = Path.home() / ".hermes" / "cron" / "output" / job_id
    outputs = list(output_dir.glob("*.md")) if job_id else []

    for run_path in RUNS.glob("*.json"):
        try:
            record = load(run_path)
        except Exception:
            continue
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

        tmp = run_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(run_path)


def reconcile_proof_assignment(
    assignment_path: Path, assignment: dict, control: dict, now: int
) -> dict:
    if assignment.get("phase") != "implementation":
        return assignment

    project = str(assignment.get("project") or "")
    branch = str(assignment.get("branch") or "")
    assignment_id = str(assignment.get("assignment_id") or "")
    if not project or not branch or not assignment_id:
        return assignment

    try:
        raw = subprocess.check_output(
            [
                "glab",
                "api",
                "--hostname",
                "gitlab.com",
                f"projects/{quote(project, safe='')}/merge_requests?state=opened&source_branch={quote(branch, safe='')}",
            ],
            text=True,
            timeout=30,
        )
        merge_requests = json.loads(raw)
    except Exception:
        return assignment
    if not isinstance(merge_requests, list) or not merge_requests:
        return assignment

    mr = merge_requests[0]
    job_id = str(control.get("cron_job_id") or "").strip()
    outputs = sorted(
        (Path.home() / ".hermes" / "cron" / "output" / job_id).glob("*.md"),
        reverse=True,
    )
    handoff = None
    implementation_run_id = None
    for output in outputs:
        try:
            text = output.read_text(encoding="utf-8")
        except OSError:
            continue
        if assignment_id not in text or "implemented-awaiting-integration" not in text:
            continue
        handoff = str(output)
        for line in text.splitlines():
            if line.startswith("Run: axis-supervisor-"):
                implementation_run_id = line.split("Run:", 1)[1].split("|", 1)[0].strip()
                break
        break
    if handoff is None:
        return assignment

    head_pipeline = mr.get("head_pipeline") or {}
    assignment["state"] = "active"
    assignment["phase"] = "awaiting-integration"
    assignment["branch_sha"] = mr.get("sha")
    assignment["merge_request"] = {
        "iid": mr.get("iid"),
        "url": mr.get("web_url"),
        "state": mr.get("state"),
    }
    assignment["pipeline"] = {
        "id": head_pipeline.get("id"),
        "status": head_pipeline.get("status"),
        "url": head_pipeline.get("web_url"),
    }
    assignment["lease"] = {
        "owner": implementation_run_id,
        "phase": "implementation-complete",
        "reconciled_at_epoch": now,
    }
    assignment["handoff"] = handoff
    assignment.setdefault("evidence", []).append(
        {
            "kind": "implementation-handoff",
            "ref": handoff,
            "recorded_at_epoch": now,
        }
    )

    tmp = assignment_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(assignment, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(assignment_path)
    return assignment


def main() -> int:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    RUNS.mkdir(mode=0o700, parents=True, exist_ok=True)
    ASSIGNMENTS.mkdir(mode=0o700, parents=True, exist_ok=True)
    control = load_control()
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
    calls_today = model_calls_today(now)
    if calls_today >= daily_limit:
        return skip(
            run_id,
            control.get("mode"),
            f"daily model call limit reached: {calls_today}/{daily_limit}",
        )

    reconcile_prior_runs(control, now)
    subprocess.run(
        [sys.executable, str(SUPERVISORCTL), "recover"],
        check=True,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if INVENTORY_LOCK.exists() and now - int(INVENTORY_LOCK.stat().st_mtime) > 300:
        stale_lock = ROOT / f"inventory.lock.stale.{now}"
        INVENTORY_LOCK.rename(stale_lock)
    try:
        INVENTORY_LOCK.mkdir(mode=0o700)
    except FileExistsError:
        return skip(run_id, control.get("mode"), "inventory generation already in progress")
    try:
        (INVENTORY_LOCK / "owner.json").write_text(
            json.dumps({"run_id": run_id, "created_at_epoch": now}) + "\n",
            encoding="utf-8",
        )
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
        return skip(
            run_id,
            control.get("mode"),
            f"live reconciliation failed closed: {type(exc).__name__}: {exc}",
        )
    finally:
        shutil.rmtree(INVENTORY_LOCK, ignore_errors=True)

    baseline = load(BASELINE)
    if baseline.get("schema") != "axis.external-development-supervisor.baseline":
        raise ValueError("unsupported baseline schema")
    if baseline.get("schema_version") != "1.0.0":
        raise ValueError("unsupported baseline schema_version")
    contract_path = ROOT / "docs" / "SUPERVISOR_CONTRACT.md"
    contract_sha256 = (
        hashlib.sha256(contract_path.read_bytes()).hexdigest()
        if contract_path.exists()
        else None
    )

    proof_assignment_id = str(control.get("proof_assignment_id") or "").strip()
    assignment_path = ASSIGNMENTS / f"{proof_assignment_id}.json" if proof_assignment_id else None
    assignment = load(assignment_path) if assignment_path and assignment_path.exists() else None
    if assignment_path is not None and assignment is not None:
        assignment = reconcile_proof_assignment(assignment_path, assignment, control, now)
    inventory = load(ROOT / "inventory.json")
    execution_graph = load(ROOT / "execution-graph.json")
    idle_proof = inventory.get("idle_proof") or {}
    if not idle_proof.get("all_configured_repositories_inspected"):
        return skip(run_id, mode, "configured repository discovery is incomplete")
    if int(idle_proof.get("unknown_count", 1)) != 0:
        return skip(run_id, mode, "inventory contains Unknown classifications")
    if int(idle_proof.get("dependency_query_failures", 1)) != 0:
        return skip(run_id, mode, "dependency retrieval is incomplete")
    active_assignments = [
        item
        for item in (inventory.get("supervisor_assignments") or [])
        if item.get("state") not in {"complete", "completed", "cancelled", "failed"}
    ]
    if mode == "observing":
        return skip(run_id, mode, "observing mode performs reconciliation without model execution")
    if mode == "draining" and not active_assignments:
        return skip(run_id, mode, "draining mode has no active assignment")
    if not execution_graph.get("executable_queue") and not active_assignments:
        return skip(run_id, control.get("mode"), "no executable or active assignment after fresh reconciliation")

    run_record = {
        "run_id": run_id,
        "status": "started",
        "host": socket.gethostname(),
        "started_at_epoch": now,
        "mode": control.get("mode"),
        "allow_repository_mutation": bool(control.get("allow_repository_mutation")),
        "baseline_converged": bool(baseline.get("converged")),
        "proof_assignment_id": proof_assignment_id or None,
        "inventory_generation_id": inventory.get("generation_id"),
        "contract_sha256": contract_sha256,
    }
    run_path = RUNS / f"{run_id}.json"
    run_tmp = run_path.with_suffix(".json.tmp")
    run_tmp.write_text(json.dumps(run_record, indent=2) + "\n", encoding="utf-8")
    run_tmp.chmod(0o600)
    run_tmp.replace(run_path)

    emit({
        "run_id": run_id,
        "wakeAgent": True,
        "skip_agent": False,
        "mode": control.get("mode"),
        "allow_repository_mutation": bool(control.get("allow_repository_mutation")),
        "baseline_converged": bool(baseline.get("converged")),
        "baseline": baseline,
        "proof_assignment": assignment,
        "tool_paths": tool_paths,
        "supervisorctl": str(SUPERVISORCTL),
        "control_path": str(CONTROL),
        "contract_sha256": contract_sha256,
        "inventory_path": str(ROOT / "inventory.json"),
        "inventory_summary": {
            "repositories_inspected": inventory.get("repositories_inspected"),
            "work_items_discovered": inventory.get("work_items_discovered"),
            "classification_counts": inventory.get("classification_counts"),
            "waiting_reason_counts": inventory.get("waiting_reason_counts"),
            "queue_depth": inventory.get("queue_depth"),
            "top_executable": (inventory.get("executable_queue") or [])[:10],
            "invariant": inventory.get("invariant"),
            "idle_proof": inventory.get("idle_proof"),
            "roadmap_confidence": inventory.get("roadmap_confidence"),
            "activity_timeline": inventory.get("activity_timeline"),
            "reconcile_error": None,
        },
        "execution_graph_summary": {
            "generation_id": execution_graph.get("generation_id"),
            "queue_depth": execution_graph.get("queue_depth"),
            "semantic_decomposition_pending": execution_graph.get(
                "semantic_decomposition_pending"
            ),
            "classifier_queue_empty": execution_graph.get("classifier_queue_empty"),
            "governed_queue_zero_proven": execution_graph.get(
                "governed_queue_zero_proven"
            ),
            "top_executable": (execution_graph.get("executable_queue") or [])[:5],
        },
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
        emit({
            "wakeAgent": False,
            "skip_agent": True,
            "reason": f"preflight failure: {type(exc).__name__}: {exc}",
        })
        raise SystemExit(0)
