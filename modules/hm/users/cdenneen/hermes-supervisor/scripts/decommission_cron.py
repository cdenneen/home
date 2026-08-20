#!/usr/bin/env python3
"""Remove only strictly owned legacy AXIS Hermes cron jobs."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
from typing import Any

CONTRACTS = {
    "axis-development-supervisor-worker": {
        "control": ("supervisor/axis-development-supervisor/control.json", "cron_job_id", "axis.external-development-supervisor.control"),
        "job": {
            "schedule_display": "every 10m",
            "script": "axis-development-supervisor-preflight.py",
            "skill": "axis-development-supervisor",
            "workdir": "/home/cdenneen/src/workspace/personal/work",
            "provider": "openai-api",
            "model": "gpt-5.4",
            "no_agent": False,
        },
    },
    "axis-development-supervisor-report": {
        "control": ("supervisor/axis-development-supervisor/control.json", "report_cron_job_id", "axis.external-development-supervisor.control"),
        "job": {"schedule_display": "every 5m", "script": "axis-development-supervisor-slack.py", "no_agent": True},
    },
    "axis-development-watchdog": {
        "control": ("supervisor/axis-development-watchdog/control.json", "cron_job_id", "axis.development-watchdog.control"),
        "job": {"schedule_display": "every 5m", "script": "axis-development-watchdog.py", "no_agent": True},
    },
}


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def hermes_homes(root: Path) -> list[Path]:
    homes = [root]
    profiles = root / "profiles"
    if profiles.is_dir():
        homes.extend(path for path in sorted(profiles.iterdir()) if path.is_dir())
    return homes


def plan_home(home: Path) -> list[dict[str, Any]]:
    jobs_path = home / "cron" / "jobs.json"
    jobs_value = load_object(jobs_path) if jobs_path.exists() else {"jobs": []}
    jobs = jobs_value.get("jobs", [])
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        raise TypeError(f"expected jobs array of objects: {jobs_path}")
    removals = []
    controls: dict[Path, dict[str, Any]] = {}
    for name, contract in CONTRACTS.items():
        matches = [job for job in jobs if job.get("name") == name]
        if len(matches) > 1:
            raise RuntimeError(f"ambiguous duplicate legacy job {name} in {home}")
        control_rel, field, schema = contract["control"]
        control_path = home / control_rel
        control = controls.get(control_path)
        if control is None and control_path.exists():
            control = controls[control_path] = load_object(control_path)
            if control.get("schema") != schema:
                raise RuntimeError(f"unexpected control schema: {control_path}")
        configured_id = str((control or {}).get(field) or "")
        if not matches:
            if configured_id:
                raise RuntimeError(f"configured legacy job {name} is missing in {home}")
            continue
        job = matches[0]
        job_id = str(job.get("id") or "")
        if not configured_id or job_id != configured_id:
            raise RuntimeError(f"legacy job {name} lacks matching configured ownership ID in {home}")
        drift = [key for key, expected in contract["job"].items() if job.get(key) != expected]
        if drift:
            raise RuntimeError(f"legacy job {name} contract drift in {home}: {', '.join(drift)}")
        removals.append({"home": home, "id": job_id, "name": name, "control": control_path, "field": field})
    return removals


def atomic_write(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".decommission.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def apply(root: Path, hermes: str) -> list[dict[str, Any]]:
    all_removals = []
    for home in hermes_homes(root):
        jobs_path = home / "cron" / "jobs.json"
        lock_path = home / "cron" / "legacy-axis-decommission.lock"
        if not jobs_path.exists() and not (home / "supervisor").exists():
            continue
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with lock_path.open("a", encoding="utf-8") as lock:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            removals = plan_home(home)
            for item in removals:
                env = {**os.environ, "HERMES_HOME": str(home)}
                subprocess.run([hermes, "cron", "pause", item["id"]], check=False, env=env, timeout=120)
                subprocess.run([hermes, "cron", "remove", item["id"]], check=True, env=env, timeout=120)
            remaining = load_object(jobs_path).get("jobs", []) if jobs_path.exists() else []
            removed_ids = {item["id"] for item in removals}
            if any(str(job.get("id")) in removed_ids for job in remaining):
                raise RuntimeError(f"Hermes did not remove owned legacy jobs from {home}")
            changed_controls: dict[Path, dict[str, Any]] = {}
            for item in removals:
                control = changed_controls.setdefault(item["control"], load_object(item["control"]))
                control[item["field"]] = None
            for path, control in changed_controls.items():
                atomic_write(path, control)
            all_removals.extend(removals)
    return all_removals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "apply"))
    parser.add_argument("--root", type=Path, default=Path.home() / ".hermes")
    parser.add_argument("--hermes")
    args = parser.parse_args()
    if args.command == "apply" and not args.hermes:
        parser.error("--hermes is required for apply")
    removals = apply(args.root, args.hermes) if args.command == "apply" else [item for home in hermes_homes(args.root) for item in plan_home(home)]
    print(json.dumps({"owned_legacy_jobs": [{"home": str(item["home"]), "id": item["id"], "name": item["name"]} for item in removals]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - fail-closed activation boundary
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
