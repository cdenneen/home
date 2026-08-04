#!/usr/bin/env python3
import json
import os
import time
from datetime import datetime
from pathlib import Path

HOME = Path.home()
ROOT = Path(os.environ.get("AXIS_SUPERVISOR_ROOT", HOME / ".hermes" / "supervisor" / "axis-development-supervisor"))
JOBS = Path(os.environ.get("AXIS_SUPERVISOR_CRON_JOBS", HOME / ".hermes" / "cron" / "jobs.json"))
CRON_DIR = JOBS.parent
GATEWAY = HOME / ".hermes" / "gateway_state.json"


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def main() -> int:
    now = int(time.time())
    errors = []
    warnings = []
    gateway = load(GATEWAY)
    jobs = load(JOBS)
    inventory = load(ROOT / "inventory.json")
    control = load(ROOT / "control.json")

    if gateway.get("gateway_state") != "running":
        errors.append("gateway is not running")
    gateway_pid = int(gateway.get("pid") or 0)
    try:
        os.kill(gateway_pid, 0)
    except OSError:
        errors.append("gateway PID is not alive")
    slack = (gateway.get("platforms") or {}).get("slack") or {}
    if slack.get("state") != "connected":
        errors.append("Slack is not connected")

    expected_jobs = {
        str(control.get("cron_job_id") or ""),
        str(control.get("report_cron_job_id") or ""),
    } - {""}
    if len(expected_jobs) != 2:
        errors.append("worker and reporter cron IDs are not configured")
    actual_jobs = {str(item.get("id")) for item in jobs.get("jobs", []) if item.get("enabled")}
    missing = sorted(expected_jobs - actual_jobs)
    if missing:
        errors.append(f"missing enabled cron jobs: {missing}")
    for job in jobs.get("jobs", []):
        if str(job.get("id")) not in expected_jobs:
            continue
        if job.get("last_error"):
            warnings.append(f"{job.get('name')} last error: {job.get('last_error')}")
        if job.get("last_delivery_error"):
            warnings.append(f"{job.get('name')} delivery error: {job.get('last_delivery_error')}")
    for marker, label in (
        (CRON_DIR / "ticker_heartbeat", "ticker heartbeat"),
        (CRON_DIR / "ticker_last_success", "ticker success"),
    ):
        if not marker.exists():
            errors.append(f"missing {label} marker")
        elif now - int(marker.stat().st_mtime) > 120:
            errors.append(f"stale {label} marker")

    generated = inventory.get("generated_at")
    if not generated:
        errors.append("inventory has no generated_at")
    else:
        try:
            generated_epoch = int(datetime.fromisoformat(generated).timestamp())
            if now - generated_epoch > 3600:
                warnings.append("inventory is older than one hour")
        except Exception:
            errors.append("inventory generated_at is invalid")
    if inventory.get("invariant", {}).get("unknown_count", 1) != 0:
        errors.append("inventory contains Unknown classifications")
    inventory_lock = ROOT / "inventory.lock"
    if inventory_lock.exists():
        lock_age = now - int(inventory_lock.stat().st_mtime)
        if lock_age > 300:
            errors.append(f"inventory lock is stale ({lock_age}s)")
        else:
            warnings.append("inventory generation is currently locked")

    pending = ROOT / "report-delivery-pending.json"
    if pending.exists():
        try:
            pending_value = load(pending)
            pending_age = now - int(pending_value.get("generated_at_epoch", now))
            if pending_age > int(control.get("report_heartbeat_minutes", 90)) * 60:
                warnings.append("report delivery acknowledgment is stale")
        except Exception:
            errors.append("pending report state is invalid")

    free_gib = os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize // (1024**3)
    minimum = int(control.get("minimum_free_disk_gib", 15))
    if free_gib < minimum:
        errors.append(f"disk free {free_gib} GiB below {minimum} GiB minimum")

    stale_runs = 0
    for path in (ROOT / "runs").glob("*.json"):
        try:
            record = load(path)
        except Exception:
            continue
        if record.get("status") == "started" and now - int(record.get("started_at_epoch", now)) > 1800:
            stale_runs += 1
    if stale_runs:
        warnings.append(f"{stale_runs} stale started run record(s)")

    recovery_leases = 0
    for path in (ROOT / "leases").glob("*/lease.json"):
        try:
            if load(path).get("recovery_required"):
                recovery_leases += 1
        except Exception:
            recovery_leases += 1
    if recovery_leases:
        errors.append(f"{recovery_leases} lease(s) require canonical recovery")

    status = "healthy" if not errors and not warnings else "degraded" if not errors else "unhealthy"
    print(json.dumps({
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "gateway_pid": gateway.get("pid"),
        "slack": slack.get("state"),
        "enabled_jobs": sorted(actual_jobs),
        "inventory_generation_id": inventory.get("generation_id"),
        "inventory_generated_at": generated,
        "queue_depth": inventory.get("queue_depth"),
        "free_disk_gib": free_gib,
    }, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "unhealthy", "errors": [f"{type(exc).__name__}: {exc}"]}))
        raise SystemExit(1)
