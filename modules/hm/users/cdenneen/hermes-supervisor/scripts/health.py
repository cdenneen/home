#!/usr/bin/env python3
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from axis_supervisor.models import validate_assignment
from axis_supervisor.observability import OperationalEventLog
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
        raise TypeError(f"expected object: {path}")
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
    except Exception as exc:  # noqa: BLE001 - health aggregates corrupt records
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
    roadmap_quality_path = ROOT / "roadmap-quality.json"
    roadmap_quality = (
        validated(
            roadmap_quality_path,
            "axis.external-development-supervisor.roadmap-quality",
            errors,
        )
        if roadmap_quality_path.exists()
        else {}
    )
    repository_convergence_path = ROOT / "repository-convergence.json"
    repository_convergence = (
        validated(
            repository_convergence_path,
            "axis.external-development-supervisor.repository-convergence",
            errors,
        )
        if repository_convergence_path.exists()
        else {}
    )
    capability_convergence_path = ROOT / "capability-convergence.json"
    capability_convergence = (
        validated(
            capability_convergence_path,
            "axis.external-development-supervisor.capability-convergence",
            errors,
        )
        if capability_convergence_path.exists()
        else {}
    )
    assignments = []
    for path in (ROOT / "assignments").glob("*.json"):
        try:
            assignments.append(
                validate_assignment(json.loads(path.read_text(encoding="utf-8")), ROOT)
            )
        except Exception as exc:  # noqa: BLE001 - health aggregates corrupt records
            errors.append(f"invalid assignment record {path.name}: {exc}")
    active_assignments = [
        item
        for item in assignments
        if item.get("lifecycle_state")
        not in {"completed", "waiting", "blocked", "failed", "cancelled", "recovery-required"}
    ]
    active_grants = []
    for path in (ROOT / "mutation-grants").glob("*/grant.json"):
        try:
            raw_grant = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid mutation grant record {path.name}: {exc}")
            continue
        if raw_grant.get("status") != "active":
            continue
        grant = validated(
            path,
            "axis.external-development-supervisor.mutation-grant",
            errors,
        )
        if grant.get("status") == "active":
            active_grants.append(grant)
    analysis_workers = [
        item
        for item in active_assignments
        if item.get("assignment_type") in {"read-only-analysis", "no-op-verification"}
    ]
    coding_workers = [
        item
        for item in active_assignments
        if item.get("assignment_type")
        in {
            "governance-document-mutation",
            "code-implementation",
            "ci-integration-repair",
        }
        and item.get("lifecycle_state") != "awaiting-integration"
    ]
    integration_workers = [
        item
        for item in active_assignments
        if item.get("lifecycle_state") == "awaiting-integration"
    ]
    recent_integrated = 0
    events_path = ROOT / "operational-events.jsonl"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                event.get("event_type") == "post_main_verified"
                and now - int(event.get("created_at_epoch") or 0) <= 86400
            ):
                recent_integrated += 1
    engineering_metrics = OperationalEventLog(ROOT, "reporter").throughput_metrics(
        now - 86_400, now
    )
    engineering_metrics_30d = OperationalEventLog(
        ROOT, "reporter"
    ).throughput_metrics(now - 30 * 86_400, now)

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

    expected_jobs = {str(control.get("cron_job_id") or "")} - {""}
    if len(expected_jobs) != 1:
        errors.append("supervisor worker cron ID is not configured")
    actual_jobs = {str(item.get("id")) for item in jobs.get("jobs", []) if item.get("enabled")}
    for name in ("axis-development-supervisor-worker",):
        matches = [item for item in jobs.get("jobs", []) if item.get("name") == name]
        if len(matches) != 1:
            errors.append(f"expected exactly one cron job named {name}, found {len(matches)}")
    legacy_report = [
        item
        for item in jobs.get("jobs", [])
        if item.get("name") == "axis-development-supervisor-report" and item.get("enabled")
    ]
    if legacy_report:
        errors.append("legacy supervisor report cron must be disabled; watchdog owns routine Slack projection")
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

    if roadmap_quality:
        if roadmap_quality.get("inventory_generation_id") != inventory.get(
            "generation_id"
        ):
            errors.append("roadmap quality source inventory revision is not current")
        if roadmap_quality.get("graph_generation_id") != graph.get("generation_id"):
            errors.append("roadmap quality source graph revision is not current")
    else:
        warnings.append("roadmap quality projection is not available")
    if repository_convergence:
        if repository_convergence.get("inventory_generation_id") != inventory.get(
            "generation_id"
        ):
            errors.append("repository convergence inventory revision is not current")
        if repository_convergence.get("status") != "green":
            errors.append(
                "repository convergence is "
                + str(repository_convergence.get("status") or "unknown")
            )
    else:
        warnings.append("repository convergence projection is not available")
    if capability_convergence:
        required_runtime_unknown = [
            value.get("display_name")
            for value in capability_convergence.get("runtimes") or []
            if value.get("status") == "unknown" and value.get("ring") == 0
        ]
        optional_runtime_unknown = [
            value.get("display_name")
            for value in capability_convergence.get("runtimes") or []
            if value.get("status") == "unknown" and value.get("ring") != 0
        ]
        deployments = capability_convergence.get("deployment_assignments") or []
        if required_runtime_unknown:
            errors.append(
                "runtime capability identity unavailable: "
                + ", ".join(str(value) for value in required_runtime_unknown)
            )
        if optional_runtime_unknown:
            warnings.append(
                "optional runtime unavailable: "
                + ", ".join(str(value) for value in optional_runtime_unknown)
            )
        elif deployments:
            warnings.append(
                f"{len(deployments)} capability deployment assignment(s) pending"
            )
    else:
        warnings.append("capability convergence projection is not available")
    free_gib = os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize // (1024**3)
    minimum = int(control.get("minimum_free_disk_gib", 15))
    if free_gib < minimum:
        errors.append(f"disk free {free_gib} GiB below {minimum} GiB minimum")

    stale_runs = 0
    for path in (ROOT / "runs").glob("*.json"):
        try:
            record = read_record(path, "axis.external-development-supervisor.run")
        except Exception:  # noqa: BLE001, S112 - corrupt runs are ignored here
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
        except Exception:  # noqa: BLE001 - corrupt leases require recovery
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

    collection = inventory.get("collection_status") or {}
    collector_issues = [
        issue
        for issue in (
            f"retrieval errors: {collection.get('retrieval_error_count')}"
            if int(collection.get("retrieval_error_count", 0))
            else None,
            f"dependency query failures: {collection.get('dependency_query_failures')}"
            if int(collection.get("dependency_query_failures", 0))
            else None,
            f"stale repositories: {collection.get('stale_repository_count')}"
            if int(collection.get("stale_repository_count", 0))
            else None,
        )
        if issue
    ]
    mutation_state = (
        "green"
        if control.get("allow_repository_mutation") or active_grants
        else "amber"
    )
    coding_state = "green" if coding_workers else "amber"
    integration_activity_state = "green" if integration_workers else "amber"
    throughput_state = "green" if recent_integrated else "amber"
    subsystem_health = {
        "scheduler": {"state": subsystem_state(scheduling_issues), "issues": scheduling_issues},
        "collector": {"state": "red" if collector_issues else "green", "issues": collector_issues},
        "semantic_supervisor": {"state": subsystem_state(worker_issues), "issues": worker_issues},
        "slack_observability": {"state": subsystem_state(observability_issues), "issues": observability_issues},
        "mutation_capability": {
            "state": mutation_state,
            "mode": "global-enabled"
            if control.get("allow_repository_mutation")
            else "bounded-active"
            if active_grants
            else "standby",
            "active_grants": len(active_grants),
            "issues": [],
        },
        "coding_worker_activity": {
            "state": coding_state,
            "active_workers": len(coding_workers),
            "issues": [],
        },
        "integration_activity": {
            "state": integration_activity_state,
            "active_workers": len(integration_workers),
            "issues": integration_issues,
        },
        "verified_roadmap_throughput": {
            "state": throughput_state,
            "post_main_verified_last_24h": recent_integrated,
            "issues": [],
        },
        "gitlab": {"state": subsystem_state(gitlab_issues), "issues": gitlab_issues},
    }
    subsystem_states = {value["state"] for value in subsystem_health.values()}
    overall_operability = (
        "red"
        if "red" in subsystem_states
        else "amber"
        if "amber" in subsystem_states
        else "green"
    )
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
        "governed_queue_depth": graph.get("queue_depth"),
        "governed_queue_zero_proven": graph.get("governed_queue_zero_proven"),
        "subsystem_health": subsystem_health,
        "overall_unattended_operability": overall_operability,
        "global_repository_mutation": bool(control.get("allow_repository_mutation")),
        "active_mutation_grants": len(active_grants),
        "analysis_workers": len(analysis_workers),
        "coding_workers": len(coding_workers),
        "integration_workers": len(integration_workers),
        "post_main_verified_last_24h": recent_integrated,
        "engineering_metrics_24h": engineering_metrics,
        "engineering_metrics_30d": engineering_metrics_30d,
        "flow_counts": graph.get("flow_counts") or {},
        "wip_limits": (graph.get("scheduler_state") or {}).get("wip_limits") or {},
        "wip_counts": (graph.get("scheduler_state") or {}).get("wip_counts") or {},
        "current_constraint": (graph.get("scheduler_state") or {}).get(
            "current_constraint"
        )
        or {},
        "roadmap_quality": roadmap_quality.get("metrics") or {},
        "roadmap_quality_trend": roadmap_quality.get("trend") or {},
        "critical_path_status": roadmap_quality.get("critical_path_status") or {},
        "repository_convergence": {
            "status": repository_convergence.get("status"),
            "counts": repository_convergence.get("counts") or {},
            "invariants": repository_convergence.get("invariants") or {},
        },
        "capability_convergence": {
            "expected_repository_revision": capability_convergence.get(
                "expected_repository_revision"
            ),
            "runtimes": capability_convergence.get("runtimes") or [],
            "deployment_assignments": capability_convergence.get(
                "deployment_assignments"
            )
            or [],
            "promotion_status": capability_convergence.get("promotion_status") or {},
        },
        "free_disk_gib": free_gib,
    }, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CLI boundary emits structured failure
        print(json.dumps({"status": "unhealthy", "errors": [f"{type(exc).__name__}: {exc}"]}))
        raise SystemExit(1)
