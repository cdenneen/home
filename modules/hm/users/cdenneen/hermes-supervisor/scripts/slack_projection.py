#!/usr/bin/env python3
import json
import os
from pathlib import Path

from axis_supervisor.slack_projection import SlackProjection

ROOT = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_ROOT",
        Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor",
    )
)


def main() -> int:
    if (ROOT / "inventory.lock").exists():
        print(json.dumps({"updated": False, "reason": "inventory-generation-in-progress"}))
        return 0
    inventory = json.loads((ROOT / "inventory.json").read_text(encoding="utf-8"))
    graph = json.loads((ROOT / "execution-graph.json").read_text(encoding="utf-8"))
    if graph.get("inventory_generation_id") != inventory.get("generation_id"):
        raise ValueError("execution graph does not match inventory generation")
    control = json.loads((ROOT / "control.json").read_text(encoding="utf-8"))
    print(json.dumps(SlackProjection(ROOT).update(inventory, graph, control), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
