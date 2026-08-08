import hashlib
import json
import statistics
import time
import uuid
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any

from .diagnostics import SubprocessDiagnostic
from .projection import CanonicalSlackProjector
from .records import (
    Ledger,
    atomic_write,
    load_object,
    load_optional,
    parse_timestamp,
    timestamp,
)
from .recovery import RecoveryExecutor, RecoveryJournal, unavailable_diagnostic

Clock = Callable[[], int]

PROGRESS_EVENTS = {
    "implementation_completed",
    "mr_created",
    "mr_merged",
    "post_main_verified",
    "capability_deployment_verified",
}
TERMINAL_ASSIGNMENT_STATES = {
    "completed",
    "waiting",
    "blocked",
    "failed",
    "cancelled",
    "recovery-required",
}


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


class Watchdog:
    def __init__(
        self,
        root: Path,
        supervisor_root: Path,
        jobs_path: Path,
        *,
        clock: Clock | None = None,
        projector: Any | None = None,
        diagnostic: Any | None = None,
        recovery: Any | None = None,
    ):
        self.root = root
        self.supervisor_root = supervisor_root
        self.jobs_path = jobs_path
        self.clock = clock or (lambda: int(time.time()))
        self.projector = projector or CanonicalSlackProjector(
            supervisor_root,
            watchdog_root=root,
            jobs_path=jobs_path,
        )
        self.diagnostic = diagnostic or SubprocessDiagnostic()
        self.recovery = recovery or RecoveryExecutor(root, supervisor_root)
        self.states = Ledger(root, "states", "axis.development-watchdog.state")
        self.observations = Ledger(
            root, "observations", "axis.development-watchdog.observation"
        )
        self.incidents = Ledger(root, "incidents", "axis.development-watchdog.incident")
        self.recoveries = Ledger(
            root, "recoveries", "axis.development-watchdog.recovery"
        )
        self.recovery_journal = RecoveryJournal(root, self.recoveries)
        self.projections = Ledger(
            root, "projections", "axis.development-watchdog.projection"
        )

    def _control(self) -> dict[str, Any]:
        control = load_object(self.root / "control.json")
        if control.get("schema") != "axis.development-watchdog.control":
            raise ValueError("watchdog control schema is invalid")
        if control.get("schema_version") != "1.0.0":
            raise ValueError("unsupported watchdog control schema version")
        if control.get("repair_repository") != "cdenneen/home":
            raise ValueError("watchdog repair authority is restricted to cdenneen/home")
        return control

    def _records(self) -> tuple[dict[str, dict[str, Any]], list[str]]:
        values: dict[str, dict[str, Any]] = {}
        errors = []
        for name in (
            "control",
            "inventory",
            "execution-graph",
            "active-mission",
            "capability-graduation",
            "capability-convergence",
            "slack-overview-state",
        ):
            path = self.supervisor_root / f"{name}.json"
            if not path.exists():
                if name in {"control", "inventory", "execution-graph", "active-mission"}:
                    errors.append(f"missing {name}.json")
                values[name] = {}
                continue
            try:
                values[name] = load_object(path)
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                errors.append(f"invalid {name}.json: {type(exc).__name__}: {exc}")
                values[name] = {}
        return values, errors

    def _outbox_health(
        self, control: dict[str, Any], now: int
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        path = self.supervisor_root / "slack-outbox.json"
        metrics: dict[str, Any] = {
            "status": "healthy",
            "queued": 0,
            "sending": 0,
            "api_accepted": 0,
            "failed": 0,
            "permanent": 0,
            "oldest_pending_age_seconds": 0,
        }
        if not path.exists():
            metrics["status"] = "missing"
            return metrics, [
                self._anomaly(
                    "slack-outbox-missing",
                    "delivery_effectiveness",
                    "canonical supervisor Slack outbox is missing",
                    1,
                )
            ]
        try:
            outbox = load_object(path)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            metrics["status"] = "corrupt"
            return metrics, [
                self._anomaly(
                    "slack-outbox-corrupt",
                    "delivery_effectiveness",
                    f"canonical supervisor Slack outbox is corrupt: {type(exc).__name__}",
                    1,
                )
            ]

        anomalies: list[dict[str, Any]] = []
        pending_ages = []
        thresholds = {
            "notification_queued": (
                "queued",
                int(control["outbox_queued_max_age_seconds"]),
            ),
            "notification_send_attempted": (
                "sending",
                int(control["outbox_sending_max_age_seconds"]),
            ),
            "Slack_API_accepted": (
                "api_accepted",
                int(control["outbox_accepted_max_age_seconds"]),
            ),
            "Slack_message_created": (
                "api_accepted",
                int(control["outbox_accepted_max_age_seconds"]),
            ),
            "Slack_message_updated": (
                "api_accepted",
                int(control["outbox_accepted_max_age_seconds"]),
            ),
        }
        for item in outbox.get("notifications") or []:
            stage = str(item.get("current_stage") or "unknown")
            if stage == "Slack_message_verified":
                continue
            event = item.get("event") or {}
            stage_history = item.get("stage_history") or []
            stage_at = next(
                (
                    parse_timestamp(entry.get("at"))
                    for entry in reversed(stage_history)
                    if entry.get("stage") == stage
                ),
                None,
            )
            observed_epoch = stage_at or int(event.get("created_at_epoch") or now)
            age = max(0, now - observed_epoch)
            pending_ages.append(age)
            attempts = int(item.get("attempts") or 0)
            permanent = bool(item.get("permanent_failure")) or (
                stage == "delivery_failed"
                and attempts >= int(control["outbox_permanent_attempts"])
            )
            if permanent:
                metrics["permanent"] += 1
            elif stage == "delivery_failed":
                metrics["failed"] += 1
            elif stage in thresholds:
                key, maximum = thresholds[stage]
                metrics[key] += 1
                if age > maximum:
                    anomalies.append(
                        self._anomaly(
                            f"slack-outbox-{key.replace('_', '-')}-stale",
                            "delivery_effectiveness",
                            f"oldest {key.replace('_', ' ')} Slack item is {age}s old",
                            1,
                        )
                    )
            else:
                metrics["sending"] += 1
        metrics["oldest_pending_age_seconds"] = max(pending_ages, default=0)
        if metrics["failed"]:
            anomalies.append(
                self._anomaly(
                    "slack-outbox-failed",
                    "delivery_effectiveness",
                    f"canonical Slack outbox has {metrics['failed']} retryable failure(s)",
                    1,
                )
            )
        if metrics["permanent"]:
            anomalies.append(
                self._anomaly(
                    "slack-outbox-permanent-failure",
                    "delivery_effectiveness",
                    f"canonical Slack outbox has {metrics['permanent']} permanent failure(s)",
                    4,
                    repair=True,
                )
            )
        if anomalies:
            metrics["status"] = "degraded"
        return metrics, anomalies

    @staticmethod
    def _job(jobs: list[dict[str, Any]], name: str) -> dict[str, Any]:
        matches = [job for job in jobs if job.get("name") == name]
        return matches[0] if len(matches) == 1 else {}

    @staticmethod
    def _expected_wait(
        supervisor_control: dict[str, Any], mission: dict[str, Any], graph: dict[str, Any]
    ) -> tuple[bool, str | None]:
        mode = str(supervisor_control.get("mode") or "unknown")
        if supervisor_control.get("kill_switch") or mode in {
            "disabled",
            "observing",
            "draining",
        }:
            return True, f"supervisor mode {mode} intentionally suppresses new execution"
        if mission.get("current_state") == "completed":
            return True, "mission is completed"
        runnable = any(
            action.get("executable")
            for action in mission.get("generated_actions") or []
        )
        if mission.get("external_blockers") and not runnable:
            return True, "mission is waiting on explicit external authority"
        nodes = graph.get("nodes") or []
        unfinished = [
            node
            for node in nodes
            if node.get("classification")
            not in {"Completed", "Integrated", "Superseded", "Invalid"}
        ]
        if unfinished and not runnable and all(
            node.get("classification") in {"Waiting", "Blocked"} for node in unfinished
        ):
            return True, "all unfinished work has an explicit waiting or blocked classification"
        return False, None

    @staticmethod
    def _mission_progress(
        mission: dict[str, Any], graduation: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        capabilities = sorted(
            [
                {
                    "capability": item.get("capability"),
                    "graduated": item.get("graduated"),
                    "gates": {
                        gate: {
                            "applicable": value.get("applicable"),
                            "state": value.get("state"),
                        }
                        for gate, value in sorted(
                            (item.get("graduation_state") or {}).items()
                        )
                    },
                    "product_subdimensions": {
                        name: {
                            "applicable": value.get("applicable"),
                            "state": value.get("state"),
                        }
                        for name, value in sorted(
                            (item.get("product_subdimensions") or {}).items()
                        )
                    },
                }
                for item in graduation.get("capabilities") or []
            ],
            key=lambda value: str(value["capability"]),
        )
        missing_gates = sorted(
            [
                {
                    "capability": item.get("capability"),
                    "gate": item.get("gate"),
                    "state": item.get("state"),
                    "external_only": item.get("external_only"),
                }
                for item in mission.get("missing_gates") or []
            ],
            key=lambda value: (
                str(value["capability"]),
                str(value["gate"]),
            ),
        )
        milestones = sorted(
            [
                {
                    "milestone": item.get("milestone"),
                    "gate": item.get("gate"),
                    "graduated": (item.get("denominator") or {}).get("graduated"),
                    "debts": sorted(
                        [
                            {
                                "kind": debt.get("kind"),
                                "ref": debt.get("ref"),
                                "gate": debt.get("gate"),
                            }
                            for debt in item.get("debts") or []
                        ],
                        key=lambda value: (
                            str(value["kind"]),
                            str(value["ref"]),
                            str(value["gate"]),
                        ),
                    ),
                }
                for item in graduation.get("milestones") or []
            ],
            key=lambda value: str(value["milestone"]),
        )
        snapshot = {
            "primary_kpi": graduation.get("primary_kpi") or {},
            "capabilities": capabilities,
            "missing_gates": missing_gates,
            "milestones": milestones,
        }
        return _digest(snapshot), snapshot

    def _historical_threshold(self, control: dict[str, Any]) -> int:
        floor = int(control["stuck_floor_seconds"])
        ceiling = int(control["stuck_ceiling_seconds"])
        multiplier = float(control["stuck_history_multiplier"])
        changes: list[int] = []
        prior_fingerprint = None
        for entry in self.observations.entries():
            fingerprint = (entry.get("mission") or {}).get("progress_fingerprint")
            observed_epoch = int(entry.get("observed_at_epoch") or 0)
            if fingerprint and fingerprint != prior_fingerprint:
                changes.append(observed_epoch)
                prior_fingerprint = fingerprint
        intervals = [right - left for left, right in pairwise(changes) if right > left]
        historical = int(statistics.median(intervals) * multiplier) if intervals else floor
        return max(floor, min(ceiling, historical))

    @staticmethod
    def _anomaly(
        code: str,
        dimension: str,
        summary: str,
        level: int,
        *,
        repair: bool = False,
    ) -> dict[str, Any]:
        return {
            "anomaly_code": code,
            "dimension": dimension,
            "summary": summary,
            "recovery_level": level,
            "repair_repository": "cdenneen/home" if repair else None,
        }

    def _observe(
        self,
        control: dict[str, Any],
        state: dict[str, Any],
        prior_heartbeat: dict[str, Any],
        now: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
        records, record_errors = self._records()
        try:
            jobs_value = load_object(self.jobs_path)
            jobs = list(jobs_value.get("jobs") or [])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            jobs = []
            record_errors.append("Hermes cron jobs are unavailable")
        anomalies: list[dict[str, Any]] = []
        dimensions: dict[str, dict[str, Any]] = {
            name: {"status": "healthy", "evidence": []}
            for name in ("liveness", "control", "delivery_effectiveness", "mission_progress")
        }

        prior_epoch = int(prior_heartbeat.get("completed_at_epoch") or 0)
        heartbeat_age = now - prior_epoch if prior_epoch else 0
        if prior_epoch and heartbeat_age > int(control["heartbeat_grace_seconds"]):
            missed = max(1, heartbeat_age // int(control["interval_seconds"]) - 1)
            anomalies.append(
                self._anomaly(
                    "watchdog-heartbeat-missed",
                    "liveness",
                    f"watchdog heartbeat was {heartbeat_age}s old; {missed} cycle(s) missed before catch-up",
                    0,
                )
            )
            dimensions["liveness"] = {
                "status": "degraded",
                "evidence": [f"prior heartbeat age {heartbeat_age}s", f"catch-up cycles {missed}"],
            }

        external_heartbeat = load_optional(self.root / "external-heartbeat.json")
        external_epoch = int(external_heartbeat.get("observed_at_epoch") or 0)
        external_age = now - external_epoch if external_epoch else 0
        if int(state.get("cycle") or 0) and (
            not external_epoch
            or external_age > int(control["external_monitor_freshness_seconds"])
            or external_heartbeat.get("status") == "error"
        ):
            anomalies.append(
                self._anomaly(
                    "watchdog-external-monitor-unavailable",
                    "liveness",
                    "independent systemd watchdog monitor is missing, stale, or failed",
                    3,
                )
            )

        worker = self._job(jobs, "axis-development-supervisor-worker")
        watchdog_job = self._job(jobs, "axis-development-watchdog")
        routine_report = self._job(jobs, "axis-development-supervisor-report")
        if not worker or not worker.get("enabled"):
            anomalies.append(
                self._anomaly(
                    "supervisor-worker-unavailable",
                    "liveness",
                    "the independent supervisor worker cron is missing or disabled",
                    4,
                    repair=True,
                )
            )
        elif worker.get("last_run_at"):
            last_worker = parse_timestamp(worker.get("last_run_at"))
            if last_worker and now - last_worker > int(
                control["supervisor_worker_freshness_seconds"]
            ):
                anomalies.append(
                    self._anomaly(
                        "supervisor-worker-stale",
                        "liveness",
                        f"supervisor worker last ran {now - last_worker}s ago",
                        4,
                        repair=True,
                    )
                )
        if not watchdog_job or not watchdog_job.get("enabled"):
            anomalies.append(
                self._anomaly(
                    "watchdog-cron-unavailable",
                    "control",
                    "the watchdog's independently owned cron is missing or disabled",
                    2,
                )
            )
        if routine_report and routine_report.get("enabled"):
            anomalies.append(
                self._anomaly(
                    "routine-slack-authority-conflict",
                    "control",
                    "legacy supervisor report cron is enabled alongside watchdog projection",
                    4,
                    repair=True,
                )
            )
        if record_errors:
            anomalies.append(
                self._anomaly(
                    "supervisor-observation-unavailable",
                    "control",
                    "; ".join(record_errors),
                    4,
                    repair=True,
                )
            )
        supervisor_control = records["control"]
        if supervisor_control and (
            supervisor_control.get("schema") != "axis.external-development-supervisor.control"
            or supervisor_control.get("version") != 4
        ):
            anomalies.append(
                self._anomaly(
                    "supervisor-control-invalid",
                    "control",
                    "supervisor control identity or version is unsupported",
                    4,
                    repair=True,
                )
            )

        slack_state = records["slack-overview-state"]
        prior_cycle = int(state.get("cycle") or 0)
        delivery_epoch = int(slack_state.get("last_successful_update_epoch") or 0)
        if prior_cycle and (
            slack_state.get("delivery_stage") == "delivery_failed"
            or (delivery_epoch and now - delivery_epoch > int(control["delivery_freshness_seconds"]))
        ):
            anomalies.append(
                self._anomaly(
                    "slack-delivery-ineffective",
                    "delivery_effectiveness",
                    str(slack_state.get("last_delivery_error") or "Slack projection is stale"),
                    1,
                )
            )
        outbox_health, outbox_anomalies = self._outbox_health(control, now)
        anomalies.extend(outbox_anomalies)
        mission = records["active-mission"]
        effectiveness = mission.get("effectiveness_metrics") or {}
        evaluated = int(effectiveness.get("assignments_evaluated") or 0)
        defects = int(effectiveness.get("state_model_defects") or 0)
        effectiveness_percent = float(effectiveness.get("effectiveness_percent") or 100)
        if defects or (evaluated >= 3 and effectiveness_percent < 50):
            anomalies.append(
                self._anomaly(
                    "delivery-effectiveness-low",
                    "delivery_effectiveness",
                    f"mission effectiveness is {effectiveness_percent:g}% with {defects} state-model defect(s)",
                    4,
                    repair=True,
                )
            )

        fingerprint, progress_snapshot = self._mission_progress(
            mission, records["capability-graduation"]
        )
        previous_fingerprint = str(state.get("mission_progress_fingerprint") or "")
        progress_since = int(state.get("mission_progress_since_epoch") or now)
        if fingerprint != previous_fingerprint:
            progress_since = now
        threshold = self._historical_threshold(control)
        expected_wait, wait_reason = self._expected_wait(
            supervisor_control, mission, records["execution-graph"]
        )
        stuck_age = now - progress_since
        if (
            previous_fingerprint
            and fingerprint == previous_fingerprint
            and stuck_age > threshold
            and not expected_wait
            and mission.get("current_state") != "completed"
        ):
            anomalies.append(
                self._anomaly(
                    "mission-progress-stuck",
                    "mission_progress",
                    f"mission progress fingerprint has not changed for {stuck_age}s; historical threshold is {threshold}s",
                    4,
                    repair=True,
                )
            )
        dimensions["mission_progress"] = {
            "status": "waiting" if expected_wait else "degraded" if any(a["dimension"] == "mission_progress" for a in anomalies) else "healthy",
            "evidence": [
                f"progress age {stuck_age}s",
                f"historical threshold {threshold}s",
                wait_reason or "no expected wait",
            ],
        }
        for dimension in dimensions:
            relevant = [a for a in anomalies if a["dimension"] == dimension]
            if relevant and dimensions[dimension]["status"] == "healthy":
                dimensions[dimension] = {
                    "status": "degraded",
                    "evidence": [a["summary"] for a in relevant],
                }
        def job_summary(value: dict[str, Any]) -> dict[str, Any]:
            return {
                key: value.get(key)
                for key in (
                    "id",
                    "name",
                    "enabled",
                    "last_run_at",
                    "last_status",
                    "last_error",
                    "next_run_at",
                )
            }

        evidence = {
            "records": records,
            "jobs": {
                "supervisor_worker": job_summary(worker),
                "watchdog": job_summary(watchdog_job),
                "legacy_report": job_summary(routine_report),
            },
            "external_heartbeat": external_heartbeat,
            "mission": {
                "progress_fingerprint": fingerprint,
                "progress_snapshot": progress_snapshot,
                "progress_since_epoch": progress_since,
                "stuck_threshold_seconds": threshold,
                "expected_wait": expected_wait,
                "expected_wait_reason": wait_reason,
            },
            "outbox": outbox_health,
            "cutover": load_optional(self.root / "slack-cutover.json"),
        }
        return evidence, anomalies, dimensions

    @staticmethod
    def _incident_id(code: str) -> str:
        return "wd-" + hashlib.sha256(code.encode()).hexdigest()[:12]

    def _reconcile_incidents(
        self,
        state: dict[str, Any],
        anomalies: list[dict[str, Any]],
        now: int,
    ) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
        current = {
            str(key): dict(value)
            for key, value in (state.get("incidents") or {}).items()
            if isinstance(value, dict)
        }
        active_codes = {str(anomaly["anomaly_code"]) for anomaly in anomalies}
        newly_opened = []
        for anomaly in anomalies:
            code = str(anomaly["anomaly_code"])
            incident_id = self._incident_id(code)
            previous = current.get(incident_id) or {}
            reopened = previous.get("status") == "resolved"
            if not previous or reopened:
                incident = {
                    "incident_id": incident_id,
                    **anomaly,
                    "status": "opened",
                    "event": "reopened" if reopened else "opened",
                    "opened_at": timestamp(now),
                    "observed_at": timestamp(now),
                    "occurrences": int(previous.get("occurrences") or 0) + 1,
                    "diagnosis": None,
                }
                self.incidents.append(incident)
                newly_opened.append(incident)
            else:
                incident = {
                    **previous,
                    **anomaly,
                    "status": "recovering",
                    "event": "observed",
                    "observed_at": timestamp(now),
                    "occurrences": int(previous.get("occurrences") or 1) + 1,
                }
            current[incident_id] = incident
        for incident_id, incident in list(current.items()):
            if incident.get("status") == "resolved" or incident.get("anomaly_code") in active_codes:
                continue
            resolved = {
                **incident,
                "status": "resolved",
                "event": "resolved",
                "observed_at": timestamp(now),
                "resolved_at": timestamp(now),
            }
            self.incidents.append(resolved)
            transaction = self.recovery_journal.for_incident(incident_id)
            if transaction:
                self.recovery_journal.transition(
                    transaction,
                    action="deterministic-health-restored",
                    target=incident.get("repair_repository")
                    or "axis-development-watchdog",
                    status="completed",
                    transition="health-restored",
                    detail="deterministic anomaly is no longer present",
                    now=now,
                )
            current[incident_id] = resolved
        return current, newly_opened

    @staticmethod
    def _recovery_action(level: int) -> str:
        return {
            0: "observe-and-catch-up",
            1: "retry-watchdog-projection",
            2: "repair-watchdog-cron",
            3: "restart-watchdog-runtime",
            4: "escalate-supervisor-repair",
            5: "require-product-owner-action",
        }[level]

    def _append_recovery(
        self,
        transaction: dict[str, Any],
        *,
        status: str,
        transition: str,
        detail: str,
        now: int,
    ) -> dict[str, Any]:
        incident = transaction["incident"]
        return self.recovery_journal.transition(
            transaction,
            action=self._recovery_action(int(incident["recovery_level"])),
            target=incident.get("repair_repository") or "axis-development-watchdog",
            status=status,
            transition=transition,
            detail=detail,
            now=now,
        )

    def _execute_recovery_transaction(
        self,
        control: dict[str, Any],
        transaction: dict[str, Any],
        now: int,
    ) -> dict[str, Any]:
        incident = transaction["incident"]
        level = int(incident["recovery_level"])
        target = incident.get("repair_repository") or "axis-development-watchdog"
        if level == 4 and target != "cdenneen/home":
            raise ValueError("level-4 repair escalation escaped cdenneen/home")
        if transaction.get("last_transition") is None:
            transaction = self._append_recovery(
                transaction,
                status="requested",
                transition="requested",
                detail=f"deterministic anomaly requested recovery level {level}",
                now=now,
            )
        if transaction.get("last_transition") == "requested":
            incident["status"] = "recovering"
            incident["event"] = "recovery-started"
            incident["observed_at"] = timestamp(now)
            if not any(
                entry.get("incident_id") == incident["incident_id"]
                and entry.get("event") == "recovery-started"
                for entry in self.incidents.entries()
            ):
                self.incidents.append(incident)
            transaction["incident"] = incident
            transaction = self._append_recovery(
                transaction,
                status="in-progress",
                transition="started",
                detail="bounded recovery execution started",
                now=now,
            )
        if transaction.get("last_transition") != "started":
            return transaction
        try:
            status, detail = self.recovery.execute(
                transaction["recovery_id"],
                incident,
                incident.get("diagnosis"),
                control,
                now,
            )
        except Exception as exc:  # noqa: BLE001 - failure is recovery evidence
            return self._append_recovery(
                transaction,
                status="failed",
                transition="failed",
                detail=f"{type(exc).__name__}: {exc}",
                now=now,
            )
        if status == "in-progress":
            return transaction
        return self._append_recovery(
            transaction,
            status=status,
            transition="waiting-human" if status == "waiting-human" else "completed",
            detail=detail,
            now=now,
        )

    def _recover_incidents(
        self,
        control: dict[str, Any],
        current: dict[str, dict[str, Any]],
        newly_opened: list[dict[str, Any]],
        now: int,
    ) -> list[dict[str, Any]]:
        transactions = []
        for incident in newly_opened:
            transaction = self.recovery_journal.begin(incident, now)
            transaction["incident"] = incident
            transaction = self._execute_recovery_transaction(control, transaction, now)
            current[incident["incident_id"]] = transaction["incident"]
            transactions.append(transaction)
        return transactions

    def _resume_pending_recoveries(
        self, control: dict[str, Any], now: int
    ) -> list[dict[str, Any]]:
        return [
            self._execute_recovery_transaction(control, transaction, now)
            for transaction in self.recovery_journal.pending()
        ]

    def _finish_projection_recoveries(
        self,
        transactions: list[dict[str, Any]],
        *,
        success: bool,
        detail: str,
        now: int,
    ) -> None:
        for transaction in transactions:
            incident = transaction["incident"]
            if int(incident["recovery_level"]) != 1:
                continue
            self._append_recovery(
                transaction,
                status="completed" if success else "failed",
                transition="completed" if success else "failed",
                detail=detail,
                now=now,
            )

    def _diagnose(
        self,
        control: dict[str, Any],
        state: dict[str, Any],
        newly_opened: list[dict[str, Any]],
        evidence: dict[str, Any],
        now: int,
    ) -> dict[str, Any] | None:
        diagnostic_incidents = [
            incident
            for incident in newly_opened
            if int(incident.get("recovery_level") or 0) in {3, 4}
        ]
        if not diagnostic_incidents:
            return None
        calls = [
            int(value)
            for value in state.get("diagnostic_calls") or []
            if now - int(value) < 86_400
        ]
        signature = _digest(
            sorted(incident["anomaly_code"] for incident in diagnostic_incidents)
        )
        last = (state.get("diagnostic_signatures") or {}).get(signature)
        if len(calls) >= int(control["diagnostic_daily_limit"]):
            return None
        if last and now - int(last) < int(control["diagnostic_cooldown_seconds"]):
            return None
        bounded_evidence = {
            "jobs": evidence["jobs"],
            "mission": evidence["mission"],
            "outbox": evidence["outbox"],
            "external_heartbeat": evidence["external_heartbeat"],
        }
        try:
            result = self.diagnostic(diagnostic_incidents, bounded_evidence, control)
        except Exception as exc:  # noqa: BLE001 - diagnostics must not stop recovery
            result = unavailable_diagnostic()
            result["summary"] = f"Diagnostic failed: {type(exc).__name__}."[:500]
        calls.append(now)
        signatures = dict(state.get("diagnostic_signatures") or {})
        signatures[signature] = now
        state["diagnostic_calls"] = calls
        state["diagnostic_signatures"] = signatures
        for incident in diagnostic_incidents:
            incident["diagnosis"] = result
            self.incidents.append(
                {
                    **incident,
                    "event": "diagnosed",
                    "observed_at": timestamp(now),
                }
            )
        return result

    @staticmethod
    def _report(
        evidence: dict[str, Any],
        dimensions: dict[str, dict[str, Any]],
        incidents: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        records = evidence["records"]
        mission = records["active-mission"]
        graduation = records["capability-graduation"]
        convergence = records["capability-convergence"]
        graph = records["execution-graph"]
        primary = graduation.get("primary_kpi") or {}
        capability_text = (
            f"{int(primary.get('count') or 0)}/{int(primary.get('denominator') or 0)} graduated"
        )
        nodes = graph.get("nodes") or []
        verified = sum(
            node.get("classification") in {"Completed", "Integrated"}
            or (node.get("verification") or {}).get("state") == "verified-complete"
            for node in nodes
        )
        roadmap_text = f"Verified product outcomes *{verified}/{len(nodes)}*"
        capability_lines = [
            f"• *{value.get('capability', 'Unknown')}* — "
            f"{'Graduated' if value.get('graduated') else value.get('first_failing_gate') or 'Evidence pending'}"
            for value in graduation.get("capabilities") or []
        ] or ["• Capability evidence is not available"]
        actions = mission.get("generated_actions") or []
        active_lines = [
            f"• *{value.get('target', 'Unknown')}* — {value.get('engineering_purpose') or value.get('reason') or 'In progress'}"
            for value in actions[:5]
        ] or ["• No active product action is currently declared"]
        runtime_lines = [
            f"• *{value.get('display_name') or value.get('runtime')}* — {value.get('status', 'unknown')}"
            for value in convergence.get("runtimes") or []
        ] or ["• Runtime evidence is not available"]
        validation_lines = [
            f"• *{value.get('title') or value.get('stream', 'Validation')}* — {value.get('status', 'pending')}"
            for value in graduation.get("validation_streams") or []
        ] or ["• No product validation stream is currently declared"]
        decisions = [
            f"• `{node.get('ref')}` — {(node.get('semantic_record') or {}).get('decision_packet', {}).get('decision_requested')}"
            for node in graph.get("nodes") or []
            if isinstance((node.get("semantic_record") or {}).get("decision_packet"), dict)
        ] or ["• No Product Owner decision is pending"]
        events = _read_jsonl(Path(evidence.get("supervisor_root") or ".") / "operational-events.jsonl")
        recent = [
            f"• {event.get('event_type', 'activity').replace('_', ' ').title()} — `{event.get('work_item') or 'AXIS'}`"
            for event in events
            if event.get("event_type") in PROGRESS_EVENTS
        ][-4:] or ["• No material product progress in the current evidence window"]
        open_incidents = [value for value in incidents.values() if value.get("status") != "resolved"]
        health_lines = [
            f"• *{name.replace('_', ' ').title()}* — {value['status']} ({'; '.join(value['evidence']) or 'no anomaly'})"
            for name, value in dimensions.items()
        ]
        overall = "healthy" if not open_incidents else "degraded"
        return {
            "summary": {
                "overall": overall,
                "mission": str(mission.get("current_state") or "unknown"),
                "capabilities": capability_text,
                "open_incidents": len(open_incidents),
            },
            "sections": [
                ("AXIS", f"Mission *{mission.get('current_state', 'unknown')}* | {capability_text}"),
                ("ROADMAP", roadmap_text),
                ("CAPABILITIES", "\n".join(capability_lines)),
                ("ACTIVE PRODUCT WORK", "\n".join(active_lines)),
                ("DEPLOYMENT RING", "\n".join(runtime_lines)),
                ("VALIDATION", "\n".join(validation_lines)),
                ("DECISIONS", "\n".join(decisions[:5])),
                ("RECENT PRODUCT PROGRESS", "\n".join(recent)),
                ("WATCHDOG", "\n".join(health_lines)),
            ],
        }

    def run(self) -> dict[str, Any]:
        now = int(self.clock())
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        control = self._control()
        state = load_optional(self.root / "state.json")
        resumed_recoveries = self._resume_pending_recoveries(control, now)
        resumed_incidents = dict(state.get("incidents") or {})
        for transaction in resumed_recoveries:
            incident = transaction.get("incident") or {}
            if incident.get("incident_id"):
                resumed_incidents[str(incident["incident_id"])] = incident
        state["incidents"] = resumed_incidents
        prior_heartbeat = load_optional(self.root / "heartbeat.json")
        cycle_id = uuid.uuid4().hex
        atomic_write(
            self.root / "heartbeat.json",
            {
                "schema": "axis.development-watchdog.heartbeat",
                "schema_version": "1.0.0",
                "cycle_id": cycle_id,
                "status": "running",
                "started_at": timestamp(now),
                "started_at_epoch": now,
                "completed_at": prior_heartbeat.get("completed_at"),
                "completed_at_epoch": prior_heartbeat.get("completed_at_epoch"),
            },
        )
        evidence, anomalies, dimensions = self._observe(
            control, state, prior_heartbeat, now
        )
        evidence["supervisor_root"] = str(self.supervisor_root)
        incidents, newly_opened = self._reconcile_incidents(
            state, anomalies, now
        )
        self._diagnose(control, state, newly_opened, evidence, now)
        new_recoveries = self._recover_incidents(
            control, incidents, newly_opened, now
        )
        projection_recoveries = resumed_recoveries + new_recoveries
        report = self._report(evidence, dimensions, incidents)
        projection_error = None
        try:
            projected = self.projector.project(
                report,
                sorted(incidents.values(), key=lambda value: value["incident_id"]),
                now,
            )
            for value in projected:
                self.projections.append(
                    {
                        "projection_id": uuid.uuid4().hex,
                        "projected_at": timestamp(now),
                        **value,
                    }
                )
            self._finish_projection_recoveries(
                projection_recoveries,
                success=True,
                detail="canonical Slack projection and readback completed",
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 - projection failure is incident evidence
            projection_error = f"{type(exc).__name__}: {exc}"
            self._finish_projection_recoveries(
                projection_recoveries,
                success=False,
                detail=projection_error,
                now=now,
            )
            self.projections.append(
                {
                    "projection_id": uuid.uuid4().hex,
                    "projected_at": timestamp(now),
                    "target_type": "dashboard",
                    "target_id": "overview",
                    "operation": "attempted",
                    "status": "failed",
                    "error": projection_error,
                }
            )
            delivery_anomaly = self._anomaly(
                "slack-delivery-failed",
                "delivery_effectiveness",
                projection_error,
                1,
            )
            anomalies.append(delivery_anomaly)
            dimensions["delivery_effectiveness"] = {
                "status": "degraded",
                "evidence": [projection_error],
            }
            incidents, delivery_opened = self._reconcile_incidents(
                {**state, "incidents": incidents}, anomalies, now
            )
            self._diagnose(control, state, delivery_opened, evidence, now)
            delivery_recoveries = self._recover_incidents(
                control, incidents, delivery_opened, now
            )
            self._finish_projection_recoveries(
                delivery_recoveries,
                success=False,
                detail=projection_error,
                now=now,
            )

        mission = evidence["mission"]
        observation = self.observations.append(
            {
                "observation_id": uuid.uuid4().hex,
                "cycle_id": cycle_id,
                "observed_at": timestamp(now),
                "observed_at_epoch": now,
                "health": dimensions,
                "anomalies": anomalies,
                "mission": mission,
                "cutover": evidence["cutover"],
                "projection_status": "failed" if projection_error else "verified",
            }
        )
        open_incidents = [
            value for value in incidents.values() if value.get("status") != "resolved"
        ]
        new_state = {
            "schema": "axis.development-watchdog.state",
            "schema_version": "1.0.0",
            "cycle": int(state.get("cycle") or 0) + 1,
            "last_cycle_id": cycle_id,
            "last_observed_at": timestamp(now),
            "last_observed_at_epoch": now,
            "overall_status": "degraded" if open_incidents else "healthy",
            "health": dimensions,
            "mission_progress_fingerprint": mission["progress_fingerprint"],
            "mission_progress_since_epoch": mission["progress_since_epoch"],
            "slack_cutover_generation": evidence["cutover"].get("generation"),
            "incidents": incidents,
            "diagnostic_calls": state.get("diagnostic_calls") or [],
            "diagnostic_signatures": state.get("diagnostic_signatures") or {},
            "last_projection_error": projection_error,
            "last_observation_id": observation["observation_id"],
        }
        atomic_write(self.root / "state.json", new_state)
        self.states.append(new_state)
        atomic_write(
            self.root / "heartbeat.json",
            {
                "schema": "axis.development-watchdog.heartbeat",
                "schema_version": "1.0.0",
                "cycle_id": cycle_id,
                "status": "completed",
                "started_at": timestamp(now),
                "started_at_epoch": now,
                "completed_at": timestamp(now),
                "completed_at_epoch": now,
            },
        )
        return {
            "cycle_id": cycle_id,
            "status": new_state["overall_status"],
            "anomalies": [value["anomaly_code"] for value in anomalies],
            "open_incidents": [value["incident_id"] for value in open_incidents],
            "projection": "failed" if projection_error else "verified",
        }
