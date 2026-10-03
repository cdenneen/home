"""Pi portfolio sweep — runs pi --print for morning/evening personal project sweep.

Invoked by systemd oneshot services with:
  SLACK_BOT_TOKEN   — from sops hermes_slack_env_ghost_chief
  SLACK_HOME_CHANNEL — from sops hermes_slack_env_ghost_chief (or C0BHLUXQ4EB override)
  PI_SWEEP_KIND     — "morning" or "evening"
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

UTC = dt.timezone.utc

MORNING_PROMPT = """\
Generate a concise personal portfolio sweep for Chris. Read-only; make no changes.

Projects to cover:
- ~/src/workspace/personal (personal AXIS, Jarvis, nix config) — open issues, blocked work, in-flight tasks
- ~/src/workspace/nix/home — recent or pending home-manager changes and any stalled PRs
- Hermes Kanban (work-ops board) — in-progress tasks, anything stuck over 4 hours

Return a brief Slack-formatted plain-text morning digest:
  - 3 to 5 highest-priority action items across all lanes
  - Any blocked or stalled items that need Chris's attention today
  - One-sentence status per active project lane
Keep the total under 2500 characters. No filler phrases.
"""

EVENING_PROMPT = """\
Generate a concise end-of-day portfolio review for Chris. Read-only; make no changes.

Projects to cover:
- ~/src/workspace/personal (personal AXIS, Jarvis, nix config) — what moved today, what is still open
- ~/src/workspace/nix/home — pending PRs or uncommitted changes
- Hermes Kanban (work-ops board) — completed, in-progress, and newly blocked tasks

Return a brief Slack-formatted plain-text evening digest:
  - What was accomplished today across lanes
  - What remains open or blocked going into tomorrow
  - Any prep or decisions Chris should make before the next morning
Keep the total under 2500 characters. No filler phrases.
"""


def slack_post(text: str) -> None:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    channel = (
        os.environ.get("PI_SWEEP_SLACK_CHANNEL")
        or os.environ.get("SLACK_HOME_CHANNEL", "")
        or "C0BHLUXQ4EB"
    )
    if not token:
        raise RuntimeError("slack-bot-token-missing")

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


def clean_output(output: str) -> str:
    lines = [line for line in output.splitlines() if not line.lower().startswith("session id:")]
    value = "\n".join(lines).strip()
    if not value:
        raise RuntimeError("pi-sweep-empty-output")
    return value[:3000]


def run_pi_sweep(kind: str, timeout: int = 360) -> str:
    prompt = MORNING_PROMPT if kind == "morning" else EVENING_PROMPT
    workdir = Path.home() / "src" / "workspace" / "personal"
    if not workdir.is_dir():
        workdir = Path.home() / "src" / "workspace"

    env = {**os.environ}
    # Ensure pi runs fully non-interactive and non-telemetry
    env.setdefault("PI_OFFLINE", "0")
    env["PI_TELEMETRY"] = "0"

    # Use litellm provider so the request routes through eros (the configured LiteLLM
    # proxy at ~/.pi/agent/models.json). The default openai provider attempts to inject
    # a litellm_session_id extra parameter that the OpenAI-compatible route rejects; the
    # litellm provider accepts it natively via drop_params.
    completed = subprocess.run(
        ["pi", "--print", "--provider", "litellm", "--model", "gpt-5.6-terra", prompt],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    output = completed.stdout.strip()
    if completed.returncode != 0:
        stderr = completed.stderr.strip()[:500]
        raise RuntimeError(f"pi-exit-{completed.returncode}: {stderr}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Pi portfolio sweep")
    parser.add_argument("kind", choices=["morning", "evening"], help="Sweep kind")
    args = parser.parse_args()

    kind = args.kind
    date = dt.datetime.now().astimezone().strftime("%A, %B %-d")
    label = "Morning" if kind == "morning" else "Evening"

    try:
        output = run_pi_sweep(kind)
        slack_post(f"*Pi {label} Portfolio Sweep — {date}*\n{clean_output(output)}")
        print(json.dumps({"status": "delivered", "kind": kind, "date": date}))
        return 0
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        print(json.dumps({"status": "error", "kind": kind, "error": error_msg}), file=sys.stderr)
        try:
            slack_post(
                f"*Pi {label} Portfolio Sweep — {date}* :warning: failed: `{error_msg[:200]}`"
            )
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
