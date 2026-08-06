#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from axis_supervisor.command_registry import command_specs, parse_command, usage
from axis_supervisor.dashboard import progress_bar, public_text
from axis_supervisor.lifecycle import is_terminal
from axis_supervisor.mutation import MutationGate, OperationClass
from axis_supervisor.observability import OperationalEventLog
from axis_supervisor.reporting import build_roadmap_semantics, require_current_sources
from axis_supervisor.schema_registry import read_record, write_record

ROOT = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_ROOT",
        Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor",
    )
)


def load(name: str, schema: str) -> dict:
    return read_record(ROOT / name, schema)


def save_control(value: dict, gate: MutationGate, decision) -> None:
    gate.require(decision, OperationClass.CONTROL)
    write_record(
        ROOT / "control.json", value, "axis.external-development-supervisor.control"
    )


def with_semantic_metadata(result: dict, semantics: dict) -> dict:
    return result | {
        "semantic_revision": semantics["semantic_revision"],
        "generated_at": semantics["generated_at"],
        "source_inventory_revision": semantics["source"]["inventory_revision"],
        "staleness": semantics["staleness"],
    }


def scheduler_summary(semantics: dict) -> dict:
    scheduler = semantics.get("scheduler_state") or {}
    return {
        "configured_batch_ceiling": scheduler.get("configured_batch_ceiling"),
        "available_model_call_budget": scheduler.get("available_model_call_budget"),
        "selected_batch": scheduler.get("selected_batch") or [],
        "deferred_count": len(scheduler.get("deferred_items") or []),
        "next_eligible_work": scheduler.get("next_eligible_work"),
        "limiting_constraint": scheduler.get("limiting_constraint"),
    }


def capability_projection() -> dict:
    path = ROOT / "capability-convergence.json"
    if not path.exists():
        return {}
    return read_record(
        path, "axis.external-development-supervisor.capability-convergence"
    )


def public_runtime_status(value: dict) -> str:
    if value.get("runtime") == "mbair":
        return "offline"
    if value.get("status") == "converged" and value.get("verification_status") == "verified":
        return "verified"
    if value.get("status") == "unknown":
        return "validation-unavailable"
    return public_text(value.get("status") or "not-observed").lower().replace(" ", "-")


def main() -> int:
    text = " ".join(sys.argv[1:]).strip()
    parsed = parse_command(text or "help")
    if parsed is None:
        raise ValueError(f"unsupported command or parameters: {text}")
    spec, argument = parsed
    command = spec["handler_key"]
    gate = MutationGate(
        ROOT, source=os.environ.get("AXIS_SUPERVISOR_COMMAND_SOURCE", "operator-cli")
    )
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
    roadmap = build_roadmap_semantics(
        inventory, graph, control, deployed_revision
    )

    if command == "help":
        print(
            json.dumps(
                with_semantic_metadata({
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
                }, roadmap),
                sort_keys=True,
            )
        )
        return 0
    if command == "status":
        print(
            json.dumps(
                with_semantic_metadata({
                    "command": "status",
                    "mode": control.get("mode"),
                    "allow_repository_mutation": control.get(
                        "allow_repository_mutation"
                    ),
                    "governed_items": roadmap.get("total_governed_items"),
                    "composition": roadmap.get("composition") or {},
                    "supervisor_work": roadmap.get("supervisor_work") or {},
                    "current_execution_frontier": roadmap.get(
                        "current_execution_frontier"
                    ),
                    "current_supervisor_focus": roadmap.get(
                        "current_supervisor_focus"
                    )
                    or {},
                    "governed_queue_zero_proven": graph.get(
                        "governed_queue_zero_proven"
                    ),
                    "confidence": (roadmap.get("coverage") or {}).get(
                        "inventory_classified"
                    ),
                    "scheduler_state": scheduler_summary(roadmap),
                }, roadmap),
                sort_keys=True,
            )
        )
        return 0
    if command == "roadmap":
        print(
            json.dumps(
                with_semantic_metadata({
                    "command": "roadmap",
                    "roadmap_endpoint": roadmap.get("roadmap_endpoint"),
                    "current_execution_frontier": roadmap.get(
                        "current_execution_frontier"
                    ),
                    "current_supervisor_focus": roadmap.get(
                        "current_supervisor_focus"
                    )
                    or {},
                    "composition": roadmap.get("composition") or {},
                    "supervisor_work": roadmap.get("supervisor_work") or {},
                    "complete_roadmap": roadmap.get("complete_roadmap") or [],
                    "strategic_programs": roadmap.get("strategic_programs") or [],
                    "scheduler_state": scheduler_summary(roadmap),
                }, roadmap),
                sort_keys=True,
            )
        )
        return 0
    if command == "milestones":
        print(
            json.dumps(
                with_semantic_metadata({
                    "command": "milestones",
                    "current_execution_frontier": roadmap.get(
                        "current_execution_frontier"
                    ),
                    "milestones": roadmap.get("complete_roadmap") or [],
                }, roadmap),
                sort_keys=True,
            )
        )
        return 0
    if command == "milestone":
        milestone = next(
            (
                value
                for value in roadmap.get("complete_roadmap") or []
                if str(value.get("key") or "").lower() == argument.lower()
            ),
            None,
        )
        result = (
            {"command": "milestone", "item": milestone}
            if milestone
            else {"command": "milestone", "error": "milestone not found"}
        )
        print(json.dumps(with_semantic_metadata(result, roadmap), sort_keys=True))
        return 0
    if command == "running":
        assignments = [
            item
            for item in inventory.get("supervisor_assignments") or []
            if not is_terminal(item)
        ]
        items = []
        for item in assignments:
            assignment_type = str(item.get("assignment_type") or "")
            focus = (
                "deployment"
                if assignment_type == "capability-deployment"
                else "validation"
                if assignment_type in {"read-only-analysis", "no-op-verification"}
                else "review"
                if item.get("lifecycle_state") == "awaiting-integration"
                else "engineering"
            )
            items.append(
                {
                    "ref": item.get("work_item") or item.get("target_ref"),
                    "title": item.get("title"),
                    "focus": focus,
                }
            )
        print(
            json.dumps(
                with_semantic_metadata(
                    {"command": "running", "count": len(items), "items": items},
                    roadmap,
                ),
                sort_keys=True,
            )
        )
        return 0
    if command == "blocked":
        all_items = [
            {
                "ref": item.get("ref"),
                "title": item.get("title"),
                "classification": item.get("classification"),
                "rationale": item.get("classification_rationale"),
            }
            for item in graph.get("nodes") or []
            if item.get("classification") in {"Blocked", "Waiting"}
        ]
        items = all_items[:50]
        print(
            json.dumps(
                with_semantic_metadata(
                    {"command": "blocked", "count": len(all_items), "items": items},
                    roadmap,
                ),
                sort_keys=True,
            )
        )
        return 0
    if command == "decisions":
        decisions = [
            {
                key: packet.get(key)
                for key in (
                    "decision_id",
                    "current_digest",
                    "decision_requested",
                    "recommendation",
                    "consequences",
                )
            }
            for node in graph.get("nodes") or []
            if isinstance(
                packet := (node.get("semantic_record") or {}).get("decision_packet"),
                dict,
            )
        ]
        print(
            json.dumps(
                with_semantic_metadata({
                    "command": "decisions",
                    "count": len(decisions),
                    "items": decisions[:20],
                }, roadmap),
                sort_keys=True,
            )
        )
        return 0
    if command in {"deployments", "validation", "capabilities"}:
        capability = capability_projection()
        runtimes = capability.get("runtimes") or []
        if command == "deployments":
            items = [
                {
                    "runtime": value.get("runtime"),
                    "surface": "axis-node"
                    if value.get("runtime") == "nyx"
                    else "axis-desktop"
                    if value.get("runtime") in {"macbookpro", "mbair"}
                    else "runtime-and-web",
                    "status": public_runtime_status(value),
                    "capability_gaps": len(value.get("capabilities_behind") or []),
                }
                for value in runtimes
            ]
            result = {
                "command": command,
                "verified": sum(value["status"] == "verified" for value in items),
                "total": max(4, len(items)),
                "items": items,
            }
        elif command == "validation":
            items = [
                {
                    "runtime": value.get("runtime"),
                    "status": "offline"
                    if value.get("runtime") == "mbair"
                    else "verified"
                    if value.get("verification_status") == "verified"
                    else "pending",
                    "health": public_text(value.get("health") or "not-observed"),
                }
                for value in runtimes
            ]
            result = {
                "command": command,
                "verified": sum(value["status"] == "verified" for value in items),
                "total": max(4, len(items)),
                "items": items,
            }
        else:
            runtime_by_name = {
                str(value.get("runtime")): value for value in runtimes
            }
            items = []
            passed = total_gates = 0
            for value in capability.get("capabilities") or []:
                projected = value.get("projected_runtimes") or []
                capability_passed = 0
                for runtime_name in projected:
                    runtime = runtime_by_name.get(str(runtime_name)) or {}
                    total_gates += 1
                    if (
                        runtime_name != "mbair"
                        and runtime.get("status") == "converged"
                        and runtime.get("verification_status") == "verified"
                        and value.get("capability")
                        not in (runtime.get("capabilities_behind") or [])
                    ):
                        capability_passed += 1
                        passed += 1
                items.append(
                    {
                        "capability": value.get("capability"),
                        "passed": capability_passed,
                        "total": len(projected),
                    }
                )
            result = {
                "command": command,
                "passed": passed,
                "total": total_gates,
                "progress": progress_bar(passed, total_gates),
                "items": items,
            }
        print(json.dumps(with_semantic_metadata(result, roadmap), sort_keys=True))
        return 0
    if command == "inspect":
        item = next((item for item in graph.get("nodes") or [] if item.get("ref") == argument), None)
        node = next(
            (node for node in graph.get("nodes") or [] if node.get("ref") == argument),
            {},
        )
        result = (
            {
                "command": "inspect",
                "item": {
                    key: item.get(key)
                    for key in (
                        "ref",
                        "title",
                        "source_state",
                        "classification",
                        "classification_rationale",
                        "milestone",
                        "dependencies",
                        "web_url",
                    )
                },
                "verification": node.get("verification") or {},
                "revalidation_tier": node.get("revalidation_tier"),
            }
            if item
            else {"command": "inspect", "error": "work item not found"}
        )
        print(json.dumps(with_semantic_metadata(result, roadmap), sort_keys=True))
        return 0
    if command == "recent":
        events = OperationalEventLog(ROOT, "reporter").events(limit=50)
        no_op_count = len(
            {
                event.get("assignment_id")
                for event in events
                if (event.get("details") or {}).get("assignment_type")
                == "no-op-verification"
                and event.get("assignment_id")
            }
        )
        recent = [
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
        print(
            json.dumps(
                with_semantic_metadata({
                    "command": "recent",
                    "items": recent,
                    "routine_unchanged_evidence_checks": no_op_count,
                }, roadmap),
                sort_keys=True,
            )
        )
        return 0
    if command in {"pause", "resume", "drain"}:
        decision = gate.decide(OperationClass.CONTROL)
        control["mode"] = {
            "pause": "observing",
            "resume": "enabled",
            "drain": "draining",
        }[command]
        control["allow_repository_mutation"] = False
        save_control(control, gate, decision)
        roadmap = build_roadmap_semantics(inventory, graph, control)
        print(
            json.dumps(
                with_semantic_metadata({
                    "command": command,
                    "mode": control["mode"],
                    "allow_repository_mutation": control[
                        "allow_repository_mutation"
                    ],
                }, roadmap),
                sort_keys=True,
            )
        )
        return 0
    if command == "reconcile":
        decision = gate.decide(OperationClass.RECONCILE)
        gate.require(decision, OperationClass.RECONCILE)
        hermes = shutil.which("hermes") or str(Path.home() / ".nix-profile/bin/hermes")
        subprocess.Popen(
            [hermes, "cron", "run", str(control.get("cron_job_id"))],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        print(
            json.dumps(
                with_semantic_metadata({
                    "command": "reconcile",
                    "triggered": control.get("cron_job_id"),
                }, roadmap),
                sort_keys=True,
            )
        )
        return 0
    raise ValueError(f"unsupported command handler: {command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
