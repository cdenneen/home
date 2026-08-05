import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .revalidation import revalidation_tier
from .verification import VERIFICATION_STANDARD, verification_for

COMPOSITION = (
    ("verified_complete", "Verified complete"),
    ("closed_pending_revalidation", "Closed, pending current revalidation"),
    ("running", "Running"),
    ("executable", "Executable"),
    ("waiting", "Waiting"),
    ("blocked", "Blocked"),
    ("invalid_superseded", "Invalid/superseded"),
    ("integrated_historical", "Integrated/historical"),
    ("other", "Other/unclassified"),
)

PROGRAMS = (
    ("efficiency_architecture", "Efficiency Architecture", (r"efficien", r"scalab", r"payload.*cost")),
    ("cognition_projection", "Cognition Projection", (r"cognition.*projection", r"projection.*cognition", r"neural map", r"\bhud\b")),
    ("runtime_decomposition", "Runtime Decomposition", (r"runtime decomposition", r"cognitionruntime", r"providerruntime", r"agencyruntime", r"serviceplane decomposition")),
    ("provider_runtime", "Provider Runtime", (r"provider runtime", r"provider execution", r"provider routing", r"epic::providers")),
    ("plugin_lifecycle", "Plugin Lifecycle", (r"plugin", r"epic::plugins")),
    ("repository_convergence", "Repository Convergence", ()),
    ("revalidation", "Revalidation", ()),
)
SCHEMA = "axis.external-development-supervisor.roadmap-semantics"
SCHEMA_VERSION = "1.2.0"


def source_staleness(
    inventory: dict, graph: dict, max_age_seconds: int = 3600
) -> dict[str, Any]:
    inventory_revision = inventory.get("generation_id")
    graph_inventory_revision = graph.get("inventory_generation_id")
    matches = bool(
        inventory_revision
        and graph_inventory_revision
        and inventory_revision == graph_inventory_revision
    )
    try:
        generated = datetime.fromisoformat(
            str(inventory.get("generated_at")).replace("Z", "+00:00")
        )
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        raw_age_seconds = int((datetime.now(timezone.utc) - generated).total_seconds())
        future_timestamp = raw_age_seconds < -300
        source_age_seconds = max(0, raw_age_seconds)
    except (TypeError, ValueError):
        source_age_seconds = max_age_seconds + 1
        future_timestamp = False
    current = matches and not future_timestamp and source_age_seconds <= max_age_seconds
    return {
        "state": "current" if current else "stale",
        "source_generations_match": matches,
        "source_age_seconds": source_age_seconds,
        "source_inventory_revision": inventory_revision,
        "graph_inventory_revision": graph_inventory_revision,
        "reason": (
            None
            if current
            else "execution graph generation mismatch"
            if not matches
            else "source inventory timestamp is in the future"
            if future_timestamp
            else f"source inventory is older than {max_age_seconds} seconds"
        ),
    }


def require_current_sources(inventory: dict, graph: dict) -> None:
    staleness = source_staleness(inventory, graph)
    if staleness["state"] != "current":
        raise ValueError(staleness["reason"])


def metric(count: int, denominator: int) -> dict[str, int | float]:
    return {
        "count": count,
        "denominator": denominator,
        "percent": round(count * 100 / denominator, 1) if denominator else 0,
    }


def composition_key(item: dict, verification: dict) -> str:
    result = verification.get("verification_result") or verification
    if result.get("disposition") == "verified-complete":
        return "verified_complete"
    if item.get("state") == "closed":
        return "closed_pending_revalidation"
    classification = item.get("classification")
    if classification == "Running":
        return "running"
    if classification == "Executable":
        return "executable"
    if classification in {"Waiting", "Revalidation"}:
        return "waiting"
    if classification == "Blocked":
        return "blocked"
    if classification in {"Invalid", "Superseded"}:
        return "invalid_superseded"
    if classification in {"Integrated", "Completed"}:
        return "integrated_historical"
    return "other"


def milestone_key(value: str | None) -> str | None:
    match = re.search(r"AX-M(\d+(?:\.\d+)?)", str(value or ""), re.IGNORECASE)
    return f"AX-M{match.group(1)}" if match else None


def item_milestone_key(item: dict) -> str | None:
    direct = milestone_key(item.get("milestone"))
    if direct:
        return direct
    for label in item.get("labels") or []:
        if str(label).lower().startswith("roadmap::"):
            key = milestone_key(label)
            if key:
                return key
    description = ((item.get("source_evidence") or {}).get("description") or "")
    match = re.search(
        r"owning_milestone:\s*(AX-M\d+(?:\.\d+)?)",
        description,
        re.IGNORECASE,
    )
    return milestone_key(match.group(1)) if match else None


def milestone_order(key: str) -> tuple[int, ...]:
    value = key.removeprefix("AX-M")
    return tuple(int(part) for part in value.split("."))


def program_matches(program: str, patterns: tuple[str, ...], item: dict) -> bool:
    if program == "repository_convergence":
        return item.get("kind") == "repository-convergence"
    if program == "revalidation":
        return item.get("classification") in {"Revalidation", "Integrated", "Completed"}
    searchable = "\n".join(
        [
            str(item.get("title") or ""),
            " ".join(str(label) for label in item.get("labels") or []),
            str(((item.get("source_evidence") or {}).get("description") or "")[:2000]),
        ]
    ).lower()
    return any(re.search(pattern, searchable) for pattern in patterns)


def milestone_reason(counts: Counter, total: int) -> str:
    if not total:
        return "No governed inventory items are currently mapped to this milestone."
    if counts["blocked"]:
        return "Concrete authority, dependency, or technical blockers remain."
    if counts["running"]:
        return "Work is already running; no additional item is currently eligible."
    if counts["waiting"]:
        return "Items are waiting on expected dependencies, review, or sequencing."
    if counts["closed_pending_revalidation"]:
        return "Closed items require current Supervisor 1.1 revalidation before readiness is claimed."
    if counts["verified_complete"] == total:
        return "All mapped items are verified complete."
    if counts["integrated_historical"]:
        return "Integrated or historical evidence still needs closure or current audit reconciliation."
    return "No mapped item currently satisfies executable eligibility."


def milestone_status(counts: Counter, total: int) -> str:
    if total and counts["verified_complete"] == total:
        return "verified"
    if counts["blocked"]:
        return "blocked"
    if counts["running"]:
        return "running"
    if counts["executable"]:
        return "progressing"
    if counts["waiting"] or counts["closed_pending_revalidation"]:
        return "waiting"
    return "future"


def queue_source(entry: dict, items: dict[str, dict]) -> str:
    ref = entry.get("target_ref") or entry.get("ref")
    target = items.get(str(ref)) if ref else {}
    target = target or {}
    candidate = entry.get("candidate") or {}
    if entry.get("kind") == "repository-convergence" or target.get("kind") == "repository-convergence":
        return "repository_convergence"
    if candidate.get("category") in {"ci", "integration"}:
        return "ci_integration"
    if entry.get("kind") == "technical-revalidation":
        return "revalidation"
    if entry.get("kind") == "semantic-decomposition" and target.get("classification") in {
        "Revalidation",
        "Integrated",
        "Completed",
    }:
        return "revalidation"
    if (entry.get("project") or target.get("project")) == "ghostspace/axis-governance":
        return "governance_reconciliation"
    if item_milestone_key(target):
        return "milestone_work"
    return "unmilestoned_work"


def build_roadmap_semantics(
    inventory: dict,
    graph: dict,
    control: dict | None = None,
    deployed_revision: dict | None = None,
) -> dict[str, Any]:
    source_items = {
        item.get("ref"): item for item in inventory.get("work_items") or []
    }
    work_items = []
    for node in graph.get("nodes") or []:
        item = dict(source_items.get(node.get("ref")) or {})
        item.update(node)
        item["state"] = node.get("source_state")
        work_items.append(item)
    items_by_ref = {item.get("ref"): item for item in work_items}
    queue = graph.get("executable_queue") or []
    assignments = inventory.get("supervisor_assignments") or []
    nodes = {node.get("ref"): node for node in graph.get("nodes") or []}
    total = len(work_items)
    composition_counts = Counter()
    verifications = {}
    tiers = {}
    for item in work_items:
        node = nodes.get(item.get("ref")) or {}
        verification = node.get("verification") or verification_for(item, assignments)
        verifications[item.get("ref")] = verification
        tiers[item.get("ref")] = node.get("revalidation_tier") or revalidation_tier(
            item, node.get("semantic_record"), verification
        )
        composition_counts[composition_key(item, verification)] += 1
    if sum(composition_counts.values()) != total:
        raise ValueError("roadmap composition does not sum to discovered inventory")

    composition = {
        key: {"label": label, **metric(composition_counts[key], total)}
        for key, label in COMPOSITION
    }
    verified_items = [
        {
            "ref": ref,
            "completion_assignment_id": verification.get("completion_assignment_id"),
            "evidence": (
                verification.get("verification_result") or verification
            ).get("evidence")
            or [],
        }
        for ref, verification in verifications.items()
        if (verification.get("verification_result") or verification).get(
            "disposition"
        )
        == "verified-complete"
    ]
    closed = [item for item in work_items if item.get("state") == "closed"]
    waiting = [item for item in work_items if item.get("classification") == "Waiting"]
    audited_refs = {
        ref
        for ref, node in nodes.items()
        if node.get("semantic_record") is not None
        or verifications.get(ref, {}).get("state") == "verified-complete"
    }
    dependency_evaluated = sum(
        1
        for item in work_items
        if isinstance(item.get("dependencies"), list) and not item.get("retrieval_errors")
    )
    classified = sum(1 for item in work_items if item.get("classification") != "Unknown")
    coverage = {
        "inventory_classified": {"label": "Inventory classified", **metric(classified, total)},
        "closed_items_reverified": {
            "label": "Closed items reverified",
            **metric(len([item for item in closed if item.get("ref") in audited_refs]), len(closed)),
        },
        "waiting_items_decomposed": {
            "label": "Waiting items decomposed",
            **metric(len([item for item in waiting if item.get("ref") in audited_refs]), len(waiting)),
        },
        "dependencies_evaluated": {
            "label": "Dependencies evaluated",
            **metric(dependency_evaluated, total),
        },
        "queue_eligibility_evaluated": {
            "label": "Queue eligibility evaluated",
            **metric(len(audited_refs), total),
        },
        "source_linkage_verified": {
            "label": "Source linkage verified",
            **metric(len(audited_refs), total),
        },
    }

    roadmap_metadata = {}
    for milestone in inventory.get("milestones") or []:
        key = milestone_key(milestone.get("title"))
        if key and milestone_order(key)[0] >= 4:
            roadmap_metadata[key] = milestone
    for item in work_items:
        key = item_milestone_key(item)
        if key and milestone_order(key)[0] >= 4:
            roadmap_metadata.setdefault(
                key,
                {"title": key, "state": "derived", "web_url": None},
            )

    queue_by_milestone: dict[str, list[tuple[int, dict]]] = {}
    for index, entry in enumerate(queue):
        target = items_by_ref.get(entry.get("target_ref") or entry.get("ref")) or {}
        key = item_milestone_key(target)
        if key and key in roadmap_metadata:
            queue_by_milestone.setdefault(key, []).append((index, entry))
    scheduler_state = graph.get("scheduler_state") or {}
    if not isinstance(scheduler_state, dict):
        raise TypeError("execution graph scheduler_state must be an object")
    current_supervisor_focus = scheduler_state.get("next_eligible_work") or {}
    if not isinstance(current_supervisor_focus, dict):
        raise TypeError("scheduler_state next_eligible_work must be an object or null")
    current_supervisor_focus_key = current_supervisor_focus.get("milestone")
    ordered_roadmap_keys = sorted(roadmap_metadata, key=milestone_order)
    current_execution_frontier = next(
        (
            key
            for key in ordered_roadmap_keys
            if any(
                composition_key(item, verifications[item.get("ref")])
                not in {"verified_complete", "invalid_superseded"}
                for item in work_items
                if item_milestone_key(item) == key
            )
        ),
        None,
    )
    frontier_order = (
        milestone_order(current_execution_frontier)
        if current_execution_frontier
        else None
    )

    milestones = []
    readiness_evaluated = 0
    for key in ordered_roadmap_keys:
        milestone = roadmap_metadata[key]
        title = milestone.get("title") or key
        members = [item for item in work_items if item_milestone_key(item) == key]
        counts = Counter(composition_key(item, verifications[item.get("ref")]) for item in members)
        queued = queue_by_milestone.get(key) or []
        queue_types = Counter(
            "semantic-audit" if entry.get("kind") == "semantic-decomposition" else "implementation"
            for _rank, entry in queued
        )
        if members and all(item.get("ref") in audited_refs for item in members):
            readiness_evaluated += 1
        progress_count = (
            counts["verified_complete"]
            + counts["closed_pending_revalidation"]
            + counts["integrated_historical"]
        )
        confidence_count = sum(1 for item in members if item.get("confidence") == "high")
        order = milestone_order(key)
        future = bool(
            frontier_order
            and order > frontier_order
            and not counts["running"]
            and not counts["executable"]
            and not counts["blocked"]
            and milestone.get("state") != "closed"
            and key != current_supervisor_focus_key
        )
        if milestone.get("state") == "closed" and members and counts["verified_complete"] == len(members):
            status = "completed"
        elif milestone.get("state") == "closed":
            status = "closed-pending-audit"
        elif key == current_execution_frontier:
            status = "execution-frontier"
        elif key == current_supervisor_focus_key:
            status = "parallel-execution"
        elif counts["blocked"]:
            status = "critical-path"
        elif future:
            status = "future"
        else:
            status = milestone_status(counts, len(members))
        if counts["blocked"]:
            health = "blocked"
        elif counts["running"]:
            health = "running"
        elif counts["executable"]:
            health = "progressing"
        elif members and counts["verified_complete"] == len(members):
            health = "verified"
        elif future:
            health = "future"
        else:
            health = "waiting"
        if future and not counts["executable"]:
            reason = "Future milestone. No executable work until prerequisite milestones complete."
        elif milestone.get("state") == "closed" and counts["closed_pending_revalidation"]:
            reason = "Completed milestone; historical delivery evidence awaits current Supervisor 1.1 revalidation."
        else:
            reason = milestone_reason(counts, len(members)) if not counts["executable"] else None
        highlights = []
        if key == current_execution_frontier:
            highlights.append("current-execution-frontier")
        if key == current_supervisor_focus_key:
            highlights.append("current-supervisor-focus")
        if counts["blocked"]:
            highlights.append("critical-path")
        if future:
            highlights.append("future")
        if milestone.get("state") == "closed" and status == "completed":
            highlights.append("completed")
        elif milestone.get("state") == "closed":
            highlights.append("historically-closed")
        milestones.append(
            {
                "key": key,
                "title": title,
                "web_url": milestone.get("web_url"),
                "milestone_state": milestone.get("state"),
                "total": len(members),
                "progress": {"label": "Delivered/integrated", **metric(progress_count, len(members))},
                "verified_complete": counts["verified_complete"],
                "closed_pending_revalidation": counts["closed_pending_revalidation"],
                "running": counts["running"],
                "executable": counts["executable"],
                "revalidation_ready": sum(
                    1 for item in members if tiers.get(item.get("ref")) in {"A", "B", "C", "D"}
                ),
                "waiting": counts["waiting"],
                "blocked": counts["blocked"],
                "invalid_superseded": counts["invalid_superseded"],
                "integrated_historical": counts["integrated_historical"],
                "status": status,
                "health": health,
                "confidence": {"label": "High-confidence classifications", **metric(confidence_count, len(members))},
                "queued_tasks": len(queued),
                "queue_breakdown": {
                    "semantic_audit": queue_types["semantic-audit"],
                    "implementation": queue_types["implementation"],
                },
                "execution_rank": min((rank for rank, _entry in queued), default=None),
                "highlights": highlights,
                "zero_executable_reason": reason,
            }
        )
    coverage["milestone_readiness_evaluated"] = {
        "label": "Milestone readiness evaluated",
        **metric(readiness_evaluated, len(milestones)),
    }

    active_execution = sorted(
        [
            milestone
            for milestone in milestones
            if milestone["queued_tasks"] or milestone["running"] or milestone["executable"]
        ],
        key=lambda milestone: (
            milestone["execution_rank"] is None,
            milestone["execution_rank"] if milestone["execution_rank"] is not None else 10**9,
            milestone_order(milestone["key"]),
        ),
    )

    strategic_programs = []
    queued_target_refs = [entry.get("target_ref") or entry.get("ref") for entry in queue]
    for program, label, patterns in PROGRAMS:
        members = [item for item in work_items if program_matches(program, patterns, item)]
        member_refs = {item.get("ref") for item in members}
        counts = Counter(composition_key(item, verifications[item.get("ref")]) for item in members)
        queued_count = sum(1 for ref in queued_target_refs if ref in member_refs)
        confidence_count = sum(1 for item in members if item.get("confidence") == "high")
        strategic_programs.append(
            {
                "key": program,
                "title": label,
                "total": len(members),
                "queued_tasks": queued_count,
                "running": counts["running"],
                "executable": counts["executable"],
                "waiting": counts["waiting"] + counts["closed_pending_revalidation"],
                "blocked": counts["blocked"],
                "verified_complete": counts["verified_complete"],
                "confidence": {"label": "High-confidence classifications", **metric(confidence_count, len(members))},
                "non_exclusive": True,
            }
        )

    source_labels = {
        "milestone_work": "Milestone work",
        "unmilestoned_work": "Unmilestoned work",
        "repository_convergence": "Repository convergence",
        "governance_reconciliation": "Governance reconciliation",
        "revalidation": "Revalidation",
        "ci_integration": "CI/integration",
    }
    source_counts = Counter(
        queue_source(entry, items_by_ref)
        for entry in queue
    )
    executable_sources = {
        key: {"label": label, "count": source_counts[key]}
        for key, label in source_labels.items()
    }
    queue_total = len(queue)
    milestone_executable = sum(milestone["executable"] for milestone in milestones)
    source_summary = ", ".join(
        f"{value['count']} {value['label'].lower()}"
        for value in executable_sources.values()
        if value["count"]
    ) or "no queued work"
    explanation = None
    if queue_total and milestone_executable == 0:
        explanation = (
            f"The ready supervisor queue contains {queue_total} tasks ({source_summary}). "
            "None are lifecycle-executable items in the displayed active milestones; those milestones remain dependency-, review-, or revalidation-gated."
        )

    verified_refs = {item["ref"] for item in verified_items}
    closed_pending = [
        item
        for item in work_items
        if item.get("state") == "closed" and item.get("ref") not in verified_refs
    ]
    tier_counts = Counter(tier for tier in tiers.values() if tier)
    revalidation_remaining = sum(tier_counts.values())
    active_keys = {
        milestone["key"]
        for milestone in milestones
        if milestone["milestone_state"] == "active"
    }
    active_milestone_pending = sum(
        1 for item in closed_pending if item_milestone_key(item) in active_keys
    )
    revalidation_plan = {
        "total_closed_pending": len(closed_pending),
        "revalidation_remaining": revalidation_remaining,
        "prioritization": [
            "active-milestone closure and dependency unlock impact",
            "completed supervisor assignments with durable proof receipts",
            "merged-MR items eligible for automatic evidence review",
            "remaining historical items by governance and execution risk",
        ],
        "milestone_impact": {
            "active_milestone_closed_pending": active_milestone_pending,
            "inactive_or_unmilestoned_closed_pending": len(closed_pending)
            - active_milestone_pending,
        },
        "tier_a_automatic_evidence": tier_counts["A"],
        "tier_b_active_technical": tier_counts["B"],
        "tier_c_corrective_implementation": tier_counts["C"],
        "tier_d_human_authority": tier_counts["D"],
        "tiers_are_exclusive": True,
    }

    queued_target_refs = {
        entry.get("target_ref") or entry.get("ref") for entry in queue
    }
    supervisor_work_remaining = total - len(verified_items)
    ready_work_total = len(queue)
    ready_work_item_count = len(queued_target_refs)
    ready_partition = Counter()
    for entry in queue:
        if entry.get("kind") == "implementation":
            ready_partition["implementation"] += 1
            continue
        source = queue_source(entry, items_by_ref)
        if source == "revalidation":
            ready_partition["revalidation"] += 1
        elif source == "governance_reconciliation":
            ready_partition["governance"] += 1
        elif source == "repository_convergence":
            ready_partition["repository_convergence"] += 1
        else:
            ready_partition["other"] += 1
    if sum(ready_partition.values()) != len(queue):
        raise ValueError("ready supervisor queue partition is inconsistent")
    need_product_owner = (
        "Not immediately"
        if tier_counts["D"] and ready_work_item_count > tier_counts["D"]
        else "Yes"
        if tier_counts["D"]
        else "No"
    )
    supervisor_work = {
        "governed_roadmap_items": total,
        "supervisor_work_remaining": supervisor_work_remaining,
        "ready_work_total": ready_work_total,
        "ready_work_item_count": ready_work_item_count,
        "implementation_executable": ready_partition["implementation"],
        "revalidation_ready": ready_partition["revalidation"],
        "governance_reconciliation_ready": ready_partition["governance"],
        "repository_convergence_ready": ready_partition["repository_convergence"],
        "other_ready": ready_partition["other"],
        "waiting_blocked_or_other_not_ready": max(
            0, supervisor_work_remaining - ready_work_item_count
        ),
        "need_product_owner_now": need_product_owner,
        "baseline_clarification": (
            "This is a current revalidation baseline, not a claim that AXIS is only "
            f"{len(verified_items)}/{total} implemented."
        ),
    }

    semantics = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "inventory_revision": inventory.get("generation_id"),
            "graph_generation_id": graph.get("generation_id"),
            "deployed_revision": deployed_revision or {},
        },
        "staleness": source_staleness(inventory, graph),
        "verification_standard": {
            "label": f"Verified under {VERIFICATION_STANDARD}",
            "definition": (
                "A work item is Verified Complete only when current main, acceptance criteria, required tests, "
                "pipeline evidence, governance linkage, closure evidence, integration, cleanup, and fresh-cycle recognition have all been rechecked."
            ),
        },
        "verification_scope": {
            "roadmap_entity": "work items",
            "acceptance_criteria": "evidence dimension; not a roadmap denominator",
            "merge_requests": "integration evidence; not a roadmap denominator",
            "milestones": "readiness aggregation over member work items",
            "implementation_slices": "execution evidence unless represented by a governed work item",
            "evidence_records": "source-linkage evidence; not a roadmap denominator",
        },
        "total_governed_items": total,
        "composition": composition,
        "coverage": coverage,
        "complete_roadmap": milestones,
        "active_execution": active_execution,
        "strategic_programs": strategic_programs,
        "roadmap_endpoint": milestones[-1]["key"] if milestones else None,
        "current_execution_frontier": current_execution_frontier,
        "scheduler_state": scheduler_state,
        "current_supervisor_focus": current_supervisor_focus,
        "supervisor_work": supervisor_work,
        "executable_sources": executable_sources,
        "ready_queue": {
            "count": queue_total,
            "milestone_lifecycle_executable": milestone_executable,
            "explanation": explanation,
        },
        "verified_items": verified_items,
        "revalidation_plan": revalidation_plan,
    }
    revision_payload = json.loads(
        json.dumps(
            {key: value for key, value in semantics.items() if key != "generated_at"}
        )
    )
    revision_payload["staleness"].pop("source_age_seconds", None)
    semantics["semantic_revision"] = hashlib.sha256(
        json.dumps(revision_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return semantics
