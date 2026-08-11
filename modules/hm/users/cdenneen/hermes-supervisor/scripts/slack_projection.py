#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

from axis_supervisor import progress_coherence
from axis_supervisor.capability_graduation import read_capability_graduation
from axis_supervisor.missions import read_mission_record
from axis_supervisor.schema_registry import read_record
from axis_supervisor.slack_projection import SlackProjection

ROOT = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_ROOT",
        Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor",
    )
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shadow", action="store_true")
    args = parser.parse_args()
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
    graduation = read_capability_graduation(ROOT / "capability-graduation.json")
    mission = read_mission_record(ROOT / "active-mission.json")
    coherence = progress_coherence(inventory, graph, graduation, mission)
    if not coherence["trusted"]:
        raise ValueError(
            "supervisor progress generations are incoherent: "
            + ", ".join(coherence["failures"])
        )
    control = read_record(
        ROOT / "control.json", "axis.external-development-supervisor.control"
    )
    if args.shadow:
        fallback, blocks, fingerprint = SlackProjection(ROOT).render(
            inventory, graph, control
        )
        print(
            json.dumps(
                {
                    "shadow": True,
                    "fallback": fallback,
                    "blocks": blocks,
                    "fingerprint": fingerprint,
                },
                sort_keys=True,
            )
        )
        return 0
    print(json.dumps(SlackProjection(ROOT).update(inventory, graph, control), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
