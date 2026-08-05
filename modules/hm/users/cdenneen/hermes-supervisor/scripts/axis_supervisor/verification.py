from typing import Any


VERIFICATION_STANDARD = "Supervisor 1.1 audit standard"
CHECK_NAMES = (
    "current_main_and_merge_rechecked",
    "acceptance_evidence_rechecked",
    "required_tests_rechecked",
    "pipeline_rechecked",
    "governance_linkage_rechecked",
    "closure_rechecked",
    "integration_rechecked",
    "cleanup_rechecked",
    "fresh_cycle_recognition",
)


def verification_for(
    item: dict, assignments: list[dict], semantic_record: dict | None = None
) -> dict[str, Any]:
    ref = item.get("ref")
    proofs = [
        assignment
        for assignment in assignments
        if (assignment.get("work_item") or assignment.get("target_ref")) == ref
        and assignment.get("state") in {"complete", "completed"}
        and assignment.get("phase") == "integrated"
    ]
    proof = proofs[-1] if proofs else {}
    evidence = proof.get("evidence") or []
    evidence_kinds = {entry.get("kind") for entry in evidence}
    planning = proof.get("planning_record") or {}
    merge_request = proof.get("merge_request") or {}
    pipeline = proof.get("pipeline") or {}
    cleanup = proof.get("cleanup") or {}
    related_mrs = item.get("merge_requests") or []

    checks = {
        "current_main_and_merge_rechecked": bool(
            item.get("repository_head")
            and item.get("classification") in {"Integrated", "Completed"}
            and merge_request.get("state") == "merged"
            and any(mr.get("state") == "merged" for mr in related_mrs)
        ),
        "acceptance_evidence_rechecked": bool(
            item.get("acceptance_criteria_present")
            and proof.get("acceptance")
            and "implementation-wwwhh" in evidence_kinds
            and "integration-wwwhh" in evidence_kinds
        ),
        "required_tests_rechecked": bool(
            proof.get("required_tests") and "post-merge-verification" in evidence_kinds
        ),
        "pipeline_rechecked": pipeline.get("status") == "success",
        "governance_linkage_rechecked": bool(
            planning.get("digest") and planning.get("approval_note")
        ),
        "closure_rechecked": item.get("state") == "closed",
        "integration_rechecked": bool(
            merge_request.get("merge_commit_sha") and merge_request.get("state") == "merged"
        ),
        "cleanup_rechecked": bool(cleanup)
        and all(
            cleanup.get(key) is True
            for key in (
                "worktree_removed",
                "local_branch_deleted",
                "remote_source_branch_absent",
                "lease_removed",
            )
        ),
        "fresh_cycle_recognition": item.get("classification") in {"Integrated", "Completed"},
    }
    semantic_result = (semantic_record or {}).get("verification_result") or {}
    semantic_checks = semantic_result.get("checks") or {}
    semantic_evidence = semantic_result.get("evidence") or []
    evidence_is_source_linked = bool(semantic_evidence) and all(
        isinstance(evidence, str)
        and evidence.strip()
        or isinstance(evidence, dict)
        and isinstance(evidence.get("ref"), str)
        and evidence["ref"].strip()
        for evidence in semantic_evidence
    )
    semantic_verified = bool(
        semantic_result.get("standard") == VERIFICATION_STANDARD
        and semantic_result.get("disposition") == "verified-complete"
        and set(semantic_checks) == set(CHECK_NAMES)
        and all(semantic_checks.get(name) is True for name in CHECK_NAMES)
        and evidence_is_source_linked
        and not semantic_result.get("failed_checks")
        and not str(semantic_result.get("failure_disposition") or "").strip()
    )
    if semantic_verified:
        checks = semantic_checks
    verified = (bool(proof) and all(checks.values())) or semantic_verified
    failed_checks = [name for name, passed in checks.items() if not passed]
    return {
        "standard": VERIFICATION_STANDARD,
        "state": "verified-complete" if verified else "pending-current-revalidation",
        "entity": "work-item",
        "checks": checks,
        "failed_checks": failed_checks,
        "proof_assignment_id": proof.get("assignment_id"),
        "semantic_record": semantic_verified,
        "evidence": [
            value
            for value in (
                planning.get("approval_note"),
                merge_request.get("url"),
                pipeline.get("url"),
                *(entry.get("ref") for entry in evidence),
            )
            if value
        ]
        + (semantic_result.get("evidence") or []),
    }
