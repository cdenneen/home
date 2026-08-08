"""Fail-closed normalization for canonical GitLab supervisor finding notes."""

import hashlib
import json
import re
PARSER_REVISION = "gitlab-finding-note-v1"
MARKER = "current-main regression finding"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.I)


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
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


def _planning_scope(
    notes: list[dict], digest: str, title: str, trusted_principals: set[str]
) -> tuple[dict | None, bool]:
    """Select the exact authorized slice referred to by a canonical finding."""
    untrusted_source = False
    for note in notes:
        body = str(note.get("body") or "")
        if "Immutable PlanningRecord" not in body or digest.lower() not in body.lower():
            continue
        author = str((note.get("author") or {}).get("username") or "")
        if author not in trusted_principals:
            untrusted_source = True
            continue
        assignment_type = _field(body, "Assignment type")
        repository = _field(body, "Repository")
        slices = _list_field(body, "Authorized slices")
        required_tests = _list_field(body, "Required tests")
        if assignment_type != "code-implementation" or not repository or not required_tests:
            continue
        title_words = {word for word in re.findall(r"[a-z0-9]+", title.lower()) if len(word) > 2}
        selected = next(
            (
                value
                for value in slices
                if title_words.intersection(re.findall(r"[a-z0-9]+", value.lower()))
            ),
            None,
        )
        if selected is None:
            continue
        slice_id, separator, paths = selected.partition(":")
        allowed_paths = [path.strip() for path in paths.split(",") if path.strip()]
        if not separator or not slice_id or not allowed_paths:
            continue
        return (
            {
                "slice_id": slice_id.strip(),
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


def normalize_gitlab_findings(
    notes: list[dict],
    owner_ref: str,
    source_sha: str | None = None,
    trusted_principals: set[str] | None = None,
) -> list[dict]:
    """Normalize only canonical current-main finding notes; invalid notes never dispatch."""
    normalized: list[dict] = []
    trusted_principals = set(trusted_principals or [])
    for note in notes:
        body = str(note.get("body") or "")
        first_line = body.splitlines()[0].strip() if body.splitlines() else ""
        if not first_line.lower().startswith(MARKER):
            continue
        note_id = note.get("id")
        if not isinstance(note_id, int):
            normalized.append(_invalid(note, owner_ref, "missing-note-id", source_sha))
            continue
        author = str((note.get("author") or {}).get("username") or "")
        if not trusted_principals or author not in trusted_principals:
            normalized.append(_invalid(note, owner_ref, "untrusted-finding-author", source_sha))
            continue
        classification = _field(body, "Classification")
        capability = _field(body, "Capability")
        affected_gates = _field(body, "Affected gates")
        authority = _field(body, "Authority")
        replay = _field(body, "Replay")
        expected = _field(body, "Expected")
        actual = _field(body, "Actual")
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
            or not re.fullmatch(r"[0-9a-f]{40}", source_sha or "", re.I)
        ):
            normalized.append(_invalid(note, owner_ref, "missing-or-invalid-canonical-field", source_sha))
            continue
        scope, untrusted_scope_source = _planning_scope(
            notes, authority_digest.lower(), first_line, trusted_principals
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
        gate_names = [value.strip() for value in affected_gates.split(",") if value.strip()]
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
                    {"identity": identity, "raw_digest": raw_digest, "source_sha": source_sha}
                ),
                "classification": classification,
                "capability": capability,
                "affected_gates": gate_names,
                "affected_tests": affected_tests,
                "expected": expected,
                "actual": actual,
                "replay": replay,
                "authority_digest": authority_digest.lower(),
                "repair_candidate": candidate,
                "shared_dependents": sorted(set(shared_dependents)),
                "provenance": {
                    "project": owner_ref.partition("#")[0],
                    "issue_iid": int(owner_ref.partition("#")[2]),
                    "note_id": note_id,
                    "note_author": str((note.get("author") or {}).get("username") or ""),
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
    return sorted(normalized, key=lambda value: (str(value.get("identity")), str(value.get("note_id"))))
