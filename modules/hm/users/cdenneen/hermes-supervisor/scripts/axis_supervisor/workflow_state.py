import fcntl
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .mutation import MutationGate, OperationClass
from .repository_ownership import (
    assignment_ownership,
    ownership_denial,
    ownership_evidence_matches,
    resolve_repository_ownership,
    validate_repository_ownership,
)
from .schema_registry import (
    RecordVersionError,
    read_record,
    validate_record,
    write_record,
)

HANDOFF_SCHEMA = "axis.external-development-supervisor.implementation-handoff"
INTEGRATION_QUEUE_SCHEMA = "axis.external-development-supervisor.integration-queue"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_paths(paths: list[str] | None) -> set[str]:
    return {
        str(PurePosixPath(str(path).lstrip("./"))).rstrip("/")
        for path in paths or []
        if str(path).strip()
    }


def _overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def classify_main_advance(
    source_main_sha: str | None,
    current_main_sha: str | None,
    implementation_paths: list[str] | None = None,
    main_changed_paths: list[str] | None = None,
    *,
    merge_commit_sha: str | None = None,
) -> str:
    if current_main_sha and merge_commit_sha == current_main_sha:
        return "integrated"
    if not source_main_sha or not current_main_sha or source_main_sha == current_main_sha:
        return "unchanged"
    implementation = _normalized_paths(implementation_paths)
    advanced = _normalized_paths(main_changed_paths)
    if not advanced:
        return "advanced-unassessed"
    if implementation and not any(_overlap(a, b) for a in implementation for b in advanced):
        return "compatible"
    return "repair-required"


class WorkflowState:
    def __init__(self, root: Path):
        self.root = root
        self.handoffs = root / "implementation-handoffs"
        self.queue_path = root / "integration-queue.json"
        self.queue_lock_path = root / "integration-queue.lock"
        self.gate = MutationGate(root, source="workflow-state")

    def _authorize(self) -> None:
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)

    def load_queue(self) -> dict:
        if self.queue_path.exists():
            try:
                return read_record(self.queue_path, INTEGRATION_QUEUE_SCHEMA)
            except RecordVersionError:
                legacy = json.loads(self.queue_path.read_text(encoding="utf-8"))
                if legacy.get("schema_version") not in {"1.0.0", "2.0.0"}:
                    raise
                for item in legacy.get("items") or []:
                    ownership = resolve_repository_ownership(
                        [item.get("responsibility")],
                        item.get("repository"),
                        context=f"integration-queue-v1-migration:{item.get('assignment_id')}",
                        allow_repository_inference=True,
                    )
                    item["responsibility"] = ownership["responsibility"]
                    item["repository_ownership"] = ownership
                    item.setdefault("origin_finding", None)
                    item.setdefault("targeted_replay", None)
                    item.setdefault("worktree_context", None)
                legacy["schema_version"] = "3.0.0"
                return legacy
        return {
            "schema": INTEGRATION_QUEUE_SCHEMA,
            "schema_version": "3.0.0",
            "updated_at": utc_now(),
            "items": [],
        }

    def write_queue(self, queue: dict) -> None:
        queue["updated_at"] = utc_now()
        self._authorize()
        write_record(self.queue_path, queue, INTEGRATION_QUEUE_SCHEMA)

    def mutate_queue(self, mutation: Callable[[dict], object]) -> object:
        self.queue_lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.queue_lock_path.open("a", encoding="utf-8") as lock:
            os.chmod(self.queue_lock_path, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            queue = self.load_queue()
            result = mutation(queue)
            self.write_queue(queue)
            return result

    def persist_handoff(self, assignment: dict, result: dict) -> dict:
        ownership = assignment_ownership(
            assignment,
            context=f"implementation-handoff:{assignment.get('assignment_id')}",
        )
        worker_handoff = result.get("handoff") or {}
        handoff = {
            "schema": HANDOFF_SCHEMA,
            "schema_version": "3.0.0",
            "assignment_id": assignment["assignment_id"],
            "work_item": assignment["work_item"],
            "repository": assignment["project"],
            "responsibility": ownership["responsibility"],
            "repository_ownership": ownership,
            "branch": str(result.get("branch") or ""),
            "commit": str(result.get("commit") or ""),
            "allowed_paths": list(assignment.get("allowed_paths") or []),
            "changed_paths": list(result.get("changed_paths") or []),
            "tests": list(worker_handoff.get("tests") or []),
            "mr_iid": worker_handoff.get("mr_iid"),
            "mr_url": worker_handoff.get("mr_url"),
            "source_main_sha": (assignment.get("source_item") or {}).get(
                "repository_head"
            ),
            "created_at": utc_now(),
            "state": "ready-for-integration",
            "origin_finding": assignment.get("origin_finding"),
            "targeted_replay": assignment.get("targeted_replay"),
            "worktree_context": assignment.get("worktree_context"),
        }
        self._authorize()
        write_record(
            self.handoffs / f"{assignment['assignment_id']}.json",
            handoff,
            HANDOFF_SCHEMA,
        )
        return handoff

    def adapt_handoff(self, handoff: dict) -> dict:
        value = dict(handoff)
        if value.get("schema") != HANDOFF_SCHEMA:
            validate_record(value, HANDOFF_SCHEMA)
        if value.get("schema_version") in {"1.0.0", "2.0.0"}:
            ownership = resolve_repository_ownership(
                [value.get("responsibility")],
                value.get("repository"),
                context=f"implementation-handoff-v1-migration:{value.get('assignment_id')}",
                allow_repository_inference=True,
            )
            value["schema_version"] = "3.0.0"
            value["responsibility"] = ownership["responsibility"]
            value["repository_ownership"] = ownership
            value.setdefault("origin_finding", None)
            value.setdefault("targeted_replay", None)
            value.setdefault("worktree_context", None)
        validate_record(value, HANDOFF_SCHEMA)
        return value

    def load_handoff(self, assignment_id: str) -> dict:
        path = self.handoffs / f"{assignment_id}.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        migrated = self.adapt_handoff(value)
        if migrated != value:
            self._authorize()
            write_record(path, migrated, HANDOFF_SCHEMA)
        return migrated

    def enqueue(
        self, assignment: dict, handoff: dict, reviewer: str
    ) -> dict:
        original_handoff = handoff
        handoff = self.adapt_handoff(handoff)
        handoff_path = self.handoffs / f"{assignment['assignment_id']}.json"
        if handoff != original_handoff or not handoff_path.exists():
            self._authorize()
            write_record(handoff_path, handoff, HANDOFF_SCHEMA)
        ownership = assignment_ownership(
            assignment,
            context=f"reviewer-handoff:{assignment.get('assignment_id')}",
        )
        handoff_ownership = validate_repository_ownership(
            handoff.get("responsibility"),
            handoff.get("repository"),
            context=f"reviewer-handoff-record:{assignment.get('assignment_id')}",
        )
        if any(
            handoff_ownership.get(key) != ownership.get(key)
            for key in ("responsibility", "repository", "canonical_repository")
        ) or not ownership_evidence_matches(
            handoff.get("repository_ownership"), handoff_ownership
        ):
            raise ownership_denial(
                ownership,
                context=f"reviewer-handoff:{assignment.get('assignment_id')}",
                reason="handoff-does-not-match-assignment-ownership",
                actual={
                    "responsibility": handoff.get("responsibility"),
                    "repository": handoff.get("repository"),
                    "repository_ownership": handoff.get("repository_ownership"),
                },
            )
        now = utc_now()
        item = {
            "assignment_id": assignment["assignment_id"],
            "work_item": assignment["work_item"],
            "repository": assignment["project"],
            "responsibility": ownership["responsibility"],
            "repository_ownership": ownership,
            "handoff_uri": (
                self.handoffs / f"{assignment['assignment_id']}.json"
            ).resolve().as_uri(),
            "mr_iid": handoff.get("mr_iid"),
            "mr_url": handoff.get("mr_url"),
            "reviewer": reviewer,
            "state": "awaiting-review",
            "main_advance": "unchanged",
            "enqueued_at": now,
            "updated_at": now,
            "last_error": None,
            "origin_finding": assignment.get("origin_finding"),
            "targeted_replay": assignment.get("targeted_replay"),
            "worktree_context": assignment.get("worktree_context"),
        }

        def append(queue: dict) -> dict:
            queue["items"] = [
                existing
                for existing in queue["items"]
                if existing["assignment_id"] != assignment["assignment_id"]
            ] + [item]
            return item

        return self.mutate_queue(append)  # type: ignore[return-value]

    def update_integration(
        self,
        assignment_id: str,
        *,
        state: str,
        main_advance: str | None = None,
        last_error: str | None = None,
    ) -> dict | None:
        def update(queue: dict) -> dict | None:
            item = next(
                (
                    value
                    for value in queue["items"]
                    if value["assignment_id"] == assignment_id
                ),
                None,
            )
            if item is None:
                return None
            item["state"] = state
            item["updated_at"] = utc_now()
            item["last_error"] = last_error
            if main_advance is not None:
                item["main_advance"] = main_advance
            return item

        return self.mutate_queue(update)  # type: ignore[return-value]
