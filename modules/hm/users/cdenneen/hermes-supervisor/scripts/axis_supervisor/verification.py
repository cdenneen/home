from typing import Any
from datetime import datetime

from .lifecycle import is_completed
from .schema_registry import validate_record
from .canonical_work_item import projection_for


VERIFICATION_SCHEMA = "axis.external-development-supervisor.verification"
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


def normalize_verification_result(value: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(value)
    normalized.setdefault("schema", VERIFICATION_SCHEMA)
    normalized.setdefault("schema_version", "1.0.0")
    validate_record(normalized, VERIFICATION_SCHEMA)
    checks = normalized["checks"]
    if set(checks) != set(CHECK_NAMES):
        raise ValueError("verification_result must contain exactly the nine canonical checks")
    expected_failed = [name for name in CHECK_NAMES if checks[name] is not True]
    if set(normalized["failed_checks"]) != set(expected_failed):
        raise ValueError("verification failed_checks do not match canonical checks")
    evidence = normalized["evidence"]
    if normalized["disposition"] == "verified-complete":
        if expected_failed or not evidence or normalized["failure_disposition"].strip():
            raise ValueError("verified-complete requires nine checks, evidence, and no failure")
    elif not normalized["failure_disposition"].strip():
        raise ValueError("incomplete verification requires a failure disposition")
    return normalized


def verification_result(
    checks: dict[str, bool | None],
    evidence: list[str | dict[str, Any]],
    *,
    tier: str,
    incomplete_disposition: str = "active-technical-revalidation",
    failure_disposition: str = "current verification is incomplete",
) -> dict[str, Any]:
    failed = [name for name in CHECK_NAMES if checks.get(name) is not True]
    complete = not failed and bool(evidence)
    return normalize_verification_result(
        {
            "schema": VERIFICATION_SCHEMA,
            "schema_version": "1.0.0",
            "standard": VERIFICATION_STANDARD,
            "tier": tier,
            "disposition": "verified-complete" if complete else incomplete_disposition,
            "checks": {name: checks.get(name) for name in CHECK_NAMES},
            "evidence": evidence,
            "failed_checks": failed,
            "failure_disposition": "" if complete else failure_disposition,
        }
    )


def historical_completion_result(item: dict, assignment: dict) -> dict[str, Any] | None:
    if not is_completed(assignment):
        return None
    evidence = assignment.get("evidence") or []
    evidence_kinds = {entry.get("kind") for entry in evidence}
    planning = assignment.get("planning_record") or {}
    merge_request = assignment.get("merge_request") or {}
    pipeline = assignment.get("pipeline") or {}
    cleanup = assignment.get("cleanup") or {}
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
            and assignment.get("acceptance")
            and "implementation-wwwhh" in evidence_kinds
            and "integration-wwwhh" in evidence_kinds
        ),
        "required_tests_rechecked": bool(
            assignment.get("required_tests")
            and "post-merge-verification" in evidence_kinds
        ),
        "pipeline_rechecked": pipeline.get("status") == "success",
        "governance_linkage_rechecked": bool(
            planning.get("digest") and planning.get("approval_note")
        ) and (
            not item.get("canonical_work_item")
            or bool((projection_for(item).get("authority_facts") or {}).get("approval_matches_record"))
        ),
        "closure_rechecked": item.get("state") == "closed",
        "integration_rechecked": bool(
            merge_request.get("merge_commit_sha")
            and merge_request.get("state") == "merged"
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
        "fresh_cycle_recognition": item.get("classification")
        in {"Integrated", "Completed"}
        and "post-merge-verification" in evidence_kinds,
    }
    refs = [
        value
        for value in (
            planning.get("approval_note"),
            merge_request.get("url"),
            pipeline.get("url"),
            *(entry.get("ref") for entry in evidence),
        )
        if value
    ]
    return verification_result(
        checks,
        refs,
        tier=str(assignment.get("revalidation_tier") or "A"),
        failure_disposition="historical completion evidence requires current revalidation",
    )


def completion_receipt(
    assignment: dict,
    inspection: dict,
    test_results: list[dict],
    cleanup: dict[str, bool],
    *,
    fresh_cycle_recognition: bool,
) -> dict[str, Any]:
    # Keep this import local: workflow_state validates assignments through this
    # module, while this receipt needs its narrow MR-projection cleanup policy.
    from .workflow_state import post_merge_cleanup_is_complete

    worker = assignment.get("worker") or {}
    handoff = worker.get("handoff") or {}
    planning = assignment.get("planning_record") or {}
    source_item = assignment.get("source_item") or {}
    merge_request = inspection.get("mr") or {}
    pipeline = inspection.get("pipeline") or {}
    required_tests = assignment.get("required_tests") or []
    evidence = [
        ref
        for ref in (
            planning.get("approval_note"),
            merge_request.get("web_url"),
            pipeline.get("web_url"),
            handoff.get("mr_url"),
            *(result.get("command") for result in test_results),
        )
        if ref
    ]
    checks = {
        "current_main_and_merge_rechecked": bool(
            merge_request.get("state") == "merged"
            and merge_request.get("merge_commit_sha")
        ),
        "acceptance_evidence_rechecked": bool(
            source_item.get("acceptance_criteria_present") and handoff.get("wwwhh")
        ),
        "required_tests_rechecked": bool(required_tests)
        and len(test_results) == len(required_tests)
        and all(result.get("returncode") == 0 for result in test_results),
        "pipeline_rechecked": pipeline.get("status") == "success",
        "governance_linkage_rechecked": bool(
            planning.get("digest") and planning.get("approval_note")
        ) and (
            not source_item.get("canonical_work_item")
            or bool((projection_for(source_item).get("authority_facts") or {}).get("approval_matches_record"))
        ),
        "closure_rechecked": source_item.get("state") == "closed",
        "integration_rechecked": bool(
            merge_request.get("state") == "merged"
            and merge_request.get("merge_commit_sha")
        ),
        "cleanup_rechecked": post_merge_cleanup_is_complete(assignment, cleanup),
        "fresh_cycle_recognition": fresh_cycle_recognition,
    }
    return verification_result(
        checks,
        evidence,
        tier=str(assignment.get("revalidation_tier") or "C"),
        failure_disposition="completion requires remaining current-cycle checks",
    )


def recognize_fresh_cycle(value: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_verification_result(value)
    checks = dict(normalized["checks"])
    checks["fresh_cycle_recognition"] = True
    return verification_result(
        checks,
        list(normalized["evidence"]),
        tier=normalized["tier"],
        incomplete_disposition=normalized["disposition"]
        if normalized["disposition"] != "verified-complete"
        else "active-technical-revalidation",
        failure_disposition=normalized["failure_disposition"]
        or "completion requires remaining current-cycle checks",
    )


def verification_for(
    item: dict,
    assignments: list[dict],
    semantic_record: dict | None = None,
    current_inventory_generation_id: str | None = None,
    current_source_fingerprint: str | None = None,
) -> dict[str, Any]:
    ref = item.get("ref")
    matching = [
        assignment
        for assignment in assignments
        if (assignment.get("work_item") or assignment.get("target_ref")) == ref
    ]
    source = "none"
    assignment_id = None
    if semantic_record is not None and semantic_record.get("verification_result") is not None:
        result = normalize_verification_result(semantic_record["verification_result"])
        qualifying_noop = any(
            assignment.get("assignment_type") == "no-op-verification"
            and assignment.get("result_state") == "no-op-verification-completed"
            and bool((assignment.get("technical_results") or {}).get("all_passed"))
            and bool((assignment.get("technical_results") or {}).get("main_sha"))
            for assignment in matching
        )
        if result["disposition"] == "verified-complete" and not qualifying_noop:
            checks = dict(result["checks"])
            checks["fresh_cycle_recognition"] = False
            result = verification_result(
                checks,
                list(result["evidence"]),
                tier=result["tier"],
                failure_disposition=(
                    "semantic analysis alone cannot prove canonical completion; "
                    "a bounded no-op verification or implementation receipt is required"
                ),
            )
        legacy_fresh = False
        if not semantic_record.get("source_inventory_generation_id"):
            try:
                legacy_fresh = datetime.fromisoformat(
                    str(semantic_record.get("revalidated_at")).replace("Z", "+00:00")
                ) >= datetime.fromisoformat(
                    str(item.get("updated_at")).replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                legacy_fresh = False
        semantic_fresh = bool(
            legacy_fresh
            or (
                current_inventory_generation_id
                and semantic_record.get("source_inventory_generation_id")
                and semantic_record.get("source_inventory_generation_id")
                != current_inventory_generation_id
                and current_source_fingerprint
                and semantic_record.get("source_fingerprint")
                == current_source_fingerprint
            )
        )
        if not semantic_fresh:
            checks = dict(result["checks"])
            checks["fresh_cycle_recognition"] = False
            result = verification_result(
                checks,
                list(result["evidence"]),
                tier=result["tier"],
                failure_disposition="semantic result awaits a fresh source cycle",
            )
        source = "semantic-record"
    else:
        result = None
        for assignment in reversed(matching):
            receipt = assignment.get("completion_receipt")
            if receipt is not None:
                result = normalize_verification_result(receipt)
                checks = dict(result["checks"])
                checks["closure_rechecked"] = (
                    item.get("source_state") or item.get("state")
                ) == "closed"
                result = verification_result(
                    checks,
                    list(result["evidence"]),
                    tier=result["tier"],
                    failure_disposition="completion requires remaining current-cycle checks",
                )
                source_generation = assignment.get("source_inventory_generation_id")
                if (
                    source_generation
                    and current_inventory_generation_id
                    and source_generation != current_inventory_generation_id
                    and assignment.get("source_fingerprint")
                    and current_source_fingerprint
                    and assignment.get("source_fingerprint")
                    != current_source_fingerprint
                    and all(
                        value
                        for name, value in result["checks"].items()
                        if name != "fresh_cycle_recognition"
                    )
                ):
                    result = recognize_fresh_cycle(result)
                source = "completion-receipt"
            else:
                result = historical_completion_result(item, assignment)
                source = "historical-adapter" if result is not None else "none"
            if result is not None:
                assignment_id = assignment.get("assignment_id")
                break
        if result is None:
            result = verification_result(
                {name: False for name in CHECK_NAMES},
                [],
                tier="A",
                failure_disposition="no authoritative verification result",
            )
    verified = result["disposition"] == "verified-complete"
    return {
        "standard": VERIFICATION_STANDARD,
        "state": "verified-complete" if verified else "pending-current-revalidation",
        "entity": "work-item",
        "checks": result["checks"],
        "failed_checks": result["failed_checks"],
        "completion_assignment_id": assignment_id,
        "source": source,
        "evidence": result["evidence"],
        "verification_result": result,
    }
