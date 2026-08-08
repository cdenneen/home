#!/usr/bin/env python3
import argparse
import json
import os
import time
from pathlib import Path

from axis_watchdog.cutover import CutoverCoordinator

HOME = Path.home()
ROOT = Path(
    os.environ.get(
        "AXIS_WATCHDOG_ROOT",
        HOME / ".hermes" / "supervisor" / "axis-development-watchdog",
    )
)
JOBS = Path(
    os.environ.get("AXIS_WATCHDOG_CRON_JOBS", HOME / ".hermes" / "cron" / "jobs.json")
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("reconcile", "rollback", "status"))
    parser.add_argument("--reason", default="operator-requested rollback")
    args = parser.parse_args()
    coordinator = CutoverCoordinator(ROOT, JOBS, clock=lambda: int(time.time()))
    if args.command == "reconcile":
        coordinator.load()
        coordinator.reconcile()
    elif args.command == "rollback":
        coordinator.rollback(args.reason)
    print(json.dumps(coordinator.load(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
