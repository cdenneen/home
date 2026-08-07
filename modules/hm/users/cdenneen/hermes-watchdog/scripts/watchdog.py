#!/usr/bin/env python3
import json
import os
from pathlib import Path

from axis_watchdog import Watchdog

HOME = Path.home()
ROOT = Path(
    os.environ.get(
        "AXIS_WATCHDOG_ROOT",
        HOME / ".hermes" / "supervisor" / "axis-development-watchdog",
    )
)
SUPERVISOR_ROOT = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_ROOT",
        HOME / ".hermes" / "supervisor" / "axis-development-supervisor",
    )
)
JOBS = Path(
    os.environ.get("AXIS_WATCHDOG_CRON_JOBS", HOME / ".hermes" / "cron" / "jobs.json")
)


def main() -> int:
    result = Watchdog(ROOT, SUPERVISOR_ROOT, JOBS).run()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - cron boundary emits structured failure
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
