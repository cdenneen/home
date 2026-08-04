#!/usr/bin/env python3
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor"
CONTROL = ROOT / "control.json"
JOBS = Path.home() / ".hermes" / "cron" / "jobs.json"
PROMPT = ROOT / "worker-prompt.txt"


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def write_atomic(path: Path, value: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def create(hermes: str, args: list[str]) -> str:
    output = subprocess.check_output([hermes, "cron", "create", *args], text=True, timeout=120)
    for line in output.splitlines():
        if line.startswith("Created job:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"could not parse cron job ID: {output}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("install", "remove", "status"))
    parser.add_argument("--hermes", required=True)
    args = parser.parse_args()

    control = load(CONTROL)
    jobs = load(JOBS) if JOBS.exists() else {"jobs": []}
    by_name = {item.get("name"): item for item in jobs.get("jobs", [])}

    worker = by_name.get("axis-development-supervisor-worker")
    reporter = by_name.get("axis-development-supervisor-report")
    if args.command == "install":
        if control.get("cron_generation") not in {None, 1}:
            raise RuntimeError("unsupported cron_generation")
        configured_worker = control.get("cron_job_id")
        configured_reporter = control.get("report_cron_job_id")
        if configured_worker and (worker is None or str(worker.get("id")) != str(configured_worker)):
            raise RuntimeError("configured worker cron ID is missing or name ownership does not match")
        if configured_reporter and (reporter is None or str(reporter.get("id")) != str(configured_reporter)):
            raise RuntimeError("configured reporter cron ID is missing or name ownership does not match")
        if not configured_worker and worker is not None:
            raise RuntimeError("refusing to adopt same-name worker without a configured ownership ID")
        if not configured_reporter and reporter is not None:
            raise RuntimeError("refusing to adopt same-name reporter without a configured ownership ID")
        if worker is None:
            worker_id = create(
                args.hermes,
                [
                    "--name", "axis-development-supervisor-worker",
                    "--workdir", "/home/cdenneen/src/workspace/personal/work",
                    "--skill", "axis-development-supervisor",
                    "--script", "axis-development-supervisor-preflight.py",
                    "--provider", "openai-api",
                    "--model", "gpt-5.5",
                    "every 10m",
                    PROMPT.read_text(encoding="utf-8").strip(),
                ],
            )
            control["cron_job_id"] = worker_id
            control["cron_generation"] = 1
            write_atomic(CONTROL, control)
        else:
            worker_id = str(worker["id"])
            desired_prompt = PROMPT.read_text(encoding="utf-8").strip()
            expected = {
                "schedule_display": "every 10m",
                "script": "axis-development-supervisor-preflight.py",
                "provider": "openai-api",
                "model": "gpt-5.5",
                "deliver": None,
                "skill": "axis-development-supervisor",
                "workdir": "/home/cdenneen/src/workspace/personal/work",
                "no_agent": False,
            }
            for key, value in expected.items():
                actual = worker.get(key)
                if key == "deliver" and actual in {None, "local"}:
                    continue
                if actual != value:
                    raise RuntimeError(f"worker cron drift: {key}={actual!r}, expected {value!r}")
            if worker.get("prompt") != desired_prompt:
                subprocess.run(
                    [
                        args.hermes,
                        "cron",
                        "edit",
                        worker_id,
                        "--provider",
                        "openai-api",
                        "--model",
                        "gpt-5.5",
                        "--prompt",
                        desired_prompt,
                    ],
                    check=True,
                    timeout=120,
                )
        if reporter is None:
            reporter_id = create(
                args.hermes,
                [
                    "--name", "axis-development-supervisor-report",
                    "--deliver", str(control.get("slack_delivery")),
                    "--script", "axis-development-supervisor-report.py",
                    "--no-agent",
                    "every 15m",
                ],
            )
            control["report_cron_job_id"] = reporter_id
            control["cron_generation"] = 1
            write_atomic(CONTROL, control)
        else:
            reporter_id = str(reporter["id"])
            expected = {
                "schedule_display": "every 15m",
                "script": "axis-development-supervisor-report.py",
                "deliver": str(control.get("slack_delivery")),
                "no_agent": True,
            }
            for key, value in expected.items():
                if reporter.get(key) != value:
                    raise RuntimeError(
                        f"reporter cron drift: {key}={reporter.get(key)!r}, expected {value!r}"
                    )
        control["cron_job_id"] = worker_id
        control["report_cron_job_id"] = reporter_id
        control["cron_generation"] = 1
        write_atomic(CONTROL, control)
        lifecycle_command = (
            "resume"
            if control.get("mode") in {"observing", "enabled", "draining"}
            else "pause"
        )
        for job_id in (worker_id, reporter_id):
            subprocess.run([args.hermes, "cron", lifecycle_command, job_id], check=True)
        print(json.dumps({"worker": worker_id, "reporter": reporter_id}, sort_keys=True))
        return 0

    if args.command == "remove":
        removed = []
        for job in (worker, reporter):
            if job is None:
                continue
            job_id = str(job["id"])
            configured_ids = {
                str(control.get("cron_job_id") or ""),
                str(control.get("report_cron_job_id") or ""),
            }
            if job_id not in configured_ids:
                raise RuntimeError(f"refusing to remove unowned cron job {job_id}")
            subprocess.run([args.hermes, "cron", "pause", job_id], check=False)
            subprocess.run([args.hermes, "cron", "remove", job_id], check=True)
            removed.append(job_id)
        control["cron_job_id"] = None
        control["report_cron_job_id"] = None
        write_atomic(CONTROL, control)
        print(json.dumps({"removed": removed}, sort_keys=True))
        return 0

    print(
        json.dumps(
            {
                "worker": (worker or {}).get("id"),
                "reporter": (reporter or {}).get("id"),
                "configured_worker": control.get("cron_job_id"),
                "configured_reporter": control.get("report_cron_job_id"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
