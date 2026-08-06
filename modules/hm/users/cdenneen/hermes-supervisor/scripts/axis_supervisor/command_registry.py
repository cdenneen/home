from __future__ import annotations

import re

COMMANDS = (
    {
        "command": "help",
        "aliases": ("commands",),
        "description": "List the deterministic AXIS Supervisor commands.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "help",
    },
    {
        "command": "status",
        "aliases": (),
        "description": "Show current supervisor and roadmap status.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "status",
    },
    {
        "command": "roadmap",
        "aliases": (),
        "description": "Show the complete roadmap projection.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "roadmap",
    },
    {
        "command": "milestones",
        "aliases": (),
        "description": "Show milestone readiness and progress.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "milestones",
    },
    {
        "command": "milestone",
        "aliases": (),
        "description": "Show one milestone proof summary.",
        "params": ("<AX-Mn>",),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "milestone",
    },
    {
        "command": "running",
        "aliases": (),
        "description": "Show active supervisor assignments.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "running",
    },
    {
        "command": "blocked",
        "aliases": (),
        "description": "Show waiting and blocked work items.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "blocked",
    },
    {
        "command": "decisions",
        "aliases": (),
        "description": "Show pending Product Owner decisions.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "decisions",
    },
    {
        "command": "deployments",
        "aliases": (),
        "description": "Show deployment progress by runtime.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "deployments",
    },
    {
        "command": "validation",
        "aliases": (),
        "description": "Show runtime validation proof.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "validation",
    },
    {
        "command": "capabilities",
        "aliases": (),
        "description": "Show capability gate progress.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "capabilities",
    },
    {
        "command": "recent",
        "aliases": (),
        "description": "Show recent supervisor activity.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "recent",
    },
    {
        "command": "inspect",
        "aliases": (),
        "description": "Inspect one governed work item.",
        "params": ("<group/project#iid>",),
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": "inspect",
    },
    {
        "command": "reconcile",
        "aliases": (),
        "description": "Trigger the configured reconciliation job.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "explicit-command",
        "handler_key": "reconcile",
    },
    {
        "command": "pause",
        "aliases": (),
        "description": "Pause mutation and keep observation enabled.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "explicit-command",
        "handler_key": "pause",
    },
    {
        "command": "resume",
        "aliases": (),
        "description": "Resume enabled supervisor operation.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "explicit-command",
        "handler_key": "resume",
    },
    {
        "command": "drain",
        "aliases": (),
        "description": "Drain active work without claiming new work.",
        "params": (),
        "authority": "product-owner-slack-dm",
        "confirmation": "explicit-command",
        "handler_key": "drain",
    },
)


def command_specs() -> tuple[dict, ...]:
    return COMMANDS


def usage(spec: dict) -> str:
    return " ".join((spec["command"], *spec["params"]))


def parse_command(text: str) -> tuple[dict, str] | None:
    command, _, argument = text.strip().partition(" ")
    command = command.lower()
    argument = argument.strip()
    spec = next(
        (
            item
            for item in COMMANDS
            if command == item["command"] or command in item["aliases"]
        ),
        None,
    )
    if spec is None:
        return None
    if spec["handler_key"] == "inspect":
        if not re.fullmatch(r"[^\s#]+/[^\s#]+#\d+", argument):
            return None
    elif spec["handler_key"] == "milestone":
        if not re.fullmatch(r"AX-M\d+(?:\.\d+)?", argument, re.IGNORECASE):
            return None
    elif argument:
        return None
    return spec, argument
