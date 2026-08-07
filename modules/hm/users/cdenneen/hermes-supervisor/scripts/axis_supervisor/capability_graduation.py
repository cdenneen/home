import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .mutation import MutationGate, OperationClass
from .schema_registry import RecordError, read_record, write_record
from .validation_evidence import ValidationEvidenceStore

SCHEMA = "axis.external-development-supervisor.capability-graduation"
SCHEMA_VERSION = "4.0.0"
GATES = (
    "implementation",
    "integration",
    "deployment",
    "validation",
    "verification",
    "operator_acceptance",
    "program_risk",
)
DENOMINATOR_STATES = (
    "active",
    "archive",
    "historical",
    "future",
    "decision",
    "blocked",
    "graduated",
)
COMPLETED_ASSIGNMENT_STATES = {
    "integrated-post-main-verified",
    "repository-converged",
    "runtime-converged",
    "canonical-complete",
}
APPLICABILITY_STATES = {"required", "conditional", "not-applicable"}


def _normalized_path(value: str) -> str:
    return str(PurePosixPath(value.lstrip("./"))).rstrip("/")


def paths_overlap(left: str, right: str) -> bool:
    left = _normalized_path(left)
    right = _normalized_path(right)
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def capabilities_for_paths(paths: list[str], matrix: dict) -> list[str]:
    normalized = [_normalized_path(value) for value in paths if str(value).strip()]
    return sorted(
        name
        for name, definition in (matrix.get("capabilities") or {}).items()
        if any(
            paths_overlap(changed, owned)
            for changed in normalized
            for owned in definition.get("paths") or []
        )
    )


def action_score(entry: dict, capability_states: dict[str, dict] | None = None) -> dict:
    states = capability_states or {}
    affected = list(entry.get("affected_capabilities") or [])
    candidate = entry.get("candidate") or {}
    required_tests = (
        candidate.get("required_tests") or entry.get("required_tests") or []
    )
    allowed_paths = candidate.get("allowed_paths") or entry.get("allowed_paths") or []
    linked = [states[name] for name in affected if name in states]
    verified_capability = sum(
        (value.get("graduation_state") or {}).get("verification", {}).get("state")
        == "passed"
        for value in linked
    )
    graduation_confidence = (
        round(
            sum(float(value.get("graduation_confidence") or 0) for value in linked)
            / len(linked),
            1,
        )
        if linked
        else 0.0
    )
    evidence_quality = min(
        100,
        20 * bool(entry.get("source_fingerprint"))
        + 20 * bool(entry.get("semantic_evidence_fingerprint"))
        + 15 * len(required_tests)
        + 10 * bool((entry.get("authority") or {}).get("source")),
    )
    unblock_value = min(
        100,
        20
        * int((entry.get("ranking_factors") or {}).get("dependency_unlock_count") or 0)
        + 15 * len(entry.get("dependencies") or [])
        + (25 if entry.get("kind") == "repository-convergence" else 0),
    )
    cost = min(
        100,
        10
        + 4 * len(allowed_paths)
        + 5 * len(required_tests)
        + (20 if entry.get("assignment_type") == "capability-deployment" else 0),
    )
    risk = max(
        [int(value.get("program_risk", {}).get("score") or 0) for value in linked]
        or [20 if (entry.get("authority") or {}).get("state") == "unresolved" else 0]
    )
    benefit = (
        verified_capability * 25
        + graduation_confidence
        + evidence_quality
        + unblock_value
    )
    score = round((benefit + 1) / (cost + risk + 1), 3)
    return {
        "score": score,
        "benefit": {
            "verified_capability": verified_capability,
            "graduation_confidence": graduation_confidence,
            "evidence_quality": evidence_quality,
            "unblock_value": unblock_value,
        },
        "penalty": {"cost": cost, "risk": risk},
        "affected_capabilities": affected,
    }


def denominator_state(node: dict) -> str:
    verification = node.get("verification") or {}
    if verification.get("state") == "verified-complete":
        return "graduated"
    if node.get("classification") == "Blocked":
        return "blocked"
    if node.get("flow_stage") == "decision":
        return "decision"
    if node.get("flow_stage") == "future":
        return "future"
    if node.get("flow_stage") == "historical" or node.get("source_state") == "closed":
        return "historical"
    if node.get("classification") in {"Invalid", "Superseded"}:
        return "archive"
    return "active"


def _gate(
    state: str,
    evidence: list[str],
    *,
    applicability: str = "required",
    condition_met: bool = False,
) -> dict:
    applicable = applicability == "required" or (
        applicability == "conditional" and condition_met
    )
    return {
        "applicable": applicable,
        "applicability": applicability,
        "state": state if applicable else "not-required",
        "evidence": sorted(set(filter(None, evidence))),
    }


def _gate_applicability(capability: str, definition: dict) -> dict[str, str]:
    applicability = definition.get("gate_applicability")
    if not isinstance(applicability, dict) or set(applicability) != set(GATES):
        raise ValueError(
            f"capability {capability!r} must explicitly declare applicability for "
            f"every gate: {', '.join(GATES)}"
        )
    normalized = {
        gate: (
            "required"
            if applicability[gate] is True
            else "not-applicable"
            if applicability[gate] is False
            else applicability[gate]
        )
        for gate in GATES
    }
    if any(value not in APPLICABILITY_STATES for value in normalized.values()):
        raise ValueError(
            f"capability {capability!r} gate applicability must be one of "
            f"{', '.join(sorted(APPLICABILITY_STATES))}"
        )
    return normalized


def _first_failing_gate(states: dict[str, dict]) -> str | None:
    return next(
        (
            gate
            for gate in GATES
            if (states.get(gate) or {}).get("applicable")
            and (states.get(gate) or {}).get("state") not in {"passed", "not-required"}
        ),
        None,
    )


def _fingerprint(value: dict) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def assignment_is_satisfied(
    assignment: dict,
    graph: dict,
    repository_convergence: dict,
    convergence: dict,
    graduation: dict,
) -> bool:
    """Return whether current canonical evidence already proves an active assignment."""
    assignment_type = assignment.get("assignment_type")
    if assignment_type == "repository-convergence":
        source = assignment.get("source_item") or {}
        facts = source.get("convergence_facts") or {}
        scope = facts.get("scope")
        repository = assignment.get("project")
        branch = facts.get("branch")
        path = facts.get("path")
        if scope not in {"branch", "worktree"}:
            return False
        if any(
            value.get("ref") == source.get("ref")
            for value in graph.get("nodes") or []
        ):
            return False
        if branch and any(
            value.get("repository") == repository
            and value.get("branch") == branch
            for value in repository_convergence.get("branches") or []
        ):
            return False
        if any(
            value.get("repository") == repository
            and (value.get("path") == path or (branch and value.get("branch") == branch))
            for value in repository_convergence.get("orphan_worktrees") or []
        ):
            return False
        return repository_convergence.get("status") == "green"
    if assignment_type == "capability-deployment":
        source = assignment.get("source_item") or {}
        target = source.get("target_runtime")
        expected = set(source.get("affected_capabilities") or [])
        expected_revision = source.get("expected_revision")
        runtime = next(
            (
                value
                for value in convergence.get("runtimes") or []
                if value.get("runtime") == target
            ),
            {},
        )
        return bool(expected) and bool(expected_revision) and (
            runtime.get("status") == "converged"
            and runtime.get("health") == "healthy"
            and runtime.get("verification_status") == "verified"
            and runtime.get("running_revision") == expected_revision
            and not expected.intersection(runtime.get("capabilities_behind") or [])
        )
    if assignment_type != "no-op-verification":
        return False
    contract = assignment.get("action_contract") or {}
    expected_gates = contract.get("expected_gates") or []
    capabilities = {
        value.get("capability"): value
        for value in graduation.get("capabilities") or []
    }
    if expected_gates:
        return all(
            ((capabilities.get(value.get("capability")) or {}).get("graduation_state") or {})
            .get(value.get("gate"), {})
            .get("state")
            in {"passed", "not-required"}
            for value in expected_gates
        )
    target = assignment.get("work_item")
    node = next(
        (value for value in graph.get("nodes") or [] if value.get("ref") == target),
        {},
    )
    return (node.get("verification") or {}).get("state") == "verified-complete"


def _risk_level(score: int) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


class MilestoneGraduationEngine:
    def build(
        self,
        nodes: list[dict],
        capabilities: list[dict],
        scheduler_state: dict,
    ) -> tuple[list[dict], dict]:
        grouped: dict[str, list[dict]] = {}
        for node in nodes:
            key = str(node.get("milestone") or "unmilestoned")
            grouped.setdefault(key, []).append(node)
        milestones = []
        total_denominator = Counter({state: 0 for state in DENOMINATOR_STATES})
        for key, members in sorted(grouped.items()):
            denominator = Counter(denominator_state(member) for member in members)
            total_denominator.update(denominator)
            refs = {str(member.get("ref")) for member in members}
            related_capabilities = [
                value
                for value in capabilities
                if refs.intersection(value.get("linked_work_items") or [])
            ]
            debts = [
                {
                    "kind": "work-item",
                    "ref": member.get("ref"),
                    "gate": denominator_state(member),
                    "reason": (
                        member.get("flow_evidence")
                        or ["graduation evidence incomplete"]
                    )[0],
                }
                for member in members
                if denominator_state(member) != "graduated"
            ]
            for capability in related_capabilities:
                for gate_name, gate in capability["graduation_state"].items():
                    if gate_name != "graduated" and gate["state"] not in {
                        "passed",
                        "not-required",
                    }:
                        debts.append(
                            {
                                "kind": "capability-gate",
                                "ref": capability["capability"],
                                "gate": gate_name,
                                "reason": (
                                    gate.get("evidence") or ["evidence missing"]
                                )[0],
                            }
                        )
            risk_score = min(
                100,
                denominator["blocked"] * 30
                + denominator["decision"] * 20
                + denominator["active"] * 5
                + sum(
                    int(value.get("program_risk", {}).get("score") or 0)
                    for value in related_capabilities
                )
                // max(1, len(related_capabilities)),
            )
            confidence_samples = [
                float(value.get("operator_confidence") or 0)
                for value in related_capabilities
            ]
            confidence_samples.extend(
                100.0 if denominator_state(member) == "graduated" else 25.0
                for member in members
            )
            confidence = (
                round(sum(confidence_samples) / len(confidence_samples), 1)
                if confidence_samples
                else 0.0
            )
            remaining = len(members) - denominator["graduated"]
            observed_delay = (scheduler_state.get("current_constraint") or {}).get(
                "estimated_roadmap_delay_days"
            )
            milestones.append(
                {
                    "milestone": key,
                    "denominator": {
                        state: denominator[state] for state in DENOMINATOR_STATES
                    },
                    "gate": "passed"
                    if not debts and risk_score < 25
                    else "blocked"
                    if denominator["blocked"] or denominator["decision"]
                    else "pending",
                    "debts": debts,
                    "program_risk": {
                        "score": risk_score,
                        "level": _risk_level(risk_score),
                        "evidence": [
                            f"{len(debts)} unresolved graduation debt(s)",
                            f"{denominator['blocked']} blocked denominator item(s)",
                        ],
                    },
                    "operator_confidence": confidence,
                    "forecast": {
                        "remaining": remaining,
                        "days": observed_delay,
                        "confidence": "observed-throughput"
                        if observed_delay is not None
                        else "insufficient-history",
                    },
                    "graduated": bool(members)
                    and denominator["graduated"] == len(members)
                    and all(value.get("graduated") for value in related_capabilities),
                }
            )
        return milestones, {
            state: total_denominator[state] for state in DENOMINATOR_STATES
        }


class CapabilityGraduationProjector:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "capability-graduation.json"
        self.matrix_path = root / "capability-runtime-matrix.json"
        self.gate = MutationGate(root, source="cycle")

    def _assignments(self, inventory: dict) -> list[dict]:
        values = {
            str(value.get("assignment_id")): value
            for value in inventory.get("supervisor_assignments") or []
        }
        for path in sorted((self.root / "assignments").glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            values[str(value.get("assignment_id"))] = value
        return list(values.values())

    @staticmethod
    def _node_paths(node: dict) -> list[str]:
        return [
            path
            for candidate in (node.get("semantic_record") or {}).get("candidate_slices")
            or []
            for path in candidate.get("allowed_paths") or []
        ]

    def build(self, inventory: dict, graph: dict, convergence: dict) -> dict:
        matrix = json.loads(self.matrix_path.read_text(encoding="utf-8"))
        applicability_model_revision = str(
            matrix.get("applicability_model_revision") or "legacy-boolean-v1"
        )
        if matrix.get("authoritative") and len(matrix.get("capabilities") or {}) != 18:
            raise ValueError("authoritative capability applicability matrix must have exactly 18 rows")
        assignments = self._assignments(inventory)
        nodes = graph.get("nodes") or []
        runtime_by_name = {
            str(value.get("runtime")): value
            for value in convergence.get("runtimes") or []
        }
        convergence_by_name = {
            str(value.get("capability")): value
            for value in convergence.get("capabilities") or []
        }
        try:
            previous = read_record(self.path, SCHEMA) if self.path.exists() else {}
        except RecordError:
            legacy = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(legacy, dict) or (
                legacy.get("schema") != SCHEMA
                or legacy.get("schema_version") not in {"2.0.0", "3.0.0"}
            ):
                raise
            previous = legacy
        previous_by_name = {
            str(value.get("capability")): value
            for value in previous.get("capabilities") or []
        }
        capability_records = []
        for name, definition in sorted((matrix.get("capabilities") or {}).items()):
            applicability = _gate_applicability(name, definition)
            owned_paths = definition.get("paths") or []
            linked_nodes = [
                node
                for node in nodes
                if any(
                    paths_overlap(candidate_path, owned_path)
                    for candidate_path in self._node_paths(node)
                    for owned_path in owned_paths
                )
            ]
            linked_refs = sorted(str(node.get("ref")) for node in linked_nodes)
            related_assignments = []
            for assignment in assignments:
                worker = assignment.get("worker") or {}
                changed_paths = (
                    worker.get("changed_paths") or assignment.get("allowed_paths") or []
                )
                if any(
                    paths_overlap(changed, owned)
                    for changed in changed_paths
                    for owned in owned_paths
                ):
                    related_assignments.append(assignment)
            completed = [
                value
                for value in related_assignments
                if value.get("result_state") in COMPLETED_ASSIGNMENT_STATES
            ]
            repository_evidence = convergence_by_name.get(name) or {}
            repository_current = bool(
                repository_evidence.get("expected_revision")
                and repository_evidence.get("evidence_fingerprint")
            )
            implementation_passed = repository_current and all(
                node.get("flow_stage")
                not in {
                    "backlog",
                    "discovery",
                    "decomposition-needed",
                    "analysis",
                    "implementation-ready",
                    "implementation",
                }
                for node in linked_nodes
            )
            integration_passed = repository_current and all(
                (node.get("verification") or {}).get("state") == "verified-complete"
                for node in linked_nodes
            )
            projected_runtimes = definition.get("runtimes") or []
            required_runtime_names = [
                value
                for value in projected_runtimes
                if (matrix.get("runtimes", {}).get(value) or {}).get(
                    "participation", "required"
                )
                != "optional"
            ]
            runtimes = [
                runtime_by_name.get(value) or {} for value in required_runtime_names
            ]
            deployment_passed = bool(runtimes) and all(
                runtime.get("status") == "converged"
                and name not in (runtime.get("capabilities_behind") or [])
                for runtime in runtimes
            )
            validation_passed = bool(runtimes) and all(
                runtime.get("health") == "healthy"
                and runtime.get("required_command_available", True)
                for runtime in runtimes
            )
            verification_passed = bool(runtimes) and all(
                runtime.get("verification_status") == "verified"
                and name not in (runtime.get("capabilities_behind") or [])
                for runtime in runtimes
            )
            labels = {
                str(label).lower()
                for node in linked_nodes
                for label in (node.get("labels") or [])
            }
            operator_evidence = [
                str(runtime.get("operator_evidence"))
                for runtime in runtimes
                if runtime.get("operator_acceptance") == "accepted"
                and runtime.get("operator_evidence")
            ]
            operator_accepted = bool(operator_evidence) or bool(
                labels.intersection(
                    {"operator::accepted", "operator-accepted", "acceptance::operator"}
                )
            )
            states = {
                "implementation": _gate(
                    "passed" if implementation_passed else "pending",
                    [
                        f"repository revision {repository_evidence.get('expected_revision', 'unknown')}",
                        str(repository_evidence.get("evidence_fingerprint") or ""),
                        *[
                            f"completed assignment {value.get('assignment_id')}"
                            for value in completed
                        ],
                    ],
                    applicability=applicability["implementation"],
                ),
                "integration": _gate(
                    "passed" if integration_passed else "pending",
                    [
                        str(repository_evidence.get("evidence_fingerprint") or ""),
                        *(
                            [f"verified work item {value}" for value in linked_refs]
                            if integration_passed
                            else [
                                f"{len(linked_refs)} linked work item(s) await integration proof"
                            ]
                        ),
                    ],
                    applicability=applicability["integration"],
                ),
                "deployment": _gate(
                    "passed" if deployment_passed else "pending",
                    [f"runtime {value}" for value in projected_runtimes],
                    applicability=applicability["deployment"],
                ),
                "validation": _gate(
                    "passed" if validation_passed else "pending",
                    [
                        f"{value.get('runtime')}: health={value.get('health') or 'unknown'}"
                        for value in runtimes
                    ],
                    applicability=applicability["validation"],
                ),
                "verification": _gate(
                    "passed" if verification_passed else "pending",
                    [
                        f"{value.get('runtime')}: verification={value.get('verification_status') or 'pending'}"
                        for value in runtimes
                    ],
                    applicability=applicability["verification"],
                ),
                "operator_acceptance": _gate(
                    "passed" if operator_accepted else "pending",
                    operator_evidence
                    or ["explicit source-linked operator acceptance is required"],
                    applicability=applicability["operator_acceptance"],
                    condition_met=operator_accepted,
                ),
            }
            pending_gates = sum(
                gate["state"] not in {"passed", "not-required"}
                for gate in states.values()
            )
            blocked_nodes = sum(
                node.get("classification") == "Blocked" for node in linked_nodes
            )
            risk_score = min(100, pending_gates * 12 + blocked_nodes * 25)
            states["program_risk"] = _gate(
                "passed"
                if risk_score < 25
                else "blocked"
                if blocked_nodes
                else "pending",
                [
                    f"{pending_gates} pending gate(s)",
                    f"{blocked_nodes} blocked linked work item(s)",
                ],
                applicability=applicability["program_risk"],
            )
            gate_denominator = sum(value["applicable"] for value in states.values())
            passed = sum(
                value["applicable"] and value["state"] == "passed"
                for value in states.values()
            )
            confidence = (
                round(passed * 100 / gate_denominator, 1)
                if gate_denominator
                else 100.0
            )
            graduated = all(
                value["state"] in {"passed", "not-required"}
                for value in states.values()
            )
            states["graduated"] = _gate(
                "passed" if graduated else "pending",
                [f"{passed}/{gate_denominator} applicable graduation gates passed"],
            )
            invalidation_payload = {
                "capability": name,
                "expected_revision": (convergence_by_name.get(name) or {}).get(
                    "expected_revision"
                ),
                "post_merge_receipts": sorted(
                    {
                        str(
                            (value.get("worker") or {}).get("commit")
                            or value.get("source_fingerprint")
                        )
                        for value in completed
                    }
                ),
            }
            invalidation_fingerprint = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(
                        invalidation_payload, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
            )
            invalidated = bool(completed) and invalidation_fingerprint != (
                previous_by_name.get(name) or {}
            ).get("invalidation_fingerprint")
            pending_runtimes = [
                value
                for value in required_runtime_names
                if name
                in ((runtime_by_name.get(value) or {}).get("capabilities_behind") or [])
            ]
            scheduled_actions = []
            if (invalidated or pending_runtimes) and not matrix.get(
                "validation_streams"
            ):
                scheduled_actions.append(
                    {
                        "fingerprint": invalidation_fingerprint,
                        "stages": list(
                            dict.fromkeys(
                                [
                                    *(["verification"] if invalidated else []),
                                    *(
                                        ["deployment", "validation", "verification"]
                                        if pending_runtimes
                                        else []
                                    ),
                                ]
                            )
                        ),
                        "repository": "ghostspace/axis-lab",
                        "runtimes": pending_runtimes,
                        "reason": "path-targeted post-merge capability evidence changed",
                    }
                )
            capability_records.append(
                {
                    "capability": name,
                    "paths": owned_paths,
                    "projected_runtimes": projected_runtimes,
                    "required_runtimes": required_runtime_names,
                    "linked_work_items": linked_refs,
                    "gate_applicability": applicability,
                    "graduation_state": states,
                    "graduation_confidence": confidence,
                    "gate_denominator": gate_denominator,
                    "gates_passed": passed,
                    "first_failing_gate": _first_failing_gate(states),
                    "operator_confidence": confidence
                    if states["operator_acceptance"]["state"] == "not-required"
                    else round(
                        (confidence + (100 if operator_accepted else 0)) / 2, 1
                    ),
                    "program_risk": {
                        "score": risk_score,
                        "level": _risk_level(risk_score),
                        "evidence": states["program_risk"]["evidence"],
                    },
                    "invalidation_fingerprint": invalidation_fingerprint,
                    "scheduled_actions": scheduled_actions,
                    "graduated": graduated,
                }
            )
        capability_by_name = {
            value["capability"]: value for value in capability_records
        }
        validation_streams = []
        evidence_store = ValidationEvidenceStore(self.root)
        for stream_name, stream in sorted(
            (matrix.get("validation_streams") or {}).items()
        ):
            stream_capabilities = [
                name
                for name in stream.get("capabilities") or []
                if name in capability_by_name
            ]
            expected_gates = [
                {
                    "capability": name,
                    "gate": gate_name,
                    "from_state": capability_by_name[name]["graduation_state"][
                        gate_name
                    ]["state"],
                    "to_state": "passed",
                }
                for name in stream_capabilities
                for gate_name in stream.get("gates") or []
                if capability_by_name[name]["graduation_state"].get(gate_name, {}).get(
                    "applicable"
                )
                and capability_by_name[name]["graduation_state"][gate_name]["state"]
                != "passed"
            ]
            findings = [
                f"{value['capability']}:{value['gate']}={value['from_state']}"
                for value in expected_gates
            ]
            evidence = evidence_store.persist(
                stream_name,
                {
                    "applicability_model_revision": applicability_model_revision,
                    "source_convergence_digest": convergence.get("convergence_digest"),
                    "runtimes": list(stream.get("runtimes") or []),
                    "capabilities": stream_capabilities,
                    "expected_gates": expected_gates,
                    "findings": findings,
                },
            )
            validation_streams.append(
                {
                    "stream": stream_name,
                    "title": str(stream.get("title") or stream_name),
                    "runtimes": list(stream.get("runtimes") or []),
                    "capabilities": stream_capabilities,
                    "expected_gates": expected_gates,
                    "first_failing_gate": (
                        {
                            "capability": expected_gates[0]["capability"],
                            "gate": expected_gates[0]["gate"],
                        }
                        if expected_gates
                        else None
                    ),
                    "status": "assignment-required"
                    if expected_gates
                    else "evidence-promoted",
                    "evidence": evidence,
                }
            )
        scored_actions = []
        for entry in graph.get("executable_queue") or []:
            paths = (
                (entry.get("candidate") or {}).get("allowed_paths")
                or entry.get("allowed_paths")
                or []
            )
            scored = dict(entry)
            scored["affected_capabilities"] = capabilities_for_paths(paths, matrix)
            scored_actions.append(
                {
                    "ref": entry.get("ref"),
                    "action_score": action_score(scored, capability_by_name),
                }
            )
        milestones, denominator = MilestoneGraduationEngine().build(
            nodes, capability_records, graph.get("scheduler_state") or {}
        )
        graduated_count = sum(value["graduated"] for value in capability_records)
        total_capabilities = len(capability_records)
        aggregate_risk = round(
            sum(value["program_risk"]["score"] for value in capability_records)
            / max(1, total_capabilities)
        )
        operator_confidence = round(
            sum(value["operator_confidence"] for value in capability_records)
            / max(1, total_capabilities),
            1,
        )
        payload = {
            "applicability_model_revision": applicability_model_revision,
            "source_inventory_generation_id": inventory.get("generation_id"),
            "source_graph_generation_id": graph.get("generation_id"),
            "source_convergence_digest": convergence.get("convergence_digest"),
            "repository_convergence_digest": convergence.get(
                "repository_convergence_digest"
            ),
            "capabilities": capability_records,
            "validation_streams": validation_streams,
            "milestones": milestones,
            "denominator": denominator,
            "action_scores": scored_actions,
        }
        projection_digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        effectiveness_fingerprint = _fingerprint(
            {
                "repository_convergence_digest": convergence.get(
                    "repository_convergence_digest"
                ),
                "capability_convergence_digest": convergence.get(
                    "convergence_digest"
                ),
                "capabilities": [
                    {
                        "capability": value["capability"],
                        "graduation_state": value["graduation_state"],
                        "linked_work_items": value["linked_work_items"],
                    }
                    for value in capability_records
                ],
                "nodes": [
                    {
                        "ref": value.get("ref"),
                        "milestone": value.get("milestone"),
                        "classification": value.get("classification"),
                        "flow_stage": value.get("flow_stage"),
                        "source_fingerprint": value.get("source_fingerprint"),
                        "verification_state": (value.get("verification") or {}).get(
                            "state"
                        ),
                    }
                    for value in nodes
                ],
            }
        )
        projection = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "projection_digest": projection_digest,
            "effectiveness_fingerprint": effectiveness_fingerprint,
            **payload,
            "primary_kpi": {
                "name": "graduated-capabilities",
                "count": graduated_count,
                "denominator": total_capabilities,
                "percent": round(graduated_count * 100 / total_capabilities, 1)
                if total_capabilities
                else 0.0,
            },
            "program_risk": {
                "score": aggregate_risk,
                "level": _risk_level(aggregate_risk),
                "evidence": [
                    f"{sum(len(value['debts']) for value in milestones)} milestone debt(s)",
                    f"{denominator['blocked']} blocked denominator item(s)",
                ],
            },
            "operator_confidence": operator_confidence,
        }
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(self.path, projection, SCHEMA)
        return projection
