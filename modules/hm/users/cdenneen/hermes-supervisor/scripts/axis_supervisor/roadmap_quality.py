import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .mutation import MutationGate, OperationClass
from .observability import record_event
from .schema_registry import read_record, write_record

SCHEMA = "axis.external-development-supervisor.roadmap-quality"


def milestone_number(value: str | None) -> int | None:
    match = re.search(r"AX-M(\d+)", value or "")
    return int(match.group(1)) if match else None


def percent(numerator: int, denominator: int) -> int:
    return round(numerator * 100 / denominator) if denominator else 0


def root_cause(item: dict, node: dict) -> tuple[str, list[str]]:
    labels = {str(value).lower() for value in item.get("labels") or []}
    authority = item.get("authority_facts") or {}
    source_kind = str(item.get("source_kind") or "")
    if source_kind.startswith("repository-") or str(item.get("ref") or "").startswith(
        "local-convergence:"
    ):
        return "repository-convergence", ["local repository custody requires convergence"]
    if node.get("verification", {}).get("state") == "verified-complete":
        return "verified-complete", ["canonical verification receipt is complete"]
    if any("supersed" in value for value in labels):
        return "superseded", ["GitLab supersession label"]
    if item.get("source_state") == "closed":
        return "historical", [
            f"closed GitLab source; merged MRs={len(item.get('merge_request_facts') or [])}"
        ]
    if authority.get("approval_mismatch") or any(
        "po-approval" in value for value in labels
    ):
        return "product-owner-decision", ["Product Owner approval is missing or stale"]
    if authority.get("decision_stop") or (
        item.get("project") == "ghostspace/axis-governance"
        and (authority.get("approval_required") or authority.get("decision_escalate"))
    ):
        return "governance-decision", ["governance decision or approval gate"]
    number = milestone_number(item.get("milestone"))
    if number is not None and number >= 5:
        return "future-roadmap", [f"owned by future milestone AX-M{number}"]
    if node.get("flow_stage") in {"implementation-ready", "implementation"}:
        return "implementation-ready", ["executable authorized implementation exists"]
    if item.get("acceptance_criteria_present") and not item.get("milestone"):
        return "decomposition-needed", [
            "acceptance criteria exist but milestone ownership is absent"
        ]
    return "active-discovery", ["open current work requires engineering discovery"]


RELATIONSHIP_MAP = {
    "blocks": "blocks",
    "blocked-by": "blocked_by",
    "is-blocked-by": "blocked_by",
    "is_blocked_by": "blocked_by",
    "prerequisite-for": "prerequisite",
    "prerequisite": "prerequisite",
    "depends-on": "depends_on",
    "depends_on": "depends_on",
    "decomposes": "decomposes_into",
    "decomposes-into": "decomposes_into",
    "parent-of": "parent_of",
    "child-of": "child_of",
    "consumes": "consumes",
    "produces": "produces",
    "supersedes": "supersedes",
    "duplicates": "duplicates",
    "governed-by": "governed_by",
    "planned-by": "planned_by",
    "verified-by": "verified_by",
    "integrates-with": "integrates_with",
    "shares-boundary": "shares_boundary",
    "authority-from": "authority_from",
    "owned-by-milestone": "owned_by_milestone",
    "discovered-during": "discovered_during",
}


def planning_relationships(item: dict) -> list[dict[str, Any]]:
    description = str((item.get("source_evidence") or {}).get("description") or "")
    lines = description.splitlines()
    in_ledger = False
    current: dict[str, str] | None = None
    records = []
    for line in lines:
        if re.match(r"^\s*relationship_ledger:\s*$", line):
            in_ledger = True
            continue
        if not in_ledger:
            continue
        if re.match(r"^\s{2}[A-Za-z_][A-Za-z0-9_]*:\s*", line):
            break
        edge_match = re.match(r"^\s*-\s+edge_id:\s*(.+?)\s*$", line)
        if edge_match:
            if current:
                records.append(current)
            current = {"edge_id": edge_match.group(1).strip("'\"")}
            continue
        field_match = re.match(
            r"^\s+(type|source|target|state):\s*(.+?)\s*$", line
        )
        if current is not None and field_match:
            current[field_match.group(1)] = field_match.group(2).strip("'\"")
    if current:
        records.append(current)
    edges = []
    for record in records:
        relationship = RELATIONSHIP_MAP.get(
            str(record.get("type") or "").lower().replace("_", "-")
        )
        if not relationship or not record.get("source") or not record.get("target"):
            continue
        edges.append(
            {
                "source": record["source"],
                "target": record["target"],
                "relationship": relationship,
                "provenance": {
                    "source_ref": item["ref"],
                    "web_url": item.get("web_url"),
                    "record": "PlanningRecord.relationship_ledger",
                    "edge_id": record.get("edge_id"),
                },
                "confidence": "high",
            }
        )
    return edges


class RoadmapQualityProjector:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "roadmap-quality.json"
        self.gate = MutationGate(root, source="graph")

    def build(self, inventory: dict, graph: dict) -> dict:
        previous = None
        if self.path.exists():
            previous = read_record(self.path, SCHEMA)
        nodes = {node["ref"]: node for node in graph.get("nodes") or []}
        items = inventory.get("work_items") or []
        cohorts = {}
        item_records = []
        typed_edges = []
        untyped_edges = []

        for edge in inventory.get("dependency_edges") or []:
            relationship = RELATIONSHIP_MAP.get(
                str(edge.get("relationship") or "").lower().replace("_", "-")
            )
            record = {
                "source": edge.get("from_ref"),
                "target": edge.get("to_ref"),
                "relationship": relationship or str(edge.get("relationship") or "related_to"),
                "provenance": {
                    "source_ref": edge.get("from_ref"),
                    "record": "GitLab issue link",
                },
                "confidence": "high" if relationship else "low",
            }
            (typed_edges if relationship else untyped_edges).append(record)

        for item in items:
            node = nodes[item["ref"]]
            cohort, evidence = root_cause(item, node)
            cohorts[item["ref"]] = cohort
            typed_edges.extend(planning_relationships(item))
            for mr in item.get("merge_request_facts") or []:
                if mr.get("state") != "merged" or not mr.get("web_url"):
                    continue
                typed_edges.append(
                    {
                        "source": item["ref"],
                        "target": mr["web_url"],
                        "relationship": "implemented_by",
                        "provenance": {
                            "source_ref": item["ref"],
                            "web_url": item.get("web_url"),
                            "record": "GitLab merged MR linkage",
                        },
                        "confidence": "high",
                    }
                )
            authority = item.get("authority_facts") or {}
            if authority.get("record_digest"):
                typed_edges.append(
                    {
                        "source": item["ref"],
                        "target": f"planning-record:{authority['record_digest']}",
                        "relationship": "planned_by",
                        "provenance": {
                            "source_ref": item["ref"],
                            "web_url": item.get("web_url"),
                            "record": "GitLab PlanningRecord digest",
                        },
                        "confidence": "high",
                    }
                )

        deduplicated = {}
        for edge in typed_edges:
            key = (edge["source"], edge["target"], edge["relationship"])
            deduplicated[key] = edge
        typed_edges = sorted(
            deduplicated.values(),
            key=lambda edge: (edge["source"], edge["target"], edge["relationship"]),
        )
        dependency_relationships = {
            "blocks",
            "blocked_by",
            "prerequisite",
            "depends_on",
            "decomposes_into",
            "parent_of",
            "child_of",
        }
        dependency_refs = {
            ref
            for edge in typed_edges
            if edge["relationship"] in dependency_relationships
            for ref in (edge["source"], edge["target"])
        }

        for item in items:
            node = nodes[item["ref"]]
            cohort = cohorts[item["ref"]]
            authority = item.get("authority_facts") or {}
            implementation_capable = bool(
                item.get("source_state") == "opened"
                and item.get("source_kind") == "gitlab-issue"
                and item.get("acceptance_criteria_present")
                and cohort
                in {
                    "decomposition-needed",
                    "active-discovery",
                    "implementation-ready",
                }
            )
            verification_path = bool(
                item.get("merge_request_facts")
                or authority.get("approved_required_tests")
                or node.get("semantic_record")
            )
            readiness = (
                "historical"
                if cohort in {"historical", "verified-complete"}
                else "convergence"
                if cohort == "repository-convergence"
                else "future-milestone"
                if cohort == "future-roadmap"
                else "ready-after-product-owner"
                if cohort == "product-owner-decision"
                else "ready-after-governance"
                if cohort == "governance-decision"
                else "implementation-ready-now"
                if cohort == "implementation-ready"
                else "ready-after-decomposition"
                if cohort == "decomposition-needed"
                else "discovery-required"
            )
            quality_issues = []
            if implementation_capable and not item.get("milestone"):
                quality_issues.append("missing milestone ownership")
            if implementation_capable and not item.get("assignees"):
                quality_issues.append("missing engineering owner")
            if implementation_capable and not authority.get("record_digest"):
                quality_issues.append("missing governing PlanningRecord")
            if implementation_capable and not authority.get("approval_matches_record"):
                quality_issues.append("execution authority is not current")
            if implementation_capable and item["ref"] not in dependency_refs:
                quality_issues.append("no typed dependency information")
            if implementation_capable and not verification_path:
                quality_issues.append("verification path is not explicit")
            item_records.append(
                {
                    "ref": item["ref"],
                    "project": item.get("project"),
                    "title": item.get("title"),
                    "cohort": cohort,
                    "cohort_evidence": root_cause(item, node)[1],
                    "flow_stage": node.get("flow_stage"),
                    "readiness": readiness,
                    "implementation_capable": implementation_capable,
                    "milestone_owned": bool(item.get("milestone")),
                    "engineering_owner_present": bool(item.get("assignees")),
                    "planning_record_present": bool(authority.get("record_digest")),
                    "acceptance_present": bool(item.get("acceptance_criteria_present")),
                    "authority_current": bool(authority.get("approval_matches_record")),
                    "dependency_information_present": item["ref"] in dependency_refs,
                    "verification_path_present": verification_path,
                    "semantic_fresh": node.get("semantic_record") is not None,
                    "quality_issues": quality_issues,
                }
            )

        cohort_counts = dict(sorted(Counter(cohorts.values()).items()))
        readiness_counts = dict(
            sorted(Counter(item["readiness"] for item in item_records).items())
        )
        implementation_items = [
            item for item in item_records if item["implementation_capable"]
        ]
        historical_items = [
            item
            for item in item_records
            if item["cohort"] in {"historical", "verified-complete"}
        ]
        decision_items = [
            item
            for item in item_records
            if item["cohort"]
            in {"product-owner-decision", "governance-decision"}
        ]
        convergence_items = [
            item for item in item_records if item["cohort"] == "repository-convergence"
        ]
        implementation_total = len(implementation_items)
        field_metrics = {
            "milestone_ownership_coverage": percent(
                sum(item["milestone_owned"] for item in implementation_items),
                implementation_total,
            ),
            "engineering_owner_coverage": percent(
                sum(
                    item["engineering_owner_present"] for item in implementation_items
                ),
                implementation_total,
            ),
            "planning_record_coverage": percent(
                sum(item["planning_record_present"] for item in implementation_items),
                implementation_total,
            ),
            "acceptance_coverage": percent(
                sum(item["acceptance_present"] for item in implementation_items),
                implementation_total,
            ),
            "execution_authority_coverage": percent(
                sum(item["authority_current"] for item in implementation_items),
                implementation_total,
            ),
            "typed_dependency_coverage": percent(
                sum(
                    item["dependency_information_present"]
                    for item in implementation_items
                ),
                implementation_total,
            ),
            "verification_path_coverage": percent(
                sum(
                    item["verification_path_present"]
                    for item in implementation_items
                ),
                implementation_total,
            ),
            "implementation_readiness_coverage": percent(
                sum(
                    item["readiness"] == "implementation-ready-now"
                    for item in implementation_items
                ),
                implementation_total,
            ),
            "historical_archive_coverage": percent(
                sum(item["flow_stage"] == "historical" for item in historical_items),
                len(historical_items),
            ),
            "decision_queue_accuracy": percent(
                sum(item["flow_stage"] == "decision" for item in decision_items),
                len(decision_items),
            ),
            "convergence_projection_accuracy": percent(
                sum(item["flow_stage"] == "convergence" for item in convergence_items),
                len(convergence_items),
            ),
            "semantic_freshness_coverage": percent(
                sum(item["semantic_fresh"] for item in item_records), len(item_records)
            ),
        }
        graph_completeness = round(
            sum(
                field_metrics[key]
                for key in (
                    "milestone_ownership_coverage",
                    "engineering_owner_coverage",
                    "planning_record_coverage",
                    "acceptance_coverage",
                    "typed_dependency_coverage",
                    "verification_path_coverage",
                )
            )
            / 6
        )
        hygiene_score = round(
            sum(
                field_metrics[key]
                for key in (
                    "historical_archive_coverage",
                    "decision_queue_accuracy",
                    "convergence_projection_accuracy",
                    "semantic_freshness_coverage",
                )
            )
            / 4
        )
        quality_score = round(
            (
                graph_completeness
                + hygiene_score
                + field_metrics["implementation_readiness_coverage"]
                + field_metrics["execution_authority_coverage"]
            )
            / 4
        )
        metrics = {
            **field_metrics,
            "graph_completeness": graph_completeness,
            "graph_confidence": "high"
            if graph_completeness >= 80
            else "medium"
            if graph_completeness >= 50
            else "low",
            "critical_path_computability": "ready"
            if field_metrics["typed_dependency_coverage"] >= 70
            else "not-ready",
            "roadmap_hygiene_score": hygiene_score,
            "roadmap_quality_score": quality_score,
        }

        def proposal(
            proposal_id: str,
            proposal_type: str,
            affected: list[dict],
            evidence: list[str],
            benefit: str,
            impact: str,
            confidence: str,
            risks: list[str],
        ) -> dict:
            return {
                "proposal_id": proposal_id,
                "proposal_type": proposal_type,
                "affected_work_items": [item["ref"] for item in affected],
                "evidence": evidence,
                "expected_engineering_benefit": benefit,
                "measurable_impact": impact,
                "confidence": confidence,
                "risks": risks,
                "requires_product_owner_authority": True,
            }

        decomposition = [
            item for item in item_records if item["cohort"] == "decomposition-needed"
        ]
        proposals = [
            proposal(
                "RQ-HISTORICAL-PROJECTION",
                "flow-projection-correction",
                historical_items,
                [f"{len(historical_items)} closed/verified items"],
                "remove historical work from active Discovery and conversion denominators",
                f"reduce active-flow pollution by {len(historical_items)} items",
                "high",
                ["must retain provenance and verification cadence"],
            ),
            proposal(
                "RQ-DECOMPOSITION-CURATION",
                "milestone-and-decomposition-review",
                decomposition,
                [
                    f"{len(decomposition)} accepted open items lack milestone ownership"
                ],
                "create a defensible implementation-readiness denominator",
                f"resolve milestone/owner/readiness gaps for {len(decomposition)} items",
                "high",
                ["bulk assignment may encode incorrect ownership without PO review"],
            ),
            proposal(
                "RQ-TYPED-DEPENDENCIES",
                "dependency-normalization",
                implementation_items,
                [
                    f"typed dependency coverage is {field_metrics['typed_dependency_coverage']}%",
                    f"{len(untyped_edges)} generic/untyped GitLab relationship(s)",
                ],
                "enable critical-path and dependency-unlock calculations",
                "reach at least 70% typed dependency coverage before critical-path automation",
                "medium",
                ["incorrect edge semantics would create false critical paths"],
            ),
        ]
        proposal_digest_payload = {
            "cohort_counts": cohort_counts,
            "readiness_distribution": readiness_counts,
            "metrics": metrics,
            "typed_edges": typed_edges,
            "items": [
                {
                    "ref": item["ref"],
                    "cohort": item["cohort"],
                    "readiness": item["readiness"],
                    "quality_issues": item["quality_issues"],
                }
                for item in item_records
            ],
            "proposals": proposals,
        }
        quality_digest = "sha256:" + hashlib.sha256(
            json.dumps(
                proposal_digest_payload, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        previous_metrics = (previous or {}).get("metrics") or {}
        trend = {
            key: metrics[key] - int(previous_metrics.get(key, metrics[key]))
            for key in metrics
            if isinstance(metrics[key], int)
        }
        projection = {
            "schema": SCHEMA,
            "schema_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "inventory_generation_id": inventory.get("generation_id"),
            "graph_generation_id": graph.get("generation_id"),
            "quality_digest": quality_digest,
            "cohort_counts": cohort_counts,
            "readiness_distribution": readiness_counts,
            "metrics": metrics,
            "trend": trend,
            "typed_edges": typed_edges,
            "untyped_edges": untyped_edges,
            "critical_path_status": {
                "computable": metrics["critical_path_computability"] == "ready",
                "typed_dependency_coverage": field_metrics[
                    "typed_dependency_coverage"
                ],
                "minimum_required_coverage": 70,
                "reason": "typed dependency coverage is below threshold"
                if field_metrics["typed_dependency_coverage"] < 70
                else "typed dependency threshold is satisfied",
            },
            "items": sorted(item_records, key=lambda item: item["ref"]),
            "proposals": proposals,
        }
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(self.path, projection, SCHEMA)
        if not previous or previous.get("quality_digest") != quality_digest:
            record_event(
                self.root,
                "roadmap_quality_retrospective",
                details={
                    "quality_digest": quality_digest,
                    "metrics": metrics,
                    "trend": trend,
                    "cohort_counts": cohort_counts,
                    "proposal_ids": [value["proposal_id"] for value in proposals],
                    "advisory_only": True,
                },
                source="graph",
                notify=False,
            )
        return projection
