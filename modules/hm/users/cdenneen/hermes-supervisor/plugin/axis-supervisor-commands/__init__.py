from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path.home() / ".hermes" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from axis_supervisor.command_registry import command_specs, parse_command, usage  # noqa: E402
from axis_supervisor.decisions import (  # noqa: E402
    APPROVE_ACTION_ID,
    APPROVE_CONDITIONS_ACTION_ID,
    CONDITIONS_SUBMIT_ACTION_ID,
    REJECT_ACTION_ID,
    SlackDecisionController,
)
from axis_supervisor.dashboard import progress_bar, public_text  # noqa: E402
from axis_supervisor.schema_registry import read_record  # noqa: E402
from axis_supervisor.slack_projection import SlackProjection  # noqa: E402

ROOT = Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor"
COMMAND_SCRIPT = Path.home() / ".hermes" / "scripts" / "axis-development-supervisor-command.py"
_AUTHORIZED = contextvars.ContextVar("axis_supervisor_command_authorized", default=False)


def _control() -> dict:
    return read_record(
        ROOT / "control.json", "axis.external-development-supervisor.control"
    )


def _platform_name(value) -> str:
    return str(getattr(value, "value", value) or "").lower()


def _pre_gateway_dispatch(*, event, **_kwargs):
    text = str(getattr(event, "text", "") or "").strip()
    if not re.match(r"^/axis(?:\s|$)", text, re.IGNORECASE):
        return None
    source = getattr(event, "source", None)
    control = _control()
    state = SlackProjection(ROOT).load_state()
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
        registry = data.get("registry") or command_specs()
        return "*AXIS Supervisor commands*\n" + "\n".join(
            f"• `!axis {usage(item)}` — {item['description']}"
            for item in registry
        )
    if command == "status":
        composition = data.get("composition") or {}
        work = data.get("supervisor_work") or {}
        focus = data.get("current_supervisor_focus") or {}
        focus_ref = focus.get("target_ref") or focus.get("ref") or "none"
        return (
            "*AXIS Executive Status*\n"
            f"Verified: {_metric(composition.get('verified_complete') or {})}\n"
            f"Roadmap remaining: {work.get('supervisor_work_remaining', 0)} | Ready: {work.get('ready_work_item_count', 0)}\n"
            f"Frontier: `{data.get('current_execution_frontier') or 'none'}`\n"
            f"Focus: `{public_text(focus.get('kind') or 'none')}` — `{public_text(focus_ref)}`"
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
            f"• *{item.get('key')}* `{progress_bar(int((item.get('progress') or {}).get('count') or 0), int((item.get('progress') or {}).get('denominator') or 0))}` "
            f"{_metric(item.get('progress') or {})} — {public_text(item.get('status'))}"
            for item in milestones
        ]
        return (
            f"{header}\nFrontier: `{data.get('current_execution_frontier') or 'none'}`\n"
            + "\n".join(lines)
        )
    if command == "milestone":
        item = data.get("item") or {}
        if data.get("error"):
            return f"*Milestone:* {data['error']}"
        progress = item.get("progress") or {}
        return (
            f"*{item.get('key')} — {public_text(item.get('title'))}*\n"
            f"`{progress_bar(int(progress.get('count') or 0), int(progress.get('denominator') or 0))}` "
            f"{_metric(progress)}\nStatus: *{public_text(item.get('status'))}* | "
            f"Verified: {item.get('verified_complete', 0)}/{item.get('total', 0)}"
        )
    if command == "running":
        lines = [
            f"• `{public_text(item.get('ref'))}` — {public_text(item.get('focus'))}: {public_text(item.get('title'))}"
            for item in data.get("items") or []
        ]
        return f"*Active roadmap work:* {data.get('count', 0)}\n" + (
            "\n".join(lines) or "No active roadmap work."
        )
    if command == "blocked":
        items = data.get("items") or []
        lines = [
            f"• `{item.get('ref')}` {item.get('classification')}: {item.get('title')}"
            for item in items[:15]
        ]
        suffix = f"\n…and {len(items) - 15} more" if len(items) > 15 else ""
        return f"*Waiting/blocked items:* {data.get('count', 0)}\n" + "\n".join(lines) + suffix
    if command == "decisions":
        lines = [
            f"• `{item.get('decision_id') or 'unnamed'}` — {public_text(item.get('decision_requested'))}"
            for item in data.get("items") or []
        ]
        return f"*Product Owner decisions pending:* {data.get('count', 0)}\n" + (
            "\n".join(lines) or "No Product Owner decision is pending."
        )
    if command in {"deployments", "validation"}:
        lines = [
            f"• *{item.get('runtime')}* — {public_text(item.get('status'))}"
            + (
                f" | {public_text(item.get('surface'))} | gaps {item.get('capability_gaps', 0)}"
                if command == "deployments"
                else f" | health {public_text(item.get('health'))}"
            )
            for item in data.get("items") or []
        ]
        return (
            f"*AXIS {command.title()}*\n"
            f"`{progress_bar(int(data.get('verified') or 0), int(data.get('total') or 0))}` "
            f"{data.get('verified', 0)}/{data.get('total', 0)} verified\n"
            + ("\n".join(lines) or "No runtime projection is available.")
        )
    if command == "capabilities":
        lines = [
            f"• *{public_text(item.get('capability'))}* — {item.get('passed', 0)}/{item.get('total', 0)} passed"
            for item in data.get("items") or []
        ]
        return (
            "*AXIS Capability Gates*\n"
            f"`{data.get('progress')}` {data.get('passed', 0)}/{data.get('total', 0)} passed\n"
            + ("\n".join(lines) or "No capability projection is available.")
        )
    if command == "recent":
        lines = [
            f"• {public_text(item.get('activity')).title()}"
            + (f" — `{public_text(item.get('ref'))}`" if item.get("ref") else "")
            for item in data.get("items") or []
        ]
        lines.append(
            f"• Routine unchanged evidence checks: {data.get('routine_unchanged_evidence_checks', 0)}"
        )
        return "*Recent AXIS activity*\n" + "\n".join(lines)
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
    environment = os.environ.copy()
    environment["AXIS_SUPERVISOR_COMMAND_SOURCE"] = "product-owner-slack"
    return subprocess.run(
        [sys.executable, str(COMMAND_SCRIPT), command_text],
        text=True,
        capture_output=True,
        timeout=30,
        env=environment,
        check=False,
    )


def _rebuild_frontier() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "axis_supervisor" / "cycle.py"), "rebuild"],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr[-2000:].strip() or completed.stdout[-2000:].strip()
        raise RuntimeError(f"frontier rebuild failed: {detail}")


async def _handle_decision_action(ack, body, action) -> None:
    await ack()
    projection = SlackProjection(ROOT)
    token = projection.env_file()["SLACK_BOT_TOKEN"]
    controller = SlackDecisionController(ROOT, projection.api, _rebuild_frontier)
    await asyncio.to_thread(controller.handle_action, token, body, action)


async def _handle_axis(raw_args: str) -> str:
    authorized = _AUTHORIZED.get()
    _AUTHORIZED.set(False)
    if not authorized:
        return "AXIS Supervisor commands are restricted to the configured Product Owner Slack DM."
    command_text = raw_args.strip() or "help"
    parsed = parse_command(command_text)
    if parsed is None:
        return "Unsupported AXIS Supervisor command. Send `!axis help` for the command list."
    spec, argument = parsed
    command_text = " ".join(value for value in (spec["command"], argument) if value)
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
        args_hint="<" + "|".join(item["command"] for item in command_specs()) + ">",
    )
    for action_id in (
        APPROVE_ACTION_ID,
        APPROVE_CONDITIONS_ACTION_ID,
        REJECT_ACTION_ID,
        CONDITIONS_SUBMIT_ACTION_ID,
    ):
        ctx.register_slack_action_handler(action_id, _handle_decision_action)
