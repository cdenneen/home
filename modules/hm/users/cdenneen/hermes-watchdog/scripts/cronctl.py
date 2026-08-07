#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from axis_watchdog.records import atomic_write, load_object

HOME = Path.home()
ROOT = Path(
    os.environ.get(
        "AXIS_WATCHDOG_ROOT",
        HOME / ".hermes" / "supervisor" / "axis-development-watchdog",
    )
)
CONTROL = ROOT / "control.json"
JOBS = Path(
    os.environ.get("AXIS_WATCHDOG_CRON_JOBS", HOME / ".hermes" / "cron" / "jobs.json")
)
JOB_NAME = "axis-development-watchdog"


def owned_job(jobs: list[dict[str, Any]], name: str = JOB_NAME) -> dict[str, Any] | None:
    matches = [item for item in jobs if item.get("name") == name]
    if len(matches) > 1:
        raise RuntimeError(f"duplicate cron jobs named {name}: {len(matches)}")
    return matches[0] if matches else None


def create(hermes: str) -> str:
    output = subprocess.check_output(
        [
            hermes,
            "cron",
            "create",
            "--name",
            JOB_NAME,
            "--script",
            "axis-development-watchdog.py",
            "--no-agent",
            "every 5m",
        ],
        text=True,
        timeout=120,
    )
    for line in output.splitlines():
        if line.startswith("Created job:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"could not parse cron job ID: {output}")


def run(hermes: str, *args: str, check: bool = True) -> None:
    subprocess.run([hermes, "cron", *args], check=check, timeout=120)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("install", "remove", "status"))
    parser.add_argument("--hermes", required=True)
    args = parser.parse_args()

    control = load_object(CONTROL)
    if control.get("schema") != "axis.development-watchdog.control":
        raise ValueError("watchdog control schema is invalid")
    if control.get("cron_generation") != 1:
        raise ValueError("unsupported watchdog cron generation")
    jobs_value = load_object(JOBS) if JOBS.exists() else {"jobs": []}
    jobs = list(jobs_value.get("jobs") or [])
    job = owned_job(jobs)
    configured = control.get("cron_job_id")
    if configured and (job is None or str(job.get("id")) != str(configured)):
        raise RuntimeError("configured watchdog cron ID is missing or ownership does not match")
    if not configured and job is not None:
        raise RuntimeError("refusing to adopt same-name watchdog cron without its ownership ID")

    if args.command == "install":
        job_id = str(job["id"]) if job else create(args.hermes)
        if job:
            expected = {
                "schedule_display": "every 5m",
                "script": "axis-development-watchdog.py",
                "no_agent": True,
            }
            drift = [
                f"{key}={job.get(key)!r}, expected {value!r}"
                for key, value in expected.items()
                if job.get(key) != value
            ]
            if drift:
                raise RuntimeError("watchdog cron drift: " + "; ".join(drift))
        control["cron_job_id"] = job_id
        atomic_write(CONTROL, control)
        run(args.hermes, "resume", job_id)
        print(json.dumps({"watchdog": job_id}, sort_keys=True))
        return 0

    if args.command == "remove":
        if job is not None:
            job_id = str(job["id"])
            run(args.hermes, "pause", job_id, check=False)
            run(args.hermes, "remove", job_id)
        control["cron_job_id"] = None
        atomic_write(CONTROL, control)
        print(json.dumps({"removed": str(job["id"]) if job else None}, sort_keys=True))
        return 0

    print(
        json.dumps(
            {
                "watchdog": (job or {}).get("id"),
                "configured_watchdog": configured,
                "enabled": bool((job or {}).get("enabled")),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CLI boundary emits structured failure
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
