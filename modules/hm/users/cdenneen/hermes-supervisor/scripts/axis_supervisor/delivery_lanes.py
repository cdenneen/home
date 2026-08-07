import hashlib
import json
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .frontier import conflict_domains
from .lifecycle import adapt_assignment, is_terminal
from .missions import read_mission_record
from .mutation import MutationGate, OperationClass
from .schema_registry import read_record, write_record
from .validation_findings import ExternalImplementationAdoptions, ValidationFindingStore
from .workflow_state import WorkflowState

SCHEMA = "axis.external-development-supervisor.delivery-board"
SCHEMA_VERSION = "1.0.0"
LANES = (
    "BACKLOG",
    "READY",
    "IMPLEMENTATION",
    "HANDOFF",
    "REVIEW",
    "REPAIR",
    "INTEGRATION",
    "MERGE_READY",
    "POST_MAIN_VERIFICATION",
    "DEPLOYMENT",
    "VALIDATION",
    "DECISION",
    "GRADUATED",
    "BLOCKED",
)
LANE_WORKER_POOL = {
    "BACKLOG": None,
    "READY": "semantic",
    "IMPLEMENTATION": "implementation",
    "HANDOFF": "implementation",
    "REVIEW": "review-integration",
    "REPAIR": "repair",
    "INTEGRATION": "review-integration",
    "MERGE_READY": "review-integration",
    "POST_MAIN_VERIFICATION": "review-integration",
    "DEPLOYMENT": "deployment",
    "VALIDATION": "validation",
    "DECISION": None,
    "GRADUATED": None,
    "BLOCKED": None,
}
WORKER_POOL_CAPACITY = {
    "semantic": 2,
    "implementation": 4,
    "review-integration": 2,
    "repair": 2,
    "deployment": 2,
    "validation": 2,
}
LANE_CAPACITY = {
    lane: WORKER_POOL_CAPACITY.get(pool) if pool else None
    for lane, pool in LANE_WORKER_POOL.items()
}
STALL_SECONDS = {
    "BACKLOG": 7 * 86_400,
    "READY": 3_600,
    "IMPLEMENTATION": 2 * 3_600,
    "HANDOFF": 15 * 60,
    "REVIEW": 4 * 3_600,
    "REPAIR": 2 * 3_600,
    "INTEGRATION": 3_600,
    "MERGE_READY": 30 * 60,
    "POST_MAIN_VERIFICATION": 3_600,
    "DEPLOYMENT": 4 * 3_600,
    "VALIDATION": 4 * 3_600,
    "DECISION": 24 * 3_600,
    "BLOCKED": 7 * 86_400,
}
CI_PENDING = {
    "created",
    "pending",
    "preparing",
    "running",
    "scheduled",
    "waiting_for_resource",
}
CI_FAILED = {"failed", "canceled", "cancelled", "skipped"}
EXPLICIT_LANE_PATTERN = re.compile(
    r"(?:delivery[-_ ]lane|lane)\s*[:=]\s*"
    + "(" + "|".join(LANES) + r")\b",
    re.IGNORECASE,
)


def utc_now(now: int | None = None) -> str:
    return datetime.fromtimestamp(
        int(time.time()) if now is None else now, timezone.utc
    ).isoformat()


def _epoch(value: object, default: int) -> int:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (TypeError, ValueError):
        return default


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _explicit_lane(item: dict[str, Any]) -> str | None:
    labels = {str(label).strip().casefold() for label in item.get("labels") or []}
    for lane in LANES:
        normalized = lane.casefold().replace("_", "-")
        if labels.intersection(
            {
                f"lane::{normalized}",
                f"delivery::{normalized}",
                f"workflow::{normalized}",
            }
        ):
            return lane
    notes = (item.get("source_evidence") or {}).get("notes") or []
    for note in notes:
        match = EXPLICIT_LANE_PATTERN.search(str(note.get("body") or ""))
        if match:
            return match.group(1).upper()
    return None


def classify_ci(mr: dict[str, Any] | None) -> str:
    if not mr:
        return "not-applicable"
    status = str(
        mr.get("pipeline_status")
        or (mr.get("head_pipeline") or {}).get("status")
        or ""
    ).casefold()
    if status == "success":
        return "passed"
    if status == "manual":
        return "manual"
    if status in CI_PENDING:
        return "pending"
    if status in CI_FAILED:
        return "failed"
    return "unknown"


def lane_from_mr(mr: dict[str, Any] | None) -> tuple[str | None, str]:
    if not mr:
        return None, "no merge request"
    state = str(mr.get("state") or "").casefold()
    ci = classify_ci(mr)
    if state == "merged":
        return "POST_MAIN_VERIFICATION", "GitLab merge request is merged"
    if state == "closed":
        return "BLOCKED", "GitLab merge request closed without integration"
    if state != "opened":
        return None, "merge request state is not actionable"
    if ci == "failed":
        return "REPAIR", "merge request pipeline requires repair"
    if ci == "manual":
        return "BLOCKED", "merge request pipeline awaits a manual gate"
    if mr.get("has_conflicts") or str(mr.get("detailed_merge_status") or "") in {
        "conflict",
        "broken_status",
        "ci_must_pass",
        "discussions_not_resolved",
        "not_approved",
    }:
        return "REPAIR", "merge request cannot advance without repair"
    if mr.get("draft") or mr.get("review_pending") or mr.get(
        "blocking_discussions_resolved"
    ) is False:
        return "REVIEW", "merge request awaits review or discussion resolution"
    merge_status = str(
        mr.get("detailed_merge_status") or mr.get("merge_status") or ""
    )
    if ci == "passed" and merge_status in {"mergeable", "can_be_merged"}:
        return "MERGE_READY", "pipeline and GitLab mergeability gates passed"
    if ci == "pending":
        return "INTEGRATION", "merge request pipeline is active"
    return "REVIEW", "merge request awaits deterministic review evidence"


def lane_from_assignment(
    assignment: dict[str, Any], integration: dict[str, Any] | None = None
) -> tuple[str, str]:
    assignment_type = str(assignment.get("assignment_type") or "")
    lifecycle = str(assignment.get("lifecycle_state") or "")
    result = str(assignment.get("result_state") or "")
    if assignment_type == "no-op-verification" and assignment.get(
        "targeted_replay"
    ) and lifecycle in {
        "ready-semantic",
        "running-semantic",
    }:
        return "VALIDATION", "targeted validation replay is active"
    if assignment_type == "capability-deployment":
        if lifecycle == "canonical-complete":
            return "GRADUATED", "deployment and canonical validation completed"
        if lifecycle == "runtime-converged":
            return "VALIDATION", "deployment completed and awaits validation"
        if lifecycle == "deployment-failed":
            return "REPAIR", "deployment failed"
        return "DEPLOYMENT", "capability deployment assignment is active"
    if lifecycle in {"ready-semantic", "ready-implementation"}:
        return "READY", "assignment is ready for a worker"
    if lifecycle == "running-semantic":
        return "READY", "semantic worker is producing executable scope"
    if lifecycle == "running-implementation":
        return "IMPLEMENTATION", "implementation worker is active"
    if lifecycle == "implementation-complete":
        return "HANDOFF", "implementation completed and handoff is pending"
    if lifecycle == "awaiting-integration":
        queue_state = str((integration or {}).get("state") or "")
        if (integration or {}).get("main_advance") in {
            "repair-required",
            "advanced-unassessed",
        }:
            return "REPAIR", "main advanced across the implementation boundary"
        if queue_state == "awaiting-review":
            return "REVIEW", "implementation handoff awaits review"
        if queue_state == "ready":
            return "MERGE_READY", "integration queue marks the MR ready"
        if queue_state == "blocked":
            return "REPAIR", "integration queue is blocked"
        return "INTEGRATION", "CI or deterministic integration is active"
    if lifecycle == "integrated-post-main-verified":
        return "POST_MAIN_VERIFICATION", "merged main verification is active"
    if lifecycle == "repository-converged":
        return "DEPLOYMENT", "repository converged and downstream deployment remains"
    if lifecycle == "runtime-converged":
        return "VALIDATION", "runtime converged and validation remains"
    if lifecycle == "canonical-complete" or result == "canonical-complete":
        return "GRADUATED", "canonical completion evidence is present"
    if lifecycle == "completed" and result == "no-op-verification-completed":
        return "GRADUATED", "technical verification completed"
    if lifecycle in {"failed", "recovery-required"}:
        return "REPAIR", f"assignment requires {lifecycle} recovery"
    if lifecycle in {"blocked", "waiting", "cancelled"}:
        return "BLOCKED", f"assignment is {lifecycle}"
    if result in {"integrated-post-main-verified", "repository-converged"}:
        return "POST_MAIN_VERIFICATION", "post-main evidence awaits recognition"
    return "BACKLOG", "assignment has no executable local state"


def lane_from_graph_node(node: dict[str, Any]) -> tuple[str, str]:
    explicit = _explicit_lane(node)
    if explicit:
        return explicit, "explicit GitLab delivery lane label or note"
    authority = str((node.get("authority") or {}).get("state") or "")
    if authority in {"needs-product-owner", "needs-governance", "prohibited"}:
        return "DECISION", f"authority state is {authority}"
    if node.get("classification") == "Blocked":
        return "BLOCKED", str(
            node.get("classification_rationale") or "source item is blocked"
        )
    flow = str(node.get("flow_stage") or "backlog")
    mapping = {
        "backlog": "BACKLOG",
        "discovery": "BACKLOG",
        "decomposition-needed": "BACKLOG",
        "decision": "DECISION",
        "future": "BACKLOG",
        "convergence": "REPAIR",
        "historical": "POST_MAIN_VERIFICATION",
        "superseded": "BLOCKED",
        "analysis": "READY",
        "implementation-ready": "READY",
        "implementation": "IMPLEMENTATION",
        "integration": "INTEGRATION",
        "verification": "POST_MAIN_VERIFICATION",
        "verified-complete": "GRADUATED",
    }
    lane = mapping.get(flow, "BACKLOG")
    return lane, "; ".join(node.get("flow_evidence") or [f"graph flow is {flow}"])


def lane_from_finding(finding: dict[str, Any]) -> tuple[str, str]:
    status = str(finding.get("status") or "")
    mapping = {
        "EVIDENCE_ONLY": "VALIDATION",
        "ACTION_REQUIRED": "VALIDATION",
        "DECISION_REQUIRED": "DECISION",
        "EXTERNAL_BLOCKED": "BLOCKED",
        "EXECUTABLE": "READY",
        "ASSIGNED": "IMPLEMENTATION",
        "INTEGRATING": "INTEGRATION",
        "REPLAY_PENDING": "VALIDATION",
        "CLOSED": "GRADUATED",
        "REOPENED": "REPAIR",
        "EXTERNAL_IMPLEMENTATION_ADOPTED": "INTEGRATION",
    }
    return mapping.get(status, "BACKLOG"), f"validation finding status is {status}"


def lane_from_capability(capability: dict[str, Any]) -> tuple[str, str]:
    if capability.get("graduated"):
        return "GRADUATED", "all applicable capability gates passed"
    gate = str(capability.get("first_failing_gate") or "implementation")
    lane = {
        "implementation": "READY",
        "integration": "INTEGRATION",
        "deployment": "DEPLOYMENT",
        "validation": "VALIDATION",
        "verification": "VALIDATION",
        "operator_acceptance": "VALIDATION",
        "program_risk": "BLOCKED",
    }.get(gate, "BACKLOG")
    return lane, f"first failing capability gate is {gate}"


class DeliveryLaneProjector:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "delivery-board.json"
        self.gate = MutationGate(root, source="delivery-lane-reconciler")

    def _previous(self) -> dict[str, Any]:
        return read_record(self.path, SCHEMA) if self.path.exists() else {}

    def _assignments(self, inventory: dict[str, Any]) -> list[dict[str, Any]]:
        values = {
            str(value.get("assignment_id")): adapt_assignment(value, self.root)
            for value in inventory.get("supervisor_assignments") or []
            if value.get("assignment_id")
        }
        for path in sorted((self.root / "assignments").glob("*.json")):
            try:
                value = adapt_assignment(
                    json.loads(path.read_text(encoding="utf-8")), self.root
                )
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if value.get("assignment_id"):
                values[str(value["assignment_id"])] = value
        return list(values.values())

    @staticmethod
    def _mr_by_ref(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
        values = {}
        for mr in [
            *(inventory.get("open_merge_requests") or []),
            *(inventory.get("external_implementation_merge_requests") or []),
        ]:
            values[f"{mr.get('project')}!{mr.get('iid')}"] = mr
        for item in inventory.get("work_items") or []:
            for mr in item.get("merge_request_facts") or []:
                values.setdefault(f"{item.get('project')}!{mr.get('iid')}", mr)
        return values

    @staticmethod
    def _item(
        *,
        delivery_id: str,
        ref: str,
        source_kind: str,
        source_authority: str,
        lane: str,
        repository: str | None,
        title: str,
        reason: str,
        updated_at: str,
        assignment_id: str | None = None,
        mr_ref: str | None = None,
        capability: str | None = None,
        milestone: str | None = None,
        paths: list[str] | None = None,
        ranking_score: int = 0,
        ci_classification: str = "not-applicable",
        main_advance: str = "not-applicable",
        origin_finding: dict | None = None,
        dispatch_generation: str | None = None,
    ) -> dict[str, Any]:
        return {
            "delivery_id": delivery_id,
            "ref": ref,
            "source_kind": source_kind,
            "source_authority": source_authority,
            "lane": lane,
            "repository": repository,
            "title": title,
            "assignment_id": assignment_id,
            "mr_ref": mr_ref,
            "capability": capability,
            "milestone": milestone,
            "entered_at": updated_at,
            "updated_at": updated_at,
            "age_seconds": 0,
            "stalled": False,
            "reason": reason,
            "conflict_domains": conflict_domains(
                {"project": repository, "allowed_paths": paths or []}
            ),
            "ranking_score": int(ranking_score),
            "ci_classification": ci_classification,
            "main_advance": main_advance,
            "origin_finding": origin_finding,
            "dispatch_generation": dispatch_generation,
        }

    def build(
        self,
        inventory: dict[str, Any],
        graph: dict[str, Any],
        graduation: dict[str, Any],
        *,
        now: int | None = None,
    ) -> dict[str, Any]:
        now = int(time.time()) if now is None else now
        now_text = utc_now(now)
        previous = self._previous()
        previous_items = {
            item["delivery_id"]: item
            for lane in previous.get("lanes") or []
            for item in lane.get("items") or []
        }
        source_items = {
            str(item.get("ref")): item for item in inventory.get("work_items") or []
        }
        nodes = {str(node.get("ref")): node for node in graph.get("nodes") or []}
        assignments = self._assignments(inventory)
        integration_items = {
            str(item.get("assignment_id")): item
            for item in WorkflowState(self.root).load_queue().get("items") or []
        }
        mr_by_ref = self._mr_by_ref(inventory)
        records: dict[str, dict[str, Any]] = {}

        assignments_by_work: dict[str, list[dict[str, Any]]] = {}
        for assignment in assignments:
            assignments_by_work.setdefault(
                str(assignment.get("work_item") or ""), []
            ).append(assignment)

        for ref, node in nodes.items():
            source = source_items.get(ref) or {}
            lane, reason = lane_from_graph_node(node)
            authority = "gitlab"
            related_mrs = list(source.get("merge_request_facts") or [])
            mr = max(
                related_mrs,
                key=lambda value: int(value.get("iid") or 0),
                default=None,
            )
            mr_ref = (
                f"{source.get('project') or node.get('project')}!{mr.get('iid')}"
                if mr
                else None
            )
            mr_lane, mr_reason = lane_from_mr(mr)
            if _explicit_lane(source):
                lane = str(_explicit_lane(source))
                reason = "explicit GitLab delivery lane label or note"
            elif mr_lane:
                lane, reason = mr_lane, mr_reason
            work_assignments = assignments_by_work.get(ref) or []
            assignment = max(
                work_assignments,
                key=lambda value: int(value.get("created_at_epoch") or 0),
                default=None,
            )
            if assignment and not _explicit_lane(source):
                local_lane, local_reason = lane_from_assignment(
                    assignment, integration_items.get(str(assignment.get("assignment_id")))
                )
                locally_authoritative = not is_terminal(assignment) or local_lane in {
                    "REPAIR",
                    "BLOCKED",
                    "POST_MAIN_VERIFICATION",
                    "DEPLOYMENT",
                    "VALIDATION",
                    "GRADUATED",
                }
                if locally_authoritative and (local_lane == "REPAIR" or not mr_lane):
                    lane, reason = local_lane, local_reason
                    authority = "local-execution"
            updated = str(source.get("updated_at") or now_text)
            records[f"source:{ref}"] = self._item(
                delivery_id=f"source:{ref}",
                ref=ref,
                source_kind=str(node.get("source_kind") or "gitlab-issue"),
                source_authority=authority,
                lane=lane,
                repository=node.get("project"),
                title=str(node.get("title") or ref),
                reason=reason,
                updated_at=updated,
                assignment_id=str(assignment.get("assignment_id"))
                if assignment
                else None,
                mr_ref=mr_ref,
                milestone=node.get("milestone"),
                paths=(assignment or {}).get("allowed_paths") or [],
                ranking_score=int(node.get("ranking_score") or 0),
                ci_classification=classify_ci(mr),
                main_advance=str(
                    (integration_items.get(str((assignment or {}).get("assignment_id"))) or {}).get(
                        "main_advance"
                    )
                    or "not-applicable"
                ),
                origin_finding=(assignment or {}).get("origin_finding"),
                dispatch_generation=(assignment or {}).get("dispatch_generation"),
            )

        source_refs = set(source_items)
        orphan_assignments = []
        for assignment in assignments:
            work_item = str(assignment.get("work_item") or "")
            if work_item in records or f"source:{work_item}" in records:
                continue
            if work_item in source_refs:
                continue
            lane, reason = lane_from_assignment(
                assignment, integration_items.get(str(assignment.get("assignment_id")))
            )
            assignment_id = str(assignment.get("assignment_id"))
            delivery_id = f"assignment:{assignment_id}"
            records[delivery_id] = self._item(
                delivery_id=delivery_id,
                ref=work_item or assignment_id,
                source_kind="supervisor-assignment",
                source_authority="local-execution",
                lane=lane,
                repository=assignment.get("project"),
                title=str(assignment.get("title") or work_item or assignment_id),
                reason=reason,
                updated_at=utc_now(int(assignment.get("created_at_epoch") or now)),
                assignment_id=assignment_id,
                paths=assignment.get("allowed_paths") or [],
                ranking_score=int((assignment.get("ranking_factors") or {}).get("score") or 0),
                main_advance=str(
                    (integration_items.get(assignment_id) or {}).get("main_advance")
                    or "not-applicable"
                ),
                origin_finding=assignment.get("origin_finding"),
                dispatch_generation=assignment.get("dispatch_generation"),
            )
            if work_item and not work_item.startswith(("runtime:", "local-convergence:")):
                orphan_assignments.append(assignment_id)

        findings = ValidationFindingStore(self.root).all()
        for finding in findings:
            lane, reason = lane_from_finding(finding)
            finding_id = str(finding["finding_id"])
            records[f"finding:{finding_id}"] = self._item(
                delivery_id=f"finding:{finding_id}",
                ref=finding_id,
                source_kind="validation-finding",
                source_authority="validation",
                lane=lane,
                repository=finding.get("repository"),
                title=str(finding.get("summary") or finding_id),
                reason=reason,
                updated_at=str(finding.get("updated_at") or now_text),
                assignment_id=finding.get("assignment_id"),
                capability=finding.get("capability"),
                paths=finding.get("allowed_paths") or [],
                ranking_score=1_000 if lane in {"READY", "REPAIR"} else 0,
                origin_finding={
                    key: finding.get(key)
                    for key in (
                        "finding_id",
                        "fingerprint",
                        "classification",
                        "capability",
                        "gate",
                        "stream",
                    )
                },
            )

        capability_by_name = {
            str(value.get("capability")): value
            for value in graduation.get("capabilities") or []
        }
        for name, capability in capability_by_name.items():
            lane, reason = lane_from_capability(capability)
            records[f"capability:{name}"] = self._item(
                delivery_id=f"capability:{name}",
                ref=f"capability:{name}",
                source_kind="capability-graduation",
                source_authority="capability",
                lane=lane,
                repository="ghostspace/axis",
                title=name,
                reason=reason,
                updated_at=str(graduation.get("generated_at") or now_text),
                capability=name,
                paths=capability.get("paths") or [],
            )

        adoptions = ExternalImplementationAdoptions(self.root).load()
        for adoption in adoptions.get("records") or []:
            mr_ref = str(adoption["mr_ref"])
            mr = mr_by_ref.get(mr_ref) or adoption
            lane, reason = lane_from_mr(mr)
            if lane is None:
                lane = {
                    "awaiting-integration": "INTEGRATION",
                    "merged-awaiting-replay": "POST_MAIN_VERIFICATION",
                    "replay-pending": "VALIDATION",
                    "verified": "GRADUATED",
                    "blocked": "REPAIR",
                }.get(str(adoption.get("state")), "INTEGRATION")
                reason = f"adopted external implementation is {adoption.get('state')}"
            records[f"external:{mr_ref}"] = self._item(
                delivery_id=f"external:{mr_ref}",
                ref=mr_ref,
                source_kind="external-implementation",
                source_authority="gitlab",
                lane=lane,
                repository=adoption.get("repository"),
                title=f"Adopted external implementation {mr_ref}",
                reason=reason,
                updated_at=str(adoption.get("updated_at") or now_text),
                mr_ref=mr_ref,
                capability=", ".join(adoption.get("capabilities") or []),
                ci_classification=classify_ci(mr),
            )

        transitions = list(previous.get("transitions") or [])
        for delivery_id, item in records.items():
            prior = previous_items.get(delivery_id)
            if prior and prior.get("lane") == item["lane"]:
                item["entered_at"] = prior["entered_at"]
            else:
                if prior:
                    item["entered_at"] = now_text
                transitions.append(
                    {
                        "transition_id": _stable_id(
                            "lane-transition",
                            f"{delivery_id}:{(prior or {}).get('lane')}:{item['lane']}:{now_text}",
                        ),
                        "delivery_id": delivery_id,
                        "ref": item["ref"],
                        "from_lane": (prior or {}).get("lane"),
                        "to_lane": item["lane"],
                        "at": now_text,
                        "source_inventory_generation_id": inventory.get("generation_id"),
                    }
                )
            item["age_seconds"] = max(0, now - _epoch(item["entered_at"], now))
            threshold = STALL_SECONDS.get(item["lane"])
            item["stalled"] = bool(
                threshold is not None and item["age_seconds"] >= threshold
            )

        transitions = transitions[-2_000:]
        lane_items = {
            lane: sorted(
                (item for item in records.values() if item["lane"] == lane),
                key=lambda item: (-item["ranking_score"], item["ref"]),
            )
            for lane in LANES
        }
        worker_assignment_lanes = [
            lane_from_assignment(
                assignment,
                integration_items.get(str(assignment.get("assignment_id"))),
            )[0]
            for assignment in assignments
            if not is_terminal(assignment)
            and assignment.get("lifecycle_state")
            not in {"ready-semantic", "ready-implementation", "implementation-complete"}
        ]
        pool_in_use = Counter(
            LANE_WORKER_POOL[lane]
            for lane in worker_assignment_lanes
            if LANE_WORKER_POOL[lane]
        )
        worker_pools = [
            {
                "pool": pool,
                "capacity": capacity,
                "in_use": pool_in_use[pool],
                "available": max(0, capacity - pool_in_use[pool]),
                "lanes": [
                    lane for lane, lane_pool in LANE_WORKER_POOL.items() if lane_pool == pool
                ],
            }
            for pool, capacity in WORKER_POOL_CAPACITY.items()
        ]
        pool_by_name = {value["pool"]: value for value in worker_pools}
        lanes = [
            {
                "lane": lane,
                "worker_pool": LANE_WORKER_POOL[lane],
                "capacity": LANE_CAPACITY[lane],
                "wip": len(lane_items[lane]),
                "available": (
                    pool_by_name[LANE_WORKER_POOL[lane]]["available"]
                    if LANE_WORKER_POOL[lane]
                    else None
                ),
                "items": lane_items[lane],
            }
            for lane in LANES
        ]

        frontier = (
            read_record(
                self.root / "executable-frontier.json",
                "axis.external-development-supervisor.executable-frontier",
            )
            if (self.root / "executable-frontier.json").exists()
            else {"selected": []}
        )
        queue_by_ref = {
            str(entry.get("ref")): entry for entry in graph.get("executable_queue") or []
        }
        active = [assignment for assignment in assignments if not is_terminal(assignment)]
        downstream_active = [
            assignment
            for assignment in active
            if lane_from_assignment(
                assignment,
                integration_items.get(str(assignment.get("assignment_id"))),
            )[0]
            in {
                "REVIEW",
                "INTEGRATION",
                "MERGE_READY",
                "POST_MAIN_VERIFICATION",
            }
        ]
        control = read_record(
            self.root / "control.json",
            "axis.external-development-supervisor.control",
        )
        global_available = max(
            0, int(control.get("max_active_assignments", 1)) - len(active)
        )
        implementation_available = min(
            global_available, pool_by_name["implementation"]["available"]
        )
        mission = (
            read_mission_record(self.root / "active-mission.json")
            if (self.root / "active-mission.json").exists()
            else {}
        )
        mission_dispatch_refs = {
            str(action.get("source_ref"))
            for action in mission.get("generated_actions") or []
            if action.get("kind") == "dispatch-executable"
            and action.get("source_ref")
        }
        refill_candidates = [
            ref
            for ref in frontier.get("selected") or []
            if (queue_by_ref.get(ref) or {}).get("assignment_type")
            in {"governance-document-mutation", "code-implementation", "ci-integration-repair"}
            and (not mission_dispatch_refs or ref in mission_dispatch_refs)
        ]
        generation_b = refill_candidates[:implementation_available]
        primary_refs = [
            str(ref)
            for ref in frontier.get("selected") or []
            if not mission_dispatch_refs or str(ref) in mission_dispatch_refs
        ]
        dispatch_generations = {
            "A": {
                "active_assignment_ids": sorted(
                    str(value.get("assignment_id")) for value in active
                ),
                "selected_refs": [] if downstream_active else primary_refs,
                "reason": "primary mission and scheduler selection",
            },
            "B": {
                "eligible": bool(downstream_active and implementation_available),
                "selected_refs": generation_b if downstream_active else [],
                "available_capacity": implementation_available,
                "downstream_assignment_ids": sorted(
                    str(value.get("assignment_id")) for value in downstream_active
                ),
                "reason": (
                    "downstream work released compatible implementation capacity"
                    if downstream_active and implementation_available
                    else "no compatible downstream refill capacity"
                ),
            },
        }

        graduated_transitions = [
            value for value in transitions if value.get("to_lane") == "GRADUATED"
        ]
        lead_times = []
        first_seen = {}
        for transition in transitions:
            delivery_id = transition["delivery_id"]
            entered = _epoch(transition["at"], now)
            first_seen.setdefault(delivery_id, entered)
            if transition.get("to_lane") == "GRADUATED":
                lead_times.append(max(0, entered - first_seen[delivery_id]))
        flow_metrics = {
            "total_items": len(records),
            "active_wip": sum(
                len(lane_items[lane])
                for lane in LANES
                if lane not in {"BACKLOG", "GRADUATED", "BLOCKED"}
            ),
            "stalled": sum(item["stalled"] for item in records.values()),
            "blocked": len(lane_items["BLOCKED"]),
            "graduated": len(lane_items["GRADUATED"]),
            "graduated_last_24h": sum(
                _epoch(value["at"], 0) >= now - 86_400
                for value in graduated_transitions
            ),
            "average_lead_time_seconds": round(sum(lead_times) / len(lead_times))
            if lead_times
            else None,
            "lane_wip": {lane: len(lane_items[lane]) for lane in LANES},
            "lane_average_age_seconds": {
                lane: round(
                    sum(item["age_seconds"] for item in lane_items[lane])
                    / len(lane_items[lane])
                )
                if lane_items[lane]
                else 0
                for lane in LANES
            },
            "generation_b_refill_count": len(generation_b if downstream_active else []),
            "milestone_lane_wip": {
                milestone: {
                    lane: sum(
                        item.get("milestone") == milestone
                        for item in lane_items[lane]
                    )
                    for lane in LANES
                    if any(
                        item.get("milestone") == milestone
                        for item in lane_items[lane]
                    )
                }
                for milestone in sorted(
                    {
                        str(item["milestone"])
                        for item in records.values()
                        if item.get("milestone")
                    }
                )
            },
        }
        source_reconciliation = {
            "gitlab_source_items": len(source_items),
            "local_assignments": len(assignments),
            "external_implementations": len(adoptions.get("records") or []),
            "validation_findings": len(findings),
            "capabilities": len(capability_by_name),
            "orphan_assignment_ids": sorted(orphan_assignments),
            "stale_assignment_generation_ids": sorted(
                str(value.get("assignment_id"))
                for value in assignments
                if value.get("source_inventory_generation_id")
                and value.get("source_inventory_generation_id")
                != inventory.get("generation_id")
                and not is_terminal(value)
            ),
            "duplicate_delivery_ids": len(records) != len(set(records)),
            "mission_id": mission.get("mission_id"),
        }
        value = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "generation_id": str(uuid.uuid4()),
            "generation_number": int(previous.get("generation_number") or 0) + 1,
            "generated_at": now_text,
            "source_inventory_generation_id": inventory.get("generation_id"),
            "source_graph_generation_id": graph.get("generation_id"),
            "lanes": lanes,
            "worker_pools": worker_pools,
            "dispatch_generations": dispatch_generations,
            "stalled_items": sorted(
                (item for item in records.values() if item["stalled"]),
                key=lambda item: (-item["age_seconds"], item["ref"]),
            ),
            "transitions": transitions,
            "source_reconciliation": source_reconciliation,
            "flow_metrics": flow_metrics,
        }
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(self.path, value, SCHEMA)
        return value
