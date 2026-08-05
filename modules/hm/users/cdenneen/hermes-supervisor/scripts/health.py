#!/usr/bin/env python3
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from axis_supervisor.schema_registry import read_record

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


def timestamp_epoch(value: str, field: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is invalid") from exc


def validated(path: Path, schema: str, errors: list[str]) -> dict:
    try:
        return read_record(path, schema)
    except Exception as exc:
        errors.append(f"invalid {schema} record {path.name}: {exc}")
        return {}


def main() -> int:
    now = int(time.time())
    errors = []
    warnings = []
    gateway = load(GATEWAY)
    jobs = load(JOBS)
    inventory = validated(
        ROOT / "inventory.json", "axis.external-development-supervisor.inventory", errors
    )
    graph = validated(
        ROOT / "execution-graph.json",
        "axis.external-development-supervisor.execution-graph",
        errors,
    )
    control = validated(
        ROOT / "control.json", "axis.external-development-supervisor.control", errors
    )
    overview = validated(
        ROOT / "slack-overview-record.json",
        "axis.external-development-supervisor.roadmap-semantics",
        errors,
    )
    overview_state = validated(
        ROOT / "slack-overview-state.json",
        "axis.external-development-supervisor.slack-state",
        errors,
    )
    outbox_path = ROOT / "slack-outbox.json"
    outbox = (
        validated(
            outbox_path,
            "axis.external-development-supervisor.slack-outbox",
            errors,
        )
        if outbox_path.exists()
        else {"notifications": []}
    )
    deployed_revision = load(ROOT / "deployed-source-revision.json")

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
        errors.append("worker and Slack projection cron IDs are not configured")
    actual_jobs = {str(item.get("id")) for item in jobs.get("jobs", []) if item.get("enabled")}
    for name in (
        "axis-development-supervisor-worker",
        "axis-development-supervisor-report",
    ):
        matches = [item for item in jobs.get("jobs", []) if item.get("name") == name]
        if len(matches) != 1:
            errors.append(f"expected exactly one cron job named {name}, found {len(matches)}")
    missing = sorted(expected_jobs - actual_jobs)
    if missing:
        errors.append(f"missing enabled cron jobs: {missing}")
    for job in jobs.get("jobs", []):
        if str(job.get("id")) not in expected_jobs:
            continue
        if job.get("last_error"):
            warnings.append(f"{job.get('name')} last error: {job.get('last_error')}")
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
            generated_epoch = timestamp_epoch(generated, "inventory generated_at")
            if now - generated_epoch > 3600:
                warnings.append("inventory is older than one hour")
        except ValueError:
            errors.append("inventory generated_at is invalid")
    if int((graph.get("classification_counts") or {}).get("Unknown", 1)) != 0:
        errors.append("inventory contains Unknown classifications")
    inventory_lock = ROOT / "inventory.lock"
    if inventory_lock.exists():
        lock_age = now - int(inventory_lock.stat().st_mtime)
        if lock_age > 300:
            errors.append(f"inventory lock is stale ({lock_age}s)")
        else:
            warnings.append("inventory generation is currently locked")

    overview_freshness_seconds = int(control.get("overview_freshness_minutes", 90)) * 60
    overview_generated_at = overview.get("generated_at")
    if not overview_generated_at:
        errors.append("Slack overview semantic record has no generated_at")
    else:
        try:
            overview_age = now - timestamp_epoch(
                overview_generated_at, "Slack overview generated_at"
            )
            if overview_age > overview_freshness_seconds:
                errors.append(f"Slack overview semantic record is stale ({overview_age}s)")
        except ValueError as exc:
            errors.append(str(exc))

    delivery_stage = overview_state.get("delivery_stage")
    if delivery_stage != "Slack_message_verified":
        errors.append(f"Slack overview delivery stage is {delivery_stage or 'missing'}")
    if overview_state.get("last_delivery_error"):
        errors.append(
            f"Slack overview delivery failed: {overview_state['last_delivery_error']}"
        )
    last_successful_update = overview_state.get("last_verified_at")
    if not last_successful_update:
        errors.append("Slack overview has no verified readback timestamp")
    else:
        try:
            success_age = now - timestamp_epoch(
                last_successful_update, "Slack overview last_verified_at"
            )
            if success_age > overview_freshness_seconds:
                errors.append(f"Slack overview last verified readback is stale ({success_age}s)")
        except ValueError as exc:
            errors.append(str(exc))

    source = overview.get("source") or {}
    if source.get("inventory_revision") != inventory.get("generation_id"):
        errors.append("Slack overview source inventory revision is not current")
    if source.get("graph_generation_id") != graph.get("generation_id"):
        errors.append("Slack overview source graph revision is not current")
    if source.get("deployed_revision") != deployed_revision:
        errors.append("Slack overview source revision does not match deployed source")
    if overview_state.get("source_revision") != deployed_revision:
        errors.append("Slack overview state source revision does not match deployed source")
    if overview_state.get("semantic_revision") != overview.get("semantic_revision"):
        errors.append("Slack overview state and semantic revisions do not match")
    failed_notifications = [
        item
        for item in outbox.get("notifications") or []
        if item.get("current_stage") == "delivery_failed"
    ]
    pending_notifications = [
        item
        for item in outbox.get("notifications") or []
        if item.get("current_stage") != "Slack_message_verified"
    ]
    if failed_notifications:
        errors.append(f"Slack outbox has {len(failed_notifications)} failed notification(s)")
    elif pending_notifications:
        warnings.append(f"Slack outbox has {len(pending_notifications)} queued notification(s)")

    schema_compatible = bool(overview)
    schema_validator = "registry"

    free_gib = os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize // (1024**3)
    minimum = int(control.get("minimum_free_disk_gib", 15))
    if free_gib < minimum:
        errors.append(f"disk free {free_gib} GiB below {minimum} GiB minimum")

    stale_runs = 0
    for path in (ROOT / "runs").glob("*.json"):
        try:
            record = read_record(path, "axis.external-development-supervisor.run")
        except Exception:
            continue
        if record.get("status") == "started" and now - int(record.get("started_at_epoch", now)) > 1800:
            stale_runs += 1
    if stale_runs:
        warnings.append(f"{stale_runs} stale started run record(s)")

    recovery_leases = len(list((ROOT / "leases").glob("stale-*/lease.json")))
    for path in (ROOT / "leases").glob("*/lease.json"):
        if path.parent.name.startswith("stale-"):
            continue
        try:
            read_record(path, "axis.external-development-supervisor.lease")
        except Exception:
            recovery_leases += 1
    if recovery_leases:
        errors.append(f"{recovery_leases} lease(s) require canonical recovery")

    def relevant(values: list[str], terms: tuple[str, ...]) -> list[str]:
        return [value for value in values if any(term in value.lower() for term in terms)]

    scheduling_issues = relevant(errors + warnings, ("cron", "ticker", "job"))
    observability_issues = relevant(
        errors + warnings,
        ("slack", "overview", "outbox", "delivery", "readback"),
    )
    integration_issues = relevant(errors + warnings, ("lease", "integration"))
    gitlab_issues = relevant(
        errors + warnings,
        ("retrieval", "dependency", "gitlab"),
    )
    worker_issues = [
        value
        for value in errors + warnings
        if value
        not in set(
            scheduling_issues
            + observability_issues
            + integration_issues
            + gitlab_issues
        )
    ]

    def subsystem_state(issues: list[str]) -> str:
        if any(issue in errors for issue in issues):
            return "red"
        return "amber" if issues else "green"

    subsystem_health = {
        "scheduling": {"state": subsystem_state(scheduling_issues), "issues": scheduling_issues},
        "worker_execution": {"state": subsystem_state(worker_issues), "issues": worker_issues},
        "integration": {"state": subsystem_state(integration_issues), "issues": integration_issues},
        "gitlab": {"state": subsystem_state(gitlab_issues), "issues": gitlab_issues},
        "slack_observability": {"state": subsystem_state(observability_issues), "issues": observability_issues},
    }
    subsystem_states = {value["state"] for value in subsystem_health.values()}
    overall_operability = "red" if "red" in subsystem_states and subsystem_health["worker_execution"]["state"] == "red" else "amber" if "red" in subsystem_states or "amber" in subsystem_states else "green"
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
        "overview_generated_at": overview_generated_at,
        "overview_delivery_stage": delivery_stage,
        "overview_last_verified_at": last_successful_update,
        "overview_semantic_revision": overview.get("semantic_revision"),
        "overview_source_revision": source.get("deployed_revision"),
        "overview_schema_compatible": schema_compatible,
        "overview_schema_validator": schema_validator,
        "governed_queue_depth": graph.get("queue_depth"),
        "governed_queue_zero_proven": graph.get("governed_queue_zero_proven"),
        "pending_slack_notifications": len(pending_notifications),
        "failed_slack_notifications": len(failed_notifications),
        "subsystem_health": subsystem_health,
        "overall_unattended_operability": overall_operability,
        "free_disk_gib": free_gib,
    }, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "unhealthy", "errors": [f"{type(exc).__name__}: {exc}"]}))
        raise SystemExit(1)
