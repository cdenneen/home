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
