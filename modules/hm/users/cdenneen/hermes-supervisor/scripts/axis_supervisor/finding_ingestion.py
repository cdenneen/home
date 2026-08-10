"""Fail-closed normalization for canonical GitLab supervisor finding notes."""

import hashlib
import json
import re
from typing import cast

PARSER_REVISION = "gitlab-finding-note-v1"
AMENDMENT_PARSER_REVISION = "gitlab-finding-note-v2"
MARKER = "current-main regression finding"
AMENDMENT_MARKER = "finding amendment"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.IGNORECASE)
_AMENDMENT_HEADER = re.compile(
    r"Finding amendment v2\s+— supersedes finding note (?P<note_id>\d+) "
    r"for structured Supervisor ingestion",
    re.IGNORECASE,
)


def _digest(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _field(body: str, name: str) -> str | None:
    match = re.search(rf"(?mi)^{re.escape(name)}:\s*(.+)$", body)
    return match.group(1).strip() if match else None


def _list_field(body: str, name: str) -> list[str]:
    match = re.search(
        rf"(?mi)^{re.escape(name)}:\s*\n(?P<items>(?:\s*-\s*.+\n?)+)", body
    )
    if not match:
        return []
    return [
        line.removeprefix("-").strip()
        for line in match.group("items").splitlines()
        if line.strip().startswith("-")
    ]


def _single_field(body: str, name: str) -> str | None:
    values = re.findall(rf"(?mi)^{re.escape(name)}:\s*(.+)$", body)
    return values[0].strip() if len(values) == 1 else None


def _exact_digest(value: str | None) -> str | None:
    match = re.fullmatch(r"`?(sha256:[0-9a-f]{64})`?", value or "", re.IGNORECASE)
    return match.group(1).lower() if match else None


def _note_version(note: dict, revision: str) -> dict:
    return {
        "note_id": note.get("id"),
        "note_author": str((note.get("author") or {}).get("username") or ""),
        "note_timestamp": note.get("updated_at") or note.get("created_at"),
        "raw_digest": _digest(str(note.get("body") or "")),
        "parser_revision": revision,
    }


def _immutable(note: dict) -> bool:
    created = note.get("created_at")
    updated = note.get("updated_at")
    return not created or not updated or created == updated


def _trusted(note: dict, principals: set[int | str]) -> bool:
    """Production trust uses immutable GitLab numeric IDs; strings support legacy fixtures."""
    author = note.get("author") or {}
    user_id = author.get("id")
    if isinstance(user_id, int):
        return user_id in principals
    return str(author.get("username") or "") in principals


def _planning_scope(
    notes: list[dict], digest: str, slice_id: str, trusted_principals: set[int | str], canonical_work_item: dict | None = None
) -> tuple[dict | None, bool]:
    """Select the exact authorized slice referred to by a canonical finding."""
    if canonical_work_item is not None:
        current = canonical_work_item.get("current_planning_record") or {}
        facts = canonical_work_item.get("authority_facts") or {}
        if (
            not canonical_work_item.get("collection_complete_for_authority")
            or not facts.get("approval_matches_record")
            or current.get("digest") != digest.lower()
        ):
            return None, False
        selected = [value for value in current.get("slices") or [] if value.get("slice_id") == slice_id]
        if len(selected) != 1 or not current.get("repository") or not current.get("required_tests"):
            return None, False
        return (
            {
                "slice_id": slice_id,
                "repository": current["repository"],
                "allowed_paths": selected[0].get("allowed_paths") or [],
                "required_tests": current["required_tests"],
                "planning_record_source": current.get("source"),
            },
            False,
        )
    # Canonical callers must supply the projection; raw-note parsing is retained
    # only for legacy inventory migration and cannot authorize a collected item.
    untrusted_source = False
    for note in notes:
        if note.get("system"):
            continue
        body = str(note.get("body") or "")
        if "Immutable PlanningRecord" not in body or digest.lower() not in body.lower():
            continue
        if not _trusted(note, trusted_principals):
            untrusted_source = True
            continue
        assignment_type = _field(body, "Assignment type")
        repository = _field(body, "Repository")
        slices = _list_field(body, "Authorized slices")
        required_tests = _list_field(body, "Required tests")
        if (
            assignment_type != "code-implementation"
            or not repository
            or not required_tests
        ):
            continue
        selected = [
            value for value in slices if value.partition(":")[0].strip() == slice_id
        ]
        if len(selected) != 1:
            continue
        selected_slice_id, separator, paths = selected[0].partition(":")
        allowed_paths = [path.strip() for path in paths.split(",") if path.strip()]
        if not separator or not selected_slice_id or not allowed_paths:
            continue
        return (
            {
                "slice_id": selected_slice_id.strip(),
                "repository": repository,
                "allowed_paths": allowed_paths,
                "required_tests": required_tests,
            },
            untrusted_source,
        )
    return None, untrusted_source


def _invalid(note: dict, owner_ref: str, reason: str, source_sha: str | None) -> dict:
    note_id = note.get("id")
    return {
        "state": "invalid",
        "identity": _digest({"owner_ref": owner_ref, "note_id": note_id}),
        "invalid_reason": reason,
        "provenance": {
            "project": owner_ref.partition("#")[0],
            "issue_iid": owner_ref.partition("#")[2],
            "note_id": note_id,
            "note_author": str((note.get("author") or {}).get("username") or ""),
            "note_timestamp": note.get("updated_at") or note.get("created_at"),
            "raw_digest": _digest(str(note.get("body") or "")),
            "source_sha": source_sha,
            "parser_revision": PARSER_REVISION,
        },
    }


def _amendment_invalid(
    original: dict | None,
    amendment: dict,
    owner_ref: str,
    reason: str,
    source_sha: str | None,
) -> dict:
    origin = original or amendment
    value = _invalid(origin, owner_ref, reason, source_sha)
    value["provenance"]["parser_revision"] = AMENDMENT_PARSER_REVISION
    value["provenance"]["version_chain"] = [
        *([_note_version(original, PARSER_REVISION)] if original else []),
        _note_version(amendment, AMENDMENT_PARSER_REVISION),
    ]
    if original:
        value["note_id"] = original.get("id")
        value["amendment_note_id"] = amendment.get("id")
    return value


def _original_fields(note: dict) -> dict | None:
    body = str(note.get("body") or "")
    authority_digest = next(
        iter(_DIGEST.findall(_field(body, "Authority") or "")), None
    )
    affected_tests = _list_field(body, "Affected tests")
    if (
        _single_field(body, "Classification") != "PRODUCT_DEFECT"
        or not _single_field(body, "Capability")
        or not _single_field(body, "Affected gates")
        or not authority_digest
        or not _single_field(body, "Replay")
        or not _single_field(body, "Expected")
        or not _single_field(body, "Actual")
        or not affected_tests
    ):
        return None
    return {
        "title": body.splitlines()[0].strip(),
        "capability": _single_field(body, "Capability"),
        "authority_digest": authority_digest.lower(),
    }


def _normalize_amendments(
    notes: list[dict],
    owner_ref: str,
    source_sha: str | None,
    trusted_principals: set[int | str],
    canonical_work_item: dict | None = None,
) -> tuple[list[dict], set[int]]:
    """Join an immutable v2 amendment to exactly one original finding lineage."""
    normalized: list[dict] = []
    amended_note_ids: set[int] = set()
    notes_by_id = {
        note.get("id"): note for note in notes if isinstance(note.get("id"), int)
    }
    amendments = [
        note
        for note in notes
        if (lines := str(note.get("body") or "").splitlines())
        and lines[0].strip().lower().startswith(AMENDMENT_MARKER)
    ]
    by_origin: dict[int, list[dict]] = {}
    for amendment in amendments:
        header = str(amendment.get("body") or "").splitlines()[0].strip()
        match = _AMENDMENT_HEADER.fullmatch(header)
        if match is None:
            referenced = re.search(
                r"supersedes finding note (?P<note_id>\d+)", header, re.IGNORECASE
            )
            original = (
                notes_by_id.get(int(referenced.group("note_id")))
                if referenced is not None
                else None
            )
            if referenced is not None:
                amended_note_ids.add(int(referenced.group("note_id")))
            reason = (
                "unsupported-finding-amendment-version"
                if re.match(r"Finding amendment v\d+", header, re.IGNORECASE)
                else "malformed-finding-amendment"
            )
            normalized.append(
                _amendment_invalid(original, amendment, owner_ref, reason, source_sha)
            )
            continue
        origin_id = int(match.group("note_id"))
        amended_note_ids.add(origin_id)
        by_origin.setdefault(origin_id, []).append(amendment)
    for origin_id, values in by_origin.items():
        original = notes_by_id.get(origin_id)
        amendment = values[0]
        if len(values) != 1:
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "competing-finding-amendments",
                    source_sha,
                )
            )
            continue
        if amendment.get("system") or (original is not None and original.get("system")):
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "system-finding-note",
                    source_sha,
                )
            )
            continue
        if not trusted_principals or not _trusted(amendment, trusted_principals):
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "untrusted-finding-author",
                    source_sha,
                )
            )
            continue
        if not _immutable(amendment) or (
            original is not None and not _immutable(original)
        ):
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "finding-amendment-edited",
                    source_sha,
                )
            )
            continue
        if (
            original is None
            or not _trusted(original, trusted_principals)
        ):
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "missing-or-untrusted-original-finding",
                    source_sha,
                )
            )
            continue
        original_fields = _original_fields(original)
        if original_fields is None:
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "malformed-original-finding",
                    source_sha,
                )
            )
            continue
        body = str(amendment.get("body") or "")
        fields = {
            name: _single_field(body, name)
            for name in (
                "Finding ID",
                "Finding class",
                "Owner work item",
                "Approved slice_id",
                "PlanningRecord revision",
                "PlanningRecord digest",
                "Repository",
                "Affected gate",
                "Expected behavior",
                "Observed behavior",
                "Source evidence",
                "Affected downstream",
                "Replay",
                "Scope",
                "Supersession",
            )
        }
        tests = _list_field(body, "Affected tests")
        digest = _exact_digest(fields["PlanningRecord digest"])
        if (
            not all(fields.values())
            or not tests
            or fields["Finding class"] != "PRODUCT_DEFECT"
            or fields["PlanningRecord revision"] != "2"
        ):
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "malformed-finding-amendment",
                    source_sha,
                )
            )
            continue
        fields = cast(dict[str, str], fields)
        if fields["Owner work item"] != owner_ref:
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "amendment-owner-mismatch",
                    source_sha,
                )
            )
            continue
        repository = fields["Repository"]
        if repository != owner_ref.partition("#")[0]:
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "amendment-repository-mismatch",
                    source_sha,
                )
            )
            continue
        if digest is None or digest != original_fields["authority_digest"]:
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "amendment-digest-mismatch",
                    source_sha,
                )
            )
            continue
        if not re.search(
            rf"\bnote\s+{origin_id}\b", fields["Source evidence"], re.IGNORECASE
        ) or not fields["Supersession"].startswith(
            "this metadata amendment preserves original finding provenance"
        ):
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "invalid-amendment-supersession",
                    source_sha,
                )
            )
            continue
        scope, untrusted_scope_source = _planning_scope(
            notes, digest, fields["Approved slice_id"], trusted_principals, canonical_work_item
        )
        if scope is None:
            reason = (
                "untrusted-planning-record"
                if untrusted_scope_source
                else "amendment-unknown-approved-slice"
            )
            normalized.append(
                _amendment_invalid(original, amendment, owner_ref, reason, source_sha)
            )
            continue
        if scope["repository"] != repository:
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "amendment-slice-repository-mismatch",
                    source_sha,
                )
            )
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", source_sha or "", re.IGNORECASE):
            normalized.append(
                _amendment_invalid(
                    original,
                    amendment,
                    owner_ref,
                    "missing-or-invalid-canonical-field",
                    source_sha,
                )
            )
            continue
        identity = _digest({"owner_ref": owner_ref, "note_id": origin_id})
        original_digest = _digest(str(original.get("body") or ""))
        amendment_digest = _digest(body)
        downstream = [
            f"{repository}!{value.strip().removeprefix('!')}"
            for value in fields["Affected downstream"].split(",")
            if value.strip()
        ]
        normalized.append(
            {
                "finding_id": fields["Finding ID"],
                "finding_key": f"{owner_ref}:{fields['Finding ID'].lower()}",
                "state": "confirmed",
                "owner_ref": owner_ref,
                "note_id": origin_id,
                "amendment_note_id": amendment.get("id"),
                "note_url": f"{owner_ref}#note_{origin_id}",
                "identity": identity,
                "revision_identity": _digest(
                    {
                        "identity": identity,
                        "raw_digests": [original_digest, amendment_digest],
                        "source_sha": source_sha,
                    }
                ),
                "classification": fields["Finding class"],
                "capability": original_fields["capability"],
                "affected_gates": [
                    value.strip()
                    for value in fields["Affected gate"].split(",")
                    if value.strip()
                ],
                "affected_tests": tests,
                "expected": fields["Expected behavior"],
                "actual": fields["Observed behavior"],
                "replay": fields["Replay"],
                "authority_digest": digest,
                "planning_record_source": scope.get("planning_record_source"),
                "repair_candidate": {
                    "slice_id": scope["slice_id"],
                    "title": original_fields["title"],
                    "category": "implementation",
                    "result": "Executable",
                    "project": scope["repository"],
                    "responsibility": "axis-runtime/product",
                    "allowed_paths": scope["allowed_paths"],
                    "required_tests": scope["required_tests"],
                    "rationale": fields["Observed behavior"],
                },
                "shared_dependents": downstream,
                "provenance": {
                    "project": repository,
                    "issue_iid": int(owner_ref.partition("#")[2]),
                    "note_id": origin_id,
                    "note_author": str(
                        (original.get("author") or {}).get("username") or ""
                    ),
                    "note_timestamp": original.get("updated_at")
                    or original.get("created_at"),
                    "raw_digest": original_digest,
                    "source_sha": source_sha,
                    "parser_revision": AMENDMENT_PARSER_REVISION,
                    "version_chain": [
                        _note_version(original, PARSER_REVISION),
                        _note_version(amendment, AMENDMENT_PARSER_REVISION),
                    ],
                },
            }
        )
    return normalized, amended_note_ids


def normalize_gitlab_findings(
    notes: list[dict],
    owner_ref: str,
    source_sha: str | None = None,
    trusted_principals: set[int | str] | None = None,
    canonical_work_item: dict | None = None,
) -> list[dict]:
    """Normalize only canonical current-main finding notes; invalid notes never dispatch."""
    trusted_principals = set(trusted_principals or [])
    normalized, amended_note_ids = _normalize_amendments(
        notes, owner_ref, source_sha, trusted_principals, canonical_work_item
    )
    for note in notes:
        body = str(note.get("body") or "")
        first_line = body.splitlines()[0].strip() if body.splitlines() else ""
        if not first_line.lower().startswith(MARKER):
            continue
        note_id = note.get("id")
        if note_id in amended_note_ids:
            continue
        if not isinstance(note_id, int):
            normalized.append(_invalid(note, owner_ref, "missing-note-id", source_sha))
            continue
        if note.get("system"):
            normalized.append(_invalid(note, owner_ref, "system-finding-note", source_sha))
            continue
        if not trusted_principals or not _trusted(note, trusted_principals):
            normalized.append(
                _invalid(note, owner_ref, "untrusted-finding-author", source_sha)
            )
            continue
        classification = _field(body, "Classification")
        capability = _field(body, "Capability")
        affected_gates = _field(body, "Affected gates")
        authority = _field(body, "Authority")
        replay = _field(body, "Replay")
        expected = _field(body, "Expected")
        actual = _field(body, "Actual")
        approved_slice_id = _field(body, "Approved slice_id")
        authority_digest = next(iter(_DIGEST.findall(authority or "")), None)
        affected_tests = _list_field(body, "Affected tests")
        shared_dependents = _list_field(body, "Shared dependents")
        if not shared_dependents:
            shared_dependents = [
                value.strip()
                for value in str(_field(body, "Shared dependents") or "").split(",")
                if value.strip()
            ]
        if (
            classification != "PRODUCT_DEFECT"
            or not capability
            or not affected_gates
            or not authority_digest
            or not replay
            or not expected
            or not actual
            or not affected_tests
            or not approved_slice_id
            or not re.fullmatch(r"[0-9a-f]{40}", source_sha or "", re.IGNORECASE)
        ):
            normalized.append(
                _invalid(
                    note, owner_ref, "missing-or-invalid-canonical-field", source_sha
                )
            )
            continue
        scope, untrusted_scope_source = _planning_scope(
            notes, authority_digest.lower(), approved_slice_id, trusted_principals, canonical_work_item
        )
        if scope is None:
            normalized.append(
                _invalid(
                    note,
                    owner_ref,
                    "untrusted-planning-record"
                    if untrusted_scope_source
                    else "unresolved-authorized-scope",
                    source_sha,
                )
            )
            continue
        identity = _digest({"owner_ref": owner_ref, "note_id": note_id})
        raw_digest = _digest(body)
        finding_id = "gitlab-finding-" + identity.removeprefix("sha256:")[:24]
        gate_names = [
            value.strip() for value in affected_gates.split(",") if value.strip()
        ]
        candidate = {
            "slice_id": scope["slice_id"],
            "title": first_line,
            "category": "implementation",
            "result": "Executable",
            "project": scope["repository"],
            "responsibility": "axis-runtime/product",
            "allowed_paths": scope["allowed_paths"],
            "required_tests": scope["required_tests"],
            "rationale": actual,
        }
        normalized.append(
            {
                "finding_id": finding_id,
                "finding_key": f"{owner_ref}:{first_line.lower()}",
                "state": "confirmed",
                "owner_ref": owner_ref,
                "note_id": note_id,
                "note_url": f"{owner_ref}#note_{note_id}",
                "identity": identity,
                "revision_identity": _digest(
                    {
                        "identity": identity,
                        "raw_digest": raw_digest,
                        "source_sha": source_sha,
                    }
                ),
                "classification": classification,
                "capability": capability,
                "affected_gates": gate_names,
                "affected_tests": affected_tests,
                "expected": expected,
                "actual": actual,
                "replay": replay,
                "authority_digest": authority_digest.lower(),
                "planning_record_source": scope.get("planning_record_source"),
                "repair_candidate": candidate,
                "shared_dependents": sorted(set(shared_dependents)),
                "provenance": {
                    "project": owner_ref.partition("#")[0],
                    "issue_iid": int(owner_ref.partition("#")[2]),
                    "note_id": note_id,
                    "note_author": str(
                        (note.get("author") or {}).get("username") or ""
                    ),
                    "note_timestamp": note.get("updated_at") or note.get("created_at"),
                    "raw_digest": raw_digest,
                    "source_sha": source_sha,
                    "parser_revision": PARSER_REVISION,
                },
            }
        )
    latest: dict[str, dict] = {}
    for finding in normalized:
        if finding.get("state") != "confirmed":
            continue
        key = str(finding.get("finding_key") or "")
        prior = latest.get(key)
        if prior is None or int(finding["note_id"]) > int(prior["note_id"]):
            latest[key] = finding
    for finding in normalized:
        if finding.get("state") != "confirmed":
            continue
        current = latest[str(finding.get("finding_key") or "")]
        if finding is not current:
            finding["state"] = "superseded"
            finding["superseded_by"] = current["identity"]
    return sorted(
        normalized,
        key=lambda value: (str(value.get("identity")), str(value.get("note_id"))),
    )
