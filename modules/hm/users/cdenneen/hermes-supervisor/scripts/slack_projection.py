#!/usr/bin/env python3
import json
import os
from pathlib import Path

from axis_supervisor.slack_projection import SlackProjection
from axis_supervisor.schema_registry import read_record

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
    inventory = read_record(
        ROOT / "inventory.json", "axis.external-development-supervisor.inventory"
    )
    graph = read_record(
        ROOT / "execution-graph.json",
        "axis.external-development-supervisor.execution-graph",
    )
    if graph.get("inventory_generation_id") != inventory.get("generation_id"):
        raise ValueError("execution graph does not match inventory generation")
    control = read_record(
        ROOT / "control.json", "axis.external-development-supervisor.control"
    )
    print(json.dumps(SlackProjection(ROOT).update(inventory, graph, control), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
