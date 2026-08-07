import fcntl
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .accounting import AccountingLedger
from .mutation import MutationGate, OperationClass
from .schema_registry import read_record, validate_record, write_record

EVENT_SCHEMA = "axis.external-development-supervisor.operational-event"
HEALTH_SCHEMA = "axis.external-development-supervisor.observability-health"
OUTBOX_SCHEMA = "axis.external-development-supervisor.slack-outbox"
DELIVERY_STAGES = frozenset(
    {
        "notification_created",
        "notification_queued",
        "notification_send_attempted",
        "Slack_API_accepted",
        "Slack_message_created",
        "Slack_message_updated",
        "Slack_message_verified",
        "delivery_failed",
        "delivery_unknown",
    }
)
NOTIFY_EVENT_TYPES = frozenset(
    {
        "assignment_retry",
        "observability_recovered",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_observability_failure(
    root: Path,
    *,
    operation: str,
    source: str,
    primary_error: BaseException,
    observability_error: BaseException,
) -> dict[str, Any]:
    path = root / "observability-health.json"
    lock_path = root / "observability-health.lock"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read_record(path, HEALTH_SCHEMA) if path.exists() else None
        now = utc_now()
        errors = list((current or {}).get("errors") or [])[-99:]
        errors.append(
            {
                "error_id": uuid.uuid4().hex,
                "operation": operation,
                "source": source,
                "primary_error": f"{type(primary_error).__name__}: {primary_error}",
                "observability_error": (
                    f"{type(observability_error).__name__}: {observability_error}"
                ),
                "occurred_at": now,
            }
        )
        value = {
            "schema": HEALTH_SCHEMA,
            "schema_version": "1.0.0",
            "status": "degraded",
            "updated_at": now,
            "errors": errors,
        }
        write_record(path, value, HEALTH_SCHEMA)
        return value


def is_routine_analysis_event(event: dict[str, Any]) -> bool:
    details = event.get("details") or {}
    if details.get("assignment_type") not in {
        "read-only-analysis",
        "no-op-verification",
    }:
        return False
    if event.get("event_type") in {"assignment_retry", "observability_recovered"}:
        return False
    return details.get("disposition") not in {
        "blocked",
        "failed",
        "recovery-required",
    }


class OperationalEventLog:
    def __init__(self, root: Path, source: str):
        self.root = root
        self.path = root / "operational-events.jsonl"
        self.outbox_path = root / "slack-outbox.json"
        self.gate = MutationGate(root, source=source)

    def _authorize(self) -> None:
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)

    def _load_outbox(self) -> dict[str, Any]:
        if self.outbox_path.exists():
            return read_record(self.outbox_path, OUTBOX_SCHEMA)
        return {
            "schema": OUTBOX_SCHEMA,
            "schema_version": "1.0.0",
            "notifications": [],
            "updated_at": utc_now(),
        }

    def emit(
        self,
        event_type: str,
        *,
        assignment: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
        notify: bool | None = None,
    ) -> dict[str, Any]:
        assignment = assignment or {}
        details = details or {}
        event = {
            "schema": EVENT_SCHEMA,
            "schema_version": "1.0.0",
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "created_at": utc_now(),
            "created_at_epoch": int(time.time()),
            "assignment_id": assignment.get("assignment_id"),
            "work_item": assignment.get("work_item") or assignment.get("target_ref"),
            "repository": assignment.get("project"),
            "lifecycle_state": assignment.get("lifecycle_state"),
            "details": details,
        }
        validate_record(event, EVENT_SCHEMA)
        self._authorize()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            os.chmod(self.path, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle, fcntl.LOCK_UN)
        should_notify = notify if notify is not None else event_type in NOTIFY_EVENT_TYPES
        if is_routine_analysis_event(event):
            should_notify = False
        if should_notify:
            outbox = self._load_outbox()
            pending = [
                item
                for item in outbox["notifications"]
                if item["current_stage"] != "Slack_message_verified"
            ]
            delivered = [
                item
                for item in outbox["notifications"]
                if item["current_stage"] == "Slack_message_verified"
            ][-200:]
            outbox["notifications"] = delivered + pending
            now = utc_now()
            outbox["notifications"].append(
                {
                    "notification_id": uuid.uuid4().hex,
                    "event_id": event["event_id"],
                    "event": event,
                    "current_stage": "notification_queued",
                    "stage_history": [
                        {"stage": "notification_created", "at": now},
                        {"stage": "notification_queued", "at": now},
                    ],
                    "attempts": 0,
                    "next_attempt_epoch": int(time.time()),
                    "last_error": None,
                    "channel": None,
                    "ts": None,
                    "recovery_summary": False,
                }
            )
            outbox["updated_at"] = now
            self._authorize()
            write_record(self.outbox_path, outbox, OUTBOX_SCHEMA)
        return event

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        values = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                values.append(validate_record(json.loads(line), EVENT_SCHEMA))
            except Exception as exc:
                raise ValueError(
                    f"corrupt operational event line {line_number}: {exc}"
                ) from exc
        return values[-limit:]

    def throughput_metrics(self, start_epoch: int, end_epoch: int) -> dict[str, Any]:
        values = [
            event
            for event in self.events(limit=100_000)
            if start_epoch
            <= int(event.get("created_at_epoch") or 0)
            <= end_epoch
        ]
        selected = {
            event.get("assignment_id"): event
            for event in values
            if event.get("event_type") == "assignment_selected"
            and event.get("assignment_id")
        }
        dispositions = {
            event.get("assignment_id"): event
            for event in values
            if event.get("event_type") == "assignment_disposition"
            and event.get("assignment_id")
        }
        implementation_selected = {
            assignment_id
            for assignment_id, event in selected.items()
            if (event.get("details") or {}).get("assignment_type")
            in {
                "governance-document-mutation",
                "code-implementation",
                "ci-integration-repair",
            }
        }
        analysis_selected = {
            assignment_id
            for assignment_id, event in selected.items()
            if (event.get("details") or {}).get("assignment_type")
            in {"read-only-analysis", "no-op-verification"}
        }
        analysis_completed = {
            assignment_id
            for assignment_id, event in dispositions.items()
            if (event.get("details") or {}).get("disposition")
            in {"analysis-completed", "no-op-verification-completed"}
        }
        implementation_completed = {
            event.get("assignment_id")
            for event in values
            if event.get("event_type") == "implementation_completed"
            and event.get("assignment_id")
        }
        post_main_verified = {
            event.get("assignment_id")
            for event in values
            if event.get("event_type") == "post_main_verified"
            and event.get("assignment_id")
        }
        merged = {
            event.get("assignment_id")
            for event in values
            if event.get("event_type") == "mr_merged"
            and event.get("assignment_id")
        }
        retries = sum(event.get("event_type") == "assignment_retry" for event in values)
        grants_consumed = sum(
            event.get("event_type") == "grant_consumed" for event in values
        )
        blocked = sum(
            (event.get("details") or {}).get("disposition")
            in {"blocked", "failed", "recovery-required"}
            for event in dispositions.values()
        )

        def average_duration(end_event_type: str) -> int | None:
            ends = {
                event.get("assignment_id"): int(event.get("created_at_epoch") or 0)
                for event in values
                if event.get("event_type") == end_event_type
                and event.get("assignment_id")
            }
            durations = [
                ends[assignment_id]
                - int(event.get("created_at_epoch") or ends[assignment_id])
                for assignment_id, event in selected.items()
                if assignment_id in ends
            ]
            return round(sum(durations) / len(durations)) if durations else None

        analysis_work_items = {
            selected[assignment_id].get("work_item")
            for assignment_id in analysis_completed
            if assignment_id in selected
        }
        implementation_work_items = {
            selected[assignment_id].get("work_item")
            for assignment_id in implementation_selected
            if assignment_id in selected
        }

        def percent(numerator: int, denominator: int) -> int:
            return round(numerator * 100 / denominator) if denominator else 0

        return {
            "start_epoch": start_epoch,
            "end_epoch": end_epoch,
            "window_days": max(1, round((end_epoch - start_epoch) / 86_400)),
            "assignments_selected": len(selected),
            "analysis_selected": len(analysis_selected),
            "analysis_completed": len(analysis_completed),
            "implementation_selected": len(implementation_selected),
            "implementation_commits": len(implementation_completed),
            "merged": len(merged),
            "post_main_verified": len(post_main_verified),
            "blocked_or_failed": blocked,
            "retries": retries,
            "grants_consumed": grants_consumed,
            "analysis_to_implementation_percent": percent(
                len(analysis_work_items & implementation_work_items),
                len(analysis_work_items),
            ),
            "implementation_to_merge_percent": percent(
                len(merged), len(implementation_selected)
            ),
            "merge_to_verified_percent": percent(
                len(post_main_verified), len(merged)
            ),
            "retry_rate_percent": percent(retries, len(selected)),
            "average_implementation_seconds": average_duration(
                "implementation_completed"
            ),
            "average_integration_seconds": average_duration(
                "post_main_verified"
            ),
        }


def record_event(
    root: Path,
    event_type: str,
    *,
    assignment: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    source: str,
    notify: bool | None = None,
) -> dict[str, Any]:
    return OperationalEventLog(root, source).emit(
        event_type, assignment=assignment, details=details, notify=notify
    )


def record_engineering_retrospective(
    root: Path, assignment: dict[str, Any], *, source: str
) -> dict[str, Any]:
    attempts = AccountingLedger(root).model_attempts_for_assignment(
        assignment["assignment_id"]
    )
    event_log = OperationalEventLog(root, source)
    historical_events = event_log.events(limit=100_000)
    terminal_events = [
        event
        for event in historical_events
        if event.get("event_type") == "assignment_disposition"
        and event.get("assignment_id") == assignment["assignment_id"]
        and (event.get("details") or {}).get("disposition")
        not in {"awaiting-integration", None}
    ]
    finished_epoch = (
        int(terminal_events[-1].get("created_at_epoch") or time.time())
        if terminal_events
        else int(time.time())
    )
    duration = max(
        0, finished_epoch - int(assignment.get("created_at_epoch") or finished_epoch)
    )
    prior_retrospectives = [
        event
        for event in historical_events
        if event.get("event_type") == "engineering_retrospective"
        and event.get("assignment_id") == assignment["assignment_id"]
    ]
    result_state = str(assignment.get("result_state") or "unknown")
    work_item_disposition = str(
        assignment.get("work_item_disposition") or "not-evaluated"
    )
    successful = result_state in {
        "analysis-completed",
        "no-op-verification-completed",
        "implementation-commit-created",
        "implementation-complete",
        "awaiting-integration",
        "integrated-post-main-verified",
        "repository-converged",
        "runtime-converged",
        "canonical-complete",
    }
    integration = assignment.get("integration") or {}
    pipeline = integration.get("pipeline") or {}
    merge_request = integration.get("merge_request") or {}
    details = {
        "assignment_type": assignment.get("assignment_type"),
        "retrospective_revision": len(prior_retrospectives) + 1,
        "supersedes_event_id": prior_retrospectives[-1]["event_id"]
        if prior_retrospectives
        else None,
        "result_state": result_state,
        "work_item_disposition": work_item_disposition,
        "duration_seconds": duration,
        "model_attempts": attempts,
        "retry_count": max(0, attempts - 1),
        "selection_rationale": assignment.get("selection_rationale"),
        "milestone": (assignment.get("source_item") or {}).get("milestone"),
        "repository": assignment.get("project"),
        "mutation_grant_id": assignment.get("mutation_grant_id"),
        "ci_duration_seconds": pipeline.get("duration"),
        "pipeline_status": pipeline.get("status"),
        "merge_conflict": bool(merge_request.get("has_conflicts")),
        "roadmap_convergence_improved": work_item_disposition
        in {
            "no-op-verified",
            "requires-implementation",
            "requires-integration",
            "evidence-recorded-awaiting-fresh-recognition",
        },
        "successful_pattern": (
            f"{assignment.get('assignment_type')}:{work_item_disposition}"
            if successful
            else None
        ),
        "failure_pattern": str(assignment.get("error") or "")[-2_000:]
        if not successful
        else None,
        "improvement_question": (
            "Did this selection reduce the current constraint or unlock more "
            "verified roadmap progress than the first deferred alternative?"
        ),
    }
    return event_log.emit(
        "engineering_retrospective",
        assignment=assignment,
        details=details,
        notify=False,
    )
