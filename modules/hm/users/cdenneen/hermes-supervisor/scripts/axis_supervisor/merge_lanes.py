"""Durable, custody-aware consumption for externally-created merge requests."""

import json
import os
import tempfile
import time
from pathlib import Path

from .integrator import Integrator
from .lifecycle import is_integrable
from .repository_ownership import responsibility_for_repository

FILENAME = "merge-lanes.json"
INTEGRATION = "INTEGRATION"
EXTERNAL_WAIT = "EXTERNAL_WAIT"
PRODUCT_OWNER_DECISION = "PRODUCT_OWNER_DECISION"
GATED_INTEGRATION = "eligible-for-gated-integration"
INSPECTION_ONLY = "inspection-only"


def lane_id(merge_request: dict) -> str:
    return f"{merge_request.get('project')}!{int(merge_request.get('iid') or 0)}"


def _approved(merge_request: dict) -> bool:
    return bool(
        merge_request.get("approved")
        or merge_request.get("approval_state") == "approved"
    )


def _mergeable(merge_request: dict) -> bool:
    return str(merge_request.get("merge_status") or "").lower() in {
        "mergeable",
        "can_be_merged",
    }


def _pipeline_successful(merge_request: dict) -> bool:
    return str(merge_request.get("pipeline_status") or "").lower() == "success"


def classify(merge_request: dict) -> tuple[str, str]:
    """Return one externally truthful lane for a currently-open merge request."""
    if merge_request.get("state") != "opened":
        return EXTERNAL_WAIT, "merge request is no longer open"
    if merge_request.get("target_branch") != "main":
        return EXTERNAL_WAIT, "merge request does not target protected main"
    if not merge_request.get("approval_facts_available"):
        return EXTERNAL_WAIT, "GitLab approval facts are unavailable"
    if not _approved(merge_request):
        return EXTERNAL_WAIT, "required approval is not observed"
    if not _mergeable(merge_request):
        return EXTERNAL_WAIT, "GitLab does not report the merge request as mergeable"
    if not merge_request.get("pipeline_facts_available"):
        return EXTERNAL_WAIT, "GitLab head-pipeline facts are unavailable"
    if not _pipeline_successful(merge_request):
        return EXTERNAL_WAIT, "head pipeline is not successful"
    if merge_request.get("draft"):
        return EXTERNAL_WAIT, "merge request remains draft"
    return INTEGRATION, "approved, mergeable, successful-pipeline merge request"


def _active_assignment_for(merge_request: dict, inventory: dict) -> dict | None:
    """Return the one active Supervisor assignment bound to this exact MR.

    Branch-prefix ownership alone is deliberately insufficient: the assignment,
    handoff, exact branch head, and a non-read-only lease must all agree before a
    lane is allowed to direct the existing gated integration path.
    """
    project = merge_request.get("project")
    source_branch = merge_request.get("source_branch")
    sha = merge_request.get("sha")
    iid = int(merge_request.get("iid") or 0)
    if not project or not source_branch or not sha or not iid:
        return None
    matches = []
    for assignment in inventory.get("supervisor_assignments") or []:
        if not isinstance(assignment, dict) or assignment.get("project") != project:
            continue
        try:
            if not is_integrable(assignment):
                continue
        except ValueError:
            continue
        worker = assignment.get("worker") or {}
        handoff = worker.get("handoff") or {}
        if (
            worker.get("branch") == source_branch
            and worker.get("commit") == sha
            and int(handoff.get("mr_iid") or 0) == iid
        ):
            matches.append(assignment)
    if len(matches) != 1:
        return None
    assignment = matches[0]
    lease_id = assignment.get("lease_id")
    matching_lease = next(
        (
            lease
            for lease in inventory.get("active_leases") or []
            if isinstance(lease, dict)
            and lease.get("assignment_id") == assignment.get("assignment_id")
            and lease.get("lease_id") == lease_id
            and not lease.get("read_only")
        ),
        None,
    )
    return assignment if matching_lease is not None else None


def _custody_for(merge_request: dict, inventory: dict) -> dict:
    """Derive fail-closed lane custody from current local and durable facts."""
    project = str(merge_request.get("project") or "")
    source_branch = str(merge_request.get("source_branch") or "")
    local = ((inventory.get("repositories") or {}).get(project) or {}).get(
        "local_facts"
    ) or {}
    branch = next(
        (
            value
            for value in local.get("remote_branches") or []
            if isinstance(value, dict) and value.get("name") == source_branch
        ),
        None,
    )
    assignment = _active_assignment_for(merge_request, inventory)
    worktree = (branch or {}).get("active_worktree")
    assignment_worktree = ((assignment or {}).get("worker") or {}).get("worktree")
    owned = bool((branch or {}).get("owned_by_supervisor"))
    exact_worktree = bool(worktree and assignment_worktree and worktree == assignment_worktree)
    eligible = bool(owned and exact_worktree and assignment)
    if eligible:
        reason = "supervisor-owned branch, worktree, assignment, and lease are bound"
    elif not branch:
        reason = "source branch has no current local custody record"
    elif not owned:
        reason = "source branch is not supervisor-owned"
    elif not worktree:
        reason = "supervisor-owned source branch has no active worktree"
    elif assignment is None:
        reason = "no uniquely bound active assignment with a writable lease"
    else:
        reason = "active worktree does not match the bound assignment"
    return {
        "supervisor_owned": owned,
        "active_worktree": worktree,
        "assignment_id": (assignment or {}).get("assignment_id"),
        "disposition": GATED_INTEGRATION if eligible else INSPECTION_ONLY,
        "reason": reason,
    }


def _load(root: Path) -> dict:
    path = root / FILENAME
    if not path.exists():
        return {"items": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": []}
    return value if isinstance(value, dict) and isinstance(value.get("items"), list) else {"items": []}


def _write(root: Path, value: dict) -> dict:
    path = root / FILENAME
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
    return value


def reconcile(root: Path, inventory: dict) -> dict:
    """Adopt every open MR into exactly one durable downstream lane.

    An INTEGRATION lane is only eligible to direct a mutation when its exact MR
    is bound to a Supervisor-owned branch, active worktree, active assignment,
    and writable lease.  External lanes stay observable and inspectable, but
    never acquire authority from adoption alone.
    """
    current = _load(root)
    previous = {
        str(item.get("lane_id")): item
        for item in current.get("items") or []
        if isinstance(item, dict) and item.get("lane_id")
    }
    now = int(time.time())
    items = []
    for merge_request in sorted(
        inventory.get("open_merge_requests") or [], key=lambda value: lane_id(value)
    ):
        identifier = lane_id(merge_request)
        if not merge_request.get("project") or not int(merge_request.get("iid") or 0):
            continue
        lane, reason = classify(merge_request)
        custody = _custody_for(merge_request, inventory)
        old = previous.get(identifier) or {}
        items.append(
            {
                "lane_id": identifier,
                "repository": merge_request["project"],
                "mr_iid": int(merge_request["iid"]),
                "mr_url": merge_request.get("web_url"),
                "mr_sha": merge_request.get("sha"),
                "source_branch": merge_request.get("source_branch"),
                "lane": lane,
                "owner": "supervisor-integration",
                "worker_class": "Integrator",
                "reason": reason,
                "adopted_at_epoch": int(old.get("adopted_at_epoch") or now),
                "updated_at_epoch": now,
                "last_consumed_at_epoch": old.get("last_consumed_at_epoch"),
                "last_inspection": old.get("last_inspection"),
                "custody": custody,
                "mutation_disposition": (
                    custody["disposition"] if lane == INTEGRATION else "not-actionable"
                ),
            }
        )
    return _write(root, {"items": items})


def consume_next(root: Path, inventory: dict, integrator: Integrator) -> dict | None:
    """Inspect the highest-priority lane and bind it to gated progression when safe."""
    lanes = reconcile(root, inventory)
    candidates = [
        value for value in lanes["items"] if value.get("lane") == INTEGRATION
    ]
    item = min(
        candidates,
        key=lambda value: (
            0
            if value.get("mutation_disposition") == GATED_INTEGRATION
            else 1,
            int(value.get("last_consumed_at_epoch") or 0),
            str(value.get("lane_id") or ""),
        ),
        default=None,
    )
    if item is None:
        return None
    try:
        responsibility = responsibility_for_repository(
            item["repository"], context=f"external-merge-lane:{item['lane_id']}"
        )
        inspection = integrator.inspect_mr(
            item["repository"], item["mr_iid"], responsibility=responsibility
        )
    except Exception as exc:  # noqa: BLE001 - preserve the externally observed blocker.
        item["lane"] = EXTERNAL_WAIT
        item["reason"] = f"integration inspection unavailable: {type(exc).__name__}"
        item["last_inspection"] = None
    else:
        item["last_inspection"] = {
            "merge_ready": bool(inspection.get("merge_ready")),
            "pipeline_status": (inspection.get("pipeline") or {}).get("status"),
            "review_pending": bool(inspection.get("review_pending")),
        }
        if not inspection.get("merge_ready"):
            item["lane"] = EXTERNAL_WAIT
            item["reason"] = "integration inspection found an external merge blocker"
        elif item.get("mutation_disposition") == GATED_INTEGRATION:
            item["lane"] = INTEGRATION
            item["reason"] = (
                "integration inspection passed; lane is bound to governed assignment "
                f"{item['custody']['assignment_id']}"
            )
        else:
            item["lane"] = INTEGRATION
            item["reason"] = (
                "integration inspection passed; merge remains non-destructive because "
                f"{(item.get('custody') or {}).get('reason') or 'custody is unavailable'}"
            )
    item["last_consumed_at_epoch"] = int(time.time())
    item["updated_at_epoch"] = int(time.time())
    _write(root, lanes)
    return item
