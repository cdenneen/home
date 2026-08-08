#!/usr/bin/env python3
import json
import os
import subprocess
import time
from pathlib import Path

from axis_watchdog.records import atomic_write, load_optional, timestamp

HOME = Path.home()
ROOT = Path(
    os.environ.get(
        "AXIS_WATCHDOG_ROOT",
        HOME / ".hermes" / "supervisor" / "axis-development-watchdog",
    )
)
SERVICE = "axis-development-watchdog-backup.service"
SYSTEMCTL = os.environ.get("AXIS_WATCHDOG_SYSTEMCTL", "systemctl")


def main() -> int:
    now = int(time.time())
    heartbeat = load_optional(ROOT / "heartbeat.json")
    completed = int(heartbeat.get("completed_at_epoch") or 0)
    maximum_age = int(os.environ.get("AXIS_WATCHDOG_MONITOR_MAX_AGE", "660"))
    stale = not completed or now - completed > maximum_age
    status = "healthy"
    error = None
    if stale:
        result = subprocess.run(
            [SYSTEMCTL, "--user", "start", "--no-block", SERVICE],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        status = "watchdog-start-requested" if result.returncode == 0 else "error"
        if result.returncode != 0:
            error = ((result.stdout or "") + (result.stderr or ""))[-1000:]
    value = {
        "schema": "axis.development-watchdog.external-heartbeat",
        "schema_version": "1.0.0",
        "observed_at": timestamp(now),
        "observed_at_epoch": now,
        "watchdog_heartbeat_epoch": completed or None,
        "watchdog_stale": stale,
        "status": status,
        "error": error,
    }
    atomic_write(ROOT / "external-heartbeat.json", value)
    print(json.dumps(value, sort_keys=True))
    return 0 if status != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
