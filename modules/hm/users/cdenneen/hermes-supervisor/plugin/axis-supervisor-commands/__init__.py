from __future__ import annotations

import asyncio
import contextvars
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor"
COMMAND_SCRIPT = Path.home() / ".hermes" / "scripts" / "axis-development-supervisor-command.py"
SUPPORTED = {
    "help",
    "commands",
    "status",
    "roadmap",
    "milestones",
    "running",
    "blocked",
    "decisions",
    "recent",
    "inspect",
    "reconcile",
    "pause",
    "resume",
    "drain",
}
_AUTHORIZED = contextvars.ContextVar("axis_supervisor_command_authorized", default=False)
def _control() -> dict:
    return json.loads((ROOT / "control.json").read_text(encoding="utf-8"))


def _parse_command(text: str) -> tuple[str, str] | None:
    command, _, argument = text.strip().partition(" ")
    command = command.lower()
    argument = argument.strip()
    if command not in SUPPORTED:
        return None
    if command == "inspect":
        if not re.fullmatch(r"[^\s#]+/[^\s#]+#\d+", argument):
            return None
    elif argument:
        return None
    return command, argument


def _platform_name(value) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _pre_gateway_dispatch(*, event, **_kwargs):
    text = str(getattr(event, "text", "") or "").strip()
    if not re.match(r"^/axis(?:\s|$)", text, re.I):
        return None
    source = getattr(event, "source", None)
    control = _control()
    state_path = ROOT / "slack-overview-state.json"
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.is_file()
        else {}
    )
    authorized = bool(
        _platform_name(getattr(source, "platform", None)) == "slack"
        and str(getattr(source, "chat_type", "") or "").lower() == "dm"
        and str(getattr(source, "user_id", "") or "")
        == str(control.get("slack_user_id") or "")
        and str(getattr(source, "chat_id", "") or "")
        == str(state.get("channel") or "")
    )
    _AUTHORIZED.set(authorized)
    return {"action": "allow"}


def _metric(value: dict) -> str:
    return f"{value.get('count', 0)}/{value.get('denominator', 0)} ({value.get('percent', 0):g}%)"


def _render(data: dict) -> str:
    command = data.get("command")
    if command == "help":
        return "*AXIS Supervisor commands*\n" + "\n".join(
            f"• `{item}`" for item in data.get("supported") or []
        )
    if command == "status":
        composition = data.get("composition") or {}
        work = data.get("supervisor_work") or {}
        focus = data.get("current_supervisor_focus") or {}
        return (
            "*AXIS Supervisor Status*\n"
            f"Mode: `{data.get('mode')}` | Mutation: `{'enabled' if data.get('allow_repository_mutation') else 'disabled'}`\n"
            f"Verified: {_metric(composition.get('verified_complete') or {})}\n"
            f"Work remaining: {work.get('supervisor_work_remaining', 0)} | Ready tasks: {work.get('ready_work_total', 0)}\n"
            f"Frontier: `{data.get('current_execution_frontier') or 'none'}`\n"
            f"Focus: `{focus.get('kind') or 'none'}` — {', '.join(focus.get('work_items') or []) or 'none'}"
        )
    if command in {"roadmap", "milestones"}:
        milestones = data.get("complete_roadmap") or data.get("milestones") or []
        composition = data.get("composition") or {}
        work = data.get("supervisor_work") or {}
        header = "*AXIS Complete Roadmap*"
        if composition:
            header += (
                f"\nVerified: {_metric(composition.get('verified_complete') or {})} | "
                f"Work remaining: {work.get('supervisor_work_remaining', 0)}"
            )
        lines = [
            f"• *{item.get('key')}* `{item.get('status')}` — progress {_metric(item.get('progress') or {})}; "
            f"verified {item.get('verified_complete', 0)}/{item.get('total', 0)}; "
            f"exec {item.get('executable', 0)}; revalidation {item.get('revalidation_ready', 0)}; "
            f"waiting {item.get('waiting', 0)}; blocked {item.get('blocked', 0)}"
            for item in milestones
        ]
        return (
            f"{header}\nFrontier: `{data.get('current_execution_frontier') or 'none'}`\n"
            + "\n".join(lines)
        )
    if command == "running":
        return f"*Running assignments:* {data.get('count', 0)}"
    if command == "blocked":
        items = data.get("items") or []
        lines = [
            f"• `{item.get('ref')}` {item.get('classification')}: {item.get('title')}"
            for item in items[:15]
        ]
        suffix = f"\n…and {len(items) - 15} more" if len(items) > 15 else ""
        return f"*Waiting/blocked items:* {data.get('count', 0)}\n" + "\n".join(lines) + suffix
    if command == "decisions":
        return f"*Product Owner decisions pending:* {data.get('count', 0)}"
    if command == "recent":
        return "*Recent supervisor activity*\n```" + json.dumps(data.get("items") or [], indent=2)[:3000] + "```"
    if command == "inspect":
        if data.get("error"):
            return f"*Inspect:* {data['error']}"
        return "*Work item*\n```" + json.dumps(data, indent=2)[:3500] + "```"
    if command in {"pause", "resume", "drain"}:
        return (
            f"*Supervisor {command} complete*\nMode: `{data.get('mode')}` | "
            f"Mutation: `{'enabled' if data.get('allow_repository_mutation') else 'disabled'}`"
        )
    if command == "reconcile":
        return f"*Supervisor reconciliation triggered:* `{data.get('triggered')}`"
    return "```" + json.dumps(data, indent=2)[:3500] + "```"


def _run_command(command_text: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(COMMAND_SCRIPT), command_text],
        text=True,
        capture_output=True,
        timeout=30,
    )


async def _handle_axis(raw_args: str) -> str:
    authorized = _AUTHORIZED.get()
    _AUTHORIZED.set(False)
    if not authorized:
        return "AXIS Supervisor commands are restricted to the configured Product Owner Slack DM."
    command_text = raw_args.strip() or "help"
    if _parse_command(command_text) is None:
        return "Unsupported AXIS Supervisor command. Send `!axis help` for the command list."
    try:
        completed = await asyncio.to_thread(_run_command, command_text)
    except subprocess.TimeoutExpired:
        return "AXIS Supervisor command timed out after 30 seconds."
    output = completed.stdout.strip()
    if len(output.encode("utf-8")) > 100_000:
        return "AXIS Supervisor command output exceeded the 100 KB response limit."
    if completed.returncode != 0:
        detail = output or completed.stderr[:2000].strip()
        return f"AXIS Supervisor command failed: {detail}"
    return _render(json.loads(output))


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    ctx.register_command(
        "axis",
        handler=_handle_axis,
        description="Operate the AXIS Development Supervisor without an LLM.",
        args_hint="<status|roadmap|milestones|running|blocked|decisions|recent|inspect|reconcile|pause|resume|drain>",
    )
