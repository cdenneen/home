import re


def roadmap_order(item: dict) -> int:
    values = [str(item.get("milestone") or ""), *(str(label) for label in item.get("labels") or [])]
    description = str(((item.get("source_evidence") or {}).get("description") or ""))
    values.append(description)
    for value in values:
        match = re.search(r"AX-M(\d+)(?:\.(\d+))?", value, re.I)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2) or 0)
            return major * 100 + minor if major >= 4 else 9_000 + major * 100 + minor
    return 9_999


def reserved_authority_required(item: dict) -> bool:
    authority = item.get("authority") or {}
    return bool(
        authority.get("approval_mismatch")
        or (authority.get("approval_required") and not authority.get("approval_matches_record"))
        or authority.get("decision_stop")
        or authority.get("decision_escalate")
    )


def revalidation_tier(
    item: dict, semantic_record: dict | None, verification: dict
) -> str | None:
    if (verification.get("verification_result") or verification).get(
        "disposition"
    ) == "verified-complete":
        return None
    revalidation_scope = bool(
        (item.get("source_state") or item.get("state")) == "closed"
        or item.get("classification") in {"Revalidation", "Integrated", "Completed"}
    )
    if not revalidation_scope:
        return None
    result = (semantic_record or {}).get("verification_result") or {}
    if result.get("disposition") == "human-authority-required" or reserved_authority_required(item):
        return "D"
    if result.get("disposition") == "corrective-implementation-required":
        return "C"
    if result.get("disposition") == "active-technical-revalidation":
        return "B"
    if (item.get("source_state") or item.get("state")) == "closed" and any(
        mr.get("state") == "merged"
        for mr in item.get("merge_request_facts") or item.get("merge_requests") or []
    ):
        return "A"
    return "B"


def revalidation_priority(item: dict, tier: str | None, all_items: list[dict]) -> tuple[int, dict]:
    milestone = roadmap_order(item)
    unlocks = sum(
        1
        for candidate in all_items
        if item.get("ref")
        in (
            candidate.get("blocking_dependency_refs")
            or candidate.get("dependencies")
            or []
        )
    )
    searchable = f"{item.get('title', '')} {' '.join(item.get('labels') or [])}".lower()
    runtime_relevance = int(
        any(term in searchable for term in ("runtime", "authority", "security", "persistence"))
    )
    release_critical = int(
        any(term in searchable for term in ("release", "conformance", "acceptance", "readiness"))
    )
    low_cost = int(tier == "A")
    score = (
        100_000
        - milestone * 50
        + unlocks * 500
        + runtime_relevance * 200
        + release_critical * 100
        + low_cost * 50
    )
    return score, {
        "milestone_order": milestone,
        "dependency_unlock_count": unlocks,
        "runtime_architecture_relevance": bool(runtime_relevance),
        "release_critical": bool(release_critical),
        "low_cost_tier_a": bool(low_cost),
    }


def select_tier_a_batch(
    queue: list[dict], batch_size: int, model_calls_remaining: int
) -> list[dict]:
    selected = []
    claimed_projects = set()
    claimed_authority = set()
    limit = min(batch_size, model_calls_remaining)
    if limit <= 0:
        return selected
    for item in queue:
        project = item.get("project")
        authority = set(
            (
                (item.get("source_item") or {}).get("authority_facts")
                or (item.get("source_item") or {}).get("authority")
                or {}
            ).get("digests")
            or []
        )
        if (
            item.get("revalidation_tier") != "A"
            or not project
            or project in claimed_projects
        ):
            continue
        if authority & claimed_authority:
            continue
        selected.append(item)
        claimed_projects.add(project)
        claimed_authority.update(authority)
        if len(selected) >= limit:
            break
    return selected
