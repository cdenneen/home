import json
import time
import uuid
from pathlib import Path

from .assignment_grants import create_grant
from .capability_graduation import read_capability_graduation
from .frontier import compatible
from .lifecycle import is_terminal
from .missions import read_mission_record
from .models import validate_assignment
from .mutation import MutationGate, OperationClass
from .noop import is_suppressed_no_op, no_op_fingerprint
from .observability import record_event
from .repository_ownership import resolve_repository_ownership
from .schema_registry import RecordError, read_record, write_record

READ_ONLY_ASSIGNMENT_TYPES = {"read-only-analysis", "no-op-verification"}
ACTION_CONTRACT_FIELDS = {
    "engineering_purpose",
    "gate_owner",
    "expected_gates",
    "expected_capabilities",
    "expected_milestones",
    "expected_debt_reduction",
    "expected_evidence",
    "capability_context",
    "merge_impact_projection",
    "convergence_fingerprint",
    "evidence_model_fingerprint",
    "applicability_model_revision",
    "pre_snapshot",
    "suppression_fingerprint",
}


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

    def _mission_action(self, item: dict, graph: dict) -> tuple[dict | None, str | None]:
        path = self.root / "active-mission.json"
        if not path.exists():
            return None, "mission-unavailable:missing"
        try:
            mission = read_mission_record(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return None, f"mission-unavailable:{type(exc).__name__}"
        item_ref = item.get("ref")
        target = item.get("target_ref") or item_ref
        matches = [
            action
            for action in mission.get("generated_actions") or []
            if action.get("kind") == "dispatch-executable"
            and action.get("source_ref") == item_ref
            and action.get("target") == target
        ]
        if not matches:
            return None, "mission-action-nonmatching"
        action = matches[0]
        if action.get("lifecycle") != "CURRENT":
            return None, "mission-action-stale"
        if (action.get("binding") or {}).get("generation", {}).get("graph") != graph.get(
            "generation_id"
        ):
            return None, "mission-action-generation-mismatch"
        return action, None

    def _finding_frontier_selected(self, item: dict, graph: dict) -> bool:
        if not item.get("finding_identity"):
            return True
        path = self.root / "executable-frontier.json"
        try:
            frontier = read_record(
                path, "axis.external-development-supervisor.executable-frontier"
            )
        except RecordError as exc:
            self._record_frontier_block(
                item, graph, f"frontier-unavailable:{type(exc).__name__}"
            )
            return False
        if frontier.get("source_generation_id") != graph.get("generation_id"):
            self._record_frontier_block(
                item,
                graph,
                "frontier-generation-mismatch",
                str(frontier.get("source_generation_id") or "") or None,
            )
            return False
        if item.get("ref") not in set(frontier.get("selected") or []):
            self._record_frontier_block(
                item,
                graph,
                "finding-not-selected-by-frontier",
                str(frontier.get("source_generation_id") or "") or None,
            )
            return False
        return True

    def _record_frontier_block(
        self,
        item: dict,
        graph: dict,
        reason: str,
        frontier_source_generation_id: str | None = None,
    ) -> None:
        record_event(
            self.root,
            "finding_dispatch_blocked",
            details={
                "reason": reason,
                "finding_identity": item.get("finding_identity"),
                "frontier_source_generation_id": frontier_source_generation_id,
                "graph_generation_id": graph.get("generation_id"),
            },
            source="dispatcher",
        )

    @staticmethod
    def _dispatchable_action(action: dict | None, item: dict) -> bool:
        if action is None:
            return False
        scope = action.get("assignment_scope") or {}
        candidate = item.get("candidate") or {}
        return bool(
            action.get("dispatch_class") == "DISPATCHABLE"
            and action.get("worker_path") == "implementation"
            and action.get("handoff_path") == "implementation-handoff"
            and action.get("review_path") == "independent-review"
            and action.get("expected_effect")
            and action.get("expected_gates")
            and scope.get("target_ref") == (item.get("target_ref") or item.get("ref"))
            and scope.get("project") == item.get("project")
            and scope.get("allowed_paths") == candidate.get("allowed_paths")
            and scope.get("required_tests") == candidate.get("required_tests")
        )

    def _effectiveness_suppressed(self, item: dict) -> bool:
        path = self.root / "capability-graduation.json"
        if not path.exists():
            return False
        graduation = read_capability_graduation(path)
        current = graduation.get("effectiveness_fingerprint")
        for assignment_path in self.assignments.glob("*.json"):
            assignment = validate_assignment(
                json.loads(assignment_path.read_text(encoding="utf-8")), self.root
            )
            contract = assignment.get("action_contract") or {}
            if (
                contract.get("source_ref") == item.get("ref")
                and contract.get("evidence_model_fingerprint") == current
                and assignment.get("result_state")
                in {
                    "analysis-completed",
                    "no-op-verification-completed",
                    "integrated-post-main-verified",
                    "repository-converged",
                    "runtime-converged",
                    "canonical-complete",
                }
            ):
                return True
        return False

    def _finding_suppressed(self, item: dict) -> bool:
        identity = item.get("finding_identity")
        if not identity:
            return False
        for assignment_path in self.assignments.glob("*.json"):
            assignment = validate_assignment(
                json.loads(assignment_path.read_text(encoding="utf-8")), self.root
            )
            if assignment.get("finding_identity") == identity:
                return True
        return False

    def dispatch(self, graph: dict, run_id: str, selected: dict | None = None) -> dict | None:
        active = self.active()
        control = json.loads((self.root / "control.json").read_text(encoding="utf-8"))
        if len(active) >= int(control.get("max_active_assignments", 1)) or (
            selected is None and not graph.get("executable_queue")
        ):
            return None
        item = selected or graph["executable_queue"][0]
        if not self._finding_frontier_selected(item, graph):
            return None
        if is_suppressed_no_op(
            item, active + self.completed_no_ops()
        ) or self._effectiveness_suppressed(item) or self._finding_suppressed(item):
            return None
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
        if any(not compatible(item, value) for value in active):
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
        decision_record = (item.get("authority") or {}).get("decision_record")
        if planning_record is None and isinstance(decision_record, dict):
            planning_record = {
                "revision": 1,
                "digest": decision_record["digest"],
                "approval_note": self.root.joinpath(
                    "decisions", f"{decision_record['decision_id']}.json"
                ).resolve().as_uri(),
                "conditions": decision_record.get("conditions"),
                "verification": decision_record.get("verification"),
            }
        assignment_type = item.get("assignment_type") or (
            "read-only-analysis"
            if item.get("kind") == "semantic-decomposition"
            else "no-op-verification"
            if item.get("kind") == "technical-revalidation"
            else "repository-convergence"
            if item.get("kind") == "repository-convergence"
            else "code-implementation"
        )
        mission_action, mission_reason = self._mission_action(item, graph)
        if item.get("finding_identity"):
            if mission_reason is not None:
                self._record_frontier_block(item, graph, mission_reason)
                return None
            if not self._dispatchable_action(mission_action, item):
                self._record_frontier_block(
                    item, graph, "mission-action-invalid-contract"
                )
                return None
        action_contract = (
            {
                "action_id": mission_action["action_id"],
                "source_ref": mission_action.get("source_ref"),
                **{
                    field: mission_action[field]
                    for field in ACTION_CONTRACT_FIELDS
                },
            }
            if mission_action is not None
            else None
        )
        ownership = resolve_repository_ownership(
            [
                item.get("responsibility"),
                (item.get("candidate") or {}).get("responsibility"),
            ],
            item.get("project"),
            context=f"dispatcher:{item.get('ref')}",
            allow_repository_inference=assignment_type
            in {"read-only-analysis", "no-op-verification"},
        )
        assignment_id = f"assignment-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        assignment = {
            "schema": "axis.external-development-supervisor.assignment",
            "schema_version": "4.0.0",
            "assignment_id": assignment_id,
            "assignment_type": assignment_type,
            "result_state": "pending",
            "work_item_disposition": "not-evaluated",
            "lifecycle_state": "ready-semantic"
            if assignment_type in READ_ONLY_ASSIGNMENT_TYPES
            else "ready-implementation",
            "kind": item.get("kind"),
            "queue_ref": item.get("ref"),
            "target_ref": item.get("target_ref") or item.get("ref"),
            "work_item": item.get("target_ref") or item.get("ref"),
            "project": ownership["canonical_repository"],
            "responsibility": ownership["responsibility"],
            "repository_ownership": ownership,
            "title": item.get("title"),
            "authority": item.get("authority"),
            "governance_state": item.get("classification")
            or (item.get("candidate") or {}).get("result"),
            "planning_record": planning_record,
            "candidate": item.get("candidate"),
            "finding_id": item.get("finding_id"),
            "finding_identity": item.get("finding_identity"),
            "shared_dependents": item.get("shared_dependents") or [],
            "allowed_paths": (item.get("candidate") or {}).get("allowed_paths") or [],
            "required_tests": (item.get("candidate") or {}).get("required_tests") or [],
            "source_item": source_item,
            "source_fingerprint": item.get("source_fingerprint"),
            "no_op_fingerprint": item.get("no_op_fingerprint")
            or (
                no_op_fingerprint(item)
                if item.get("assignment_type") == "no-op-verification"
                else None
            ),
            "source_inventory_generation_id": graph.get("inventory_generation_id"),
            "revalidation_tier": item.get("revalidation_tier"),
            "ranking_factors": item.get("ranking_factors"),
            "selection_rationale": item.get("selection_rationale")
            or "highest deterministic eligible queue entry",
            "action_contract": action_contract,
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
                if assignment["assignment_type"] in READ_ONLY_ASSIGNMENT_TYPES
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

    def completed_no_ops(self) -> list[dict]:
        values = []
        for path in self.assignments.glob("*.json"):
            value = validate_assignment(
                json.loads(path.read_text(encoding="utf-8")), self.root
            )
            if (
                value.get("assignment_type") == "no-op-verification"
                and value.get("result_state") == "no-op-verification-completed"
            ):
                values.append(value)
        return values
