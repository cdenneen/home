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
        return "*AXIS product commands*\n" + "\n".join(
            f"• `!axis {usage(item)}` — {item['description']}"
            for item in registry
        )
    if command == "status":
        kpi = data.get("primary_kpi") or {}
        roadmap = data.get("roadmap_progress") or {}
        risk = data.get("program_risk") or {}
        return (
            "*AXIS Product Status*\n"
            f"Capabilities: {kpi.get('count', 0)}/{kpi.get('denominator', 0)} graduated\n"
            f"Roadmap: {roadmap.get('verified', 0)}/{roadmap.get('total', 0)} verified | "
            f"Frontier `{roadmap.get('frontier') or 'none'}`\n"
            f"Production confidence: {data.get('production_confidence') or 0:g}% | "
            f"Operator confidence: {data.get('operator_confidence') or 0:g}%\n"
            f"Risk: {public_text(risk.get('level') or 'unknown')} ({risk.get('score', 0)}/100) | "
            f"Active product work: {data.get('active_product_work', 0)} | "
            f"Decisions: {data.get('pending_decisions', 0)}"
        )
    if command in {"roadmap", "milestones"}:
        milestones = data.get("milestones") or data.get("items") or []
        lines = [
            f"• *{item.get('key')}* `{progress_bar((item.get('progress') or {}).get('count', 0), (item.get('progress') or {}).get('denominator', 0))}` "
            f"{_metric(item.get('progress') or {})} | production {item.get('production_confidence') or 0:g}% | "
            f"operator {item.get('operator_confidence') if item.get('operator_confidence') is not None else 'not required'} | "
            f"risk {public_text((item.get('program_risk') or {}).get('level') or 'unknown')} | debt {len(item.get('debts') or [])}"
            for item in milestones
        ]
        return f"*AXIS Roadmap*\nFrontier: `{data.get('frontier') or 'none'}`\n" + "\n".join(lines)
    if command == "milestone":
        item = data.get("item") or {}
        if data.get("error"):
            return f"*Milestone:* {data['error']}"
        progress = item.get("progress") or {}
        risk = item.get("program_risk") or {}
        return (
            f"*{item.get('key')} — {public_text(item.get('title'))}*\n"
            f"Delivery `{progress_bar(progress.get('count', 0), progress.get('denominator', 0))}` {_metric(progress)}\n"
            f"Production `{progress_bar(item.get('production_confidence') or 0, 100)}` {item.get('production_confidence') or 0:g}% | "
            f"Operator `{progress_bar(item.get('operator_confidence') or 0, 100)}` "
            f"{item.get('operator_confidence') if item.get('operator_confidence') is not None else 'not required'}\n"
            f"Risk: {public_text(risk.get('level') or 'unknown')} ({risk.get('score', 0)}/100) | "
            f"Debt: {len(item.get('debts') or [])} | Constraint: {public_text(item.get('constraint') or 'none')}"
        )
    if command == "decisions":
        lines = [
            f"• `{item.get('decision_id') or 'unnamed'}` — {public_text(item.get('decision_requested'))}"
            for item in data.get("items") or []
        ]
        return f"*Product Owner decisions pending:* {data.get('count', 0)}\n" + (
            "\n".join(lines) or "No Product Owner decision is pending."
        )
    if command == "deployments":
        lines = [
            f"• *{item.get('ring')}* — {public_text(item.get('status'))} | "
            f"gaps {len(item.get('capability_gaps') or [])}"
            for item in data.get("items") or []
        ]
        return (
            "*AXIS Deployment Ring*\n"
            f"`{progress_bar(data.get('verified') or 0, data.get('total') or 0)}` "
            f"{data.get('verified', 0)}/{data.get('total', 0)} verified\n"
            + ("\n".join(lines) or "No runtime projection is available.")
        )
    if command == "validation":
        lines = [
            f"• *{public_text(item.get('title') or item.get('stream'))}* — "
            f"{public_text(item.get('status'))} | evidence `{public_text((item.get('evidence') or {}).get('uri') or 'pending')}`"
            for item in data.get("items") or []
        ]
        return f"*AXIS Validation*\n{data.get('promoted', 0)}/{data.get('total', 0)} promoted\n" + "\n".join(lines)
    if command == "capabilities":
        lines = [
            f"• *{public_text(item.get('product_capability'))}* — production {item.get('production_confidence') or 0:g}% | "
            f"operator {item.get('operator_confidence') if item.get('operator_confidence') is not None else 'not required'} | "
            f"{public_text(item.get('first_failing_gate') or 'graduated')}"
            for item in data.get("items") or []
        ]
        return (
            "*AXIS Capabilities*\n"
            f"Production confidence {data.get('production_confidence') or 0:g}% | "
            f"Operator confidence {data.get('operator_confidence') or 0:g}%\n"
            + ("\n".join(lines) or "No capability projection is available.")
        )
    if command == "capability":
        item = data.get("item") or {}
        return (
            f"*{public_text(item.get('product_capability'))} Capability*\n"
            f"Production confidence: {item.get('production_confidence') or 0:g}% | "
            f"Operator confidence: {item.get('operator_confidence') if item.get('operator_confidence') is not None else 'not required'}\n"
            f"First evidence gap: {public_text(item.get('first_failing_gate') or 'none')} | "
            f"Risk: {public_text((item.get('program_risk') or {}).get('level') or 'unknown')}\n"
            f"Source-linked product items: {', '.join(item.get('linked_work_items') or []) or 'none'}\n"
            f"Projected merge impacts: {len(data.get('merge_impact_projection') or [])}"
        )
    if command == "risk":
        risk = data.get("program_risk") or {}
        lines = [
            f"• *{item.get('milestone')}* — risk {public_text((item.get('risk') or {}).get('level') or 'unknown')} | "
            f"debt {len(item.get('debt') or [])} | constraint {public_text(item.get('constraint') or 'none')}"
            for item in data.get("milestones") or []
        ]
        return f"*AXIS Risk* — {public_text(risk.get('level') or 'unknown')} ({risk.get('score', 0)}/100)\n" + "\n".join(lines)
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
        return "*Product evidence detail*\n```" + json.dumps(data, indent=2)[:3500] + "```"
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
