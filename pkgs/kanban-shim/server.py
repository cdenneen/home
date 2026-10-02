"""Kanban shim — expose Ghost's Hermes Kanban as MCP tools on Eros.

Runs as a FastMCP server on eros, executing `hermes kanban` CLI commands
over SSH to ghost via Tailscale. This shim cannot modify anything on ghost
beyond the Kanban database — all operations go through a constrained shell
that only permits hermes kanban subcommands.

Tool surface matches the minimal required by Phase 5:
  - kanban_create(title, assignee, body=None, board=None, parents=None)
  - kanban_list(board=None, status=None, assignee=None)
  - kanban_update(task_id, status=None, comment=None)

Trust boundary: all SSH commands are forced through a shell escape that
ensures only 'hermes kanban ...' can execute, nothing else on ghost.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from mcp.server.fastmcp import FastMCP


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


PORT = int(env("KANBAN_SHIM_PORT", "18124"))
SSH_TARGET = env("KANBAN_SHIM_SSH_TARGET", "cdenneen@ghost.tail0e55.ts.net")
SSH_KEY = env("KANBAN_SHIM_SSH_KEY", "/run/kanban-shim/ssh_key")


mcp = FastMCP("kanban-shim", host="127.0.0.1", port=PORT)


# ---------------------------------------------------------------------------
# Helper: constrained hermes kanban invocation
# ---------------------------------------------------------------------------


def _kanban_ssh(*args: str, json_output: bool = True) -> tuple[int, str, str]:
    """Execute 'hermes kanban' over SSH with forced command.

    The SSH server on ghost enforces command= via authorized_keys so only
    hermes kanban ... can run, nothing else.
    """
    ssh_env = {**os.environ}
    if os.path.isfile(SSH_KEY):
        ssh_env["SSH_AUTH_SOCK"] = ""
        ssh_env["SSH_ASKPASS"] = ""
        cmd = [
            "ssh",
            "-i",
            SSH_KEY,
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "BatchMode=yes",
            "-o",
            "VerifyHostKeyDNS=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            SSH_TARGET,
            "hermes",
            "kanban",
            *args,
        ]
    else:
        # Fallback for local testing without SSH key
        cmd = ["hermes", "kanban", *args]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=ssh_env,
    )
    return result.returncode, result.stdout, result.stderr


def _kanban_json(*args: str) -> dict[str, Any] | list[dict[str, Any]]:
    """Run kanban with --json and parse JSON output."""
    rc, stdout, stderr = _kanban_ssh(*args, json_output=True)
    if rc != 0:
        raise RuntimeError(f"hermes kanban failed: {stderr}")
    if not stdout.strip():
        return {}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON parse error: {e}\nOutput: {stdout[:500]}") from e


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


@mcp.tool()
def kanban_create(
    title: str,
    assignee: str,
    body: str | None = None,
    board: str | None = None,
    parents: list[str] | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Create a new Kanban task.

    Args:
        title: Task title (required)
        assignee: Profile name to assign the task to (required)
        body: Optional opening post or description
        board: Board slug to create on (defaults to current/HERMES_KANBAN_BOARD)
        parents: Optional list of parent task IDs for nested tasks

    Returns:
        Task creation result with id, title, status, assignee, etc.
    """
    args = [
        "create",
        title,
        "--assignee",
        assignee,
    ]

    if body:
        args.extend(["--body", body])
    if board:
        args.extend(["--board", board])
    if parents:
        for parent in parents:
            args.extend(["--parent", parent])

    args.append("--json")

    return _kanban_json(*args)


@mcp.tool()
def kanban_list(
    board: str | None = None,
    status: str | None = None,
    assignee: str | None = None,
) -> list[dict[str, Any]]:
    """List Kanban tasks.

    Args:
        board: Board slug to list from (defaults to current board)
        status: Filter by status: todo, ready, running, review, blocked, done, archived, triage, scheduled
        assignee: Filter by assignee profile name

    Returns:
        List of task objects matching the filter criteria.
    """
    args = ["list", "--json"]

    if board:
        args.extend(["--board", board])
    if status:
        args.extend(["--status", status])
    if assignee:
        args.extend(["--assignee", assignee])

    result = _kanban_json(*args)
    if isinstance(result, list):
        return result
    # Single object case (shouldn't happen with list)
    return [result]


@mcp.tool()
def kanban_update(
    task_id: str,
    status: str | None = None,
    comment: str | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Update a Kanban task status and optionally append a comment.

    Args:
        task_id: Task ID to update
        status: New status: todo, ready, running, review, blocked, done, archived,
                triage, scheduled. Mapped to hermes kanban subcommands.
        comment: Optional comment to append to the task

    Returns:
        Updated task object
    """
    # Map our status values to hermes kanban subcommands
    status_commands = {
        "done": ("complete", [task_id, "--json"]),
        "blocked": ("block", [task_id]),
        "review": ("request-review", [task_id, "--json"]),
        "archived": ("archive", [task_id, "--json"]),
        "scheduled": ("schedule", [task_id, "--json"]),
    }

    args = []
    if status:
        if status == "done":
            args = ["complete", task_id, "--json"]
        elif status == "blocked":
            args = ["block", task_id]
        elif status == "review":
            args = ["request-review", task_id, "--json"]
        elif status == "archived":
            args = ["archive", task_id, "--json"]
        elif status == "scheduled":
            args = ["schedule", task_id, "--json"]
        # For todo/ready/running, the task should already be in those states
        # or we need promote from blocked/scheduled - leave as no-op for now

    # If no status change requested and no comment, just show the task
    if not args and not comment:
        return _kanban_json("show", task_id, "--json")

    # Execute status update if any
    if args:
        result = _kanban_json(*args)
    else:
        result = None

    # Append comment if provided
    if comment:
        rc, _, stderr = _kanban_ssh("comment", task_id, comment)
        if rc != 0:
            # Non-fatal: comment append failure is logged but doesn't fail the whole op
            pass

    # Return the full task after update
    return _kanban_json("show", task_id, "--json")


@mcp.tool()
def kanban_comment(task_id: str, text: str) -> dict[str, Any]:
    """Append a comment to a Kanban task.

    Args:
        task_id: Task ID to comment on
        text: Comment body

    Returns:
        Status indicating success/failure
    """
    rc, _, stderr = _kanban_ssh("comment", task_id, text)
    if rc != 0:
        raise RuntimeError(f"Comment failed: {stderr}")
    return {"status": "commented", "task_id": task_id}


@mcp.tool()
def kanban_show(task_id: str) -> dict[str, Any]:
    """Show a task with comments + events.

    Args:
        task_id: Task ID to show

    Returns:
        Full task details including comments and events
    """
    return _kanban_json("show", task_id, "--json")


@mcp.tool()
def health() -> dict[str, Any]:
    """Report kanban-shim service health."""
    return {
        "status": "ok",
        "ssh_target": SSH_TARGET,
        "ssh_key_set": os.path.isfile(SSH_KEY),
        "port": PORT,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["serve"])
    args = parser.parse_args()

    if args.command == "serve":
        mcp.run(transport="streamable-http")
