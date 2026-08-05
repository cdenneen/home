import time
import uuid
import json
from pathlib import Path

from .lifecycle import is_terminal
from .models import validate_assignment
from .mutation import MutationGate, OperationClass
from .schema_registry import write_record


class Dispatcher:
    def __init__(self, root: Path):
        self.root = root
        self.assignments = root / "assignments"
        self.assignments.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.gate = MutationGate(root, source="dispatcher")

    def active(self) -> list[dict]:
        values = []
        for path in self.assignments.glob("*.json"):
            value = validate_assignment(
                json.loads(path.read_text(encoding="utf-8")), self.root
            )
            if not is_terminal(value):
                values.append(value)
        return values

    def dispatch(self, graph: dict, run_id: str, selected: dict | None = None) -> dict | None:
        if self.active() or (selected is None and not graph.get("executable_queue")):
            return None
        item = selected or graph["executable_queue"][0]
        source_item = item.get("source_item") or {}
        authority_facts = source_item.get("authority_facts") or {}
        planning_record = None
        if (
            authority_facts.get("approval_matches_record")
            and authority_facts.get("record_digest")
            and authority_facts.get("approval_note")
        ):
            planning_record = {
                "revision": int(authority_facts.get("record_revision") or 1),
                "digest": authority_facts.get("record_digest"),
                "approval_note": authority_facts.get("approval_note"),
            }
        assignment_id = f"assignment-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        assignment = {
            "schema": "axis.external-development-supervisor.assignment",
            "schema_version": "1.0.0",
            "assignment_id": assignment_id,
            "lifecycle_state": "ready-semantic"
            if item.get("kind") in {"semantic-decomposition", "technical-revalidation"}
            else "ready-implementation",
            "kind": item.get("kind"),
            "queue_ref": item.get("ref"),
            "target_ref": item.get("target_ref") or item.get("ref"),
            "work_item": item.get("target_ref") or item.get("ref"),
            "project": item.get("project"),
            "title": item.get("title"),
            "authority": item.get("authority"),
            "governance_state": item.get("classification")
            or (item.get("candidate") or {}).get("result"),
            "planning_record": planning_record,
            "candidate": item.get("candidate"),
            "allowed_paths": (item.get("candidate") or {}).get("allowed_paths") or [],
            "required_tests": (item.get("candidate") or {}).get("required_tests") or [],
            "source_item": source_item,
            "source_fingerprint": item.get("source_fingerprint"),
            "source_inventory_generation_id": graph.get("inventory_generation_id"),
            "revalidation_tier": item.get("revalidation_tier"),
            "ranking_factors": item.get("ranking_factors"),
            "created_by_run": run_id,
            "created_at_epoch": int(time.time()),
            "lease_id": None,
            "lease_uri": None,
            "worker": None,
        }
        validate_assignment(assignment)
        path = self.assignments / f"{assignment_id}.json"
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(path, assignment, "axis.external-development-supervisor.assignment")
        return assignment
