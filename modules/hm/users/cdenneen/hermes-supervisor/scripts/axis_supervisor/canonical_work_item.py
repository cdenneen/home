"""One fail-closed reconstruction of a governed GitLab work item."""

import hashlib
import json
import re


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.IGNORECASE)
_RECORD = re.compile(
    r"(?mi)^\s*(?:#+\s*)?(?:immutable\s+)?planningrecord\s+v(\d+)\b"
)
_APPROVAL = re.compile(
    r"\*\*approve\*\*|product owner approval|approved .*planningrecord",
    re.IGNORECASE,
)


def _trusted(note: dict, principals: set[int | str]) -> bool:
    if note.get("system"):
        return False
    author = note.get("author") or {}
    user_id = author.get("id")
    return user_id in principals if isinstance(user_id, int) else str(author.get("username") or "") in principals


def _immutable(note: dict) -> bool:
    created, updated = note.get("created_at"), note.get("updated_at")
    return not created or not updated or created == updated


def _field(body: str, name: str) -> str | None:
    values = re.findall(rf"(?mi)^{re.escape(name)}:\s*(.+)$", body)
    return values[0].strip() if len(values) == 1 else None


def _list(body: str, name: str) -> list[str]:
    match = re.search(rf"(?mi)^{re.escape(name)}:\s*\n((?:\s*-\s*.+\n?)+)", body)
    return [line.removeprefix("-").strip() for line in match.group(1).splitlines()] if match else []


def _payload_identity(record: dict) -> str:
    payload = {
        field: record[field]
        for field in (
            "revision",
            "digest",
            "assignment_type",
            "repository",
            "slices",
            "required_tests",
        )
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def _record(note: dict, principals: set[int | str], issue_url: str) -> dict | None:
    body = str(note.get("body") or "")
    marker = _RECORD.search(body)
    digest = _DIGEST.search(_field(body, "Digest") or "")
    note_id = note.get("id")
    if (
        not marker
        or not digest
        or not isinstance(note_id, int)
        or note_id < 1
        or not _trusted(note, principals)
        or not _immutable(note)
    ):
        return None
    slices = []
    for value in _list(body, "Authorized slices"):
        slice_id, separator, paths = value.partition(":")
        paths = [path.strip() for path in paths.split(",") if path.strip()]
        if separator and slice_id.strip() and paths:
            slices.append({"slice_id": slice_id.strip(), "allowed_paths": paths})
    assignment_type = _field(body, "Assignment type")
    repository = _field(body, "Repository")
    required_tests = _list(body, "Required tests")
    if not assignment_type or not repository or not slices or not required_tests:
        return None
    record = {
        "revision": int(marker.group(1) or 1),
        "digest": digest.group(0).lower(),
        "note_id": note_id,
        "assignment_type": assignment_type.lower(),
        "repository": repository,
        "slices": slices,
        "required_tests": required_tests,
        "source": {
            "note_id": note_id,
            "note_url": f"{issue_url}#note_{note_id}"
            if issue_url
            else None,
        },
    }
    record["payload_identity"] = _payload_identity(record)
    return record


def reconstruct_work_item(
    description: str,
    notes: list[dict],
    trusted_principals: set[int | str],
    *,
    notes_state: str,
    issue_url: str = "",
) -> dict:
    """Return the sole authority projection; incomplete collection never grants authority."""
    complete = notes_state == "NOTES_OK"
    records = (
        [_record(note, trusted_principals, issue_url) for note in notes] if complete else []
    )
    records = [record for record in records if record is not None]
    highest = max((record["revision"] for record in records), default=None)
    current = [record for record in records if record["revision"] == highest] if highest else []
    conflict = len({record["payload_identity"] for record in current}) > 1
    selected = (
        min(current, key=lambda record: int(record["source"]["note_id"]))
        if current and not conflict
        else None
    )
    approvals = []
    if selected:
        for note in notes:
            body = str(note.get("body") or "")
            stated_revision = re.search(r"(?i)planningrecord\s+v(\d+)\b", body)
            if (
                _trusted(note, trusted_principals)
                and _immutable(note)
                and _APPROVAL.search(body)
                and selected["digest"] in body.lower()
                and stated_revision is not None
                and int(stated_revision.group(1)) == selected["revision"]
            ):
                approvals.append(note)
    approval = approvals[0] if len(approvals) == 1 else None
    approval_note = (
        f"{issue_url}#note_{approval.get('id')}"
        if approval and issue_url and approval.get("id")
        else None
    )
    current_authority = bool(
        complete
        and selected
        and selected["source"].get("note_url")
        and approval
        and approval_note
    )
    facts = {
        "collection_complete_for_authority": complete,
        "record_digest": selected.get("digest") if selected else None,
        "record_revision": selected.get("revision") if selected else None,
        "approval_note": approval_note,
        "record_source": selected.get("source") if selected else None,
        "approval_source": {
            "note_id": approval.get("id"),
            "note_url": approval_note,
        }
        if approval
        else None,
        "approval_matches_record": current_authority,
        "approval_mismatch": bool(complete and selected and not current_authority),
        "approved_assignment_type": selected.get("assignment_type") if selected else None,
        "approved_allowed_paths": selected["slices"][0]["allowed_paths"]
        if selected and len(selected["slices"]) == 1
        else [],
        "approved_required_tests": selected.get("required_tests") if selected else [],
    }
    return {
        "schema": "axis.supervisor.canonical-work-item.v1",
        # Description records are retained as non-authoritative history only. They
        # can never supply fields to, or be merged with, the current note record.
        "description_history": {
            "digest_present": bool(_DIGEST.search(description)),
            "revision": int(match.group(1)) if (match := _RECORD.search(description)) else None,
            "superseded": bool(selected and _RECORD.search(description)),
        },
        "collection_complete_for_authority": complete,
        "records": sorted(records, key=lambda value: (value["revision"], str(value.get("note_id")))),
        "current_planning_record": selected,
        "record_conflict": conflict,
        "matching_approval_note_id": approval.get("id") if approval else None,
        "slice_inventory": selected.get("slices") if selected else [],
        "authority_facts": facts,
    }


def projection_for(item: dict) -> dict:
    """Use persisted reconstruction only; legacy records deliberately default deny."""
    value = item.get("canonical_work_item")
    if isinstance(value, dict):
        return value
    return {
        "collection_complete_for_authority": False,
        "current_planning_record": None,
        "authority_facts": {},
    }


def authority_lineage_for(item: dict, candidate: dict | None = None) -> dict | None:
    """Return the immutable executable-authority chain, or deny by omission."""
    projection = projection_for(item)
    facts = projection.get("authority_facts") or {}
    record = projection.get("current_planning_record") or {}
    if not (
        projection.get("collection_complete_for_authority")
        and facts.get("approval_matches_record")
        and record.get("digest")
        and record.get("source")
        and facts.get("approval_source")
        and facts.get("approval_note")
    ):
        return None
    lineage = {
        "record_digest": record["digest"],
        "record_revision": record["revision"],
        "record_source": record["source"],
        "approval_source": facts["approval_source"],
        "approval_note": facts["approval_note"],
    }
    if candidate is not None:
        slice_id = candidate.get("slice_id")
        slices = [value for value in record.get("slices") or [] if value.get("slice_id") == slice_id]
        if len(slices) != 1:
            return None
        if sorted(candidate.get("allowed_paths") or []) != sorted(slices[0].get("allowed_paths") or []):
            return None
        if list(candidate.get("required_tests") or []) != list(record.get("required_tests") or []):
            return None
        lineage["slice_id"] = slice_id
    return lineage


def lineage_matches(item: dict, lineage: object, candidate: dict | None = None) -> bool:
    return isinstance(lineage, dict) and lineage == authority_lineage_for(item, candidate)
