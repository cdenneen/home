import time
import uuid
import json
from pathlib import Path

from .lifecycle import is_terminal
from .assignment_grants import create_grant
from .models import validate_assignment
from .mutation import MutationGate, OperationClass
from .observability import record_event
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
        return sorted(
            values,
            key=lambda value: (
                int(value.get("last_integration_check_epoch") or 0),
                int(value.get("created_at_epoch") or 0),
                str(value.get("assignment_id") or ""),
            ),
        )

    def dispatch(self, graph: dict, run_id: str, selected: dict | None = None) -> dict | None:
        active = self.active()
        control = json.loads((self.root / "control.json").read_text(encoding="utf-8"))
        if len(active) >= int(control.get("max_active_assignments", 1)) or (
            selected is None and not graph.get("executable_queue")
        ):
            return None
        item = selected or graph["executable_queue"][0]
        quarantine_path = self.root / "quarantines.json"
        if quarantine_path.exists():
            quarantine = json.loads(quarantine_path.read_text(encoding="utf-8"))
            now = int(time.time())
            if any(
                value.get("work_item") == item.get("target_ref")
                and int(value.get("expires_at_epoch") or 0) > now
                for value in quarantine.get("items") or []
            ):
                return None
        if any(value.get("project") == item.get("project") for value in active):
            return None
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
            "assignment_type": item.get("assignment_type")
            or (
                "read-only-analysis"
                if item.get("kind") == "semantic-decomposition"
                else "no-op-verification"
                if item.get("kind") == "technical-revalidation"
                else "repository-convergence"
                if item.get("kind") == "repository-convergence"
                else "code-implementation"
            ),
            "result_state": "pending",
            "work_item_disposition": "not-evaluated",
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
            "selection_rationale": item.get("selection_rationale")
            or "highest deterministic eligible queue entry",
            "created_by_run": run_id,
            "created_at_epoch": int(time.time()),
            "lease_id": None,
            "lease_uri": None,
            "worker": None,
            "mutation_grant_id": None,
            "mutation_grant_uri": None,
        }
        if assignment["assignment_type"] == "code-implementation":
            prior_failures = []
            for prior_path in self.assignments.glob("*.json"):
                prior = validate_assignment(
                    json.loads(prior_path.read_text(encoding="utf-8")), self.root
                )
                if (
                    prior.get("work_item") == assignment["work_item"]
                    and prior.get("assignment_type")
                    in {"code-implementation", "ci-integration-repair"}
                    and prior.get("lifecycle_state") == "failed"
                ):
                    prior_failures.append(prior)
            if prior_failures:
                prior = max(
                    prior_failures,
                    key=lambda value: int(value.get("created_at_epoch") or 0),
                )
                patch_path = (
                    self.root
                    / "recovery"
                    / f"{prior['assignment_id']}.planned.patch"
                )
                assignment["assignment_type"] = "ci-integration-repair"
                assignment["recovery_context"] = {
                    "prior_assignment_id": prior["assignment_id"],
                    "failure": str(prior.get("error") or "")[-12_000:],
                    "prior_patch": patch_path.read_text(encoding="utf-8")[-50_000:]
                    if patch_path.exists()
                    else None,
                    "changed_hypothesis": (
                        "Preserve participant-visible success paths; apply current-control "
                        "denial only when current controls fail."
                    ),
                }
        path = self.assignments / f"{assignment_id}.json"
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        if assignment["assignment_type"] in {
            "governance-document-mutation",
            "code-implementation",
            "ci-integration-repair",
        }:
            create_grant(self.root, assignment, control)
        validate_assignment(assignment)
        write_record(path, assignment, "axis.external-development-supervisor.assignment")
        record_event(
            self.root,
            "assignment_selected",
            assignment=assignment,
            details={
                "model": "gpt-5.4"
                if assignment["kind"] in {"semantic-decomposition", "technical-revalidation"}
                else "gpt-5.3-codex",
                "authority": assignment.get("authority"),
                "assignment_type": assignment["assignment_type"],
                "mutation_grant_id": assignment.get("mutation_grant_id"),
                "selection_rationale": assignment["selection_rationale"],
                "expected_next_phase": assignment["lifecycle_state"],
            },
            source="dispatcher",
        )
        return assignment
