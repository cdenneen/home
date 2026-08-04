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


def main() -> int:
    text = " ".join(sys.argv[1:]).strip()
    command, _, argument = text.partition(" ")
    command = command.lower()
    inventory = load("inventory.json")
    graph = load("execution-graph.json")
    control = load("control.json")

    if command in {"status", "roadmap"}:
        print(
            json.dumps(
                {
                    "mode": control.get("mode"),
                    "classifications": inventory.get("classification_counts"),
                    "governed_queue_depth": graph.get("queue_depth"),
                    "governed_queue_zero_proven": graph.get(
                        "governed_queue_zero_proven"
                    ),
                    "confidence": inventory.get("roadmap_confidence"),
                },
                sort_keys=True,
            )
        )
        return 0
    if command == "milestones":
        print(json.dumps(inventory.get("milestones") or [], sort_keys=True))
        return 0
    if command == "running":
        print(json.dumps(inventory.get("active_assignments") or [], sort_keys=True))
        return 0
    if command == "blocked":
        print(
            json.dumps(
                [
                    item
                    for item in inventory.get("work_items") or []
                    if item.get("classification") in {"Blocked", "Waiting"}
                ],
                sort_keys=True,
            )
        )
        return 0
    if command == "decisions":
        print(
            json.dumps(
                [
                    node.get("semantic_record", {}).get("decision_packet")
                    for node in graph.get("nodes") or []
                    if node.get("semantic_record", {}).get("decision_packet")
                ],
                sort_keys=True,
            )
        )
        return 0
    if command == "inspect":
        item = next(
            (item for item in inventory.get("work_items") or [] if item.get("ref") == argument),
            None,
        )
        print(json.dumps(item or {"error": "work item not found"}, sort_keys=True))
        return 0
    if command == "recent":
        print(json.dumps((inventory.get("activity_timeline") or [])[:10]))
        return 0
    if command in {"pause", "resume", "drain"}:
        control["mode"] = {
            "pause": "observing",
            "resume": "enabled",
            "drain": "draining",
        }[command]
        control["allow_repository_mutation"] = command == "resume"
        save_control(control)
        print(json.dumps({"mode": control["mode"]}, sort_keys=True))
        return 0
    if command == "reconcile":
        hermes = shutil.which("hermes") or str(Path.home() / ".nix-profile/bin/hermes")
        subprocess.run(
            [hermes, "cron", "run", str(control.get("cron_job_id"))], check=True
        )
        print(json.dumps({"triggered": control.get("cron_job_id")}, sort_keys=True))
        return 0
    raise ValueError(f"unsupported command: {command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
