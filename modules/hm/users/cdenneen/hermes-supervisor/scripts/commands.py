#!/usr/bin/env python3
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor"


def load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def save_control(value: dict) -> None:
    path = ROOT / "control.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def load_optional(name: str) -> dict:
    path = ROOT / name
    return load(name) if path.is_file() else {}


def main() -> int:
    text = " ".join(sys.argv[1:]).strip()
    command, _, argument = text.partition(" ")
    command = command.lower()
    inventory = load("inventory.json")
    graph = load("execution-graph.json")
    control = load("control.json")
    roadmap = load_optional("slack-overview-record.json")

    if command in {"help", "commands"}:
        print(
            json.dumps(
                {
                    "command": "help",
                    "supported": [
                        "status",
                        "roadmap",
                        "milestones",
                        "running",
                        "blocked",
                        "decisions",
                        "recent",
                        "inspect <group/project#iid>",
                        "reconcile",
                        "pause",
                        "resume",
                        "drain",
                    ],
                },
                sort_keys=True,
            )
        )
        return 0
    if command == "status":
        print(
            json.dumps(
                {
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
                    "confidence": inventory.get("roadmap_confidence"),
                },
                sort_keys=True,
            )
        )
        return 0
    if command == "roadmap":
        print(
            json.dumps(
                {
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
                },
                sort_keys=True,
            )
        )
        return 0
    if command == "milestones":
        print(
            json.dumps(
                {
                    "command": "milestones",
                    "current_execution_frontier": roadmap.get(
                        "current_execution_frontier"
                    ),
                    "milestones": roadmap.get("complete_roadmap") or [],
                },
                sort_keys=True,
            )
        )
        return 0
    if command == "running":
        assignments = [
            item
            for item in inventory.get("supervisor_assignments") or []
            if item.get("state")
            not in {"complete", "completed", "cancelled", "failed"}
        ]
        print(
            json.dumps(
                {"command": "running", "count": len(assignments), "items": assignments},
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
            for item in inventory.get("work_items") or []
            if item.get("classification") in {"Blocked", "Waiting"}
        ]
        items = all_items[:50]
        print(
            json.dumps(
                {"command": "blocked", "count": len(all_items), "items": items},
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
                {
                    "command": "decisions",
                    "count": len(decisions),
                    "items": decisions[:20],
                },
                sort_keys=True,
            )
        )
        return 0
    if command == "inspect":
        item = next(
            (item for item in inventory.get("work_items") or [] if item.get("ref") == argument),
            None,
        )
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
                        "state",
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
        print(json.dumps(result, sort_keys=True))
        return 0
    if command == "recent":
        print(
            json.dumps(
                {
                    "command": "recent",
                    "items": (inventory.get("activity_timeline") or [])[:10],
                },
                sort_keys=True,
            )
        )
        return 0
    if command in {"pause", "resume", "drain"}:
        control["mode"] = {
            "pause": "observing",
            "resume": "enabled",
            "drain": "draining",
        }[command]
        control["allow_repository_mutation"] = command == "resume"
        save_control(control)
        print(
            json.dumps(
                {
                    "command": command,
                    "mode": control["mode"],
                    "allow_repository_mutation": control[
                        "allow_repository_mutation"
                    ],
                },
                sort_keys=True,
            )
        )
        return 0
    if command == "reconcile":
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
                {
                    "command": "reconcile",
                    "triggered": control.get("cron_job_id"),
                },
                sort_keys=True,
            )
        )
        return 0
    raise ValueError(f"unsupported command: {command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
