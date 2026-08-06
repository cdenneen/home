import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .authority import AuthorityResolver
from .capability_graduation import (
    SCHEMA as CAPABILITY_GRADUATION_SCHEMA,
)
from .capability_graduation import (
    action_score,
    capabilities_for_paths,
)
from .classifier import (
    CLASSIFICATIONS,
    adapt_source_item,
    classify_source_item,
    legacy_fingerprint_item,
)
from .decisions import DecisionStore
from .decomposition import SemanticDecompositionEngine
from .frontier import ExecutableFrontier
from .mutation import MutationGate, OperationClass
from .noop import (
    is_suppressed_no_op,
    no_op_fingerprint,
    targeted_post_merge_fingerprint,
)
from .repository_ownership import (
    RepositoryOwnershipDenied,
    responsibility_for_repository,
    validate_repository_ownership,
)
from .revalidation import (
    revalidation_priority,
    revalidation_tier,
    select_tier_a_batch,
)
from .schema_registry import RecordError, read_record, write_record
from .verification import verification_for

AUTHORITY_PRIORITY = {
    "direct": 30,
    "inherited": 20,
    "preparation-only": 0,
    "unresolved": -40,
    "needs-product-owner": -60,
    "needs-governance": -60,
    "prohibited": -100,
}


def _authority_adjustment(authority: dict) -> int:
    return AUTHORITY_PRIORITY.get(str(authority.get("state") or "unresolved"), -40)


def _rank_node(item: dict, authority: dict) -> tuple[int, dict]:
    labels = {str(value).lower() for value in item.get("labels") or []}
    factors = {
        "base": 100,
        "repository_convergence": item.get("kind") == "repository-convergence",
        "priority_label": None,
        "blocking_dependency_count": len(item.get("dependencies") or []),
        "authority_state": authority.get("state"),
        "authority_adjustment": _authority_adjustment(authority),
    }
    score = factors["base"]
    if factors["repository_convergence"]:
        score += 80
    if labels.intersection({"priority::critical", "p0", "critical"}):
        factors["priority_label"] = "critical"
        score += 60
    elif labels.intersection({"priority::high", "p1", "high"}):
        factors["priority_label"] = "high"
        score += 40
    score -= factors["blocking_dependency_count"] * 20
    score += factors["authority_adjustment"]
    return score, factors


def _rank_queue_entry(entry: dict, authority: dict) -> None:
    adjustment = _authority_adjustment(authority)
    entry["ranking_score"] = int(entry.get("ranking_score") or 0) + adjustment
    factors = dict(entry.get("ranking_factors") or {})
    factors.update(
        {
            "authority_state": authority.get("state"),
            "authority_adjustment": adjustment,
        }
    )
    entry["ranking_factors"] = factors


MUTATING_ASSIGNMENT_TYPES = {
    "governance-document-mutation",
    "code-implementation",
    "ci-integration-repair",
}
FLOW_STAGES = (
    "backlog",
    "discovery",
    "decomposition-needed",
    "decision",
    "future",
    "convergence",
    "historical",
    "superseded",
    "analysis",
    "implementation-ready",
    "implementation",
    "integration",
    "verification",
    "verified-complete",
)


def milestone_number(value: str | None) -> int | None:
    match = re.search(r"AX-M(\d+)", value or "")
    return int(match.group(1)) if match else None


def _flow_state(
    item: dict,
    semantic: dict | None,
    verification: dict,
    authority: dict,
    assignments: list[dict],
) -> tuple[str, list[str]]:
    active = [
        assignment
        for assignment in assignments
        if assignment.get("work_item") == item.get("ref")
        and assignment.get("lifecycle_state")
        not in {
            "completed",
            "waiting",
            "blocked",
            "failed",
            "cancelled",
            "recovery-required",
        }
    ]
    if active:
        assignment = sorted(
            active,
            key=lambda value: int(value.get("created_at_epoch") or 0),
            reverse=True,
        )[0]
        lifecycle = assignment.get("lifecycle_state")
        assignment_type = assignment.get("assignment_type")
        if lifecycle == "awaiting-integration":
            return "integration", [
                f"active assignment {assignment['assignment_id']} awaits deterministic integration"
            ]
        if assignment_type in MUTATING_ASSIGNMENT_TYPES:
            return "implementation", [
                f"active coding assignment {assignment['assignment_id']}"
            ]
        if assignment_type == "no-op-verification":
            return "verification", [
                f"active no-op verification {assignment['assignment_id']}"
            ]
        return "analysis", [f"active analysis assignment {assignment['assignment_id']}"]
    if verification.get("state") == "verified-complete":
        return "verified-complete", [
            "canonical verification receipt has all required checks"
        ]
    completion_assignments = [
        assignment
        for assignment in assignments
        if assignment.get("work_item") == item.get("ref")
        and assignment.get("result_state")
        in {
            "integrated-post-main-verified",
            "repository-converged",
            "runtime-converged",
            "canonical-complete",
        }
    ]
    if completion_assignments:
        return "verification", [
            "implementation is merged and awaits fresh canonical recognition"
        ]
    source_kind = str(item.get("source_kind") or "")
    if source_kind.startswith("repository-") or str(item.get("ref") or "").startswith(
        "local-convergence:"
    ):
        return "convergence", ["local repository custody requires convergence"]
    if semantic is not None and any(
        candidate.get("result") == "Executable"
        and candidate.get("category")
        in {"audit", "tests", "fixtures", "benchmark", "negative-test"}
        and candidate.get("required_tests")
        for candidate in semantic.get("candidate_slices") or []
    ):
        return "verification", [
            "historical evidence has an explicit bounded technical verification action"
        ]
    if item.get("source_state") == "closed":
        return "historical", [
            f"closed source projected outside active engineering flow; classification={item.get('classification')}"
        ]
    if item.get("classification") == "Superseded":
        return "superseded", ["source classification is Superseded"]
    if authority.get("state") in {
        "needs-product-owner",
        "needs-governance",
        "prohibited",
    }:
        return "decision", [
            f"authority decision required: {authority.get('state')}",
            str(authority.get("reason") or "decision evidence is incomplete"),
        ]
    number = milestone_number(item.get("milestone"))
    if number is not None and number >= 5:
        return "future", [f"owned by future milestone AX-M{number}"]
    if item.get("acceptance_criteria_present") and not item.get("milestone"):
        return "decomposition-needed", [
            "acceptance criteria exist but milestone ownership/decomposition is absent"
        ]
    if semantic is None:
        return "discovery", ["no current semantic engineering record exists"]
    candidates = [
        candidate
        for candidate in semantic.get("candidate_slices") or []
        if candidate.get("result") == "Executable"
    ]
    implementation_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("category")
        in {"implementation", "documentation", "ci", "compatibility"}
    ]
    if implementation_candidates and authority.get("state") in {"direct", "inherited"}:
        return "implementation-ready", [
            f"{len(implementation_candidates)} executable candidate(s)",
            f"authority is {authority.get('state')}",
        ]
    if candidates:
        return "analysis", [
            f"{len(candidates)} executable analysis/verification candidate(s) remain"
        ]
    if authority.get("state") in {
        "needs-product-owner",
        "needs-governance",
        "unresolved",
        "prohibited",
    }:
        return "backlog", [
            f"authority constraint: {authority.get('state')}",
            str(authority.get("reason") or "authority evidence is incomplete"),
        ]
    if item.get("classification") in {"Integrated", "Completed", "Revalidation"}:
        return "verification", [
            f"source classification is {item.get('classification')} but canonical verification is incomplete"
        ]
    return "backlog", [
        str(
            item.get("waiting_reason")
            or item.get("classification_rationale")
            or "no executable downstream action is currently proven"
        )
    ]


def _execution_order(item: dict) -> int:
    assignment_type = item.get("assignment_type")
    if assignment_type in MUTATING_ASSIGNMENT_TYPES:
        return 0
    if assignment_type == "repository-convergence":
        return 1
    if assignment_type == "no-op-verification":
        return 2
    return 3


def _selection_rationale(item: dict) -> str:
    factors = item.get("ranking_factors") or {}
    assignment_type = item.get("assignment_type") or item.get("kind")
    if assignment_type in MUTATING_ASSIGNMENT_TYPES:
        policy = "implementation-ready work preempts additional analysis"
    elif assignment_type == "repository-convergence":
        policy = "repository convergence removes an execution blocker"
    elif item.get("target_ref"):
        policy = "analysis selected to produce a governed downstream action"
    else:
        policy = "highest deterministic queue rank"
    return (
        f"{policy}; score={int(item.get('ranking_score') or 0)}; "
        f"dependency_unlocks={int(factors.get('dependency_unlock_count') or 0)}; "
        f"authority={((item.get('authority') or {}).get('state') or 'unknown')}"
    )


def _scheduler_state(
    queue: list[dict], control: dict, scheduler_context: dict | None
) -> dict:
    ceiling = max(
        1,
        min(
            int(control.get("tier_a_batch_size", 2)),
            int(control.get("max_active_assignments", 1)),
        ),
    )
    context = scheduler_context or {}
    active_assignments = context.get("active_assignments") or []
    wip_counts = {
        "analysis": sum(
            assignment.get("assignment_type") == "read-only-analysis"
            and assignment.get("lifecycle_state") != "awaiting-integration"
            for assignment in active_assignments
        ),
        "implementation": sum(
            assignment.get("assignment_type") in MUTATING_ASSIGNMENT_TYPES
            and assignment.get("lifecycle_state") != "awaiting-integration"
            for assignment in active_assignments
        ),
        "integration": sum(
            assignment.get("lifecycle_state") == "awaiting-integration"
            for assignment in active_assignments
        ),
        "verification": sum(
            assignment.get("assignment_type") == "no-op-verification"
            for assignment in active_assignments
        ),
        "validation": sum(
            assignment.get("assignment_type") == "capability-deployment"
            for assignment in active_assignments
        ),
    }
    maximum_active = int(control.get("max_active_assignments", 1))
    verification_limit = min(2, maximum_active)
    validation_limit = min(1, maximum_active)
    downstream_wip_high = (
        wip_counts["validation"] >= validation_limit
        or wip_counts["verification"] >= verification_limit
        or wip_counts["validation"] + wip_counts["verification"] >= 2
    )
    wip_limits = {
        "analysis": 1
        if wip_counts["implementation"] or wip_counts["integration"]
        else min(2, maximum_active),
        "implementation": 0
        if downstream_wip_high
        else 1
        if wip_counts["integration"]
        else min(2, maximum_active),
        "integration": 1,
        "verification": verification_limit,
        "validation": validation_limit,
    }
    available_slots = max(0, maximum_active - len(active_assignments))
    budget_present = scheduler_context is not None and (
        "model_calls_remaining" in context or "available_model_call_budget" in context
    )
    budget_value = context.get(
        "available_model_call_budget", context.get("model_calls_remaining")
    )
    budget = (
        max(0, int(budget_value))
        if budget_present and budget_value is not None
        else None
    )
    next_work = queue[0] if queue else None
    implementation_ready = sum(
        item.get("assignment_type") in MUTATING_ASSIGNMENT_TYPES for item in queue
    )

    implementation_selected = False
    limiting_constraint = "queue-depth"
    if available_slots == 0:
        selected = []
        limiting_constraint = "wip-capacity"
    elif not queue:
        selected = []
        limiting_constraint = "no-executable-work"
    elif budget is None:
        selected = []
        limiting_constraint = "available-model-call-budget-unknown"
    elif budget == 0:
        selected = [
            item for item in queue if item.get("kind") == "repository-convergence"
        ][:ceiling]
        limiting_constraint = (
            "model-call-budget-exhausted"
            if not selected
            else "configured-batch-ceiling"
        )
    else:
        scheduling_queue = [
            item
            for item in queue
            if not downstream_wip_high
            or item.get("assignment_type") not in MUTATING_ASSIGNMENT_TYPES
        ]
        implementation_candidates = [
            item
            for item in scheduling_queue
            if item.get("assignment_type") in MUTATING_ASSIGNMENT_TYPES
        ]
        if wip_counts["implementation"] >= wip_limits["implementation"]:
            implementation_candidates = []
        selected = []
        selected_projects = set()
        for item in implementation_candidates:
            project = item.get("project")
            if project in selected_projects:
                continue
            selected.append(item)
            selected_projects.add(project)
            if len(selected) >= min(
                ceiling,
                budget,
                available_slots,
                wip_limits["implementation"] - wip_counts["implementation"],
            ):
                break
        if selected:
            implementation_selected = True
            limiting_constraint = "implementation-ready"
        priority_refs = list(control.get("semantic_priority_refs") or [])
        priority_order = {ref: index for index, ref in enumerate(priority_refs)}
        priority_candidates = sorted(
            [
                item
                for item in scheduling_queue
                if item.get("target_ref") in priority_order
            ],
            key=lambda item: priority_order[item["target_ref"]],
        )
        if not selected and wip_counts["analysis"] < wip_limits["analysis"]:
            selected = priority_candidates[:1]
        if not selected and wip_counts["analysis"] < wip_limits["analysis"]:
            selected = select_tier_a_batch(
                scheduling_queue, min(ceiling, available_slots), budget
            )
        if not selected and wip_counts["analysis"] < wip_limits["analysis"]:
            selected = scheduling_queue[:1]
        tier_a_candidates = [
            item for item in queue if item.get("revalidation_tier") == "A"
        ]
        if downstream_wip_high and implementation_ready and not selected:
            limiting_constraint = "downstream-wip-throttle"
        elif implementation_selected:
            pass
        elif len(selected) >= budget and budget < ceiling:
            limiting_constraint = "available-model-call-budget"
        elif len(selected) >= ceiling and len(queue) > len(selected):
            limiting_constraint = "configured-batch-ceiling"
        elif tier_a_candidates and len(tier_a_candidates) > len(selected):
            limiting_constraint = "tier-a-independence"
        elif len(selected) == 1 and not tier_a_candidates and len(queue) > 1:
            limiting_constraint = "single-item-dispatch"
        else:
            limiting_constraint = "queue-depth"

    selected_refs = {item["ref"] for item in selected}
    if selected:
        next_work = selected[0]

    def summary(item: dict | None) -> dict | None:
        if item is None:
            return None
        return {
            key: item.get(key)
            for key in (
                "ref",
                "target_ref",
                "kind",
                "project",
                "milestone",
                "ranking_score",
                "revalidation_tier",
                "assignment_type",
                "selection_rationale",
                "ranking_factors",
            )
        }

    analysis_ready = sum(
        item.get("assignment_type") in {"read-only-analysis", "no-op-verification"}
        for item in queue
    )
    metrics = context.get("engineering_metrics") or {}
    verified_samples = int(metrics.get("post_main_verified") or 0)
    metric_days = max(1, int(metrics.get("window_days") or 30))
    estimated_delay = (
        round(len(queue) / (verified_samples / metric_days), 1)
        if verified_samples >= 3
        else None
    )
    if downstream_wip_high:
        constraint_name = "validation-verification-wip"
        evidence = [
            f"validation WIP {wip_counts['validation']}/{wip_limits['validation']}",
            f"verification WIP {wip_counts['verification']}/{wip_limits['verification']}",
        ]
        action = "drain validation and verification evidence before expanding implementation WIP"
    elif wip_counts["integration"] >= wip_limits["integration"]:
        constraint_name = "integration"
        evidence = [
            f"integration WIP {wip_counts['integration']}/{wip_limits['integration']}"
        ]
        action = "resolve the oldest integration before starting same-repository work"
    elif (
        implementation_ready
        and wip_counts["implementation"] >= wip_limits["implementation"]
    ):
        constraint_name = "implementation-wip"
        evidence = [
            f"implementation WIP {wip_counts['implementation']}/{wip_limits['implementation']}",
            f"{implementation_ready} implementation-ready queue item(s)",
        ]
        action = "finish or unblock active coding assignments"
    elif implementation_ready:
        constraint_name = "implementation"
        evidence = [f"{implementation_ready} implementation-ready queue item(s)"]
        action = "dispatch highest-unlock implementation immediately"
    elif analysis_ready:
        constraint_name = "implementation-ready-supply"
        evidence = [
            f"{analysis_ready} analysis/verification queue item(s)",
            f"analysis→implementation conversion {int(metrics.get('analysis_to_implementation_percent') or 0)}%",
        ]
        action = "analyze critical-path items with authority paths most likely to yield implementation"
    else:
        constraint_name = "governed-work-supply"
        evidence = ["no executable queue entry is currently proven"]
        action = "surface exact governance, Product Owner, or dependency actions"

    return {
        "configured_batch_ceiling": ceiling,
        "available_model_call_budget": budget,
        "selected_batch": [summary(item) for item in selected],
        "deferred_items": [
            summary(item) for item in queue if item["ref"] not in selected_refs
        ],
        "next_eligible_work": summary(next_work),
        "limiting_constraint": limiting_constraint,
        "wip_limits": wip_limits,
        "wip_counts": wip_counts,
        "available_capacity": available_slots,
        "capacity_rebalance": {
            "implementation_throttled": downstream_wip_high,
            "reason": "validation/verification WIP is at the downstream threshold"
            if downstream_wip_high
            else "downstream evidence WIP is below the throttle threshold",
            "downstream_wip": wip_counts["validation"] + wip_counts["verification"],
        },
        "current_constraint": {
            "name": constraint_name,
            "evidence": evidence,
            "engineering_impact": ("verified roadmap progress is paced by this stage"),
            "estimated_roadmap_delay_days": estimated_delay,
            "forecast_confidence": "insufficient-history"
            if estimated_delay is None
            else "observed-throughput",
            "recommended_action": action,
        },
    }


def write_execution_graph(path: Path, graph: dict, gate: MutationGate) -> None:
    decision = gate.decide(OperationClass.RECONCILIATION)
    gate.require(decision, OperationClass.RECONCILIATION)
    write_record(path, graph, "axis.external-development-supervisor.execution-graph")


class ExecutionGraphBuilder:
    def __init__(self, root: Path):
        self.root = root
        self.decomposition = SemanticDecompositionEngine(root)
        self.decisions = DecisionStore(root)
        self.authority = AuthorityResolver()
        self.gate = MutationGate(root, source="graph")

    def build(self, inventory: dict, scheduler_context: dict | None = None) -> dict:
        control = read_record(
            self.root / "control.json",
            "axis.external-development-supervisor.control",
        )
        semantic_priority = {
            ref: 100_000 - index
            for index, ref in enumerate(control.get("semantic_priority_refs") or [])
        }
        raw_source_items = sorted(
            inventory.get("work_items") or [], key=lambda item: item["ref"]
        )
        refs = [item["ref"] for item in raw_source_items]
        if len(refs) != len(set(refs)):
            raise ValueError("inventory contains duplicate normalized source refs")
        source_by_ref = {item["ref"]: item for item in raw_source_items}
        source_items = [adapt_source_item(item) for item in raw_source_items]
        classified_items = [classify_source_item(item) for item in source_items]
        items_by_ref = {item["ref"]: item for item in classified_items}
        assignment_by_id = {
            assignment.get("assignment_id"): assignment
            for assignment in inventory.get("supervisor_assignments") or []
        }
        for assignment in (scheduler_context or {}).get("active_assignments") or []:
            assignment_by_id[assignment.get("assignment_id")] = assignment
        assignments = list(assignment_by_id.values())
        nodes = []
        queue = []
        semantic_pending = 0
        semantic_unresolved = 0
        policy_suppressed_executable = 0

        for item in classified_items:
            source_fingerprint = self.decomposition.source_fingerprint(
                source_by_ref[item["ref"]]
            )
            legacy_fingerprint = self.decomposition.legacy_source_fingerprint(
                legacy_fingerprint_item(item)
            )
            try:
                semantic = self.decomposition.load(
                    item["ref"],
                    source_fingerprint,
                    compatibility_fingerprints={legacy_fingerprint},
                )
            except (ValueError, RepositoryOwnershipDenied) as exc:
                if not isinstance(
                    exc, RepositoryOwnershipDenied
                ) and "semantic evidence fingerprint mismatch" not in str(exc):
                    raise
                semantic = None
            verification = verification_for(
                item,
                assignments,
                semantic,
                current_inventory_generation_id=inventory.get("generation_id"),
                current_source_fingerprint=source_fingerprint,
            )
            tier = revalidation_tier(item, semantic, verification)
            controlling_parent = (
                (semantic or {}).get("authority_resolution") or {}
            ).get("controlling_parent")
            authority = self.authority.resolve(
                item, semantic, items_by_ref.get(controlling_parent)
            )
            decision_record = self.decisions.approval_for(
                item["ref"], (semantic or {}).get("decision_packet") or {}
            )
            if decision_record is not None:
                authority = {
                    "state": "direct",
                    "source": decision_record,
                    "reason": "exact immutable Product Owner decision",
                    "decision_record": decision_record,
                }
            flow_stage, flow_evidence = _flow_state(
                item, semantic, verification, authority, assignments
            )
            ranking_score, ranking_factors = _rank_node(item, authority)
            node = {
                "ref": item["ref"],
                "source_kind": item.get("source_kind"),
                "kind": item.get("kind"),
                "project": item.get("project"),
                "title": item.get("title"),
                "labels": item.get("labels") or [],
                "milestone": item.get("milestone"),
                "source_state": item.get("source_state"),
                "classification": item["classification"],
                "blocker_type": item.get("blocker_type"),
                "waiting_reason": item.get("waiting_reason"),
                "classification_rationale": item.get("classification_rationale"),
                "authority": authority,
                "dependencies": item.get("dependencies") or [],
                "ranking_score": ranking_score,
                "ranking_factors": ranking_factors,
                "semantic_record": semantic,
                "source_fingerprint": source_fingerprint,
                "verification": verification,
                "revalidation_tier": tier,
                "flow_stage": flow_stage,
                "flow_evidence": flow_evidence,
            }
            nodes.append(node)

            if (
                verification["verification_result"]["disposition"]
                == "verified-complete"
            ):
                continue
            if flow_stage in {"historical", "future", "decision", "superseded"}:
                continue
            if semantic is not None and authority["state"] in {
                "unresolved",
                "needs-product-owner",
                "needs-governance",
            }:
                semantic_unresolved += 1
            if item["classification"] in {
                "Blocked",
                "Running",
                "Invalid",
                "Superseded",
                "Unknown",
            }:
                continue
            if item.get("kind") == "repository-convergence":
                if (
                    item["classification"] == "Executable"
                    and authority["state"] == "direct"
                    and control.get("allow_repository_mutation")
                ):
                    responsibility = responsibility_for_repository(
                        item.get("project"),
                        context=f"repository-convergence:{item['ref']}",
                    )
                    entry = {
                        **item,
                        "assignment_type": "repository-convergence",
                        "responsibility": responsibility,
                        "flow_stage": "implementation-ready",
                        "authority": authority,
                        "source_item": item,
                        "source_fingerprint": source_fingerprint,
                        "ranking_score": ranking_score,
                        "ranking_factors": ranking_factors,
                    }
                    queue.append(entry)
                elif item["classification"] == "Executable":
                    policy_suppressed_executable += 1
                continue
            if (
                item["classification"]
                in {
                    "Executable",
                    "Waiting",
                    "Revalidation",
                    "Integrated",
                    "Completed",
                }
                and semantic is None
            ):
                if not item.get("project"):
                    continue
                pending = self.decomposition.pending_item(item)
                pending["source_fingerprint"] = source_fingerprint
                pending["revalidation_tier"] = tier
                pending["milestone"] = item.get("milestone")
                pending["flow_stage"] = "analysis"
                if tier:
                    score, factors = revalidation_priority(item, tier, classified_items)
                    pending["ranking_score"] = score
                    pending["ranking_factors"] = factors
                else:
                    pending["ranking_score"] = semantic_priority.get(
                        item["ref"], pending["ranking_score"]
                    )
                _rank_queue_entry(pending, pending["authority"])
                queue.append(pending)
                semantic_pending += 1
                continue
            if semantic is not None:
                technical_candidate_ids = {
                    candidate.get("slice_id")
                    for candidate in semantic.get("candidate_slices") or []
                    if candidate.get("result") == "Executable"
                    and candidate.get("category")
                    in {"audit", "tests", "fixtures", "benchmark", "negative-test"}
                    and candidate.get("required_tests")
                }
                if tier == "B" and control.get("allow_technical_revalidation"):
                    for candidate in semantic.get("candidate_slices") or []:
                        if (
                            candidate.get("result") == "Executable"
                            and candidate.get("category")
                            in {
                                "audit",
                                "tests",
                                "fixtures",
                                "benchmark",
                                "negative-test",
                            }
                            and candidate.get("required_tests")
                        ):
                            ownership = validate_repository_ownership(
                                candidate.get("responsibility"),
                                candidate.get("project"),
                                context=f"technical-revalidation:{candidate.get('slice_id')}",
                            )
                            entry = {
                                "ref": f"technical-revalidation:{item['ref']}:{candidate['slice_id']}",
                                "kind": "technical-revalidation",
                                "assignment_type": "no-op-verification",
                                "flow_stage": "verification",
                                "target_ref": item["ref"],
                                "project": ownership["canonical_repository"],
                                "responsibility": ownership["responsibility"],
                                "repository_ownership": ownership,
                                "title": candidate.get("title"),
                                "classification": "Executable",
                                "ranking_score": int(
                                    candidate.get("ranking_score") or 200
                                ),
                                "authority": authority,
                                "candidate": candidate,
                                "source_item": item,
                                "source_fingerprint": source_fingerprint,
                                "revalidation_tier": "B",
                                "milestone": item.get("milestone"),
                                "semantic_evidence_fingerprint": semantic.get(
                                    "evidence_fingerprint"
                                ),
                            }
                            targeted_fingerprint = targeted_post_merge_fingerprint(
                                entry, assignments
                            )
                            entry["targeted_post_merge_fingerprint"] = (
                                targeted_fingerprint
                            )
                            entry["no_op_fingerprint"] = no_op_fingerprint(
                                entry, semantic
                            )
                            if is_suppressed_no_op(entry, assignments):
                                policy_suppressed_executable += 1
                                continue
                            _rank_queue_entry(entry, authority)
                            queue.append(entry)
                            break
                elif tier == "B":
                    policy_suppressed_executable += len(technical_candidate_ids)
                for candidate in semantic.get("candidate_slices") or []:
                    if candidate.get("result") != "Executable":
                        continue
                    if (
                        tier == "B"
                        and candidate.get("slice_id") in technical_candidate_ids
                    ):
                        continue
                    if authority["state"] not in {"direct", "inherited"}:
                        continue
                    category = candidate.get("category")
                    assignment_type = (
                        "governance-document-mutation"
                        if category == "documentation"
                        else "ci-integration-repair"
                        if category in {"ci", "compatibility"}
                        else "code-implementation"
                    )
                    ownership = validate_repository_ownership(
                        candidate.get("responsibility"),
                        candidate.get("project"),
                        context=f"implementation-candidate:{candidate.get('slice_id')}",
                    )
                    entry = {
                        "ref": f"slice:{item['ref']}:{candidate['slice_id']}",
                        "kind": "implementation",
                        "assignment_type": assignment_type,
                        "flow_stage": "implementation-ready",
                        "target_ref": item["ref"],
                        "project": ownership["canonical_repository"],
                        "responsibility": ownership["responsibility"],
                        "repository_ownership": ownership,
                        "title": candidate.get("title"),
                        "classification": "Executable",
                        "ranking_score": int(candidate.get("ranking_score") or 200),
                        "authority": authority,
                        "candidate": candidate,
                        "source_item": item,
                        "source_fingerprint": source_fingerprint,
                        "revalidation_tier": tier,
                        "milestone": item.get("milestone"),
                    }
                    _rank_queue_entry(entry, authority)
                    queue.append(entry)

        for entry in queue:
            entry["selection_rationale"] = _selection_rationale(entry)
        queue.sort(
            key=lambda entry: (
                _execution_order(entry),
                -int(entry.get("ranking_score") or 0),
                entry["ref"],
            )
        )
        nodes.sort(key=lambda node: node["ref"])
        classification_counts = Counter(node["classification"] for node in nodes)
        for classification in CLASSIFICATIONS:
            classification_counts.setdefault(classification, 0)
        flow_counts = Counter(node["flow_stage"] for node in nodes)
        for stage in FLOW_STAGES:
            flow_counts.setdefault(stage, 0)
        waiting_reason_counts = Counter(
            node.get("waiting_reason") or "Unknown"
            for node in nodes
            if node["classification"] == "Waiting"
        )
        matrix_path = self.root / "capability-runtime-matrix.json"
        matrix = (
            json.loads(matrix_path.read_text(encoding="utf-8"))
            if matrix_path.exists()
            else {}
        )
        graduation_path = self.root / "capability-graduation.json"
        try:
            graduation = (
                read_record(graduation_path, CAPABILITY_GRADUATION_SCHEMA)
                if graduation_path.exists()
                else {}
            )
        except RecordError:
            graduation = {}
        capability_states = {
            value["capability"]: value for value in graduation.get("capabilities") or []
        }
        for entry in queue:
            candidate = entry.get("candidate") or {}
            affected = capabilities_for_paths(
                candidate.get("allowed_paths") or entry.get("allowed_paths") or [],
                matrix,
            )
            entry["affected_capabilities"] = affected
            scoring_input = entry | {"affected_capabilities": affected}
            scoring = action_score(scoring_input, capability_states)
            entry["action_score"] = scoring
            entry["ranking_score"] = int(entry.get("ranking_score") or 0) + round(
                float(scoring["score"]) * 10
            )
            entry.setdefault("ranking_factors", {})["graduation_action_score"] = scoring
            entry["selection_rationale"] = _selection_rationale(entry)
        queue.sort(
            key=lambda entry: (
                _execution_order(entry),
                -float((entry.get("action_score") or {}).get("score") or 0),
                -int(entry.get("ranking_score") or 0),
                entry["ref"],
            )
        )
        collection = inventory.get("collection_status") or {}
        unresolved_convergence = any(
            node["kind"] == "repository-convergence"
            and node["classification"] in {"Waiting", "Blocked", "Running", "Unknown"}
            for node in nodes
        )
        proof_conditions = {
            "executable_queue_empty": not queue,
            "semantic_decomposition_complete": semantic_pending == 0,
            "semantic_authority_resolved": semantic_unresolved == 0,
            "all_configured_repositories_inspected": bool(
                collection.get("all_configured_repositories_inspected")
            ),
            "all_source_items_classified": sum(classification_counts.values())
            == len(nodes),
            "no_unknown_classifications": classification_counts["Unknown"] == 0,
            "blocked_items_isolated": all(
                node.get("blocker_type")
                for node in nodes
                if node["classification"] == "Blocked"
            ),
            "dependency_collection_complete": collection.get(
                "dependency_query_failures", 0
            )
            == 0,
            "source_retrieval_complete": collection.get("retrieval_error_count", 0)
            == 0,
            "repository_refs_current": collection.get("stale_repository_count", 0) == 0,
            "no_running_items": classification_counts["Running"] == 0,
            "no_unqueued_executable_nodes": classification_counts["Executable"] == 0,
            "no_active_assignments": collection.get("active_assignment_count", 0) == 0,
            "no_active_leases": collection.get("active_lease_count", 0) == 0,
            "state_records_valid": not collection.get("state_record_errors"),
            "repository_convergence_resolved": not unresolved_convergence,
            "no_policy_suppressed_executable_work": policy_suppressed_executable == 0,
        }
        governed_zero = all(proof_conditions.values())
        graph = {
            "schema": "axis.external-development-supervisor.execution-graph",
            "schema_version": "1.0.0",
            "generation_id": str(uuid.uuid4()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "inventory_generation_id": inventory.get("generation_id"),
            "nodes": nodes,
            "edges": sorted(
                inventory.get("dependency_edges") or [],
                key=lambda edge: (
                    edge["from_ref"],
                    edge["to_ref"],
                    edge["relationship"],
                ),
            ),
            "classification_counts": dict(sorted(classification_counts.items())),
            "flow_counts": dict(sorted(flow_counts.items())),
            "waiting_reason_counts": dict(sorted(waiting_reason_counts.items())),
            "executable_queue": queue,
            "queue_depth": len(queue),
            "semantic_decomposition_pending": semantic_pending,
            "semantic_authority_unresolved": semantic_unresolved,
            "policy_suppressed_executable_count": policy_suppressed_executable,
            "scheduler_state": _scheduler_state(queue, control, scheduler_context),
            "queue_zero_proof": proof_conditions,
            "governed_queue_zero_proven": governed_zero,
        }
        write_execution_graph(self.root / "execution-graph.json", graph, self.gate)
        ExecutableFrontier(self.root).build(queue, assignments, graph["generation_id"])
        return graph
