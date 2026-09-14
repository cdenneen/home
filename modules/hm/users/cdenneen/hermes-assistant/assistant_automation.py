"""Read-only Hermes assistant health, soak, briefing, and Slack delivery."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
from itertools import pairwise
from pathlib import Path

UTC = dt.timezone.utc
SOAK_HOURS = 48
SOAK_MAX_GAP_SECONDS = 90 * 60
SOAK_MIN_OBSERVATIONS = 44


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def sanitized_child_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SLACK_") and key != "HERMES_ASSISTANT_SLACK_CHANNEL"
    }


def run_command(command: list[str], timeout: int = 240) -> str:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        env=sanitized_child_env(),
        text=True,
        timeout=timeout,
    )
    return completed.stdout.strip()


def runtime_token(kind: str) -> Path:
    filename = "google_token.json" if kind == "personal" else "msgraph_token_cache.json"
    return Path.home() / ".hermes" / "profiles" / "assistant" / filename


def check_runtime_token(kind: str) -> None:
    path = runtime_token(kind)
    if not path.is_file():
        raise RuntimeError("runtime-token-missing")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError("runtime-token-mode")


def health_checks(kind: str) -> dict[str, str]:
    checks: dict[str, str] = {}

    if run_command(["systemctl", "--user", "is-active", "hermes-mesh-gateway.service"]) != "active":
        raise RuntimeError("gateway-inactive")
    checks["gateway"] = "ok"

    check_runtime_token(kind)
    checks["runtime_token"] = "ok"

    model_output = run_command(["hermes", "-p", "assistant", "config", "get", "model.default"])
    if model_output.splitlines()[-1].strip() != "claude-sonnet-4-6":
        raise RuntimeError("assistant-model-mismatch")
    checks["model"] = "ok"

    if kind == "personal":
        run_command(["hermes-google-workspace", "gmail", "search", "is:unread", "--max", "1"])
        checks["mail"] = "ok"
        run_command(["hermes-google-workspace", "calendar", "list"])
        checks["calendar"] = "ok"
    else:
        status_output = run_command(["hermes-msgraph", "status"])
        status_data = json.loads(status_output)
        for name in ("profile", "mail", "calendar", "teams"):
            if status_data.get(name) != "ok":
                raise RuntimeError(f"msgraph-{name}")
            checks[name] = "ok"

    return checks


def state_directory() -> Path:
    configured = os.environ.get("HERMES_ASSISTANT_STATE_DIR")
    return Path(configured) if configured else Path.home() / ".local" / "state" / "hermes-assistant"


def read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def atomic_write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def append_history(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")
    path.chmod(0o600)
    lines = path.read_text().splitlines()
    if len(lines) > 336:
        path.write_text("\n".join(lines[-336:]) + "\n")
        path.chmod(0o600)


def soak_evidence(
    path: Path, started: dt.datetime, timestamp: dt.datetime, status: str
) -> dict[str, int]:
    timestamps: list[dt.datetime] = []
    failures = 0
    invalid_records = 0
    started_text = started.isoformat()

    if path.exists():
        try:
            lines = path.read_text().splitlines()
        except OSError:
            lines = []
            invalid_records += 1
        for line in lines:
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or record.get("soak_started_at") != started_text:
                    continue
                checked_at = record.get("checked_at")
                if not isinstance(checked_at, str):
                    raise TypeError
                timestamps.append(dt.datetime.fromisoformat(checked_at))
                failures += record.get("status") != "ok"
            except (json.JSONDecodeError, TypeError, ValueError):
                invalid_records += 1

    timestamps.append(timestamp)
    failures += status != "ok"
    continuity_points = sorted([started, *timestamps])
    maximum_gap = max(
        (
            int((current - previous).total_seconds())
            for previous, current in pairwise(continuity_points)
        ),
        default=0,
    )
    return {
        "soak_observations": len(timestamps),
        "soak_failures": failures,
        "soak_invalid_records": invalid_records,
        "soak_max_gap_seconds": maximum_gap,
    }


def slack_post(text: str) -> None:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = os.environ.get("HERMES_ASSISTANT_SLACK_CHANNEL") or os.environ.get("SLACK_HOME_CHANNEL", "")
    if not token or not channel:
        raise RuntimeError("slack-configuration-missing")

    request = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=urllib.parse.urlencode({"channel": channel, "text": text}).encode(),
        headers={"Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError(f"slack-delivery-{result.get('error', 'failed')}")


def record_health(kind: str, checks: dict[str, str] | None, error: str | None) -> dict[str, object]:
    directory = state_directory()
    state_path = directory / "status.json"
    history_path = directory / "health.jsonl"
    previous = read_json(state_path)
    timestamp = now_utc()

    started_text = previous.get("soak_started_at")
    if isinstance(started_text, str):
        started = dt.datetime.fromisoformat(started_text)
    else:
        started = timestamp
    deadline = started + dt.timedelta(hours=SOAK_HOURS)
    status = "ok" if error is None else "failed"
    evidence = soak_evidence(history_path, started, timestamp, status)
    soak_complete = timestamp >= deadline
    soak_accepted = (
        soak_complete
        and evidence["soak_failures"] == 0
        and evidence["soak_invalid_records"] == 0
        and evidence["soak_max_gap_seconds"] <= SOAK_MAX_GAP_SECONDS
        and evidence["soak_observations"] >= SOAK_MIN_OBSERVATIONS
    )
    current: dict[str, object] = {
        "schema": "hermes.assistant.health.v1",
        "host": os.uname().nodename,
        "scope": kind,
        "status": status,
        "checks": checks or {},
        "error": error,
        "checked_at": timestamp.isoformat(),
        "soak_started_at": started.isoformat(),
        "soak_deadline": deadline.isoformat(),
        "soak_complete": soak_complete,
        "soak_accepted": soak_accepted,
        "soak_reported": previous.get("soak_reported") is True,
        **evidence,
    }
    atomic_write_json(state_path, current)
    append_history(history_path, current)

    previous_status = previous.get("status")
    label = "Personal" if kind == "personal" else "Work"
    if not previous_status:
        icon = ":white_check_mark:" if status == "ok" else ":warning:"
        slack_post(
            f"{icon} {label} Hermes assistant 48-hour read-only soak started; "
            f"hourly health is {status}. Daily briefing schedule is enabled."
        )
    elif soak_complete and not current["soak_reported"]:
        result = "accepted" if soak_accepted else "requires review"
        icon = ":white_check_mark:" if soak_accepted else ":warning:"
        slack_post(
            f"{icon} {label} Hermes assistant 48-hour read-only soak {result}: "
            f"{evidence['soak_observations']} observations, "
            f"{evidence['soak_failures']} failed checks, "
            f"{evidence['soak_invalid_records']} invalid records, "
            f"maximum gap {evidence['soak_max_gap_seconds']} seconds. "
            "No mail, calendar, or Teams write authority is granted."
        )
        current["soak_reported"] = True
        atomic_write_json(state_path, current)
    elif previous_status != status:
        icon = ":white_check_mark:" if status == "ok" else ":warning:"
        slack_post(f"{icon} {label} Hermes assistant health changed: {previous_status} -> {status}.")

    return current


def command_health(kind: str) -> int:
    checks = None
    error_name = None
    try:
        checks = health_checks(kind)
    except (json.JSONDecodeError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        error_name = type(error).__name__
    current = record_health(kind, checks, error_name)
    print(json.dumps(current, sort_keys=True))
    return 0 if error_name is None else 1


def brief_prompt(kind: str) -> str:
    if kind == "personal":
        return """Create Chris's concise personal daily brief using only the read-only personal-google-assistant skill.
Include: today's calendar in time order; conflicts or tight transitions in the next 48 hours; at most five unread messages that appear time-sensitive or require action; and a short action list.
Do not send, modify, label, or delete mail. Do not create or modify calendar events. Do not include access tokens, message IDs, raw message bodies, or unrelated private content.
Return Slack-ready plain text under 2600 characters. If a section has nothing actionable, say none."""
    return """Create Chris's concise work daily brief using only the read-only work-microsoft-assistant skill and the approved custom Microsoft Graph app.
Include: today's calendar in time order; conflicts or tight transitions in the next 48 hours; at most five unread or time-sensitive mail items; relevant Teams items requiring Chris's action; and a short action list.
Do not send, move, delete, categorize, or modify mail. Do not create or modify calendar events. Do not post or react in Teams. Do not include access tokens, object IDs, raw message bodies, or unrelated corporate content.
Return Slack-ready plain text under 2600 characters. If a section has nothing actionable, say none."""


def clean_brief(output: str) -> str:
    lines = [line for line in output.splitlines() if not line.lower().startswith("session id:")]
    value = "\n".join(lines).strip()
    if not value:
        raise RuntimeError("assistant-brief-empty")
    return value[:3000]


def command_brief(kind: str) -> int:
    health_checks(kind)
    output = run_command(
        [
            "hermes",
            "-p",
            "assistant",
            "chat",
            "-Q",
            "--oneshot",
            "--source",
            "cron",
            "--run-budget",
            "240",
            "--max-turns",
            "20",
            "-q",
            brief_prompt(kind),
        ],
        timeout=300,
    )
    label = "Personal" if kind == "personal" else "Work"
    date = dt.datetime.now().astimezone().strftime("%A, %B %-d")
    slack_post(f"*{label} Assistant Daily Brief — {date}*\n{clean_brief(output)}")
    print(json.dumps({"status": "delivered", "kind": kind, "date": date}))
    return 0


def command_status() -> int:
    print(json.dumps(read_json(state_directory() / "status.json"), indent=2, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("health", "brief", "status"))
    parser.add_argument(
        "--kind",
        choices=("personal", "work"),
        default=os.environ.get("HERMES_ASSISTANT_KIND"),
    )
    arguments = parser.parse_args()
    if arguments.command == "status":
        return command_status()
    if arguments.kind not in ("personal", "work"):
        parser.error("--kind or HERMES_ASSISTANT_KIND is required")
    if arguments.command == "health":
        return command_health(arguments.kind)
    return command_brief(arguments.kind)


if __name__ == "__main__":
    sys.exit(main())
