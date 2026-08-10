import re

from .canonical_work_item import projection_for


CLASSIFICATIONS = {
    "Executable",
    "Running",
    "Blocked",
    "Waiting",
    "Integrated",
    "Superseded",
    "Completed",
    "Invalid",
    "Revalidation",
    "Unknown",
}


def adapt_source_item(item: dict) -> dict:
    """Adapt pre-convergence inventory records into normalized source facts."""
    if item.get("source_kind"):
        return dict(item)
    kind = str(item.get("kind") or "issue")
    source_kind = {
        "repository-root": "repository-root",
        "repository-worktree": "repository-worktree",
        "repository-branch": "repository-branch",
        "repository-convergence": "repository-branch",
    }.get(kind, "gitlab-issue")
    decomposition = item.get("decomposition") or {}
    adapted = dict(item)
    adapted.update(
        {
            "source_kind": source_kind,
            "kind": kind,
            "source_state": item.get("source_state") or item.get("state") or "opened",
            "authority_facts": dict(projection_for(item).get("authority_facts") or item.get("authority_facts") or item.get("authority") or {}),
            "blocking_dependency_refs": list(
                item.get("blocking_dependency_refs") or item.get("dependencies") or []
            ),
            "merge_request_facts": list(
                item.get("merge_request_facts") or item.get("merge_requests") or []
            ),
            "acceptance_facts": dict(
                item.get("acceptance_facts")
                or {
                    "ids": decomposition.get("acceptance_ids") or [],
                    "open_ids": decomposition.get("open_acceptance_ids") or [],
                }
            ),
            "source_evidence": dict(item.get("source_evidence") or {}),
            "retrieval_errors": list(item.get("retrieval_errors") or []),
            "mutation_allowed": bool(item.get("mutation_allowed", True)),
        }
    )
    return adapted


def legacy_fingerprint_item(item: dict) -> dict:
    """Reconstruct the pre-convergence fingerprint projection for migration."""
    legacy = dict(item)
    authority = item.get("authority") or item.get("authority_facts") or {}
    legacy["authority"] = {
        key: authority.get(key)
        for key in (
            "digests",
            "approved",
            "approval_digests",
            "record_digest",
            "approval_matches_record",
            "approval_mismatch",
            "execution_rag",
            "approval_required",
            "decision_stop",
            "decision_escalate",
        )
    }
    evidence = item.get("source_evidence") or {}
    legacy["source_evidence"] = {
        "description": evidence.get("description") or "",
        "notes": evidence.get("notes") or [],
        "dependency_refs": item.get("dependencies")
        or item.get("blocking_dependency_refs")
        or [],
        "parent_refs": evidence.get("parent_refs") or [],
        "related_mrs": evidence.get("related_mrs")
        or evidence.get("related_mr_urls")
        or [],
    }
    return legacy


def _waiting_reason(item: dict, blocker_type: str | None) -> str:
    authority = projection_for(item).get("authority_facts") or item.get("authority_facts") or {}
    labels = " ".join(str(value).lower() for value in item.get("labels") or [])
    evidence = item.get("source_evidence") or {}
    combined = (
        f"{item.get('title', '')}\n{evidence.get('description', '')}\n{labels}"
    ).lower()
    if authority.get("approval_required") and not authority.get(
        "approval_matches_record"
    ):
        return "Product Owner approval"
    if (
        authority.get("decision_stop")
        or authority.get("decision_escalate")
        or blocker_type == "governance"
    ):
        return "Governance approval"
    if item.get("blocking_dependency_refs") or blocker_type == "dependency":
        return "Dependency"
    if re.search(
        r"\bupstream\b|waiting on implementation|implementation prerequisite", combined
    ):
        return "Upstream implementation"
    if re.search(
        r"future milestone|future slice|not before|roadmap sequencing|backlog|planned",
        combined,
    ):
        return "Future milestone sequencing"
    if re.search(r"external dependency|third[- ]party|upstream project|vendor", combined):
        return "External dependency"
    if re.search(r"merge order|merge-order|must merge after|after mr", combined):
        return "Merge ordering"
    if re.search(r"time[- ]bound|after \d{4}|not before \d{4}|cooldown", combined):
        return "Time gate"
    if re.search(r"budget|cost limit|spend", combined):
        return "Budget"
    if re.search(r"capacity|resource limit|disk|memory|runner unavailable", combined):
        return "Resource"
    if re.search(r"tool unavailable|unsupported tool|missing tool", combined):
        return "Tool limitation"
    if item.get("milestone"):
        return "Future milestone sequencing"
    return "Governance approval"


def _acceptance_decomposition(
    item: dict, classification: str, waiting_reason: str | None
) -> dict:
    acceptance = item.get("acceptance_facts") or {}
    rationale = "item is not Waiting"
    if classification == "Waiting":
        if waiting_reason in {"Product Owner approval", "Governance approval"}:
            rationale = "open acceptance slices inherit the unresolved authority gate"
        elif waiting_reason in {
            "Dependency",
            "Upstream implementation",
            "Merge ordering",
        }:
            rationale = (
                "acceptance slices were inspected; no independent "
                "authority/dependency-free slice was evidenced"
            )
        elif acceptance.get("open_ids"):
            rationale = (
                "open acceptance slices exist but source facts do not prove "
                "independent executability"
            )
        else:
            rationale = (
                "no explicit acceptance-ledger child slice was found in source facts"
            )
    return {
        "evaluated": classification == "Waiting",
        "acceptance_ids": acceptance.get("ids") or [],
        "open_acceptance_ids": acceptance.get("open_ids") or [],
        "executable_slices": [],
        "rationale": rationale,
    }


def _classify_convergence(item: dict) -> tuple[str, str | None, str | None, str]:
    facts = item.get("convergence_facts") or {}
    scope = facts.get("scope")
    if facts.get("dirty") is True:
        return (
            "Blocked",
            "repository conflict",
            None,
            "dirty repository state requires provenance/evidence disposition",
        )
    if facts.get("related_open_merge_request"):
        return "Running", None, None, "repository state belongs to an open merge request"
    if (
        scope in {"worktree", "branch"}
        and facts.get("integrated_into_default")
        and facts.get("supervisor_owned")
        and facts.get("remote_fresh")
        and (scope != "worktree" or facts.get("under_owned_worktree_root"))
    ):
        return (
            "Executable",
            None,
            None,
            "clean supervisor-owned repository state is integrated and removable",
        )
    return (
        "Waiting",
        "repository conflict",
        "Repository convergence",
        "repository state requires provenance or merge disposition",
    )


def classify_source_item(item: dict) -> dict:
    """Classify one normalized source item without performing I/O."""
    item = adapt_source_item(item)
    normalized = dict(item)
    normalized["state"] = item.get("source_state")
    normalized["dependencies"] = list(item.get("blocking_dependency_refs") or [])
    normalized["merge_requests"] = list(item.get("merge_request_facts") or [])
    normalized["authority"] = dict(projection_for(item).get("authority_facts") or item.get("authority_facts") or item.get("authority") or {})

    if item.get("retrieval_errors"):
        classification, blocker_type, waiting_reason, rationale = (
            "Unknown",
            "tool",
            None,
            "live source retrieval failed: " + ", ".join(item["retrieval_errors"]),
        )
    elif str(item.get("source_kind") or "").startswith("repository-"):
        normalized["kind"] = "repository-convergence"
        classification, blocker_type, waiting_reason, rationale = _classify_convergence(
            item
        )
        if classification == "Executable":
            normalized["authority"]["repository_convergence_authorized"] = True
    else:
        labels = {str(value).lower() for value in item.get("labels") or []}
        authority = projection_for(item).get("authority_facts") or item.get("authority_facts") or {}
        merge_requests = item.get("merge_request_facts") or []
        dependencies = item.get("blocking_dependency_refs") or []
        waiting_reason = None
        blocker_type = None
        if "invalid" in labels:
            classification, rationale = "Invalid", "explicit invalid label"
        elif "superseded" in labels or "workflow::superseded" in labels:
            classification, rationale = "Superseded", "explicit superseded label"
        elif item.get("source_state") == "closed":
            if any(mr.get("state") == "merged" for mr in merge_requests):
                classification, rationale = (
                    "Integrated",
                    "closed with merged implementation MR",
                )
            else:
                classification, rationale = (
                    "Revalidation",
                    "closed without verified merged implementation evidence",
                )
        elif any(mr.get("state") == "opened" for mr in merge_requests):
            classification, rationale = "Running", "open implementation MR"
        elif any(mr.get("state") == "merged" for mr in merge_requests):
            classification, rationale = (
                "Integrated",
                "implementation MR is merged; issue state needs evidence reconciliation",
            )
        elif dependencies:
            classification, blocker_type, rationale = (
                "Waiting",
                "dependency",
                "open blocking dependency relationship",
            )
        elif authority.get("approval_mismatch"):
            classification, blocker_type, rationale = (
                "Blocked",
                "approval",
                "Product Owner approval digest does not match the PlanningRecord digest",
            )
        elif authority.get("decision_stop"):
            classification, blocker_type, rationale = (
                "Blocked",
                "governance",
                "PlanningRecord decision is stop",
            )
        elif "blocked" in labels or "workflow::blocked" in labels:
            classification, blocker_type, rationale = (
                "Blocked",
                "dependency",
                "explicit blocked label",
            )
        elif authority.get("approval_required") and not authority.get(
            "approval_matches_record"
        ):
            classification, blocker_type, rationale = (
                "Blocked",
                "approval",
                "PlanningRecord approval is required",
            )
        elif authority.get("execution_rag") in {"red", "amber"} and not authority.get(
            "approval_matches_record"
        ):
            classification, blocker_type, rationale = (
                "Blocked",
                "governance",
                f"execution RAG is {authority['execution_rag']}",
            )
        elif authority.get("decision_escalate") and not authority.get(
            "approval_matches_record"
        ):
            classification, blocker_type, rationale = (
                "Blocked",
                "approval",
                "PlanningRecord decision is escalate without matching approval",
            )
        elif authority.get("approval_matches_record"):
            classification, rationale = (
                "Executable",
                "governed execution authority is present",
            )
        elif authority.get("execution_rag") == "green":
            classification, blocker_type, rationale = (
                "Blocked",
                "approval",
                "green execution RAG lacks exact authenticated PlanningRecord approval",
            )
        else:
            classification, blocker_type, rationale = (
                "Waiting",
                "governance",
                "no current execution authority found in source facts",
            )
        if classification == "Executable" and not item.get("mutation_allowed"):
            classification = "Waiting"
            blocker_type = "repository policy"
            rationale = "project is outside the explicit mutation allowlist"
            waiting_reason = "External dependency"
        if classification == "Waiting" and waiting_reason is None:
            waiting_reason = _waiting_reason(item, blocker_type)

    if classification not in CLASSIFICATIONS:
        raise ValueError(f"unsupported classification: {classification}")
    normalized.update(
        {
            "classification": classification,
            "blocker_type": blocker_type,
            "waiting_reason": waiting_reason,
            "classification_rationale": rationale,
            "confidence": "low"
            if classification == "Unknown"
            else "medium"
            if classification == "Waiting"
            else "high",
            "decomposition": _acceptance_decomposition(
                item, classification, waiting_reason
            ),
        }
    )
    return normalized
