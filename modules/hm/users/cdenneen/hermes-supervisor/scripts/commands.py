#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

from axis_supervisor.capability_graduation import read_capability_graduation
from axis_supervisor.command_registry import command_specs, parse_command, usage
from axis_supervisor.dashboard import PRODUCT_CAPABILITIES, public_text
from axis_supervisor.decisions import DecisionStore
from axis_supervisor.missions import read_mission_record
from axis_supervisor.observability import OperationalEventLog
from axis_supervisor.reporting import build_roadmap_semantics, require_current_sources
from axis_supervisor.schema_registry import read_record

ROOT = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_ROOT",
        Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor",
    )
)


def load(name: str, schema: str) -> dict:
    return read_record(ROOT / name, schema)


def load_optional(name: str, schema: str) -> dict:
    path = ROOT / name
    return read_record(path, schema) if path.exists() else {}


def with_semantic_metadata(result: dict, semantics: dict) -> dict:
    return result | {
        "semantic_revision": semantics["semantic_revision"],
        "generated_at": semantics["generated_at"],
        "source_inventory_revision": semantics["source"]["inventory_revision"],
        "staleness": semantics["staleness"],
    }


def capability_projection() -> dict:
    return load_optional(
        "capability-convergence.json",
        "axis.external-development-supervisor.capability-convergence",
    )


def graduation_projection() -> dict:
    path = ROOT / "capability-graduation.json"
    return read_capability_graduation(path) if path.exists() else {}


def mission_projection() -> dict:
    path = ROOT / "active-mission.json"
    return read_mission_record(path) if path.exists() else {}


def public_runtime_status(value: dict, *, offline: bool = False) -> str:
    if offline:
        return "offline"
    if value.get("status") == "converged" and value.get(
        "verification_status"
    ) == "verified":
        return "verified"
    if value.get("status") == "unknown":
        return "validation-unavailable"
    return public_text(value.get("status") or "not-observed").lower().replace(" ", "-")


def milestone_projection(roadmap: dict, graduation: dict) -> list[dict]:
    evidence = {
        str(value.get("milestone")): value
        for value in graduation.get("milestones") or []
    }
    return [
        value
        | {
            "dimensions": (evidence.get(str(value.get("key"))) or {}).get(
                "dimensions"
            )
            or {},
            "production_confidence": (
                evidence.get(str(value.get("key"))) or {}
            ).get("production_confidence"),
            "operator_confidence": (
                evidence.get(str(value.get("key"))) or {}
            ).get("operator_confidence"),
            "program_risk": (evidence.get(str(value.get("key"))) or {}).get(
                "program_risk"
            )
            or {},
            "debts": (evidence.get(str(value.get("key"))) or {}).get("debts")
            or [],
            "constraint": (evidence.get(str(value.get("key"))) or {}).get(
                "constraint"
            ),
        }
        for value in roadmap.get("complete_roadmap") or []
    ]


def capability_items(graduation: dict) -> list[dict]:
    records = {
        str(value.get("capability")): value
        for value in graduation.get("capabilities") or []
    }
    return [
        {
            "product_capability": label,
            "capability": name,
            "product_subdimensions": (records.get(name) or {}).get(
                "product_subdimensions"
            )
            or {},
            "production_confidence": (records.get(name) or {}).get(
                "production_confidence"
            ),
            "operator_confidence": (records.get(name) or {}).get(
                "operator_confidence"
            ),
            "graduated": bool((records.get(name) or {}).get("graduated")),
            "first_failing_gate": (records.get(name) or {}).get(
                "first_failing_gate"
            ),
            "program_risk": (records.get(name) or {}).get("program_risk") or {},
            "graduation_state": (records.get(name) or {}).get("graduation_state")
            or {},
            "linked_work_items": (records.get(name) or {}).get("linked_work_items")
            or [],
            "projected_runtimes": (records.get(name) or {}).get(
                "projected_runtimes"
            )
            or [],
        }
        for label, name in PRODUCT_CAPABILITIES
    ]


def pending_decisions(graph: dict, root: Path | None = None) -> list[dict]:
    store = DecisionStore(root or ROOT)
    return [
        {
            key: packet.get(key)
            for key in (
                "decision_id",
                "current_digest",
                "decision_requested",
                "recommendation",
                "consequences",
                "downstream_effects",
                "response_syntax",
            )
        }
        for node in graph.get("nodes") or []
        if isinstance(
            packet := (node.get("semantic_record") or {}).get("decision_packet"),
            dict,
        )
        and store.load(str(packet.get("decision_id") or node.get("ref") or ""))
        is None
    ]


def deployment_items(convergence: dict) -> list[dict]:
    runtimes = {
        str(value.get("runtime")): value
        for value in convergence.get("runtimes") or []
    }
    ghost = runtimes.get("ghost") or {}
    web_verified = bool(
        ghost.get("verification_status") == "verified"
        and "Web Presentation" not in (ghost.get("capabilities_behind") or [])
    )

    def runtime_item(
        label: str, name: str, *, offline: bool = False, required: bool = True
    ) -> dict:
        value = runtimes.get(name) or {}
        return {
            "ring": label,
            "runtime": name,
            "status": public_runtime_status(value, offline=offline),
            "display_state": "gray" if offline else "normal",
            "required": required,
            "production_revision": value.get("running_revision"),
            "capability_gaps": value.get("capabilities_behind") or [],
        }

    return [
        runtime_item("Ghost Runtime", "ghost"),
        {
            "ring": "Web",
            "runtime": "ghost-web",
            "status": "verified" if web_verified else "validation-pending",
            "display_state": "normal",
            "required": True,
            "production_revision": ghost.get("running_revision"),
            "capability_gaps": []
            if web_verified
            else ["Web Presentation"],
        },
        runtime_item("Nyx", "nyx"),
        runtime_item("macbookpro", "macbookpro"),
        runtime_item("mbair", "mbair", offline=True, required=False),
    ]


def emit(result: dict, roadmap: dict) -> int:
    print(json.dumps(with_semantic_metadata(result, roadmap), sort_keys=True))
    return 0


def main() -> int:
    text = " ".join(sys.argv[1:]).strip()
    parsed = parse_command(text or "help")
    if parsed is None:
        raise ValueError(f"unsupported command or parameters: {text}")
    spec, argument = parsed
    command = spec["handler_key"]
    inventory = load("inventory.json", "axis.external-development-supervisor.inventory")
    graph = load(
        "execution-graph.json", "axis.external-development-supervisor.execution-graph"
    )
    control = load("control.json", "axis.external-development-supervisor.control")
    require_current_sources(inventory, graph)
    revision_path = ROOT / "deployed-source-revision.json"
    deployed_revision = (
        json.loads(revision_path.read_text(encoding="utf-8"))
        if revision_path.is_file()
        else {}
    )
    roadmap = build_roadmap_semantics(inventory, graph, control, deployed_revision)
    graduation = graduation_projection()
    convergence = capability_projection()
    mission = mission_projection()
    milestones = milestone_projection(roadmap, graduation)
    delivery_board_path = ROOT / "delivery-board.json"
    delivery_board = (
        load("delivery-board.json", "axis.external-development-supervisor.delivery-board")
        if delivery_board_path.exists()
        else {}
    )

    if command == "help":
        return emit(
            {
                "command": "help",
                "supported": [usage(item) for item in command_specs()],
                "registry": [
                    {
                        "command": item["command"],
                        "aliases": list(item["aliases"]),
                        "description": item["description"],
                        "params": list(item["params"]),
                        "authority": item["authority"],
                        "confirmation": item["confirmation"],
                    }
                    for item in command_specs()
                ],
            },
            roadmap,
        )
    if command == "status":
        primary = graduation.get("primary_kpi") or {}
        return emit(
            {
                "command": "status",
                "primary_kpi": primary,
                "roadmap_progress": {
                    "verified": ((roadmap.get("composition") or {}).get(
                        "verified_complete"
                    ) or {}).get("count", 0),
                    "total": roadmap.get("total_governed_items", 0),
                    "frontier": roadmap.get("current_execution_frontier"),
                },
                "production_confidence": graduation.get("production_confidence"),
                "operator_confidence": graduation.get("operator_confidence"),
                "program_risk": graduation.get("program_risk") or {},
                "active_product_work": len(mission.get("generated_actions") or []),
                "deployment_ring": deployment_items(convergence),
                "pending_decisions": len(pending_decisions(graph)),
            },
            roadmap,
        )
    if command == "roadmap":
        return emit(
            {
                "command": "roadmap",
                "endpoint": roadmap.get("roadmap_endpoint"),
                "frontier": roadmap.get("current_execution_frontier"),
                "progress": (roadmap.get("composition") or {}).get(
                    "verified_complete"
                )
                or {},
                "milestones": milestones,
            },
            roadmap,
        )
    if command == "milestones":
        return emit(
            {
                "command": "milestones",
                "frontier": roadmap.get("current_execution_frontier"),
                "items": milestones,
            },
            roadmap,
        )
    if command == "milestone":
        item = next(
            (
                value
                for value in milestones
                if str(value.get("key") or "").lower() == argument.lower()
            ),
            None,
        )
        return emit(
            {"command": "milestone", "item": item}
            if item
            else {"command": "milestone", "error": "milestone not found"},
            roadmap,
        )
    if command in {"capabilities", "capability"}:
        items = capability_items(graduation)
        if command == "capability":
            item = next(
                value
                for value in items
                if value["product_capability"].lower() == argument.lower()
            )
            related_actions = [
                {
                    "target": action.get("target"),
                    "gate": action.get("gate"),
                    "reason": public_text(action.get("reason")),
                    "expected_evidence": [
                        public_text(value)
                        for value in action.get("expected_evidence") or []
                    ],
                    "merge_impact_projection": action.get(
                        "merge_impact_projection"
                    )
                    or {},
                }
                for action in mission.get("generated_actions") or []
                if item["capability"] in (action.get("expected_capabilities") or [])
            ]
            return emit(
                {
                    "command": "capability",
                    "item": item,
                    "generated_actions": related_actions,
                    "merge_impact_projection": [
                        value
                        for value in graduation.get("merge_impact_projection") or []
                        if item["capability"]
                        in (value.get("affected_capabilities") or [])
                    ],
                },
                roadmap,
            )
        return emit(
            {
                "command": "capabilities",
                "primary_kpi": graduation.get("primary_kpi") or {},
                "production_confidence": graduation.get("production_confidence"),
                "operator_confidence": graduation.get("operator_confidence"),
                "items": items,
            },
            roadmap,
        )
    if command == "deployments":
        items = deployment_items(convergence)
        required = [value for value in items if value["required"]]
        return emit(
            {
                "command": "deployments",
                "verified": sum(value["status"] == "verified" for value in required),
                "total": len(required),
                "optional": len(items) - len(required),
                "items": items,
            },
            roadmap,
        )
    if command == "validation":
        streams = graduation.get("validation_streams") or []
        return emit(
            {
                "command": "validation",
                "promoted": sum(
                    value.get("status") == "evidence-promoted" for value in streams
                ),
                "total": len(streams),
                "items": streams,
            },
            roadmap,
        )
    if command == "flow":
        return emit(
            {
                "command": "flow",
                "generation": delivery_board.get("generation_number"),
                "lanes": delivery_board.get("lanes") or [],
                "worker_pools": delivery_board.get("worker_pools") or [],
                "stalled_items": delivery_board.get("stalled_items") or [],
                "dispatch_generations": delivery_board.get("dispatch_generations")
                or {},
                "flow_metrics": delivery_board.get("flow_metrics") or {},
                "source_reconciliation": delivery_board.get("source_reconciliation")
                or {},
            },
            roadmap,
        )
    if command == "risk":
        return emit(
            {
                "command": "risk",
                "program_risk": graduation.get("program_risk") or {},
                "milestones": [
                    {
                        "milestone": value.get("key"),
                        "risk": value.get("program_risk") or {},
                        "debt": value.get("debts") or [],
                        "constraint": value.get("constraint"),
                    }
                    for value in milestones
                ],
            },
            roadmap,
        )
    if command == "decisions":
        items = pending_decisions(graph)
        return emit(
            {"command": "decisions", "count": len(items), "items": items[:20]},
            roadmap,
        )
    if command == "recent":
        events = OperationalEventLog(ROOT, "reporter").events(limit=50)
        items = [
            {
                "activity": public_text(event.get("event_type")).replace("_", " "),
                "ref": event.get("work_item"),
            }
            for event in reversed(events)
            if event.get("event_type")
            in {
                "implementation_completed",
                "mr_created",
                "mr_merged",
                "post_main_verified",
                "capability_deployment_verified",
                "assignment_retry",
            }
        ][:10]
        return emit({"command": "recent", "items": items}, roadmap)
    if command == "inspect":
        parts = argument.split()
        product_ref = parts[0]
        privileged_view = parts[1].lower() if len(parts) == 2 else None
        item = next(
            (
                value
                for value in graph.get("nodes") or []
                if value.get("ref") == product_ref
            ),
            None,
        )
        if item is None:
            return emit(
                {"command": "inspect", "error": "product item not found"}, roadmap
            )
        capability_context = [
            value
            for value in graduation.get("capabilities") or []
            if product_ref in (value.get("linked_work_items") or [])
        ]
        capability_names = {
            str(value.get("capability")) for value in capability_context
        }
        actions = [
            value
            for value in mission.get("generated_actions") or []
            if value.get("target") == product_ref
            or capability_names.intersection(value.get("expected_capabilities") or [])
        ]
        merge_impacts = [
            value
            for value in graduation.get("merge_impact_projection") or []
            if value.get("target_ref") == product_ref
        ]
        result = {
            "command": "inspect",
            "view": privileged_view or "summary",
            "summary": {
                "ref": product_ref,
                "title": public_text(item.get("title")),
                "milestone": item.get("milestone"),
                "product_state": public_text(
                    item.get("classification") or item.get("flow_stage") or "unknown"
                ),
                "capabilities": sorted(capability_names),
                "evidence_state": public_text(
                    (item.get("verification") or {}).get("state") or "pending"
                ),
                "active_product_actions": len(actions),
                "projected_merge_impacts": len(merge_impacts),
            },
        }
        if privileged_view == "details":
            result["details"] = item
            result["privileged_summary"] = {
                "source_url": item.get("web_url"),
                "dependency_count": len(item.get("dependencies") or []),
                "acceptance_present": bool(item.get("acceptance_criteria_present")),
            }
        elif privileged_view == "evidence":
            result["evidence"] = {
                "verification": item.get("verification") or {},
                "semantic_record": item.get("semantic_record"),
                "capability_context": capability_context,
                "generated_actions": actions,
                "merge_impact_projection": merge_impacts,
            }
            verification = item.get("verification") or {}
            result["privileged_summary"] = {
                "verification_state": public_text(
                    verification.get("state") or "pending"
                ),
                "failed_check_count": len(verification.get("failed_checks") or []),
                "evidence_reference_count": len(verification.get("evidence") or []),
                "capability_count": len(capability_context),
                "product_action_count": len(actions),
                "merge_impact_count": len(merge_impacts),
            }
        return emit(result, roadmap)
    raise ValueError(f"unsupported command handler: {command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
