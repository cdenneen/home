#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from axis_supervisor.command_registry import command_specs, parse_command, usage
from axis_supervisor.lifecycle import is_terminal
from axis_supervisor.mutation import MutationGate, OperationClass
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
    if command == "running":
        assignments = [
            item
            for item in inventory.get("supervisor_assignments") or []
            if not is_terminal(item)
        ]
        print(
            json.dumps(
                with_semantic_metadata(
                    {"command": "running", "count": len(assignments), "items": assignments},
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
            node.get("semantic_record", {}).get("decision_packet")
            for node in graph.get("nodes") or []
            if node.get("semantic_record", {}).get("decision_packet")
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
        recent = sorted(
            [
                {
                    "ref": node.get("ref"),
                    "revalidated_at": (node.get("semantic_record") or {}).get(
                        "revalidated_at"
                    ),
                    "classification": node.get("classification"),
                }
                for node in graph.get("nodes") or []
                if (node.get("semantic_record") or {}).get("revalidated_at")
            ],
            key=lambda item: str(item.get("revalidated_at") or ""),
            reverse=True,
        )[:10]
        print(
            json.dumps(
                with_semantic_metadata({
                    "command": "recent",
                    "items": recent,
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
