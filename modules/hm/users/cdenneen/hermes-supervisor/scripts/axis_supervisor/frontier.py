import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .mutation import MutationGate, OperationClass
from .schema_registry import write_record


FRONTIER_SCHEMA = "axis.external-development-supervisor.executable-frontier"
STAGE_CAPACITIES = {
    "semantic": 2,
    "implementation": 4,
    "integration": 2,
    "repair": 2,
    "deployment": 2,
}
TERMINAL_STATES = {
    "completed",
    "repository-converged",
    "runtime-converged",
    "deployment-failed",
    "waiting",
    "blocked",
    "failed",
    "cancelled",
    "recovery-required",
    "canonical-complete",
}


def stage_for(value: dict) -> str:
    assignment_type = value.get("assignment_type")
    lifecycle = value.get("lifecycle_state")
    if assignment_type == "capability-deployment":
        return "deployment"
    if lifecycle == "awaiting-integration":
        return "integration"
    if assignment_type == "ci-integration-repair":
        return "repair"
    if assignment_type in {"read-only-analysis", "no-op-verification"}:
        return "semantic"
    return "implementation"


def _paths(value: dict) -> list[str]:
    paths = value.get("allowed_paths")
    if paths is None:
        paths = (value.get("candidate") or {}).get("allowed_paths")
    return sorted(
        {
            str(PurePosixPath(str(path).lstrip("./")))
            for path in paths or []
            if str(path).strip()
        }
    )


def conflict_domains(value: dict) -> list[str]:
    repository = str(value.get("project") or value.get("repository") or "unknown")
    paths = _paths(value)
    if not paths:
        return [f"repo:{repository}:*"]
    return [f"repo:{repository}:path:{path}" for path in paths]


def _path_overlaps(left: str, right: str) -> bool:
    if left == "*" or right == "*":
        return True
    left = left.rstrip("/")
    right = right.rstrip("/")
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def compatible(left: dict, right: dict) -> bool:
    left_repository = str(left.get("project") or left.get("repository") or "")
    right_repository = str(right.get("project") or right.get("repository") or "")
    if left_repository != right_repository:
        return True
    left_paths = _paths(left) or ["*"]
    right_paths = _paths(right) or ["*"]
    return not any(_path_overlaps(a, b) for a in left_paths for b in right_paths)


def _quarantined_refs(root: Path, now: int) -> set[str]:
    path = root / "quarantines.json"
    if not path.exists():
        return set()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {
        str(item.get("work_item"))
        for item in value.get("items") or []
        if item.get("work_item") and int(item.get("expires_at_epoch") or 0) > now
    }


def build_executable_frontier(
    root: Path,
    queue: list[dict],
    active_assignments: list[dict],
    source_generation_id: str | None = None,
    *,
    now: int | None = None,
) -> dict:
    now = int(time.time()) if now is None else now
    active = [
        value
        for value in active_assignments
        if value.get("lifecycle_state") not in TERMINAL_STATES
    ]
    in_use = {stage: 0 for stage in STAGE_CAPACITIES}
    for value in active:
        in_use[stage_for(value)] += 1

    entries = []
    selected: list[str] = []
    selected_values: list[dict] = []
    deferred = []
    seen: set[str] = set()
    quarantined = _quarantined_refs(root, now)
    for value in queue:
        entry_id = str(value.get("ref") or "")
        stage = stage_for(value)
        entry = {
            "entry_id": entry_id,
            "target_ref": str(value.get("target_ref") or entry_id),
            "repository": str(value.get("project") or "unknown"),
            "stage": stage,
            "allowed_paths": _paths(value),
            "conflict_domains": conflict_domains(value),
            "ranking_score": int(value.get("ranking_score") or 0),
            "assignment_type": value.get("assignment_type"),
        }
        entries.append(entry)
        if entry_id in seen:
            deferred.append(
                {"entry_id": entry_id, "reason": "duplicate", "conflicts_with": entry_id}
            )
            continue
        seen.add(entry_id)
        if entry["target_ref"] in quarantined:
            deferred.append(
                {"entry_id": entry_id, "reason": "quarantined", "conflicts_with": None}
            )
            continue
        if in_use[stage] + sum(stage_for(item) == stage for item in selected_values) >= STAGE_CAPACITIES[stage]:
            deferred.append(
                {"entry_id": entry_id, "reason": "stage-capacity", "conflicts_with": None}
            )
            continue
        conflict = next(
            (
                item
                for item in [*active, *selected_values]
                if not compatible(value, item)
            ),
            None,
        )
        if conflict is not None:
            deferred.append(
                {
                    "entry_id": entry_id,
                    "reason": "conflict-domain",
                    "conflicts_with": str(
                        conflict.get("assignment_id") or conflict.get("ref") or "active"
                    ),
                }
            )
            continue
        selected.append(entry_id)
        selected_values.append(value)

    return {
        "schema": FRONTIER_SCHEMA,
        "schema_version": "1.0.0",
        "generation_id": str(uuid.uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_generation_id": source_generation_id,
        "capacities": dict(STAGE_CAPACITIES),
        "in_use": in_use,
        "entries": entries,
        "selected": selected,
        "deferred": deferred,
    }


class ExecutableFrontier:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "executable-frontier.json"
        self.gate = MutationGate(root, source="frontier")

    def build(
        self,
        queue: list[dict],
        active_assignments: list[dict],
        source_generation_id: str | None = None,
    ) -> dict:
        value = build_executable_frontier(
            self.root, queue, active_assignments, source_generation_id
        )
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(self.path, value, FRONTIER_SCHEMA)
        return value
