import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lifecycle import adapt_assignment, is_terminal
from .mutation import MutationGate, OperationClass
from .schema_registry import validate_record, write_record

SCHEMA = "axis.external-development-supervisor.active-mission"
SCHEMA_VERSION = "5.0.0"
DESIRED_END_STATE = "all-capabilities-graduated"
MAX_ACTIONS = 8
MAX_OBSERVATIONS = 100
COMPLETED_RESULT_STATES = {
    "analysis-completed",
    "no-op-verification-completed",
    "integrated-post-main-verified",
    "repository-converged",
    "runtime-converged",
    "canonical-complete",
}
EXTERNAL_AUTHORITY_STATES = {
    "needs-product-owner",
    "needs-governance",
    "prohibited",
}
GATE_OWNERS = {
    "analysis": "semantic-analysis",
    "implementation": "engineering",
    "integration": "repository-integration",
    "deployment": "deployment",
    "validation": "runtime-validation",
    "verification": "technical-verification",
    "operator_acceptance": "product-owner",
    "program_risk": "mission-supervisor",
}
GATE_ORDER = (
    "implementation",
    "integration",
    "deployment",
    "validation",
    "verification",
    "operator_acceptance",
    "program_risk",
)
MUTATING_ASSIGNMENT_TYPES = {
    "governance-document-mutation",
    "code-implementation",
    "ci-integration-repair",
}


def _authority_state(value: dict[str, Any]) -> str:
    """Return the authority state published by a graph entry or mission action."""
    return str(
        value.get("authority_state")
        or (value.get("authority") or {}).get("state")
        or ""
    )


def dispatch_class_for(entry: dict[str, Any], expected_gates: list[dict[str, Any]]) -> str:
    """Classify whether an executable-queue entry may dispatch a product mutation.

    Technical revalidation can be executable read-only work, but preparation-only
    authority never authorizes a governed repository mutation.
    """
    candidate = entry.get("candidate") or {}
    if (
        entry.get("assignment_type") in MUTATING_ASSIGNMENT_TYPES
        and _authority_state(entry) != "preparation-only"
        and entry.get("project")
        and candidate.get("allowed_paths")
        and candidate.get("required_tests")
        and expected_gates
    ):
        return "DISPATCHABLE"
    return "INVALID"


def is_dispatchable_governed_mutation(action: dict[str, Any]) -> bool:
    """Whether an action can truthfully require a governed product transition."""
    return bool(
        action.get("kind") == "dispatch-executable"
        and action.get("executable")
        and action.get("dispatch_class") == "DISPATCHABLE"
        and _authority_state(action) != "preparation-only"
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:16]
    return f"mission-action-{digest}"


def _fingerprint(value: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _summarize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    passed = 0
    denominator = 0
    failing = []
    for capability in snapshot.get("capabilities") or []:
        capability_passed = 0
        capability_denominator = 0
        first_failing = None
        for gate in capability.get("gates") or []:
            if not gate["applicable"]:
                continue
            capability_denominator += 1
            if gate["state"] == "passed":
                capability_passed += 1
            elif first_failing is None:
                first_failing = gate["gate"]
        capability["passed"] = capability_passed
        capability["denominator"] = capability_denominator
        capability["confidence"] = (
            round(capability_passed * 100 / capability_denominator, 1)
            if capability_denominator
            else 100.0
        )
        capability["first_failing_gate"] = first_failing
        if first_failing:
            failing.append(
                {"capability": capability["capability"], "gate": first_failing}
            )
        passed += capability_passed
        denominator += capability_denominator
    snapshot["passed"] = passed
    snapshot["denominator"] = denominator
    snapshot["confidence"] = (
        round(passed * 100 / denominator, 1) if denominator else 100.0
    )
    snapshot["first_failing_gates"] = failing
    return snapshot


def _normalized_snapshot(
    graduation: dict[str, Any], capabilities: list[str]
) -> dict[str, Any]:
    records = {
        str(value.get("capability")): value
        for value in graduation.get("capabilities") or []
    }
    names = sorted(set(capabilities)) if capabilities else sorted(records)
    snapshot = {
        "applicability_model_revision": str(
            graduation.get("applicability_model_revision") or "legacy-boolean-v1"
        ),
        "capabilities": [],
    }
    for name in names:
        states = (records.get(name) or {}).get("graduation_state") or {}
        gates = []
        for gate_name in GATE_ORDER:
            gate = states.get(gate_name) or {}
            state = str(gate.get("state") or "missing")
            applicable = bool(
                gate.get("applicable", state not in {"not-required", "missing"})
            )
            gates.append(
                {
                    "gate": gate_name,
                    "applicable": applicable,
                    "state": state if applicable else "not-required",
                }
            )
        snapshot["capabilities"].append({"capability": name, "gates": gates})
    return _summarize_snapshot(snapshot)


def _backfilled_pre_snapshot(
    post: dict[str, Any],
    expected_gates: list[dict[str, Any]],
    revision: str,
) -> dict[str, Any]:
    pre = json.loads(json.dumps(post))
    pre["applicability_model_revision"] = revision or post.get(
        "applicability_model_revision"
    )
    expected = {
        (str(value.get("capability")), str(value.get("gate"))): str(
            value.get("from_state") or "pending"
        )
        for value in expected_gates
    }
    for capability in pre.get("capabilities") or []:
        name = capability["capability"]
        for gate in capability.get("gates") or []:
            state = expected.get((name, gate["gate"]))
            if state:
                gate["state"] = state
                gate["applicable"] = state != "not-required"
    return _summarize_snapshot(pre)


def _snapshot_delta(pre: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    def states(snapshot: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
        return {
            (capability["capability"], gate["gate"]): gate
            for capability in snapshot.get("capabilities") or []
            for gate in capability.get("gates") or []
        }

    before = states(pre)
    after = states(post)
    comparable = sorted(
        key
        for key in before.keys() & after.keys()
        if before[key]["applicable"] and after[key]["applicable"]
    )
    reduced = sum(
        before[key]["state"] != "passed" and after[key]["state"] == "passed"
        for key in comparable
    )
    regressed = sum(
        before[key]["state"] == "passed" and after[key]["state"] != "passed"
        for key in comparable
    )
    pre_passed = sum(before[key]["state"] == "passed" for key in comparable)
    post_passed = sum(after[key]["state"] == "passed" for key in comparable)
    denominator = len(comparable)
    pre_confidence = round(pre_passed * 100 / denominator, 1) if denominator else 100.0
    post_confidence = (
        round(post_passed * 100 / denominator, 1) if denominator else 100.0
    )
    return {
        "comparable_gate_count": denominator,
        "gates_reduced": reduced,
        "gates_regressed": regressed,
        "confidence_before": pre_confidence,
        "confidence_after": post_confidence,
        "confidence_delta": round(post_confidence - pre_confidence, 1),
        "applicability_revision_changed": pre.get("applicability_model_revision")
        != post.get("applicability_model_revision"),
        "first_failing_gates_before": pre.get("first_failing_gates") or [],
        "first_failing_gates_after": post.get("first_failing_gates") or [],
    }


def _meaningful_frontier_progress(assignment: dict[str, Any]) -> bool:
    """Whether bounded read-only work truthfully clarified the next frontier.

    A semantic analysis or no-op verification can establish that the current
    work item requires implementation.  That does not pass the gate it was
    asked to inspect, but it is still an evidence-backed convergence outcome.
    Keep an explicit false available for future producers while treating
    existing completed assignments as compatible with the retrospective
    roadmap-convergence signal.
    """
    return (
        assignment.get("result_state")
        in {"analysis-completed", "no-op-verification-completed"}
        and assignment.get("work_item_disposition") == "requires-implementation"
        and assignment.get("roadmap_convergence_improved") is not False
    )


def _assignment_summary(value: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "assignment_id": str(value.get("assignment_id") or "unknown"),
        "project": str(value.get("project") or ""),
        "work_item": str(value.get("work_item") or ""),
        "lifecycle_state": str(value.get("lifecycle_state") or "unknown"),
        "result_state": str(value.get("result_state") or "pending"),
    }
    if value.get("action_contract"):
        summary["action_contract"] = value["action_contract"]
    return summary


def _external_node(node: dict[str, Any]) -> bool:
    authority_state = str((node.get("authority") or {}).get("state") or "")
    blocker_type = str(node.get("blocker_type") or "").lower()
    return authority_state in EXTERNAL_AUTHORITY_STATES or any(
        marker in blocker_type
        for marker in ("external", "human", "authority", "product-owner", "governance")
    )


def has_runnable_action(mission: dict[str, Any]) -> bool:
    return any(
        (
            is_dispatchable_governed_mutation(action)
            and bool(action.get("source_ref"))
        )
        or (
            action.get("kind") == "reconcile-active-assignment"
            and bool(action.get("assignment_id"))
        )
        or (
            action.get("kind")
            in {"collect-capability-evidence", "validate-capability-stream"}
            and bool(action.get("executable"))
        )
        for action in mission.get("generated_actions") or []
    )


def _backfill_action_context(action: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(action)
    capabilities = sorted(set(migrated.get("expected_capabilities") or []))
    migrated.setdefault(
        "capability_context",
        [{"capability": capability} for capability in capabilities],
    )
    migrated.setdefault(
        "merge_impact_projection",
        {
            "affected_capabilities": capabilities,
            "product_subdimensions": [],
            "milestones": list(migrated.get("expected_milestones") or []),
            "gates": list(migrated.get("expected_gates") or []),
            "production_confidence_before": (
                migrated.get("pre_snapshot") or {}
            ).get("confidence"),
        },
    )
    return migrated


def _action_binding(action: dict[str, Any], graph: dict[str, Any], graduation: dict[str, Any]) -> dict[str, Any]:
    capabilities = sorted(set(action.get("expected_capabilities") or []))
    records = {str(value.get("capability")): value for value in graduation.get("capabilities") or []}
    linked = {str(ref) for capability in capabilities for ref in (records.get(capability) or {}).get("linked_work_items") or []}
    refs = linked | {str(value) for value in (action.get("target"), action.get("source_ref")) if value}
    edges = sorted(
        (
            edge
            for edge in graph.get("edges") or []
            if edge.get("from_ref") in refs or edge.get("to_ref") in refs
        ),
        key=_canonical_json,
    )
    authority = next((node.get("authority") or {} for node in graph.get("nodes") or [] if node.get("ref") in refs), {})
    projection = [{"capability": name, "state": (records.get(name) or {}).get("graduation_state"), "invalidation": (records.get(name) or {}).get("invalidation_fingerprint")} for name in capabilities]
    return {
        "capabilities": capabilities,
        "milestones": sorted(set(action.get("expected_milestones") or [])),
        "applicability_revision": str(graduation.get("applicability_model_revision") or "legacy-boolean-v1"),
        "capability_projection_fingerprint": _fingerprint({"capabilities": projection}),
        "dependency_fingerprint": _fingerprint({"edges": edges}),
        "gate_owner": str(action.get("gate_owner") or "unknown"),
        "expected_transition": list(action.get("expected_gates") or []),
        "authority_fingerprint": _fingerprint(authority),
        "generation": {"inventory": graph.get("inventory_generation_id"), "graph": graph.get("generation_id"), "convergence": graduation.get("source_convergence_digest")},
        "evidence_fingerprint": _fingerprint({"evidence": sorted(set(action.get("expected_evidence") or []))}),
    }


def _action_purpose(action: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    return (str(action.get("gate") or ""), str(action.get("gate_owner") or ""), tuple(sorted(str(value) for value in action.get("expected_capabilities") or [])))


def adapt_mission_record(value: dict[str, Any]) -> dict[str, Any]:
    migrated = json.loads(json.dumps(value))
    version = migrated.get("schema_version")
    if version == SCHEMA_VERSION:
        return migrated
    if version == "1.0.0":
        observations = []
        for observation in list(migrated.get("observations") or [])[
            -MAX_OBSERVATIONS:
        ]:
            if isinstance(observation, dict):
                observations.append(
                    {
                        "observed_at": str(observation.get("observed_at") or utc_now()),
                        "source": str(observation.get("source") or "migration"),
                        "summary": str(observation.get("summary") or observation)[
                            :4096
                        ],
                    }
                )
            else:
                observations.append(
                    {
                        "observed_at": utc_now(),
                        "source": "migration",
                        "summary": str(observation)[:4096],
                    }
                )
        migrated["observations"] = observations
        migrated.setdefault("action_effectiveness", [])
        migrated.setdefault(
            "effectiveness_metrics",
            {
                "assignments_evaluated": 0,
                "effective_assignments": 0,
                "zero_effect_assignments": 0,
                "suppressed_fingerprints": 0,
                "state_model_defects": 0,
                "applicability_revisions": 0,
                "gates_reduced": 0,
                "confidence_delta": 0.0,
                "effectiveness_percent": 100.0,
            },
        )
        migrated.setdefault("graduation_progress", {})
        migrated["graduation_progress"].setdefault("suppressed_action_count", 0)
    if version in {"1.0.0", "2.0.0"}:
        migrated["schema_version"] = SCHEMA_VERSION
        migrated["generated_actions"] = [
            _backfill_action_context(action)
            for action in migrated.get("generated_actions") or []
        ]
    if version in {"3.0.0", "4.0.0"}:
        migrated["schema_version"] = SCHEMA_VERSION
        migrated["generated_actions"] = [
            _backfill_action_context(action)
            | {
                "binding": {
                    "capabilities": sorted(set(action.get("expected_capabilities") or [])),
                    "milestones": sorted(set(action.get("expected_milestones") or [])),
                    "applicability_revision": str(action.get("applicability_model_revision") or "legacy-boolean-v1"),
                    "capability_projection_fingerprint": _fingerprint({"legacy": action.get("capability_context") or []}),
                    "dependency_fingerprint": _fingerprint({"legacy": action.get("source_ref")}),
                    "gate_owner": str(action.get("gate_owner") or "unknown"),
                    "expected_transition": list(action.get("expected_gates") or []),
                    "authority_fingerprint": _fingerprint({}),
                    "generation": {"inventory": None, "graph": None, "convergence": action.get("convergence_fingerprint")},
                    "evidence_fingerprint": _fingerprint({"evidence": action.get("expected_evidence") or []}),
                },
                "lifecycle": "STALE",
            }
            for action in migrated.get("generated_actions") or []
        ]
    if migrated.get("schema_version") == SCHEMA_VERSION:
        migrated["generated_actions"] = [
            action
            if action.get("binding")
            else action
            | {
                "binding": {
                    "capabilities": sorted(set(action.get("expected_capabilities") or [])),
                    "milestones": sorted(set(action.get("expected_milestones") or [])),
                    "applicability_revision": str(action.get("applicability_model_revision") or "legacy-boolean-v1"),
                    "capability_projection_fingerprint": _fingerprint({"legacy": action.get("capability_context") or []}),
                    "dependency_fingerprint": _fingerprint({"legacy": action.get("source_ref")}),
                    "gate_owner": str(action.get("gate_owner") or "unknown"),
                    "expected_transition": list(action.get("expected_gates") or []),
                    "authority_fingerprint": _fingerprint({}),
                    "generation": {"inventory": None, "graph": None, "convergence": action.get("convergence_fingerprint")},
                    "evidence_fingerprint": _fingerprint({"evidence": action.get("expected_evidence") or []}),
                },
                "lifecycle": "STALE",
            }
            for action in migrated.get("generated_actions") or []
        ]
    migrated.setdefault("retired_actions", [])
    migrated.setdefault("invalidation_map", {})
    migrated.setdefault(
        "source_generations",
        {
            "inventory": None,
            "graph": None,
            "graduation": None,
            "convergence": None,
        },
    )
    return migrated


def read_mission_record(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    migrated = adapt_mission_record(raw)
    return validate_record(migrated, SCHEMA, record_path=path)


class ActiveMissionState:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "active-mission.json"
        self.gate = MutationGate(root, source="mission-reconciler")

    def _previous(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return read_mission_record(self.path)

    def _assignments(self, inventory: dict[str, Any]) -> list[dict[str, Any]]:
        values = {
            str(value.get("assignment_id")): adapt_assignment(value, self.root)
            for value in inventory.get("supervisor_assignments") or []
            if value.get("assignment_id")
        }
        for path in sorted((self.root / "assignments").glob("*.json")):
            try:
                value = adapt_assignment(
                    json.loads(path.read_text(encoding="utf-8")), self.root
                )
            except (OSError, json.JSONDecodeError):
                continue
            if value.get("assignment_id"):
                values[str(value["assignment_id"])] = value
        return list(values.values())

    @staticmethod
    def _missing_gates(
        graduation: dict[str, Any], graph: dict[str, Any]
    ) -> list[dict[str, Any]]:
        nodes = {str(node.get("ref")): node for node in graph.get("nodes") or []}
        executable_capabilities = {
            str(capability)
            for entry in graph.get("executable_queue") or []
            for capability in entry.get("affected_capabilities") or []
        }
        missing = []
        for capability in graduation.get("capabilities") or []:
            name = str(capability.get("capability") or "unknown")
            linked = [
                nodes[ref]
                for ref in capability.get("linked_work_items") or []
                if ref in nodes
                and nodes[ref].get("classification")
                not in {"Completed", "Integrated", "Superseded", "Invalid"}
            ]
            linked_external = bool(linked) and all(
                _external_node(node) for node in linked
            )
            states = capability.get("graduation_state") or {}
            unresolved_names = {
                gate_name
                for gate_name, gate in states.items()
                if gate_name != "graduated"
                and gate.get("state") not in {"passed", "not-required"}
            }
            for gate_name in sorted(unresolved_names):
                gate = states[gate_name]
                external_only = gate_name == "operator_acceptance" or (
                    gate_name in {"implementation", "integration", "program_risk"}
                    and linked_external
                    and name not in executable_capabilities
                )
                missing.append(
                    {
                        "capability": name,
                        "gate": gate_name,
                        "state": str(gate.get("state") or "pending"),
                        "evidence": [
                            str(value) for value in gate.get("evidence") or []
                        ],
                        "external_only": external_only,
                    }
                )
        return missing

    @staticmethod
    def _external_blockers(
        missing: list[dict[str, Any]], graph: dict[str, Any]
    ) -> list[dict[str, str]]:
        blockers = [
            {
                "ref": f"capability:{gate['capability']}:{gate['gate']}",
                "kind": "external-graduation-gate",
                "reason": "; ".join(gate["evidence"])
                or f"{gate['gate']} requires external evidence",
            }
            for gate in missing
            if gate["external_only"]
        ]
        blockers.extend(
            {
                "ref": str(node.get("ref") or "unknown"),
                "kind": str(node.get("blocker_type") or "external-authority"),
                "reason": str(
                    (node.get("authority") or {}).get("reason")
                    or node.get("classification_rationale")
                    or "external authority is required"
                ),
            }
            for node in graph.get("nodes") or []
            if node.get("classification") == "Blocked" and _external_node(node)
        )
        unique = {(value["ref"], value["kind"]): value for value in blockers}
        return [unique[key] for key in sorted(unique)]

    @staticmethod
    def _action_contract(
        action_id: str,
        gate_name: str,
        capabilities: list[str],
        expected_gates: list[dict[str, str]],
        expected_evidence: list[str],
        graph: dict[str, Any],
        graduation: dict[str, Any],
    ) -> dict[str, Any]:
        nodes = {str(value.get("ref")): value for value in graph.get("nodes") or []}
        capability_records = {
            str(value.get("capability")): value
            for value in graduation.get("capabilities") or []
        }
        linked_refs = {
            str(ref)
            for capability in capabilities
            for ref in (capability_records.get(capability) or {}).get(
                "linked_work_items"
            )
            or []
        }
        milestones = sorted(
            {
                str(nodes[ref].get("milestone"))
                for ref in linked_refs
                if ref in nodes and nodes[ref].get("milestone")
            }
        )
        debt = sorted(
            [
                {
                    "milestone": str(milestone.get("milestone") or "unmilestoned"),
                    "kind": str(value.get("kind") or "unknown"),
                    "ref": str(value.get("ref") or "unknown"),
                    "gate": str(value.get("gate") or "unknown"),
                    "reason": str(value.get("reason") or "evidence missing"),
                }
                for milestone in graduation.get("milestones") or []
                for value in milestone.get("debts") or []
                if value.get("ref") in capabilities or value.get("ref") in linked_refs
            ],
            key=lambda value: (
                value["milestone"],
                value["kind"],
                value["ref"],
                value["gate"],
            ),
        )
        milestones = sorted(
            set(milestones) | {value["milestone"] for value in debt}
        )
        convergence_fingerprint = graduation.get("repository_convergence_digest")
        evidence_model_fingerprint = str(
            graduation.get("effectiveness_fingerprint")
            or graduation.get("projection_digest")
            or ""
        )
        applicability_model_revision = str(
            graduation.get("applicability_model_revision") or "legacy-boolean-v1"
        )
        pre_snapshot = _normalized_snapshot(graduation, capabilities)
        capability_context = [
            {
                "capability": name,
                "product_subdimensions": (
                    capability_records.get(name) or {}
                ).get("product_subdimensions")
                or {},
                "production_confidence": (capability_records.get(name) or {}).get(
                    "production_confidence"
                ),
                "operator_confidence": (capability_records.get(name) or {}).get(
                    "operator_confidence"
                ),
                "first_failing_gate": (capability_records.get(name) or {}).get(
                    "first_failing_gate"
                ),
                "program_risk": (capability_records.get(name) or {}).get(
                    "program_risk"
                )
                or {},
            }
            for name in sorted(set(capabilities))
        ]
        merge_impact_projection = {
            "affected_capabilities": sorted(set(capabilities)),
            "product_subdimensions": sorted(
                {
                    dimension
                    for value in capability_context
                    for dimension, state in value["product_subdimensions"].items()
                    if state.get("applicable")
                }
            ),
            "milestones": milestones,
            "gates": expected_gates,
            "production_confidence_before": pre_snapshot.get("confidence"),
        }
        suppression_fingerprint = _fingerprint(
            {
                "action_id": action_id,
                "convergence_fingerprint": convergence_fingerprint,
                "evidence_model_fingerprint": evidence_model_fingerprint,
                "applicability_model_revision": applicability_model_revision,
            }
        )
        return {
            "engineering_purpose": f"advance {gate_name} with one evidence-backed state change",
            "gate_owner": GATE_OWNERS[gate_name],
            "expected_gates": expected_gates,
            "expected_capabilities": sorted(set(capabilities)),
            "expected_milestones": milestones,
            "expected_debt_reduction": debt,
            "expected_evidence": sorted(set(filter(None, expected_evidence))),
            "capability_context": capability_context,
            "merge_impact_projection": merge_impact_projection,
            "convergence_fingerprint": convergence_fingerprint,
            "evidence_model_fingerprint": evidence_model_fingerprint,
            "applicability_model_revision": applicability_model_revision,
            "pre_snapshot": pre_snapshot,
            "suppression_fingerprint": suppression_fingerprint,
        }

    @staticmethod
    def _effectiveness(
        previous: dict[str, Any],
        assignments: list[dict[str, Any]],
        graduation: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], set[str], list[dict[str, str]], dict[str, Any]]:
        current_fingerprint = str(
            graduation.get("effectiveness_fingerprint")
            or graduation.get("projection_digest")
            or ""
        )
        prior = {
            str(value.get("assignment_id")): value
            for value in previous.get("action_effectiveness") or []
        }
        evaluations: list[dict[str, Any]] = []
        suppressed: set[str] = set()
        observations: list[dict[str, str]] = []
        for assignment in sorted(
            assignments, key=lambda value: str(value.get("assignment_id") or "")
        ):
            if assignment.get("result_state") not in COMPLETED_RESULT_STATES:
                continue
            contract = assignment.get("action_contract") or {}
            baseline = str(contract.get("evidence_model_fingerprint") or "")
            suppression_fingerprint = str(
                contract.get("suppression_fingerprint") or ""
            )
            if not baseline or not suppression_fingerprint:
                continue
            assignment_id = str(assignment.get("assignment_id"))
            old = prior.get(assignment_id) or {}
            expected_gates = list(contract.get("expected_gates") or [])
            expected_capabilities = list(contract.get("expected_capabilities") or [])
            if not expected_capabilities:
                expected_capabilities = sorted(
                    {
                        str(value.get("capability"))
                        for value in expected_gates
                        if value.get("capability")
                    }
                )
            post_snapshot = _normalized_snapshot(
                graduation, expected_capabilities
            )
            pre_snapshot = contract.get("pre_snapshot")
            if not isinstance(pre_snapshot, dict):
                pre_snapshot = _backfilled_pre_snapshot(
                    post_snapshot,
                    expected_gates,
                    str(
                        contract.get("applicability_model_revision")
                        or "legacy-boolean-v1"
                    ),
                )
            normalized_delta = _snapshot_delta(pre_snapshot, post_snapshot)
            observed_gates = [
                {
                    "capability": str(value.get("capability") or "unknown"),
                    "gate": str(value.get("gate") or "unknown"),
                    "state": next(
                        (
                            gate["state"]
                            for capability in post_snapshot.get("capabilities") or []
                            if capability["capability"]
                            == str(value.get("capability"))
                            for gate in capability.get("gates") or []
                            if gate["gate"] == str(value.get("gate"))
                        ),
                        "missing",
                    ),
                }
                for value in expected_gates
            ]
            changed = bool(observed_gates) and all(
                value["state"] in {"passed", "not-required"}
                for value in observed_gates
            )
            model_revised = normalized_delta["applicability_revision_changed"]
            frontier_progress = _meaningful_frontier_progress(assignment)
            zero_effect_cycles = (
                0
                if changed or model_revised or frontier_progress
                else 1
                if old.get("observed_fingerprint") != current_fingerprint
                else int(old.get("zero_effect_cycles") or 0) + 1
            )
            classification = (
                "effective"
                if changed
                else "applicability-revised"
                if model_revised
                else "frontier-progress"
                if frontier_progress
                else "state-model-defect"
                if zero_effect_cycles >= 3
                else "zero-effect"
            )
            evaluation = {
                "assignment_id": assignment_id,
                "action_id": str(contract.get("action_id") or "unknown"),
                "suppression_fingerprint": suppression_fingerprint,
                "baseline_fingerprint": baseline,
                "observed_fingerprint": current_fingerprint,
                "expected_gates": expected_gates,
                "observed_gates": observed_gates,
                "observed_delta": changed,
                "frontier_progress": frontier_progress,
                "pre_snapshot": pre_snapshot,
                "post_snapshot": post_snapshot,
                "normalized_delta": normalized_delta,
                "zero_effect_cycles": zero_effect_cycles,
                "classification": classification,
                "evaluated_at": utc_now(),
            }
            evaluations.append(evaluation)
            if not changed and not model_revised and not frontier_progress:
                suppressed.add(suppression_fingerprint)
            if classification == "state-model-defect" and old.get(
                "classification"
            ) != "state-model-defect":
                observations.append(
                    {
                        "observed_at": utc_now(),
                        "source": "action-effectiveness",
                        "summary": (
                            f"state-model defect: assignment {assignment_id} produced "
                            "no expected evidence delta for three reconciliation cycles; "
                            f"fingerprint {suppression_fingerprint} remains suppressed"
                        ),
                    }
                )
        effective = sum(
            value["classification"] in {"effective", "frontier-progress"}
            for value in evaluations
        )
        zero_effect = sum(
            value["classification"] in {"zero-effect", "state-model-defect"}
            for value in evaluations
        )
        defects = sum(
            value["classification"] == "state-model-defect" for value in evaluations
        )
        revised = sum(
            value["classification"] == "applicability-revised"
            for value in evaluations
        )
        gates_reduced = sum(
            value["normalized_delta"]["gates_reduced"] for value in evaluations
        )
        confidence_delta = round(
            sum(
                value["normalized_delta"]["confidence_delta"]
                for value in evaluations
            ),
            1,
        )
        metrics = {
            "assignments_evaluated": len(evaluations),
            "effective_assignments": effective,
            "zero_effect_assignments": zero_effect,
            "suppressed_fingerprints": len(suppressed),
            "state_model_defects": defects,
            "applicability_revisions": revised,
            "gates_reduced": gates_reduced,
            "confidence_delta": confidence_delta,
            "effectiveness_percent": round(
                effective * 100 / (effective + zero_effect), 1
            )
            if effective + zero_effect
            else 100.0,
        }
        return evaluations, suppressed, observations, metrics

    @staticmethod
    def _actions(
        missing: list[dict[str, Any]],
        graph: dict[str, Any],
        graduation: dict[str, Any],
        active_assignments: list[dict[str, Any]],
        suppressed_fingerprints: set[str],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        internal = {
            (gate["capability"], gate["gate"]): gate
            for gate in missing
            if not gate["external_only"]
        }

        for assignment in active_assignments:
            target = assignment["work_item"] or assignment["assignment_id"]
            gate_name = (
                "integration"
                if assignment["lifecycle_state"] == "awaiting-integration"
                else "implementation"
            )
            action_id = _stable_id(
                "reconcile-assignment", assignment["assignment_id"]
            )
            contract = dict(assignment.get("action_contract") or {})
            contract.pop("action_id", None)
            if not contract:
                contract = ActiveMissionState._action_contract(
                    action_id,
                    gate_name,
                    [],
                    [],
                    [f"durable assignment {assignment['assignment_id']} advances"],
                    graph,
                    graduation,
                )
            actions.append(
                {
                    "action_id": action_id,
                    "kind": "reconcile-active-assignment",
                    "target": target,
                    "gate": gate_name,
                    "reason": f"continue {assignment['lifecycle_state']} assignment from durable state",
                    "executable": True,
                    "attempt_limit": 1,
                    "source_ref": target,
                    "assignment_id": assignment["assignment_id"],
                    **contract,
                }
            )

        for entry in graph.get("executable_queue") or []:
            affected = [
                str(value) for value in entry.get("affected_capabilities") or []
            ]
            expected_pairs = [
                (capability, gate)
                for capability, gate in internal
                if capability in affected
            ]
            if not expected_pairs:
                expected_pairs = [
                    (str(value.get("capability") or "unknown"), str(value.get("gate")))
                    for value in entry.get("expected_gates") or []
                    if value.get("gate")
            ]
            gate_name = expected_pairs[0][1] if expected_pairs else "analysis"
            if gate_name not in GATE_OWNERS:
                gate_name = (
                    "verification" if "verification" in gate_name.lower() else "implementation"
                )
            expected_gates = [
                {
                    "capability": capability,
                    "gate": gate,
                    "from_state": str(internal[(capability, gate)]["state"])
                    if (capability, gate) in internal
                    else "pending",
                    "to_state": "passed",
                }
                for capability, gate in expected_pairs
            ]
            expected_evidence = [
                evidence
                for capability, gate in expected_pairs
                if (capability, gate) in internal
                for evidence in internal[(capability, gate)]["evidence"]
            ] or list((entry.get("candidate") or {}).get("required_tests") or [])
            target = str(entry.get("target_ref") or entry.get("ref") or "unknown")
            action_id = _stable_id("dispatch", str(entry.get("ref") or target))
            contract = ActiveMissionState._action_contract(
                action_id,
                gate_name,
                affected,
                expected_gates,
                expected_evidence,
                graph,
                graduation,
            )
            actions.append(
                {
                    "action_id": action_id,
                    "kind": "dispatch-executable",
                    "target": target,
                    "gate": gate_name,
                    "reason": str(
                        entry.get("selection_rationale")
                        or "deterministic executable frontier entry"
                    ),
                    "executable": True,
                    "attempt_limit": 1,
                    "source_ref": str(entry.get("ref") or target),
                    "assignment_id": None,
                    "dispatch_class": dispatch_class_for(entry, expected_gates),
                    "authority_state": _authority_state(entry) or None,
                    "assignment_scope": {
                        "target_ref": target,
                        "project": entry.get("project"),
                        "allowed_paths": (entry.get("candidate") or {}).get(
                            "allowed_paths"
                        )
                        or [],
                        "required_tests": (entry.get("candidate") or {}).get(
                            "required_tests"
                        )
                        or [],
                    },
                    "worker_path": "implementation",
                    "handoff_path": "implementation-handoff",
                    "review_path": "independent-review",
                    "expected_effect": entry.get("expected_effect")
                    or "advance the expected gate",
                    **contract,
                }
            )

        validation_streams = graduation.get("validation_streams") or []
        for stream in validation_streams:
            if stream.get("status") != "assignment-required":
                continue
            expected_gates = list(stream.get("expected_gates") or [])
            if not expected_gates:
                continue
            stream_name = str(stream.get("stream") or "unknown")
            gate_name = str(expected_gates[0].get("gate") or "verification")
            action_id = _stable_id(
                "validation-stream",
                stream_name,
                str((stream.get("evidence") or {}).get("evidence_id") or "current"),
            )
            contract = ActiveMissionState._action_contract(
                action_id,
                gate_name,
                list(stream.get("capabilities") or []),
                expected_gates,
                [
                    str((stream.get("evidence") or {}).get("uri") or ""),
                    *[
                        str(value)
                        for value in (stream.get("evidence") or {}).get("findings")
                        or []
                    ],
                ],
                graph,
                graduation,
            )
            actions.append(
                {
                    "action_id": action_id,
                    "kind": "validate-capability-stream",
                    "target": stream_name,
                    "gate": gate_name,
                    "reason": f"consolidated {stream.get('title') or stream_name}",
                    "executable": True,
                    "attempt_limit": 1,
                    "source_ref": None,
                    "assignment_id": None,
                    **contract,
                }
            )

        for capability in (
            [] if validation_streams else graduation.get("capabilities") or []
        ):
            name = str(capability.get("capability") or "unknown")
            for scheduled in capability.get("scheduled_actions") or []:
                stages = [str(value) for value in scheduled.get("stages") or []]
                applicable_stages = [
                    stage for stage in stages if (name, stage) in internal
                ]
                if not applicable_stages:
                    continue
                gate_name = applicable_stages[0]
                action_id = _stable_id(
                    "capability-evidence",
                    name,
                    str(scheduled.get("fingerprint") or gate_name),
                )
                contract = ActiveMissionState._action_contract(
                    action_id,
                    gate_name,
                    [name],
                    [
                        {
                            "capability": name,
                            "gate": stage,
                            "from_state": str(internal[(name, stage)]["state"]),
                            "to_state": "passed",
                        }
                        for stage in applicable_stages
                    ],
                    [
                        evidence
                        for stage in applicable_stages
                        for evidence in internal[(name, stage)]["evidence"]
                    ],
                    graph,
                    graduation,
                )
                actions.append(
                    {
                        "action_id": action_id,
                        "kind": "collect-capability-evidence",
                        "target": name,
                        "gate": gate_name,
                        "reason": str(
                            scheduled.get("reason")
                            or "capability graduation evidence is stale or missing"
                        ),
                        "executable": True,
                        "attempt_limit": 1,
                        "source_ref": str(scheduled.get("repository") or "") or None,
                        "assignment_id": None,
                        **contract,
                    }
                )

        covered = {
            (gate["capability"], gate["gate"])
            for action in actions
            for gate in action["expected_gates"]
        }
        for (capability, gate_name), gate in internal.items():
            if gate_name == "program_risk" or (capability, gate_name) in covered:
                continue
            action_id = _stable_id("reconcile-evidence", capability, gate_name)
            contract = ActiveMissionState._action_contract(
                action_id,
                gate_name,
                [capability],
                [
                    {
                        "capability": capability,
                        "gate": gate_name,
                        "from_state": str(gate["state"]),
                        "to_state": "passed",
                    }
                ],
                gate["evidence"],
                graph,
                graduation,
            )
            actions.append(
                {
                    "action_id": action_id,
                    "kind": "reconcile-missing-evidence",
                    "target": capability,
                    "gate": gate_name,
                    "reason": "; ".join(gate["evidence"])
                    or f"{gate_name} evidence is missing",
                    "executable": True,
                    "attempt_limit": 1,
                    "source_ref": None,
                    "assignment_id": None,
                    **contract,
                }
            )

        unique = {action["action_id"]: action for action in actions}
        return [
            action
            for action in unique.values()
            if action["suppression_fingerprint"] not in suppressed_fingerprints
            and (
                action.get("expected_gates")
                or action.get("expected_debt_reduction")
                or action.get("expected_capabilities")
            )
        ][:MAX_ACTIONS]

    def reconcile(
        self,
        inventory: dict[str, Any],
        graph: dict[str, Any],
        graduation: dict[str, Any],
        *,
        persist: bool = True,
    ) -> dict[str, Any]:
        previous = self._previous()
        assignments = self._assignments(inventory)
        active = sorted(
            (
                _assignment_summary(value)
                for value in assignments
                if not is_terminal(value)
            ),
            key=lambda value: value["assignment_id"],
        )
        completed = sorted(
            (
                _assignment_summary(value)
                for value in assignments
                if value.get("result_state") in COMPLETED_RESULT_STATES
            ),
            key=lambda value: value["assignment_id"],
        )
        missing = self._missing_gates(graduation, graph)
        blockers = self._external_blockers(missing, graph)
        effectiveness, suppressed, defect_observations, effectiveness_metrics = (
            self._effectiveness(previous, assignments, graduation)
        )
        actions = self._actions(
            missing, graph, graduation, active, suppressed
        )
        current_actions = []
        for action in actions:
            action = dict(action)
            action["binding"] = _action_binding(action, graph, graduation)
            action["lifecycle"] = "CURRENT"
            current_actions.append(action)
        current_by_purpose = {_action_purpose(action): action for action in current_actions}
        retired_actions = list(previous.get("retired_actions") or [])
        retired_ids = {(value.get("action_id"), value.get("classification")) for value in retired_actions}
        missing_keys = {(value["capability"], value["gate"]) for value in missing}
        for old in previous.get("generated_actions") or []:
            expected = old.get("binding", {}).get("expected_transition") or old.get("expected_gates") or []
            expected_keys = {(value.get("capability"), value.get("gate")) for value in expected}
            replacement = current_by_purpose.get(_action_purpose(old))
            if expected_keys and not expected_keys.intersection(missing_keys):
                classification = "SATISFIED"
            elif replacement is None:
                classification = "BLOCKED" if any(
                    value["external_only"] and (value["capability"], value["gate"]) in expected_keys
                    for value in missing
                ) else "STALE"
            elif old.get("action_id") != replacement.get("action_id"):
                classification = "SUPERSEDED"
            elif old.get("binding") != replacement["binding"]:
                classification = "STALE"
            else:
                continue
            key = (old.get("action_id"), classification)
            if key not in retired_ids:
                retired_actions.append({
                    "action_id": str(old.get("action_id")),
                    "classification": classification,
                    "retired_at": utc_now(),
                    "purpose": str(old.get("engineering_purpose") or ""),
                    "binding": old.get("binding") or {},
                    "replacement_action_id": replacement.get("action_id") if replacement else None,
                })
                retired_ids.add(key)
        actions = current_actions
        invalidation_map = {
            action["action_id"]: {
                "changed_paths": sorted({
                    path
                    for node in graph.get("nodes") or []
                    if node.get("ref") in {action.get("target"), action.get("source_ref")}
                    for candidate in ((node.get("semantic_record") or {}).get("candidate_slices") or [])
                    for path in candidate.get("allowed_paths") or []
                }),
                "dependency_fingerprint": action["binding"]["dependency_fingerprint"],
                "evidence_fingerprint": action["binding"]["evidence_fingerprint"],
            }
            for action in actions
        }
        kpi = graduation.get("primary_kpi") or {}
        graduated = int(kpi.get("count") or 0)
        denominator = int(kpi.get("denominator") or 0)
        desired_achieved = (
            denominator > 0
            and graduated == denominator
            and not missing
            and not active
            and not actions
        )
        external_only = (
            bool(missing)
            and all(gate["external_only"] for gate in missing)
            and not active
            and not actions
        )
        should_terminate = desired_achieved or external_only
        reason = (
            "desired-state-achieved"
            if desired_achieved
            else "all-remaining-paths-external-only"
            if external_only
            else "work-remains"
        )
        current_state = (
            "completed"
            if desired_achieved
            else "blocked-external"
            if external_only
            else "active"
        )
        now = utc_now()
        observations = (
            list(previous.get("observations") or []) + defect_observations
        )[-MAX_OBSERVATIONS:]
        projection = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "mission_id": str(
                previous.get("mission_id") or "axis-capability-graduation"
            ),
            "created_at": str(previous.get("created_at") or now),
            "updated_at": now,
            "desired_end_state": DESIRED_END_STATE,
            "current_state": current_state,
            "missing_gates": missing,
            "generated_actions": actions,
            "retired_actions": retired_actions[-MAX_OBSERVATIONS:],
            "invalidation_map": invalidation_map,
            "active_assignments": active,
            "completed_assignments": completed,
            "external_blockers": blockers,
            "action_effectiveness": effectiveness,
            "effectiveness_metrics": effectiveness_metrics,
            "graduation_progress": {
                "graduated": graduated,
                "denominator": denominator,
                "percent": float(kpi.get("percent") or 0),
                "missing_gate_count": len(missing),
                "active_assignment_count": len(active),
                "completed_assignment_count": len(completed),
                "suppressed_action_count": len(suppressed),
            },
            "source_generations": {
                "inventory": inventory.get("generation_id"),
                "graph": graph.get("generation_id"),
                "graduation": graduation.get("projection_digest"),
                "convergence": graduation.get("source_convergence_digest"),
            },
            "termination_condition": {
                "desired_state_achieved": desired_achieved,
                "every_remaining_path_external_only": external_only,
                "should_terminate": should_terminate,
                "reason": reason,
            },
            "observations": observations,
        }
        if persist:
            decision = self.gate.decide(OperationClass.RECONCILIATION)
            self.gate.require(decision, OperationClass.RECONCILIATION)
            write_record(self.path, projection, SCHEMA)
        return projection

    def observe(self, response: Any, *, source: str) -> dict[str, Any]:
        mission = read_mission_record(self.path)
        summary = (
            response
            if isinstance(response, str)
            else json.dumps(response, sort_keys=True, default=str)
        )
        mission["observations"] = (
            list(mission.get("observations") or [])
            + [{"observed_at": utc_now(), "source": source, "summary": summary[:4096]}]
        )[-MAX_OBSERVATIONS:]
        mission["updated_at"] = utc_now()
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(self.path, mission, SCHEMA)
        return mission


def mission_summary(mission: dict[str, Any]) -> dict[str, Any]:
    return {
        "mission_id": mission.get("mission_id"),
        "desired_end_state": mission.get("desired_end_state"),
        "current_state": mission.get("current_state"),
        "graduation_progress": mission.get("graduation_progress"),
        "generated_actions": mission.get("generated_actions") or [],
        "retired_actions": mission.get("retired_actions") or [],
        "effectiveness_metrics": mission.get("effectiveness_metrics") or {},
        "external_blockers": mission.get("external_blockers") or [],
        "termination_condition": mission.get("termination_condition"),
    }
