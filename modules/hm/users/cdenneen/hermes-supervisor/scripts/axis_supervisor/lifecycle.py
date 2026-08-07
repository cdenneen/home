import os
from pathlib import Path
from typing import Any

from .repository_ownership import resolve_repository_ownership


LIFECYCLE_STATES = frozenset(
    {
        "ready-semantic",
        "ready-implementation",
        "running-semantic",
        "running-implementation",
        "implementation-complete",
        "awaiting-integration",
        "integrated-post-main-verified",
        "repository-convergence-pending",
        "repository-converged",
        "runtime-convergence-pending",
        "runtime-converged",
        "canonical-complete",
        "deployment-failed",
        "completed",
        "waiting",
        "blocked",
        "failed",
        "cancelled",
        "recovery-required",
    }
)
TERMINAL_STATES = frozenset(
    {
        "completed",
        "repository-converged",
        "runtime-converged",
        "canonical-complete",
        "deployment-failed",
        "waiting",
        "blocked",
        "failed",
        "cancelled",
        "recovery-required",
    }
)


def lifecycle_state(value: dict[str, Any] | str) -> str:
    if isinstance(value, str):
        state = value
    else:
        state = str(value.get("lifecycle_state") or "")
        if not state:
            state = _historical_state(value)
    if state not in LIFECYCLE_STATES:
        raise ValueError(f"unsupported lifecycle_state: {state or '<missing>'}")
    return state


def _historical_state(value: dict[str, Any]) -> str:
    state = str(value.get("state") or "")
    phase = str(value.get("phase") or "")
    if state in {"complete", "completed"} or phase in {"integrated", "semantic-complete"}:
        return "completed"
    if state == "cancelled":
        return "cancelled"
    if state == "failed" or phase == "failed":
        return "failed"
    if state == "blocked":
        return "blocked"
    if state == "waiting":
        return "waiting"
    if phase == "recovery":
        return "recovery-required"
    if phase == "awaiting-integration":
        return "awaiting-integration"
    if phase == "integration":
        return "awaiting-integration"
    if phase == "semantic":
        return "ready-semantic" if state == "ready" else "running-semantic"
    if phase == "implementation":
        return "ready-implementation" if state == "ready" else "running-implementation"
    raise ValueError(f"cannot migrate historical lifecycle state={state!r} phase={phase!r}")


def adapt_assignment(
    value: dict[str, Any], root: Path | None = None
) -> dict[str, Any]:
    adapted = dict(value)
    adapted.setdefault("schema", "axis.external-development-supervisor.assignment")
    original_version = adapted.get("schema_version")
    legacy = original_version in {None, "1.0.0", "2.0.0", "3.0.0", "4.0.0"}
    if legacy:
        adapted["schema_version"] = "5.0.0"
    if original_version in {None, "1.0.0", "2.0.0"} and adapted.get("project"):
        ownership = resolve_repository_ownership(
            [adapted.get("responsibility")],
            adapted.get("project"),
            context=f"assignment-v1-migration:{adapted.get('assignment_id')}",
            allow_repository_inference=True,
        )
        adapted["responsibility"] = ownership["responsibility"]
        adapted["repository_ownership"] = ownership
    adapted.setdefault("planning_record", None)
    adapted.setdefault("allowed_paths", [])
    adapted.setdefault("required_tests", [])
    adapted.setdefault("action_contract", None)
    if original_version == "3.0.0" and isinstance(adapted["action_contract"], dict):
        contract = dict(adapted["action_contract"])
        capabilities = sorted(set(contract.get("expected_capabilities") or []))
        contract.setdefault(
            "capability_context",
            [{"capability": capability} for capability in capabilities],
        )
        contract.setdefault(
            "merge_impact_projection",
            {
                "affected_capabilities": capabilities,
                "product_subdimensions": [],
                "milestones": list(contract.get("expected_milestones") or []),
                "gates": list(contract.get("expected_gates") or []),
                "production_confidence_before": (
                    contract.get("pre_snapshot") or {}
                ).get("confidence"),
            },
        )
        adapted["action_contract"] = contract
    kind = str(adapted.get("kind") or "")
    assignment_type = adapted.get("assignment_type")
    if not assignment_type:
        assignment_type = {
            "semantic-decomposition": "read-only-analysis",
            "technical-revalidation": "no-op-verification",
            "repository-convergence": "repository-convergence",
        }.get(kind, "code-implementation")
    adapted["assignment_type"] = assignment_type
    state = lifecycle_state(value)
    if not adapted.get("result_state"):
        if state == "completed" and assignment_type == "read-only-analysis":
            adapted["result_state"] = "analysis-completed"
        elif state == "completed" and assignment_type == "no-op-verification":
            adapted["result_state"] = "no-op-verification-completed"
        elif state == "awaiting-integration":
            adapted["result_state"] = "awaiting-integration"
        elif state in {"blocked", "waiting", "failed", "cancelled", "recovery-required"}:
            adapted["result_state"] = state
        else:
            adapted["result_state"] = "pending"
    if not adapted.get("work_item_disposition"):
        verification = (((adapted.get("worker") or {}).get("record") or {}).get(
            "verification_result"
        ) or {})
        disposition = verification.get("disposition")
        if assignment_type == "no-op-verification" and disposition == "verified-complete":
            adapted["work_item_disposition"] = "no-op-verified"
        elif disposition == "corrective-implementation-required":
            adapted["work_item_disposition"] = "requires-implementation"
        elif disposition == "human-authority-required":
            adapted["work_item_disposition"] = "requires-human-decision"
        elif assignment_type in {"read-only-analysis", "no-op-verification"}:
            adapted["work_item_disposition"] = "analyzed-only"
        else:
            adapted["work_item_disposition"] = "not-evaluated"
    adapted.setdefault("mutation_grant_id", None)
    adapted.setdefault("mutation_grant_uri", None)
    adapted.setdefault("origin_finding", None)
    adapted.setdefault("targeted_replay", None)
    adapted.setdefault("worktree_context", None)
    adapted.setdefault("bootstrap_override", None)
    adapted.setdefault("delivery_lane", None)
    adapted.setdefault("dispatch_generation", None)
    adapted.setdefault("lane_entered_at", None)
    legacy_lease = adapted.pop("lease", None)
    if legacy_lease and not adapted.get("lease_id"):
        runtime_root = root or Path(
            os.environ.get(
                "AXIS_SUPERVISOR_ROOT",
                Path.home()
                / ".hermes"
                / "supervisor"
                / "axis-development-supervisor",
            )
        )
        lease_id = str(
            (legacy_lease or {}).get("lease_id")
            or adapted.get("assignment_id")
            or ""
        )
        lease_path = runtime_root / "leases" / lease_id / "lease.json"
        if lease_id and lease_path.exists():
            adapted["lease_id"] = lease_id
            adapted["lease_uri"] = lease_path.resolve().as_uri()
    adapted["lifecycle_state"] = state
    adapted.pop("state", None)
    adapted.pop("phase", None)
    return adapted


def set_lifecycle(value: dict[str, Any], state: str) -> None:
    value["lifecycle_state"] = lifecycle_state(state)
    value.pop("state", None)
    value.pop("phase", None)


def is_terminal(value: dict[str, Any] | str) -> bool:
    return lifecycle_state(value) in TERMINAL_STATES


def is_completed(value: dict[str, Any] | str) -> bool:
    return lifecycle_state(value) == "completed"


def is_integrable(value: dict[str, Any] | str) -> bool:
    return lifecycle_state(value) == "awaiting-integration"


def is_read_only_work(value: dict[str, Any]) -> bool:
    return value.get("assignment_type") in {
        "read-only-analysis",
        "no-op-verification",
    }
