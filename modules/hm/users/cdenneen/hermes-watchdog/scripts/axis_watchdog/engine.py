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
from .recovery import (
    RecoveryExecutor,
    RecoveryJournal,
    occurrence_generation,
    unavailable_diagnostic,
)

Clock = Callable[[], int]

PROGRESS_EVENTS = {
    "implementation_completed",
    "mr_created",
    "mr_merged",
    "post_main_verified",
    "capability_deployment_verified",
}
REAL_PRODUCT_EVENTS = {
    "implementation_completed",
    "mr_created",
    "mr_updated",
    "mr_merged",
    "post_main_verified",
    "capability_deployment_verified",
    "validation_completed",
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


def _progress_coherence(
    inventory: dict[str, Any],
    graph: dict[str, Any],
    graduation: dict[str, Any],
    mission: dict[str, Any],
) -> dict[str, Any]:
    """Independently validate the Supervisor's published progress snapshot."""
    inventory_generation = inventory.get("generation_id")
    graph_generation = graph.get("generation_id")
    graduation_digest = graduation.get("projection_digest")
    convergence_digest = graduation.get("source_convergence_digest")
    mission_sources = mission.get("source_generations") or {}
    checks = {
        "graph_inventory": (graph.get("inventory_generation_id"), inventory_generation),
        "graduation_inventory": (
            graduation.get("source_inventory_generation_id"),
            inventory_generation,
        ),
        "graduation_graph": (graduation.get("source_graph_generation_id"), graph_generation),
        "mission_inventory": (mission_sources.get("inventory"), inventory_generation),
        "mission_graph": (mission_sources.get("graph"), graph_generation),
        "mission_graduation": (mission_sources.get("graduation"), graduation_digest),
        "mission_convergence": (mission_sources.get("convergence"), convergence_digest),
    }
    failures = [
        name
        for name, (actual, expected) in checks.items()
        if actual is None or expected is None or actual != expected
    ]
    return {
        "trusted": not failures,
        "failures": failures,
        "checks": {
            name: {"actual": actual, "expected": expected}
            for name, (actual, expected) in checks.items()
        },
    }


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


def _event_project(event: dict[str, Any]) -> str:
    details = event.get("details") or {}
    return str(
        event.get("project")
        or event.get("repository")
        or details.get("repository")
        or details.get("project")
        or ""
    )


def _is_real_product_event(event: dict[str, Any], repositories: set[str]) -> bool:
    """Reject worker, Slack, and other internal activity as delivery evidence."""
    event_type = str(event.get("event_type") or "")
    if event_type not in REAL_PRODUCT_EVENTS:
        return False
    if repositories and _event_project(event) not in repositories:
        return False
    if event_type == "implementation_completed":
        # A changed worktree without a durable commit remains internal custody.
        return bool((event.get("details") or {}).get("commit"))
    if event_type in {"mr_created", "mr_updated", "mr_merged"}:
        details = event.get("details") or {}
        return bool(details.get("mr_iid") or details.get("mr_url"))
    return True


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
        fault: Any | None = None,
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
        self.fault = fault
        self._health_restored_this_cycle: list[str] = []
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

    def _real_delivery(
        self, inventory: dict[str, Any], state: dict[str, Any], now: int
    ) -> dict[str, Any]:
        repositories = set(inventory.get("repository_allowlist") or [])
        if not repositories:
            repositories = set((inventory.get("repositories") or {}).keys())
        events = _read_jsonl(self.supervisor_root / "operational-events.jsonl")
        real_events = [
            event
            for event in events
            if _is_real_product_event(event, repositories)
            and int(event.get("created_at_epoch") or 0) <= now
        ]
        for merge_request in inventory.get("open_merge_requests") or []:
            updated_epoch = parse_timestamp(merge_request.get("updated_at"))
            if (
                updated_epoch is not None
                and updated_epoch <= now
                and _event_project(merge_request) in repositories
            ):
                real_events.append(
                    {
                        "event_type": "mr_updated",
                        "created_at": merge_request.get("updated_at"),
                        "created_at_epoch": updated_epoch,
                        "repository": merge_request.get("project"),
                        "details": {
                            "mr_iid": merge_request.get("iid"),
                            "mr_url": merge_request.get("web_url"),
                            "sha": merge_request.get("sha"),
                        },
                    }
                )
        latest = max(real_events, key=lambda event: int(event.get("created_at_epoch") or 0), default=None)
        latest_epoch = int((latest or {}).get("created_at_epoch") or 0)
        prior_epoch = int(state.get("last_real_product_transition_epoch") or 0)
        prior_transition = state.get("last_real_product_transition")
        prior_type = state.get("last_real_product_transition_type")
        if prior_epoch > latest_epoch:
            latest_epoch = prior_epoch
            latest = None
        threshold = int(self._control()["delivery_freshness_seconds"])
        return {
            "last_real_product_transition": (latest or {}).get("created_at")
            or prior_transition,
            "last_real_product_transition_epoch": latest_epoch or None,
            "last_real_product_transition_type": (latest or {}).get("event_type")
            or prior_type,
            "time_since_real_product_transition_seconds": max(0, now - latest_epoch)
            if latest_epoch
            else threshold + 1,
            "threshold_seconds": threshold,
            "event_count": len(real_events),
        }

    @staticmethod
    def _dispatchable_governed_mutation_action(action: dict[str, Any]) -> bool:
        """Return whether an action can require a governed product transition.

        ``executable`` also covers bounded evidence and validation work.  Delivery
        freshness is about repository mutation, so it must only consider the more
        specific dispatch contract published by Supervisor.
        """
        authority_state = str(
            action.get("authority_state")
            or (action.get("authority") or {}).get("state")
            or ""
        )
        scope = action.get("assignment_scope") or {}
        return bool(
            action.get("kind") == "dispatch-executable"
            and action.get("executable")
            and action.get("dispatch_class") == "DISPATCHABLE"
            and authority_state != "preparation-only"
            and action.get("source_ref")
            and action.get("expected_effect")
            and action.get("expected_gates")
            and action.get("worker_path") == "implementation"
            and action.get("handoff_path") == "implementation-handoff"
            and action.get("review_path") == "independent-review"
            and scope.get("target_ref") == action.get("target")
            and scope.get("project")
            and scope.get("allowed_paths")
            and scope.get("required_tests")
        )

    @staticmethod
    def _external_only_validation_stream(
        action: dict[str, Any], missing_gates: list[dict[str, Any]]
    ) -> bool:
        """Whether a validation stream can only advance external acceptance.

        Validation streams are normally autonomous evidence work.  Product-owner
        streams are different: when every expected gate is currently marked
        ``external_only``, the system cannot make the next transition itself.
        """
        if (
            action.get("kind") != "validate-capability-stream"
            or action.get("gate_owner") != "product-owner"
        ):
            return False
        expected_gates = action.get("expected_gates") or []
        external_gates = {
            (str(gate.get("capability") or ""), str(gate.get("gate") or ""))
            for gate in missing_gates
            if gate.get("external_only")
        }
        return bool(expected_gates) and all(
            (str(gate.get("capability") or ""), str(gate.get("gate") or ""))
            in external_gates
            for gate in expected_gates
        )

    @classmethod
    def _runnable_expected_action(
        cls, action: dict[str, Any], missing_gates: list[dict[str, Any]]
    ) -> bool:
        """Recognize autonomous work that should keep progress monitoring active."""
        if not action.get("executable"):
            return False
        if action.get("kind") == "dispatch-executable":
            return cls._dispatchable_governed_mutation_action(action)
        if cls._external_only_validation_stream(action, missing_gates):
            return False
        return action.get("kind") in {
            "collect-capability-evidence",
            "reconcile-active-assignment",
            "reconcile-missing-evidence",
            "validate-capability-stream",
        }

    @classmethod
    def _executable_work_exists(cls, graph: dict[str, Any], mission: dict[str, Any]) -> bool:
        """Whether governed repository mutation work is actually dispatchable.

        Graph nodes and queue entries alone can represent preparation-only or
        no-op revalidation.  The current mission action is the bounded dispatch
        contract and is therefore the authoritative source for delivery pressure.
        """
        del graph
        return any(
            cls._dispatchable_governed_mutation_action(action)
            for action in mission.get("generated_actions") or []
        )

    def _stale_approved_merge_requests(
        self, inventory: dict[str, Any], now: int, threshold: int
    ) -> list[dict[str, Any]]:
        try:
            merge_lanes = json.loads(
                (self.supervisor_root / "merge-lanes.json").read_text(
                    encoding="utf-8"
                )
            ).get("items") or []
        except (OSError, json.JSONDecodeError, AttributeError):
            merge_lanes = []
        owned_lanes = {
            f"{lane.get('repository')}!{lane.get('mr_iid')}"
            for lane in merge_lanes
            if isinstance(lane, dict)
            and lane.get("owner")
            and lane.get("lane") not in {"EXTERNAL_WAIT", "PRODUCT_OWNER_DECISION"}
        }
        stale = []
        for merge_request in inventory.get("open_merge_requests") or []:
            mergeable = str(merge_request.get("merge_status") or "").lower() in {
                "mergeable",
                "can_be_merged",
            }
            approved = bool(
                merge_request.get("approved")
                or merge_request.get("approved_by")
                or merge_request.get("approval_state") == "approved"
            )
            owner = merge_request.get("merge_owner") or merge_request.get("assignees")
            updated_epoch = parse_timestamp(merge_request.get("updated_at"))
            if (
                mergeable
                and approved
                and not owner
                and f"{merge_request.get('project')}!{merge_request.get('iid')}"
                not in owned_lanes
                and updated_epoch is not None
                and now - updated_epoch > threshold
            ):
                stale.append(merge_request)
        return stale

    @staticmethod
    def _job(jobs: list[dict[str, Any]], name: str) -> dict[str, Any]:
        matches = [job for job in jobs if job.get("name") == name]
        return matches[0] if len(matches) == 1 else {}

    @staticmethod
    def _slack_writer_state(
        jobs: list[dict[str, Any]], generation: str
    ) -> dict[str, Any]:
        active_reporters = [
            job
            for job in jobs
            if job.get("name") == "axis-development-supervisor-report"
            and job.get("enabled")
        ]
        watchdog_job = Watchdog._job(jobs, "axis-development-watchdog")
        watchdog_writer = bool(watchdog_job.get("enabled")) and generation in {
            "C",
            "D",
            "E",
        }
        active_writers = len(active_reporters) + int(watchdog_writer)
        return {
            "generation": generation,
            "active_reporter_ids": [str(job.get("id") or "") for job in active_reporters],
            "watchdog_mode": "shadow" if generation in {"A", "B"} else "writer",
            "watchdog_writer": watchdog_writer,
            "active_writer_count": active_writers,
            "conflict": active_writers > 1,
        }

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
        missing_gates = mission.get("missing_gates") or []
        runnable = any(
            Watchdog._runnable_expected_action(action, missing_gates)
            for action in mission.get("generated_actions") or []
        )
        if mission.get("external_blockers") and not runnable:
            return True, "mission is waiting on explicit external authority"
        if missing_gates and not runnable and all(
            gate.get("external_only") for gate in missing_gates
        ):
            return True, "mission is waiting on external-only acceptance"
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
        repair_repository = "cdenneen/home" if repair else None
        evidence_fingerprint = _digest(
            {
                "anomaly_code": code,
                "dimension": dimension,
                "recovery_level": level,
                "repair_repository": repair_repository,
            }
        )
        return {
            "anomaly_code": code,
            "dimension": dimension,
            "summary": summary,
            "recovery_level": level,
            "repair_repository": repair_repository,
            "evidence_fingerprint": evidence_fingerprint,
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
        cutover = load_optional(self.root / "slack-cutover.json")
        cutover_generation = str(cutover.get("generation") or "A")
        writer_state = self._slack_writer_state(jobs, cutover_generation)
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
        if writer_state["conflict"]:
            anomalies.append(
                self._anomaly(
                    "routine-slack-authority-conflict",
                    "control",
                    "multiple active Slack writers violate the configured cutover generation",
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

        real_delivery = self._real_delivery(records["inventory"], state, now)
        executable_work = self._executable_work_exists(records["execution-graph"], mission)
        if (
            executable_work
            and real_delivery["time_since_real_product_transition_seconds"]
            > real_delivery["threshold_seconds"]
        ):
            anomalies.append(
                self._anomaly(
                    "real-product-delivery-stale",
                    "delivery_effectiveness",
                    "no governed-repository product transition for "
                    f"{real_delivery['time_since_real_product_transition_seconds']}s while executable work exists",
                    5,
                )
            )
        stale_merge_requests = self._stale_approved_merge_requests(
            records["inventory"], now, int(control["delivery_freshness_seconds"])
        )
        if stale_merge_requests:
            refs = ", ".join(
                f"{value.get('project')}!{value.get('iid')}"
                for value in stale_merge_requests[:4]
            )
            anomalies.append(
                self._anomaly(
                    "stale-approved-merge-lane",
                    "delivery_effectiveness",
                    "approved mergeable merge request(s) lack a merge-lane owner: " + refs,
                    2,
                )
            )

        coherence = _progress_coherence(
            records["inventory"],
            records["execution-graph"],
            records["capability-graduation"],
            mission,
        )
        previous_fingerprint = str(state.get("mission_progress_fingerprint") or "")
        progress_since = int(state.get("mission_progress_since_epoch") or now)
        threshold = self._historical_threshold(control)
        if not coherence["trusted"]:
            fingerprint = "untrusted:" + _digest(coherence)
            progress_snapshot = {"coherence": coherence}
            progress_since = now
            expected_wait = False
            wait_reason = "source generations are incoherent"
            anomalies.append(
                self._anomaly(
                    "supervisor-state-incoherent",
                    "mission_progress",
                    "supervisor state generations disagree: "
                    + ", ".join(coherence["failures"]),
                    4,
                    repair=True,
                )
            )
        else:
            fingerprint, progress_snapshot = self._mission_progress(
                mission, records["capability-graduation"]
            )
            if fingerprint != previous_fingerprint:
                progress_since = now
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
        stuck_age = now - progress_since
        dimensions["mission_progress"] = {
            "status": "waiting" if expected_wait else "degraded" if any(a["dimension"] == "mission_progress" for a in anomalies) else "healthy",
            "evidence": [
                f"progress age {stuck_age}s",
                f"historical threshold {threshold}s",
                wait_reason or "no expected wait",
            ],
        }
        dimensions["delivery_effectiveness"]["evidence"] = [
            "last real product transition "
            + (
                str(real_delivery["last_real_product_transition"])
                if real_delivery["last_real_product_transition"]
                else "not observed"
            ),
            f"time since real product transition "
            f"{real_delivery['time_since_real_product_transition_seconds']}s",
        ]
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
                "coherence": coherence,
            },
            "real_delivery": {
                **real_delivery,
                "executable_work_exists": executable_work,
                "stale_approved_merge_requests": [
                    {
                        "project": value.get("project"),
                        "iid": value.get("iid"),
                        "web_url": value.get("web_url"),
                    }
                    for value in stale_merge_requests
                ],
            },
            "outbox": outbox_health,
            "cutover": cutover,
            "slack_writers": writer_state,
        }
        return evidence, anomalies, dimensions

    @staticmethod
    def _incident_id(code: str) -> str:
        return "wd-" + hashlib.sha256(code.encode()).hexdigest()[:12]

    def _rehydrate_completed_recoveries(
        self, state: dict[str, Any], anomalies: list[dict[str, Any]]
    ) -> None:
        incidents = dict(state.get("incidents") or {})
        for anomaly in anomalies:
            incident_id = self._incident_id(str(anomaly["anomaly_code"]))
            transaction, health_restored = self.recovery_journal.completed_observation(
                incident_id,
                str(anomaly["evidence_fingerprint"]),
            )
            if not transaction:
                continue
            authoritative = dict(transaction.get("incident") or {})
            current = incidents.get(incident_id) or {}
            same_occurrence = bool(current) and (
                current.get("occurrence_generation")
                == transaction.get("occurrence_generation")
                and current.get("evidence_fingerprint")
                == transaction.get("evidence_fingerprint")
            )
            if health_restored:
                if same_occurrence:
                    resolved = next(
                        (
                            entry
                            for entry in reversed(self.incidents.entries())
                            if entry.get("incident_id") == incident_id
                            and entry.get("occurrence_generation")
                            == transaction.get("occurrence_generation")
                            and entry.get("event") == "resolved"
                        ),
                        {
                            **authoritative,
                            "status": "resolved",
                            "event": "resolved",
                            "resolved_at": transaction.get("updated_at"),
                        },
                    )
                    incidents[incident_id] = dict(resolved)
                continue
            if (
                current
                and current.get("status") != "resolved"
                and not same_occurrence
            ):
                continue
            authoritative["status"] = "recovering"
            authoritative["event"] = "recovery-started"
            incidents[incident_id] = authoritative
        state["incidents"] = incidents

    def _close_inactive_completed_recoveries(
        self,
        state: dict[str, Any],
        anomalies: list[dict[str, Any]],
        now: int,
    ) -> None:
        active_evidence = {
            (
                self._incident_id(str(anomaly["anomaly_code"])),
                str(anomaly["evidence_fingerprint"]),
            )
            for anomaly in anomalies
        }
        incidents = dict(state.get("incidents") or {})
        incident_entries = self.incidents.entries()
        for transaction in self.recovery_journal.unfinalized_completed():
            identity = (
                str(transaction["incident_id"]),
                str(transaction["evidence_fingerprint"]),
            )
            if identity in active_evidence:
                continue
            authoritative = dict(transaction.get("incident") or {})
            current = incidents.get(str(transaction["incident_id"])) or {}
            if current and current.get("status") != "resolved" and (
                current.get("occurrence_generation")
                != transaction.get("occurrence_generation")
                or current.get("evidence_fingerprint")
                != transaction.get("evidence_fingerprint")
            ):
                continue
            resolved = next(
                (
                    entry
                    for entry in reversed(incident_entries)
                    if entry.get("incident_id") == transaction["incident_id"]
                    and entry.get("occurrence_generation")
                    == transaction.get("occurrence_generation")
                    and entry.get("event") == "resolved"
                ),
                None,
            )
            if resolved is None:
                resolved = {
                    **authoritative,
                    "status": "resolved",
                    "event": "resolved",
                    "observed_at": timestamp(now),
                    "resolved_at": timestamp(now),
                }
                self.incidents.append(resolved)
                incident_entries.append(resolved)
            transaction = self.recovery_journal.transition(
                transaction,
                action="deterministic-health-restored",
                target=authoritative.get("repair_repository")
                or "axis-development-watchdog",
                status="completed",
                transition="health-restored",
                detail="deterministic anomaly is absent during startup finalization",
                now=now,
            )
            incidents[str(transaction["incident_id"])] = dict(resolved)
            self._health_restored_this_cycle.append(transaction["recovery_id"])
        state["incidents"] = incidents

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
                opened_at = timestamp(now)
                incident = {
                    "incident_id": incident_id,
                    **anomaly,
                    "status": "opened",
                    "event": "reopened" if reopened else "opened",
                    "opened_at": opened_at,
                    "observed_at": timestamp(now),
                    "occurrences": int(previous.get("occurrences") or 0) + 1,
                    "diagnosis": None,
                }
                incident["occurrence_generation"] = occurrence_generation(incident)
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
                transaction = self.recovery_journal.transition(
                    transaction,
                    action="deterministic-health-restored",
                    target=incident.get("repair_repository")
                    or "axis-development-watchdog",
                    status="completed",
                    transition="health-restored",
                    detail="deterministic anomaly is no longer present",
                    now=now,
                )
                self._health_restored_this_cycle.append(transaction["recovery_id"])
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
                and entry.get("opened_at") == incident.get("opened_at")
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
        repositories = set(records["inventory"].get("repository_allowlist") or [])
        if not repositories:
            repositories = set((records["inventory"].get("repositories") or {}).keys())
        recent = [
            f"• {event.get('event_type', 'activity').replace('_', ' ').title()} — `{event.get('work_item') or 'AXIS'}`"
            for event in events
            if _is_real_product_event(event, repositories)
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
        self._health_restored_this_cycle = []
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
        self._close_inactive_completed_recoveries(state, anomalies, now)
        self._rehydrate_completed_recoveries(state, anomalies)
        incidents, newly_opened = self._reconcile_incidents(
            state, anomalies, now
        )
        if self._health_restored_this_cycle and self.fault:
            self.fault("after-health-restored-before-state")
        self._diagnose(control, state, newly_opened, evidence, now)
        new_recoveries = self._recover_incidents(
            control, incidents, newly_opened, now
        )
        if any(
            transaction.get("status") == "completed"
            for transaction in new_recoveries
        ) and self.fault:
            self.fault("after-recovery-completed-before-state")
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
            "last_real_product_transition": evidence["real_delivery"][
                "last_real_product_transition"
            ],
            "last_real_product_transition_epoch": evidence["real_delivery"][
                "last_real_product_transition_epoch"
            ],
            "time_since_real_product_transition_seconds": evidence["real_delivery"][
                "time_since_real_product_transition_seconds"
            ],
            "slack_cutover_generation": evidence["cutover"].get("generation"),
            "incidents": incidents,
            "diagnostic_calls": state.get("diagnostic_calls") or [],
            "diagnostic_signatures": state.get("diagnostic_signatures") or {},
            "last_projection_error": projection_error,
            "last_observation_id": observation["observation_id"],
        }
        atomic_write(self.root / "state.json", new_state)
        self.states.append(new_state)
        self.recovery_journal.mark_state_finalized(new_state["incidents"], now)
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
