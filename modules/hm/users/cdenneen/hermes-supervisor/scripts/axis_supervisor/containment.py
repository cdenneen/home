"""Bounded execution-strategy containment for the legacy Axis-control dispatcher.

Scope (see docs/recovery/* and the 2026-08-28 convergence audit): this module
operates entirely within the existing legacy controller. It creates no new
writer, does not touch controller custody, and does not require canonical
activation. It exists to stop the dispatcher from indefinitely repeating a
non-converging execution strategy for the same canonical work item, while
never blocking a genuine mutation attempt and never manufacturing mutation
authority that the underlying graph/queue didn't already produce.

State is always derived fresh from the durable assignment history for a work
item - never a separately mutable counter - so a process restart, a new
assignment ID, a reworded task title, a new worktree/branch/session, or
another analysis reaching the same conclusion cannot reset it. Only two
things reset it: a genuine canonical-convergence outcome, or an actual
mutation-type dispatch (code-implementation / ci-integration-repair), which
is a different execution strategy than repeated analysis-only dispatch.

Fail-open, not fail-closed: this is a new, additive safety layer sitting on
top of an already-running, single-writer legacy controller that this program
is explicitly required to keep operational. A bug or unexpected data shape
in this module must degrade to "no containment applied" for the affected
work item, not to blocking dispatch - the opposite of this project's usual
"unknown fails closed" default elsewhere, deliberately, because here
availability of the sole existing writer is the more load-bearing invariant.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

MUTATION_ASSIGNMENT_TYPES = {"code-implementation", "ci-integration-repair"}
NON_MUTATING_ASSIGNMENT_TYPES = {"read-only-analysis", "no-op-verification"}
CONVERGENCE_LIFECYCLE_STATES = {"runtime-converged", "repository-converged"}
CONVERGENCE_RESULT_STATES = {"integrated-post-main-verified"}
CONVERGENCE_DISPOSITIONS = {"canonical-complete"}

# Evidence-derived (2026-08-28 audit): axis#7 reached its own
# "requires-implementation" conclusion twice across 43 non-mutating
# dispatches without ever being followed by an implementation attempt.
NON_MUTATING_STREAK_CEILING = 6

# axis#7 showed 8 consecutive genuine failures with no cooldown at all.
FAILURE_STREAK_THRESHOLD = 4
COOLDOWN_SECONDS = 4 * 3600

# Shadow-only (never enforced): semantics of "mutation attempt" are not yet
# cleanly comparable across work items (the one positive case study,
# axis#96, converged on either its 7th mutation-type assignment or its 11th
# assignment overall depending on how "attempt" is counted). Record when
# these thresholds would fire; do not block on them until more live evidence
# accumulates.
MUTATION_ATTEMPT_SHADOW_THRESHOLDS = (8, 10, 12)

# A failed outcome whose error text matches one of these is infrastructure
# unavailability, not an execution failure of the work itself - it must not
# increment (or reset) the failure streak, and must never be recorded as if
# the canonical work item itself failed.
_INFRA_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "i/o timeout",
    "connection refused",
    "connection reset",
    "unreachable",
    "dns",
    "name resolution",
    "network is unreachable",
    "temporary failure in name resolution",
    "econnrefused",
    "econnreset",
)

_EVENTS_FILENAME = "containment/events.jsonl"
_QUARANTINE_FILENAME = "quarantines.json"
_QUARANTINE_REASON_PREFIX = "containment:"


@dataclass(frozen=True)
class ContainmentState:
    work_item: str
    non_mutating_streak: int
    failure_streak: int
    mutation_attempt_count: int
    escalated: bool
    cooling: bool
    cooldown_until_epoch: int | None
    last_reset_reason: str
    last_reset_epoch: int
    evaluated_assignment_count: int
    shadow_mutation_thresholds_exceeded: tuple[int, ...]


def _is_convergence(assignment: dict[str, Any]) -> bool:
    return (
        assignment.get("lifecycle_state") in CONVERGENCE_LIFECYCLE_STATES
        or assignment.get("result_state") in CONVERGENCE_RESULT_STATES
        or assignment.get("work_item_disposition") in CONVERGENCE_DISPOSITIONS
    )


def _is_infra_failure(assignment: dict[str, Any]) -> bool:
    if assignment.get("result_state") != "failed":
        return False
    error = str(assignment.get("error") or "").lower()
    return any(marker in error for marker in _INFRA_ERROR_MARKERS)


def _timestamp(assignment: dict[str, Any]) -> int:
    return int(assignment.get("created_at_epoch") or 0)


def load_assignments_for_work_item(
    root: Path, work_item: str
) -> list[dict[str, Any]]:
    assignments_dir = root / "assignments"
    values = []
    for path in assignments_dir.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict):
            continue
        if value.get("work_item") == work_item:
            values.append(value)
    return values


def compute_state(
    work_item: str, assignments: list[dict[str, Any]], now: int | None = None
) -> ContainmentState:
    """Pure function: derive containment state from a work item's full
    assignment history. No I/O - callers load history separately so this
    stays trivially testable and trivially safe to recompute after a
    restart."""
    now = int(now if now is not None else time.time())
    ordered = sorted(assignments, key=_timestamp)

    non_mutating_streak = 0
    failure_streak = 0
    failure_streak_type: str | None = None
    mutation_attempt_count = 0
    last_reset_reason = "no-history"
    last_reset_epoch = 0
    shadow_exceeded: list[int] = []

    for assignment in ordered:
        result_state = assignment.get("result_state")
        if result_state == "pending":
            # Outcome not yet known - contributes to no streak either way.
            continue
        ts = _timestamp(assignment)
        assignment_type = assignment.get("assignment_type")

        if _is_convergence(assignment):
            non_mutating_streak = 0
            failure_streak = 0
            failure_streak_type = None
            mutation_attempt_count = 0
            last_reset_reason = "canonical-advancement"
            last_reset_epoch = ts
            continue

        if assignment_type in MUTATION_ASSIGNMENT_TYPES:
            mutation_attempt_count += 1
            non_mutating_streak = 0
            last_reset_reason = "material-implementation-attempt"
            last_reset_epoch = ts
            for threshold in MUTATION_ATTEMPT_SHADOW_THRESHOLDS:
                if mutation_attempt_count >= threshold and threshold not in shadow_exceeded:
                    shadow_exceeded.append(threshold)
        elif assignment_type in NON_MUTATING_ASSIGNMENT_TYPES:
            non_mutating_streak += 1
        # Any other assignment_type (e.g. repository-convergence) leaves the
        # non-mutating streak untouched - it's neither a repeat of the
        # analysis-only strategy nor a mutation attempt.

        if _is_infra_failure(assignment):
            # Infrastructure unavailability: neutral. The work item was
            # never actually attempted, so this must not look like the work
            # itself failing, and must not silently clear a real streak either.
            continue

        # Failure streak is scoped to a single execution strategy
        # (assignment_type). A change of strategy - e.g. code-implementation
        # repeatedly failing, then the dispatcher's own existing logic
        # switching to ci-integration-repair - is exactly this project's own
        # pre-existing notion of "a materially different execution plan"
        # (dispatcher.py already reclassifies to ci-integration-repair on
        # prior implementation failure, treating it as a distinct repair
        # strategy, not "more of the same"). Without this, the real axis#96
        # convergence case - 3 code-implementation failures followed by a
        # repair strategy that succeeded on its 4th attempt - would have been
        # blocked by cooldown partway through, which the 2026-08-28 audit's
        # test #15 exists specifically to catch. Repeated failures WITHIN one
        # strategy (e.g. axis#7's 8 consecutive read-only-analysis failures)
        # still accumulate correctly.
        if assignment_type != failure_streak_type:
            failure_streak = 0
            failure_streak_type = assignment_type
        if result_state == "failed":
            failure_streak += 1
        else:
            failure_streak = 0

    cooldown_until: int | None = None
    cooling = False
    if failure_streak >= FAILURE_STREAK_THRESHOLD and ordered:
        last_ts = _timestamp(ordered[-1])
        cooldown_until = last_ts + COOLDOWN_SECONDS
        cooling = cooldown_until > now

    return ContainmentState(
        work_item=work_item,
        non_mutating_streak=non_mutating_streak,
        failure_streak=failure_streak,
        mutation_attempt_count=mutation_attempt_count,
        escalated=non_mutating_streak >= NON_MUTATING_STREAK_CEILING,
        cooling=cooling,
        cooldown_until_epoch=cooldown_until,
        last_reset_reason=last_reset_reason,
        last_reset_epoch=last_reset_epoch,
        evaluated_assignment_count=len(ordered),
        shadow_mutation_thresholds_exceeded=tuple(shadow_exceeded),
    )


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _load_quarantines(root: Path) -> dict[str, Any]:
    path = root / _QUARANTINE_FILENAME
    if not path.exists():
        return {"items": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": []}
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        return {"items": []}
    return value


def _set_containment_cooldown_quarantine(
    root: Path, work_item: str, expires_at_epoch: int | None
) -> None:
    """Write (or clear) this module's own cooldown entry in the shared,
    pre-existing quarantines.json. Only ever touches entries this module
    created (tagged by reason prefix) - any manually-added operator entries
    for other work items or other reasons are preserved untouched.

    Deliberately NOT used for the escalation case: quarantines.json blocks
    ALL dispatch for a work item regardless of assignment type, which would
    also block a genuine implementation attempt - exactly what escalation
    must never do. Cooldown uses it because cooldown's whole point is to
    pause the failing execution strategy broadly for a bounded window.
    """
    current = _load_quarantines(root)
    others = [
        item
        for item in current["items"]
        if not (
            item.get("work_item") == work_item
            and str(item.get("reason", "")).startswith(_QUARANTINE_REASON_PREFIX)
        )
    ]
    if expires_at_epoch is not None:
        others.append(
            {
                "work_item": work_item,
                "expires_at_epoch": expires_at_epoch,
                "reason": f"{_QUARANTINE_REASON_PREFIX}consecutive-failure-cooldown",
            }
        )
    _atomic_write_json(root / _QUARANTINE_FILENAME, {"items": others})


def _append_event(root: Path, event: dict[str, Any]) -> None:
    path = root / _EVENTS_FILENAME
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        handle.write(json.dumps(event, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def evaluate(root: Path, work_item: str, now: int | None = None) -> ContainmentState | None:
    """Compute containment state for a work item and durably record the
    decision (append-only event, plus refreshing the cooldown quarantine
    entry if applicable). Returns None (fail-open) on any unexpected error -
    logged as its own event, never raised, so a bug here cannot block the
    legacy dispatcher from operating.
    """
    now = int(now if now is not None else time.time())
    try:
        assignments = load_assignments_for_work_item(root, work_item)
        state = compute_state(work_item, assignments, now=now)
        _set_containment_cooldown_quarantine(root, work_item, state.cooldown_until_epoch)
        _append_event(
            root,
            {
                "schema": "axis.external-development-supervisor.containment-event",
                "schema_version": "1.0.0",
                "work_item": work_item,
                "execution_strategy": "non-mutating-analysis"
                if state.non_mutating_streak > 0
                else "mutation",
                "non_mutating_streak": state.non_mutating_streak,
                "failure_streak": state.failure_streak,
                "mutation_attempt_count": state.mutation_attempt_count,
                "shadow_mutation_thresholds_exceeded": list(
                    state.shadow_mutation_thresholds_exceeded
                ),
                "escalated": state.escalated,
                "cooling": state.cooling,
                "cooldown_until_epoch": state.cooldown_until_epoch,
                "last_reset_reason": state.last_reset_reason,
                "last_reset_epoch": state.last_reset_epoch,
                "evaluated_assignment_count": state.evaluated_assignment_count,
                "decision": (
                    "escalate-non-mutating-dispatch"
                    if state.escalated
                    else "cooldown-mutation-dispatch"
                    if state.cooling
                    else "no-containment-action"
                ),
                "recorded_at_epoch": now,
            },
        )
        return state
    except Exception as exc:  # noqa: BLE001 - fail-open is deliberate, see module docstring
        try:
            _append_event(
                root,
                {
                    "schema": "axis.external-development-supervisor.containment-event",
                    "schema_version": "1.0.0",
                    "work_item": work_item,
                    "decision": "containment-evaluation-error-fail-open",
                    "reason": f"{type(exc).__name__}: {exc}",
                    "recorded_at_epoch": now,
                },
            )
        except Exception:
            pass
        return None


def blocks_non_mutating_dispatch(state: ContainmentState | None) -> bool:
    """True only for another non-mutating (analysis/no-op) dispatch of the
    same work item. Never true for a mutation-type dispatch - escalation
    must never prevent the exact action (implementation) it exists to force
    a decision about."""
    return state is not None and state.escalated


def blocks_mutation_dispatch(state: ContainmentState | None) -> bool:
    """True only during an active cooldown window. Read-only/status/
    reconciliation work (non-mutating dispatch) remains available during
    cooldown by design - only the failing mutation strategy pauses."""
    return state is not None and state.cooling


STALE_LEASE_MIN_AGE_SECONDS = 7 * 86400


def sweep_stale_leases(
    root: Path, now: int | None = None, min_age_seconds: int = STALE_LEASE_MIN_AGE_SECONDS
) -> list[dict[str, Any]]:
    """Archive (never delete) leases already excluded from active custody by
    the existing collector.py convention (directories named `stale-*`).

    Conservative by construction: only ever touches directories the rest of
    the system has already stopped treating as live (collector.py's own
    active-lease scan skips any `stale-*` directory outright - see
    collector.py's lease scan loop). This function adds a second,
    independent check before archiving: the lease's own referenced
    assignment must be lifecycle-terminal (or missing entirely) - if a
    stale-prefixed lease's assignment is somehow still non-terminal, that is
    exactly the "still live" case the Product Owner asked to be proven
    against, so it is left in place rather than archived.

    Returns the list of reclamation records for leases actually archived.
    """
    from .lifecycle import is_terminal  # local import: avoid a hard dependency

    now = int(now if now is not None else time.time())
    leases_dir = root / "leases"
    archive_dir = root / "leases-archive"
    assignments_dir = root / "assignments"
    reclaimed: list[dict[str, Any]] = []

    if not leases_dir.exists():
        return reclaimed

    for lease_dir in sorted(leases_dir.glob("stale-*")):
        lease_path = lease_dir / "lease.json"
        if not lease_dir.is_dir():
            continue
        try:
            lease = json.loads(lease_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        acquired_at = int(lease.get("acquired_at_epoch") or 0)
        heartbeat_at = int(lease.get("heartbeat_at_epoch") or acquired_at)
        age_seconds = now - max(acquired_at, heartbeat_at)
        if age_seconds < min_age_seconds:
            continue

        assignment_id = lease.get("assignment_id")
        owner_still_live = False
        assignment_terminal_state = "missing"
        if assignment_id:
            assignment_path = assignments_dir / f"{assignment_id}.json"
            if assignment_path.exists():
                try:
                    assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
                    if not is_terminal(assignment):
                        owner_still_live = True
                        assignment_terminal_state = "non-terminal"
                    else:
                        assignment_terminal_state = str(
                            assignment.get("lifecycle_state") or "terminal"
                        )
                except (OSError, json.JSONDecodeError, ValueError):
                    # Cannot prove terminality - fail toward NOT reclaiming.
                    owner_still_live = True
                    assignment_terminal_state = "unreadable"

        record = {
            "schema": "axis.external-development-supervisor.lease-reclamation",
            "schema_version": "1.0.0",
            "lease_dir": lease_dir.name,
            "assignment_id": assignment_id,
            "owner_run_id": lease.get("owner_run_id"),
            "heartbeat_at_epoch": heartbeat_at,
            "age_seconds_at_reclamation": age_seconds,
            "assignment_terminal_state": assignment_terminal_state,
            "reclaimed": not owner_still_live,
            "reclamation_timestamp_epoch": now,
        }
        if owner_still_live:
            record["reason"] = "assignment not lifecycle-terminal or unreadable - left in place"
            _append_event(root, {**record, "decision": "lease-sweep-skipped-live-owner"})
            continue

        archive_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = archive_dir / lease_dir.name
        if not destination.exists():
            lease_dir.rename(destination)
        record["reason"] = "stale-prefixed, past minimum age, assignment lifecycle-terminal"
        _append_event(root, {**record, "decision": "lease-sweep-archived"})
        reclaimed.append(record)

    return reclaimed
