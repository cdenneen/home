"""One fail-closed reconstruction of a governed GitLab work item."""

import re


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.IGNORECASE)
_RECORD = re.compile(r"(?:immutable\s+)?planningrecord\s+v?(\d+)?", re.IGNORECASE)
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


def _record(note: dict, principals: set[int | str]) -> dict | None:
    body = str(note.get("body") or "")
    marker = _RECORD.search(body)
    digest = _DIGEST.search(_field(body, "Digest") or "")
    if not marker or not digest or not _trusted(note, principals) or not _immutable(note):
        return None
    slices = []
    for value in _list(body, "Authorized slices"):
        slice_id, separator, paths = value.partition(":")
        paths = [path.strip() for path in paths.split(",") if path.strip()]
        if separator and slice_id.strip() and paths:
            slices.append({"slice_id": slice_id.strip(), "allowed_paths": paths})
    assignment_type = _field(body, "Assignment type")
    return {
        "revision": int(marker.group(1) or 1),
        "digest": digest.group(0).lower(),
        "note_id": note.get("id"),
        "assignment_type": assignment_type.lower() if assignment_type else None,
        "repository": _field(body, "Repository"),
        "slices": slices,
        "required_tests": _list(body, "Required tests"),
    }


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
    records = [_record(note, trusted_principals) for note in notes] if complete else []
    records = [record for record in records if record is not None]
    highest = max((record["revision"] for record in records), default=None)
    current = [record for record in records if record["revision"] == highest] if highest else []
    conflict = len({record["digest"] for record in current}) > 1
    selected = current[0] if current and not conflict else None
    approvals = []
    if selected:
        for note in notes:
            body = str(note.get("body") or "")
            stated_revision = re.search(r"(?i)planningrecord\s+v(\d+)", body)
            if (
                _trusted(note, trusted_principals)
                and _immutable(note)
                and _APPROVAL.search(body)
                and selected["digest"] in body.lower()
                and (stated_revision is None or int(stated_revision.group(1)) == selected["revision"])
            ):
                approvals.append(note)
    approval = approvals[0] if len(approvals) == 1 else None
    approval_note = f"{issue_url}#note_{approval.get('id')}" if approval and issue_url and approval.get("id") else None
    current_authority = bool(complete and selected and approval)
    facts = {
        "collection_complete_for_authority": complete,
        "record_digest": selected.get("digest") if selected else None,
        "record_revision": selected.get("revision") if selected else None,
        "approval_note": approval_note,
        "approval_matches_record": current_authority,
        "approval_mismatch": bool(complete and selected and not current_authority),
        "approved_assignment_type": selected.get("assignment_type") if selected else None,
        "approved_allowed_paths": selected["slices"][0]["allowed_paths"] if selected and len(selected["slices"]) == 1 else [],
        "approved_required_tests": selected.get("required_tests") if selected else [],
    }
    return {
        "schema": "axis.supervisor.canonical-work-item.v1",
        "description_digest_present": bool(_DIGEST.search(description)),
        "collection_complete_for_authority": complete,
        "records": sorted(records, key=lambda value: (value["revision"], str(value.get("note_id")))),
        "current_record": selected,
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
    return {"collection_complete_for_authority": False, "authority_facts": {}}
