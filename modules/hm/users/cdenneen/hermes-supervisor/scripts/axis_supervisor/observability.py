import fcntl
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mutation import MutationGate, OperationClass
from .schema_registry import read_record, validate_record, write_record

EVENT_SCHEMA = "axis.external-development-supervisor.operational-event"
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
        "assignment_selected",
        "worker_started",
        "assignment_retry",
        "implementation_completed",
        "mr_created",
        "mr_merged",
        "post_main_verified",
        "grant_consumed",
        "assignment_disposition",
        "observability_recovered",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
