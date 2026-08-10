"""Durable ownership and read-only consumption for externally-created merge requests."""

import json
import os
import tempfile
import time
from pathlib import Path

from .integrator import Integrator
from .repository_ownership import responsibility_for_repository

FILENAME = "merge-lanes.json"
INTEGRATION = "INTEGRATION"
EXTERNAL_WAIT = "EXTERNAL_WAIT"
PRODUCT_OWNER_DECISION = "PRODUCT_OWNER_DECISION"


def lane_id(merge_request: dict) -> str:
    return f"{merge_request.get('project')}!{int(merge_request.get('iid') or 0)}"


def _approved(merge_request: dict) -> bool:
    return bool(
        merge_request.get("approved")
        or merge_request.get("approved_by")
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
    if not _approved(merge_request):
        return EXTERNAL_WAIT, "required approval is not observed"
    if not _mergeable(merge_request):
        return EXTERNAL_WAIT, "GitLab does not report the merge request as mergeable"
    if not _pipeline_successful(merge_request):
        return EXTERNAL_WAIT, "head pipeline is not successful"
    if merge_request.get("draft"):
        return EXTERNAL_WAIT, "merge request remains draft"
    return INTEGRATION, "approved, mergeable, successful-pipeline merge request"


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

    This intentionally does not infer authority to merge a branch created outside
    Supervisor custody.  The existing Integrator consumes INTEGRATION lanes by
    re-inspecting their GitLab evidence; a later explicit merge authorization may
    perform the mutation through the normal gate.
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
        old = previous.get(identifier) or {}
        items.append(
            {
                "lane_id": identifier,
                "repository": merge_request["project"],
                "mr_iid": int(merge_request["iid"]),
                "mr_url": merge_request.get("web_url"),
                "mr_sha": merge_request.get("sha"),
                "lane": lane,
                "owner": "supervisor-integration",
                "worker_class": "Integrator",
                "reason": reason,
                "adopted_at_epoch": int(old.get("adopted_at_epoch") or now),
                "updated_at_epoch": now,
                "last_consumed_at_epoch": old.get("last_consumed_at_epoch"),
                "last_inspection": old.get("last_inspection"),
                "mutation_disposition": (
                    "await-explicit-lane-bound-authorization"
                    if lane == INTEGRATION
                    else "not-actionable"
                ),
            }
        )
    return _write(root, {"items": items})


def consume_next(root: Path, inventory: dict, integrator: Integrator) -> dict | None:
    """Make an adopted lane consumable now, without mutating an external branch."""
    lanes = reconcile(root, inventory)
    candidates = [
        value for value in lanes["items"] if value.get("lane") == INTEGRATION
    ]
    item = min(
        candidates,
        key=lambda value: (
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
        else:
            item["lane"] = INTEGRATION
            item["reason"] = (
                "integration inspection passed; merge awaits explicit lane-bound "
                "authorization"
            )
    item["last_consumed_at_epoch"] = int(time.time())
    item["updated_at_epoch"] = int(time.time())
    _write(root, lanes)
    return item
