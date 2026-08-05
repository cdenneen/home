import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .authority import AuthorityResolver
from .classifier import (
    CLASSIFICATIONS,
    adapt_source_item,
    classify_source_item,
    legacy_fingerprint_item,
)
from .decomposition import SemanticDecompositionEngine
from .mutation import MutationGate, OperationClass
from .revalidation import (
    revalidation_priority,
    revalidation_tier,
    select_tier_a_batch,
)
from .schema_registry import read_record, write_record
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


def _scheduler_state(
    queue: list[dict], control: dict, scheduler_context: dict | None
) -> dict:
    ceiling = max(1, int(control.get("tier_a_batch_size", 2)))
    context = scheduler_context or {}
    budget_present = scheduler_context is not None and (
        "model_calls_remaining" in context
        or "available_model_call_budget" in context
    )
    budget_value = context.get(
        "available_model_call_budget", context.get("model_calls_remaining")
    )
    budget = max(0, int(budget_value)) if budget_present and budget_value is not None else None
    next_work = queue[0] if queue else None

    if not queue:
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
            "model-call-budget-exhausted" if not selected else "configured-batch-ceiling"
        )
    else:
        priority_refs = list(control.get("semantic_priority_refs") or [])
        priority_order = {ref: index for index, ref in enumerate(priority_refs)}
        priority_candidates = sorted(
            [item for item in queue if item.get("target_ref") in priority_order],
            key=lambda item: priority_order[item["target_ref"]],
        )
        selected = priority_candidates[:1]
        if not selected:
            selected = select_tier_a_batch(queue, ceiling, budget)
        if not selected:
            selected = queue[:1]
        tier_a_candidates = [
            item for item in queue if item.get("revalidation_tier") == "A"
        ]
        if len(selected) >= budget and budget < ceiling:
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
            )
        }

    return {
        "configured_batch_ceiling": ceiling,
        "available_model_call_budget": budget,
        "selected_batch": [summary(item) for item in selected],
        "deferred_items": [
            summary(item) for item in queue if item["ref"] not in selected_refs
        ],
        "next_eligible_work": summary(next_work),
        "limiting_constraint": limiting_constraint,
    }


def write_execution_graph(path: Path, graph: dict, gate: MutationGate) -> None:
    decision = gate.decide(OperationClass.RECONCILIATION)
    gate.require(decision, OperationClass.RECONCILIATION)
    write_record(path, graph, "axis.external-development-supervisor.execution-graph")


class ExecutionGraphBuilder:
    def __init__(self, root: Path):
        self.root = root
        self.decomposition = SemanticDecompositionEngine(root)
        self.authority = AuthorityResolver()
        self.gate = MutationGate(root, source="graph")

    def build(
        self, inventory: dict, scheduler_context: dict | None = None
    ) -> dict:
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
        assignments = inventory.get("supervisor_assignments") or []
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
            semantic = self.decomposition.load(
                item["ref"],
                source_fingerprint,
                compatibility_fingerprints={legacy_fingerprint},
            )
            verification = verification_for(
                item,
                assignments,
                semantic,
                current_inventory_generation_id=inventory.get("generation_id"),
                current_source_fingerprint=source_fingerprint,
            )
            tier = revalidation_tier(item, semantic, verification)
            controlling_parent = (
                ((semantic or {}).get("authority_resolution") or {}).get(
                    "controlling_parent"
                )
            )
            authority = self.authority.resolve(
                item, semantic, items_by_ref.get(controlling_parent)
            )
            ranking_score, ranking_factors = _rank_node(item, authority)
            node = {
                "ref": item["ref"],
                "source_kind": item.get("source_kind"),
                "kind": item.get("kind"),
                "project": item.get("project"),
                "title": item.get("title"),
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
            }
            nodes.append(node)

            if verification["verification_result"]["disposition"] == "verified-complete":
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
                    entry = {
                        **item,
                        "assignment_type": "repository-convergence",
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
            if item["classification"] in {
                "Executable",
                "Waiting",
                "Revalidation",
                "Integrated",
                "Completed",
            } and semantic is None:
                pending = self.decomposition.pending_item(item)
                pending["source_fingerprint"] = source_fingerprint
                pending["revalidation_tier"] = tier
                pending["milestone"] = item.get("milestone")
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
                            entry = {
                                "ref": f"technical-revalidation:{item['ref']}:{candidate['slice_id']}",
                                "kind": "technical-revalidation",
                                "assignment_type": "no-op-verification",
                                "target_ref": item["ref"],
                                "project": candidate.get("project")
                                or item.get("project"),
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
                            }
                            _rank_queue_entry(entry, authority)
                            queue.append(entry)
                            break
                elif tier == "B":
                    policy_suppressed_executable += len(technical_candidate_ids)
                for candidate in semantic.get("candidate_slices") or []:
                    if candidate.get("result") != "Executable":
                        continue
                    if tier == "B" and candidate.get("slice_id") in technical_candidate_ids:
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
                    entry = {
                        "ref": f"slice:{item['ref']}:{candidate['slice_id']}",
                        "kind": "implementation",
                        "assignment_type": assignment_type,
                        "target_ref": item["ref"],
                        "project": candidate.get("project") or item.get("project"),
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

        queue.sort(
            key=lambda entry: (-int(entry.get("ranking_score") or 0), entry["ref"])
        )
        nodes.sort(key=lambda node: node["ref"])
        classification_counts = Counter(node["classification"] for node in nodes)
        for classification in CLASSIFICATIONS:
            classification_counts.setdefault(classification, 0)
        waiting_reason_counts = Counter(
            node.get("waiting_reason") or "Unknown"
            for node in nodes
            if node["classification"] == "Waiting"
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
            "repository_refs_current": collection.get("stale_repository_count", 0)
            == 0,
            "no_running_items": classification_counts["Running"] == 0,
            "no_unqueued_executable_nodes": classification_counts["Executable"] == 0,
            "no_active_assignments": collection.get("active_assignment_count", 0) == 0,
            "no_active_leases": collection.get("active_lease_count", 0) == 0,
            "state_records_valid": not collection.get("state_record_errors"),
            "repository_convergence_resolved": not unresolved_convergence,
            "no_policy_suppressed_executable_work": policy_suppressed_executable
            == 0,
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
        return graph
