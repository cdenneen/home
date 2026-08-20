"""Durably stop repeated semantic work until its authority evidence changes."""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


FILENAME = "semantic-escalations.json"


def authority_invariant(value: dict) -> dict | None:
    """Return the stable authority facts which permit one semantic attempt.

    This guard is deliberately narrower than ordinary semantic work.  It applies
    only where the source itself requires both approval and escalation, so a
    failed model output cannot consume a fresh retry budget on the next cycle.
    """
    source = value.get("source_item") or {}
    facts = source.get("authority_facts") or {}
    if (
        value.get("assignment_type") != "read-only-analysis"
        or not facts.get("approval_required")
        or not facts.get("decision_escalate")
    ):
        return None
    item_ref = str(value.get("target_ref") or value.get("work_item") or "")
    repository_head = str(source.get("repository_head") or "")
    record_revision = facts.get("record_revision")
    record_digest = str(facts.get("record_digest") or "")
    source_fingerprint = str(value.get("source_fingerprint") or "")
    if not item_ref or not record_revision or not (repository_head or source_fingerprint):
        return None
    return {
        "item_ref": item_ref,
        "repository_head": repository_head or None,
        "record_revision": int(record_revision),
        "record_digest": record_digest or None,
        "source_fingerprint": source_fingerprint or None,
    }


def invariant_key(invariant: dict) -> str:
    encoded = json.dumps(invariant, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _load(root: Path) -> dict:
    path = root / FILENAME
    if not path.exists():
        return {"items": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"items": []}
    return value if isinstance(value, dict) and isinstance(value.get("items"), list) else {"items": []}


def pending(root: Path, value: dict) -> dict | None:
    invariant = authority_invariant(value)
    if invariant is None:
        return None
    key = invariant_key(invariant)
    return next(
        (
            item
            for item in _load(root)["items"]
            if item.get("state") == "pending-human-escalation"
            and item.get("invariant_key") == key
        ),
        None,
    )


def exclude_pending(root: Path, values: list[dict]) -> list[dict]:
    """Keep quarantined semantic entries out of the rebuilt executable frontier."""
    return [value for value in values if pending(root, value) is None]


def quarantine_failed_assignment(root: Path, assignment: dict) -> dict | None:
    invariant = authority_invariant(assignment)
    if invariant is None:
        return None
    key = invariant_key(invariant)
    current = _load(root)
    item = {
        "state": "pending-human-escalation",
        "invariant_key": key,
        "authority_invariant": invariant,
        "assignment_id": assignment.get("assignment_id"),
        "failure": str(assignment.get("error") or "semantic assignment failed")[-2_000:],
        "quarantined_at_epoch": int(time.time()),
    }
    current["items"] = [
        value for value in current["items"] if value.get("invariant_key") != key
    ] + [item]
    path = root / FILENAME
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(current, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return item
