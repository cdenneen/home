#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
from pathlib import Path

from axis_supervisor.mutation import MutationGate, OperationClass
from axis_supervisor.schema_registry import read_record, write_record

ROOT = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_ROOT",
        Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor",
    )
)
CONTROL = ROOT / "control.json"
JOBS = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_CRON_JOBS", Path.home() / ".hermes" / "cron" / "jobs.json"
    )
)
PROMPT = ROOT / "worker-prompt.txt"


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise TypeError(f"expected object: {path}")
    return value


def write_control(value: dict, gate: MutationGate, decision) -> None:
    gate.require(decision, OperationClass.SCHEDULER)
    write_record(CONTROL, value, "axis.external-development-supervisor.control")


def create(hermes: str, args: list[str], gate: MutationGate, decision) -> str:
    gate.require(decision, OperationClass.SCHEDULER)
    output = subprocess.check_output([hermes, "cron", "create", *args], text=True, timeout=120)
    for line in output.splitlines():
        if line.startswith("Created job:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"could not parse cron job ID: {output}")


def mutate(
    gate: MutationGate, decision, command: list[str], *, check: bool = False, **kwargs
):
    gate.require(decision, OperationClass.SCHEDULER)
    return subprocess.run(command, check=check, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("install", "remove", "status"))
    parser.add_argument("--hermes", required=True)
    args = parser.parse_args()

    control = read_record(CONTROL, "axis.external-development-supervisor.control")
    gate = MutationGate(
        ROOT, source=os.environ.get("AXIS_SUPERVISOR_MUTATION_SOURCE", "operator-cli")
    )
    decision = (
        gate.decide(OperationClass.SCHEDULER)
        if args.command in {"install", "remove"}
        else None
    )
    jobs = load(JOBS) if JOBS.exists() else {"jobs": []}
    def owned_job(name: str) -> dict | None:
        matches = [item for item in jobs.get("jobs", []) if item.get("name") == name]
        if len(matches) > 1:
            raise RuntimeError(f"duplicate supervisor cron jobs named {name}: {len(matches)}")
        return matches[0] if matches else None

    worker = owned_job("axis-development-supervisor-worker")
    projection = owned_job("axis-development-supervisor-report")
    if args.command == "install":
        if control.get("cron_generation") not in {None, 1}:
            raise RuntimeError("unsupported cron_generation")
        configured_worker = control.get("cron_job_id")
        configured_projection = control.get("report_cron_job_id")
        if configured_worker and (worker is None or str(worker.get("id")) != str(configured_worker)):
            raise RuntimeError("configured worker cron ID is missing or name ownership does not match")
        if not configured_worker and worker is not None:
            raise RuntimeError("refusing to adopt same-name worker without a configured ownership ID")
        if projection is not None:
            if not configured_projection:
                raise RuntimeError(
                    "refusing to disable same-name Slack projection without its configured ownership ID"
                )
            if str(projection.get("id")) != str(configured_projection):
                raise RuntimeError(
                    "configured Slack projection cron ID ownership does not match"
                )
            projection_id = str(projection["id"])
            mutate(
                gate,
                decision,
                [args.hermes, "cron", "pause", projection_id],
                check=False,
            )
            mutate(
                gate,
                decision,
                [args.hermes, "cron", "remove", projection_id],
                check=True,
            )
        control["report_cron_job_id"] = None
        if worker is None:
            worker_id = create(
                args.hermes,
                [
                    "--name", "axis-development-supervisor-worker",
                    "--workdir", "/home/cdenneen/src/workspace/personal/work",
                    "--skill", "axis-development-supervisor",
                    "--script", "axis-development-supervisor-preflight.py",
                    "--provider", "openai-api",
                    "--model", "gpt-5.4",
                    "every 10m",
                    PROMPT.read_text(encoding="utf-8").strip(),
                ],
                gate,
                decision,
            )
            control["cron_job_id"] = worker_id
            control["cron_generation"] = 1
            write_control(control, gate, decision)
        else:
            worker_id = str(worker["id"])
            desired_prompt = PROMPT.read_text(encoding="utf-8").strip()
            expected = {
                "schedule_display": "every 10m",
                "script": "axis-development-supervisor-preflight.py",
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
            if (
                worker.get("prompt") != desired_prompt
                or worker.get("provider") != "openai-api"
                or worker.get("model") != "gpt-5.4"
            ):
                mutate(
                    gate,
                    decision,
                    [
                        args.hermes,
                        "cron",
                        "edit",
                        worker_id,
                        "--provider",
                        "openai-api",
                        "--model",
                        "gpt-5.4",
                        "--prompt",
                        desired_prompt,
                    ],
                    check=True,
                    timeout=120,
                )
        control["cron_job_id"] = worker_id
        control["report_cron_job_id"] = None
        control["cron_generation"] = 1
        write_control(control, gate, decision)
        lifecycle_command = (
            "resume"
            if control.get("mode") in {"observing", "enabled", "draining"}
            else "pause"
        )
        mutate(
            gate,
            decision,
            [args.hermes, "cron", lifecycle_command, worker_id],
            check=True,
        )
        print(
            json.dumps(
                {"worker": worker_id, "slack_projection": None}, sort_keys=True
            )
        )
        return 0

    if args.command == "remove":
        removed = []
        for job in (worker, projection):
            if job is None:
                continue
            job_id = str(job["id"])
            configured_ids = {
                str(control.get("cron_job_id") or ""),
                str(control.get("report_cron_job_id") or ""),
            }
            if job_id not in configured_ids:
                raise RuntimeError(f"refusing to remove unowned cron job {job_id}")
            mutate(gate, decision, [args.hermes, "cron", "pause", job_id], check=False)
            mutate(gate, decision, [args.hermes, "cron", "remove", job_id], check=True)
            removed.append(job_id)
        control["cron_job_id"] = None
        control["report_cron_job_id"] = None
        write_control(control, gate, decision)
        print(json.dumps({"removed": removed}, sort_keys=True))
        return 0

    print(
        json.dumps(
            {
                "worker": (worker or {}).get("id"),
                "slack_projection": (projection or {}).get("id"),
                "configured_worker": control.get("cron_job_id"),
                "configured_slack_projection": control.get("report_cron_job_id"),
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
