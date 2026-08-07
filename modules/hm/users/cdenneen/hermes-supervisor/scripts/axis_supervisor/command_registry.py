from __future__ import annotations

import re


def _spec(
    command: str,
    description: str,
    params: tuple[str, ...] = (),
    aliases: tuple[str, ...] = (),
) -> dict:
    return {
        "command": command,
        "aliases": aliases,
        "description": description,
        "params": params,
        "authority": "product-owner-slack-dm",
        "confirmation": "none",
        "handler_key": command,
    }


COMMANDS = (
    _spec("help", "List the focused AXIS product commands.", aliases=("commands",)),
    _spec("status", "Show the current AXIS product status."),
    _spec("roadmap", "Show roadmap outcomes and milestone dimensions."),
    _spec("milestones", "Show multidimensional milestone progress."),
    _spec("milestone", "Show one milestone with evidence context.", ("<AX-Mn>",)),
    _spec("capabilities", "Show CLI, Node, Web, Desktop, HUD, and Neural."),
    _spec(
        "capability",
        "Drill into one product capability and its evidence.",
        ("<CLI|Node|Web|Desktop|HUD|Neural>",),
    ),
    _spec("deployments", "Show the product deployment ring."),
    _spec("validation", "Show product validation evidence."),
    _spec("flow", "Show durable delivery lanes, capacity, stalls, and flow."),
    _spec("risk", "Show product risk, debt, and constraints."),
    _spec("decisions", "Show pending Product Owner decisions."),
    _spec("recent", "Show recent product progress."),
    _spec(
        "inspect",
        "Inspect a product item; details and evidence are explicit privileged views.",
        ("<group/project#iid>", "[details|evidence]"),
    ),
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
        if not re.fullmatch(
            r"[^\s#]+/[^\s#]+#\d+(?:\s+(?:details|evidence))?",
            argument,
            re.IGNORECASE,
        ):
            return None
    elif spec["handler_key"] == "milestone":
        if not re.fullmatch(r"AX-M\d+(?:\.\d+)?", argument, re.IGNORECASE):
            return None
    elif spec["handler_key"] == "capability":
        if argument.lower() not in {"cli", "node", "web", "desktop", "hud", "neural"}:
            return None
    elif argument:
        return None
    return spec, argument
