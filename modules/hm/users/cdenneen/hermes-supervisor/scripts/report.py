#!/usr/bin/env python3
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("AXIS_SUPERVISOR_ROOT", Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor"))
INVENTORY = ROOT / "inventory.json"
CONTROL = ROOT / "control.json"
STATE = ROOT / "report-delivery-state.json"
PENDING = ROOT / "report-delivery-pending.json"
INVENTORY_LOCK = ROOT / "inventory.lock"
CRON_JOBS = Path(os.environ.get("AXIS_SUPERVISOR_CRON_JOBS", Path.home() / ".hermes" / "cron" / "jobs.json"))


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def grouped_blockers(items: list[dict]) -> dict[str, list[str]]:
    groups = {"Human": [], "Governance": [], "Technical": [], "External/Infrastructure": []}
    for item in items:
        if item.get("classification") != "Blocked":
            continue
        blocker = str(item.get("blocker_type") or "technical").lower()
        summary = f"{item.get('ref')}: {item.get('classification_rationale')}"
        if blocker in {"approval"}:
            groups["Human"].append(summary)
        elif blocker in {"governance"}:
            groups["Governance"].append(summary)
        elif blocker in {"external", "infrastructure", "provider", "budget"}:
            groups["External/Infrastructure"].append(summary)
        else:
            groups["Technical"].append(summary)
    return groups


def humanize_timeline(value: str) -> str:
    value = re.sub(r"^axis-supervisor-[^:]+:\s*", "", value).strip()
    if not value:
        return "I refreshed the execution inventory."
    if value[0].islower():
        value = "I " + value
    return value.rstrip(".") + "."


def main() -> int:
    if INVENTORY_LOCK.exists():
        print("[SILENT]")
        return 0
    inventory_bytes = INVENTORY.read_bytes()
    inventory = json.loads(inventory_bytes)
    if not isinstance(inventory, dict):
        raise ValueError("inventory must be an object")
    if inventory.get("schema") != "axis.external-development-supervisor.inventory":
        raise ValueError("unsupported inventory schema")
    if inventory.get("schema_version") != "1.0.0":
        raise ValueError("unsupported inventory schema_version")
    inventory_sha256 = hashlib.sha256(inventory_bytes).hexdigest()
    control = load(CONTROL)
    if control.get("schema") != "axis.external-development-supervisor.control":
        raise ValueError("unsupported control schema")
    if control.get("schema_version") != "1.0.0":
        raise ValueError("unsupported control schema_version")
    counts = inventory.get("classification_counts") or {}
    waiting = inventory.get("waiting_reason_counts") or {}
    queue = inventory.get("executable_queue") or []
    timeline = inventory.get("activity_timeline") or []
    confidence = inventory.get("roadmap_confidence") or {}
    idle = inventory.get("idle_proof") or {}
    assignments = inventory.get("supervisor_assignments") or []
    leases = inventory.get("active_leases") or []
    open_mrs = inventory.get("open_merge_requests") or []
    if int(inventory.get("queue_depth", -1)) != len(queue):
        raise ValueError("inventory queue_depth does not match executable_queue length")
    if int(counts.get("Executable", -1)) != len(queue):
        raise ValueError("Executable classification count does not match queue length")
    if inventory.get("invariant", {}).get("unknown_count", 0) != counts.get("Unknown", 0) + waiting.get("Unknown", 0):
        raise ValueError("inventory Unknown counts are inconsistent")

    fingerprint_payload = {
        "counts": counts,
        "waiting": waiting,
        "queue": [item.get("ref") for item in queue],
        "blockers": sorted(
            f"{item.get('ref')}:{item.get('blocker_type')}:{item.get('classification_rationale')}"
            for item in (inventory.get("work_items") or [])
            if item.get("classification") == "Blocked"
        ),
        "open_mrs": [
            f"{item.get('project')}!{item.get('iid')}:{item.get('state')}:{item.get('merge_status')}"
            for item in open_mrs
        ],
        "repositories": sorted(
            f"{name}:{(repo.get('local') or {}).get('head')}:{(repo.get('local') or {}).get('dirty')}"
            for name, repo in (inventory.get("repositories") or {}).items()
        ),
        "active_assignments": [item.get("assignment_id") for item in assignments if item.get("state") not in {"complete", "completed", "cancelled"}],
        "completed_assignments": sorted(
            str(item.get("assignment_id"))
            for item in assignments
            if item.get("state") in {"complete", "completed"}
        ),
        "leases": sorted(
            f"{item.get('assignment_id')}:{item.get('phase')}:{item.get('expires_at_epoch')}"
            for item in leases
        ),
        "idle_proof": idle,
        "confidence": confidence.get("percent"),
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode()).hexdigest()
    try:
        state = load(STATE)
    except Exception:
        state = {"version": 1, "reported_completed_assignment_ids": []}
    try:
        jobs = load(CRON_JOBS)
    except Exception:
        jobs = {"jobs": []}
    report_job_id = str(control.get("report_cron_job_id") or "")
    report_job = next(
        (item for item in jobs.get("jobs", []) if str(item.get("id")) == report_job_id),
        {},
    )
    try:
        pending = load(PENDING)
    except Exception:
        pending = None
    if pending is not None:
        completed = int((report_job.get("repeat") or {}).get("completed") or 0)
        if completed > int(pending.get("completed_before", completed)):
            if report_job.get("last_status") == "ok" and not report_job.get("last_delivery_error"):
                state = {
                    "version": 1,
                    "last_fingerprint": pending.get("fingerprint"),
                    "last_report_at_epoch": pending.get("generated_at_epoch"),
                    "last_report_id": pending.get("report_id"),
                    "reported_completed_assignment_ids": pending.get(
                        "reported_completed_assignment_ids", []
                    ),
                }
                tmp = STATE.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
                tmp.chmod(0o600)
                tmp.replace(STATE)
                PENDING.unlink(missing_ok=True)
            else:
                output_dir = Path.home() / ".hermes" / "cron" / "output" / report_job_id
                pending_report_id = str(pending.get("report_id") or "")
                for output_path in sorted(output_dir.glob("*.md"), reverse=True):
                    text = output_path.read_text(encoding="utf-8")
                    if pending_report_id not in text:
                        continue
                    print(text.split("---\n", 1)[-1].strip())
                    return 0
                raise RuntimeError("failed report delivery has no recoverable output record")
    now = int(time.time())
    run_id = f"axis-supervisor-report-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    heartbeat = int(control.get("report_heartbeat_minutes", 90)) * 60
    changed = fingerprint != state.get("last_fingerprint")
    heartbeat_due = now - int(state.get("last_report_at_epoch", 0)) >= heartbeat
    if not changed and not heartbeat_due:
        print("[SILENT]")
        return 0

    blockers = grouped_blockers(inventory.get("work_items") or [])
    need_po = bool(blockers["Human"])
    top = queue[:3]
    current_focus = top[0] if top else None
    completed_assignments = [item for item in assignments if item.get("state") in {"complete", "completed"}]
    previously_reported = set(state.get("reported_completed_assignment_ids") or [])
    newly_completed = [
        item for item in completed_assignments if item.get("assignment_id") not in previously_reported
    ]

    human_timeline = [humanize_timeline(item) for item in timeline]
    if human_timeline:
        summary_change = human_timeline[0]
    else:
        summary_change = "I rebuilt the complete execution inventory."
    if current_focus:
        summary = f"{summary_change} I am continuing with {current_focus['title']}."
    else:
        summary = f"{summary_change} No governed executable item remains after full classification and decomposition review."

    print("🟢 AXIS Development Supervisor\n")
    print("Summary")
    print(summary + "\n")

    print("Since Last Update")
    for item in human_timeline[:5]:
        print(f"- {item}")
    print()

    print("✅ Completed")
    if newly_completed:
        for item in newly_completed[-3:]:
            mr = (item.get("merge_request") or {}).get("url")
            print(f"- {item.get('work_item') or item.get('assignment_id')} integrated" + (f" ({mr})" if mr else ""))
    else:
        print("- No new implementation completion since the prior briefing.")
    print()

    print("🚧 Current Focus")
    if current_focus:
        print(f"- {current_focus['title']}")
    else:
        print("- Whole-ecosystem reconciliation, decomposition review, and blocker monitoring.")
    print()

    print("Why This Work")
    print(current_focus.get("next_action") if current_focus else "The queue-zero invariant is currently satisfied; scheduled discovery continues.")
    print()

    print("⚠ Blockers")
    for group in ("Human", "Governance", "Technical", "External/Infrastructure"):
        values = blockers[group]
        print(f"{group}: " + ("; ".join(values[:3]) if values else "None"))
    print()

    print("Need Product Owner?")
    print("YES" if need_po else "NO")
    if need_po:
        print("Exact approval or governance corrections are listed above. Other executable work continues independently.")
    else:
        print("I will continue automatically.")
    print()

    print("➡ Next")
    if top:
        for index, item in enumerate(top, 1):
            print(f"{index}. {item['title']}")
    else:
        print("1. Rebuild the execution graph on the next scheduled cycle.")
        print("2. Re-evaluate Waiting items for newly executable child slices.")
        print("3. Continue repository convergence where provenance is sufficient.")
    print()

    print("📈 Roadmap Progress")
    print(f"Repositories: {inventory.get('repositories_inspected', 0)}")
    print(f"Discovered: {inventory.get('work_items_discovered', 0)}")
    print(
        f"Executable: {counts.get('Executable', 0)} | Running: {counts.get('Running', 0)} | "
        f"Blocked: {counts.get('Blocked', 0)} | Waiting: {counts.get('Waiting', 0)}"
    )
    print(
        f"Integrated: {counts.get('Integrated', 0)} | Completed: {counts.get('Completed', 0)} | "
        f"Superseded: {counts.get('Superseded', 0)} | Invalid: {counts.get('Invalid', 0)} | "
        f"Unknown: {counts.get('Unknown', 0)}"
    )
    print(f"Queue depth: {inventory.get('queue_depth', 0)}")
    active_assignments = [item for item in assignments if item.get("state") not in {"complete", "completed", "cancelled"}]
    print(f"Assignments: {len(active_assignments)} active | Leases: {len(leases)}")
    print()

    print("Waiting Breakdown")
    print(
        f"Governance: {waiting.get('Governance approval', 0)} | "
        f"Product Owner: {waiting.get('Product Owner approval', 0)} | "
        f"Dependency: {waiting.get('Dependency', 0)} | "
        f"Future milestone: {waiting.get('Future milestone sequencing', 0)}"
    )
    print(
        f"Repository convergence: {waiting.get('Repository convergence', 0)} | "
        f"External/upstream: {waiting.get('External dependency', 0) + waiting.get('Upstream implementation', 0)} | "
        f"Merge ordering: {waiting.get('Merge ordering', 0)} | Unknown: {waiting.get('Unknown', 0)}"
    )
    print(
        f"Time: {waiting.get('Time gate', 0)} | Budget: {waiting.get('Budget', 0)} | "
        f"Resource: {waiting.get('Resource', 0)} | Tool: {waiting.get('Tool limitation', 0)}"
    )
    print()

    if inventory.get("queue_depth", 0) == 0:
        print("Why I Am Currently Idle")
        print(
            f"Inspected {idle.get('repositories_inspected', 0)} repositories and "
            f"{inventory.get('work_items_discovered', 0)} work items; evaluated "
            f"{idle.get('dependency_queries', 0)} dependency sets and every Waiting item for decomposition. "
            f"Unknown={idle.get('unknown_count', 0)}; Queue-zero proven={idle.get('queue_zero_proven', False)}."
        )
        print()

    print("Roadmap Confidence")
    print(f"{confidence.get('percent', 0)}%")
    print("Reason: " + "; ".join(confidence.get("reasons") or ["inventory generated"]))
    uncertainty = confidence.get("remaining_uncertainty") or []
    print("Remaining uncertainty: " + ("; ".join(uncertainty) if uncertainty else "None"))
    print()

    print("Engineering Decisions")
    print("- Queue contains every currently Executable item; blocked/running/completed work is excluded.")
    print(f"- Waiting decomposition evaluated: {inventory.get('decomposition', {}).get('waiting_items_evaluated', 0)} items.")
    print()

    print("--- Technical Details ---")
    print(f"Run: {run_id} | Mode: {control.get('mode')} | Confidence: {confidence.get('percent', 0)}%")
    print("Repository SHAs: " + ", ".join(
        f"{name}={(repo.get('local') or {}).get('head', 'unavailable')}"
        for name, repo in (inventory.get("repositories") or {}).items()
    ))
    print("MRs/Pipelines/Issues: " + (", ".join(item.get("web_url", "") for item in open_mrs) or "No open MRs"))
    print(f"Worktrees/Branches/Leases: {len(active_assignments)} active assignments; {len(leases)} active leases")
    print(
        f"Evidence: inventory generated {inventory.get('generated_at')} sha256:{inventory_sha256}; "
        f"queue-zero proven={idle.get('queue_zero_proven', False)}"
    )

    report_record = {
        "schema": "axis.external-development-supervisor.report",
        "schema_version": "1.0.0",
        "report_id": run_id,
        "kind": "change" if changed else "heartbeat",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "inventory_generation_id": inventory.get("generation_id"),
            "inventory_generated_at": inventory.get("generated_at"),
            "inventory_sha256": inventory_sha256,
        },
        "snapshot": {
            "classifications": counts,
            "waiting_by_reason": waiting,
            "queue_depth": inventory.get("queue_depth", 0),
            "active_assignments": len(active_assignments),
            "active_leases": len(leases),
        },
        "po_decision": {
            "required": need_po,
            "affected_refs": blockers["Human"],
        },
        "idle_proof": idle,
        "confidence": confidence,
        "evidence": [
            item.get("web_url") for item in open_mrs if item.get("web_url")
        ],
    }
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    report_path = reports_dir / f"{run_id}.json"
    report_tmp = report_path.with_suffix(".json.tmp")
    report_tmp.write_text(json.dumps(report_record, indent=2) + "\n", encoding="utf-8")
    report_tmp.chmod(0o600)
    report_tmp.replace(report_path)

    pending_state = {
        "version": 1,
        "report_id": run_id,
        "fingerprint": fingerprint,
        "generated_at_epoch": now,
        "completed_before": int((report_job.get("repeat") or {}).get("completed") or 0),
        "reported_completed_assignment_ids": sorted(
            previously_reported
            | {
                str(item.get("assignment_id"))
                for item in newly_completed
                if item.get("assignment_id")
            }
        ),
    }
    tmp = PENDING.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pending_state, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(PENDING)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            "🔴 AXIS Development Supervisor\n\n"
            "Summary\nReporting failed; development evidence remains in GitLab and cron output.\n\n"
            f"--- Technical Details ---\n{type(exc).__name__}: {exc}"
        )
        raise SystemExit(1)
