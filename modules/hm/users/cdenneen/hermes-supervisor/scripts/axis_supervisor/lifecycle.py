import os
from pathlib import Path
from typing import Any


LIFECYCLE_STATES = frozenset(
    {
        "ready-semantic",
        "ready-implementation",
        "running-semantic",
        "running-implementation",
        "awaiting-integration",
        "completed",
        "waiting",
        "blocked",
        "failed",
        "cancelled",
        "recovery-required",
    }
)
TERMINAL_STATES = frozenset(
    {"completed", "waiting", "blocked", "failed", "cancelled", "recovery-required"}
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
    adapted.setdefault("schema_version", "1.0.0")
    adapted.setdefault("planning_record", None)
    adapted.setdefault("allowed_paths", [])
    adapted.setdefault("required_tests", [])
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
    adapted["lifecycle_state"] = lifecycle_state(value)
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
    return value.get("kind") in {"semantic-decomposition", "technical-revalidation"}
