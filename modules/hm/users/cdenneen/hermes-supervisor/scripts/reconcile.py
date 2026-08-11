#!/usr/bin/env python3
import os
from pathlib import Path

from axis_supervisor.collector import main


ROOT = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_ROOT",
        Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor",
    )
)


def require_staged_inventory() -> None:
    """Keep the public reconciler from publishing an incomplete snapshot.

    Collection is deliberately a staging-only operation. The preflight process
    passes this exact path and then asks ``cycle rebuild`` to derive and
    publish graph, graduation, mission, frontier, and inventory together.
    """
    configured = os.environ.get("AXIS_SUPERVISOR_INVENTORY_PATH")
    expected = ROOT / "inventory.pending.json"
    if not configured:
        raise RuntimeError(
            "reconciler requires AXIS_SUPERVISOR_INVENTORY_PATH="
            f"{expected}; canonical inventory publication is cycle-owned"
        )
    if Path(configured).resolve() != expected.resolve():
        raise RuntimeError(
            "reconciler accepts only the preflight staged inventory path; "
            "canonical inventory publication is cycle-owned"
        )


if __name__ == "__main__":
    require_staged_inventory()
    raise SystemExit(main())
