import hashlib
import json


def no_op_fingerprint(entry: dict, semantic_record: dict | None = None) -> str:
    source = entry.get("source_item") or {}
    candidate = entry.get("candidate") or {}
    semantic = semantic_record or entry.get("semantic_record") or {}
    payload = {
        "schema": "axis.supervisor.no-op-fingerprint.v1",
        "target_ref": entry.get("target_ref") or entry.get("ref"),
        "project": entry.get("project"),
        "slice_id": candidate.get("slice_id"),
        "required_tests": candidate.get("required_tests") or entry.get("required_tests") or [],
        "repository_head": source.get("repository_head"),
        "source_state": source.get("source_state") or source.get("state"),
        "merge_request_facts": source.get("merge_request_facts") or source.get("merge_requests") or [],
        "acceptance_facts": source.get("acceptance_facts"),
        "source_evidence": source.get("source_evidence"),
        "authority_digest": (source.get("authority_facts") or source.get("authority") or {}).get("record_digest"),
        "semantic_evidence_fingerprint": semantic.get("evidence_fingerprint")
        or entry.get("semantic_evidence_fingerprint"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def completed_no_op_fingerprints(assignments: list[dict]) -> set[str]:
    return {
        str(value["no_op_fingerprint"])
        for value in assignments
        if value.get("assignment_type") == "no-op-verification"
        and value.get("result_state") == "no-op-verification-completed"
        and value.get("no_op_fingerprint")
    }


def is_suppressed_no_op(entry: dict, assignments: list[dict]) -> bool:
    if entry.get("assignment_type") != "no-op-verification":
        return False
    fingerprint = str(entry.get("no_op_fingerprint") or no_op_fingerprint(entry))
    return fingerprint in completed_no_op_fingerprints(assignments)
