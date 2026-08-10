import hashlib
import json

from .capability_graduation import paths_overlap
from .canonical_work_item import projection_for

POST_MERGE_STATES = {
    "integrated-post-main-verified",
    "repository-converged",
    "runtime-converged",
    "canonical-complete",
}


def targeted_post_merge_fingerprint(entry: dict, assignments: list[dict]) -> str | None:
    candidate = entry.get("candidate") or {}
    allowed_paths = candidate.get("allowed_paths") or entry.get("allowed_paths") or []
    if not allowed_paths:
        return None
    relevant = []
    for assignment in assignments:
        if assignment.get("project") != entry.get("project"):
            continue
        if assignment.get("result_state") not in POST_MERGE_STATES:
            continue
        worker = assignment.get("worker") or {}
        changed_paths = (
            worker.get("changed_paths") or assignment.get("allowed_paths") or []
        )
        if not any(
            paths_overlap(changed, allowed)
            for changed in changed_paths
            for allowed in allowed_paths
        ):
            continue
        relevant.append(
            {
                "assignment_id": assignment.get("assignment_id"),
                "commit": worker.get("commit"),
                "changed_paths": sorted(changed_paths),
                "completion_receipt": assignment.get("completion_receipt"),
            }
        )
    payload = {
        "schema": "axis.supervisor.targeted-post-merge-fingerprint.v1",
        "project": entry.get("project"),
        "allowed_paths": sorted(allowed_paths),
        "relevant_post_merges": sorted(
            relevant, key=lambda value: str(value.get("assignment_id") or "")
        ),
    }
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def no_op_fingerprint(entry: dict, semantic_record: dict | None = None) -> str:
    source = entry.get("source_item") or {}
    candidate = entry.get("candidate") or {}
    semantic = semantic_record or entry.get("semantic_record") or {}
    payload = {
        "schema": "axis.supervisor.no-op-fingerprint.v1",
        "target_ref": entry.get("target_ref") or entry.get("ref"),
        "project": entry.get("project"),
        "slice_id": candidate.get("slice_id"),
        "required_tests": candidate.get("required_tests")
        or entry.get("required_tests")
        or [],
        "repository_head": None
        if entry.get("targeted_post_merge_fingerprint")
        else source.get("repository_head"),
        "targeted_post_merge_fingerprint": entry.get("targeted_post_merge_fingerprint"),
        "source_state": source.get("source_state") or source.get("state"),
        "merge_request_facts": source.get("merge_request_facts")
        or source.get("merge_requests")
        or [],
        "acceptance_facts": source.get("acceptance_facts"),
        "source_evidence": source.get("source_evidence"),
        "authority_digest": (projection_for(source).get("authority_facts") or source.get("authority_facts") or source.get("authority") or {}).get("record_digest"),
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
