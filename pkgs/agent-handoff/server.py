"""Agent handoff service — durable cross-agent context and session bridging.

Runs as a FastMCP server on eros, backed by cdenneen/agent-context on GitHub.
Every agent that connects to the eros MCP aggregate automatically gets these
tools; the agent-handoff skill teaches agents when to use them proactively.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


REPO_DIR = Path(env("AGENT_HANDOFF_REPO", "/var/lib/agent-handoff/agent-context"))
GITHUB_REPO = env("AGENT_HANDOFF_GITHUB_REPO", "cdenneen/agent-context")
DEPLOY_KEY = env("AGENT_HANDOFF_DEPLOY_KEY", "/run/agent-handoff/deploy_key")
PORT = int(env("AGENT_HANDOFF_PORT", "18123"))

# Sub-directories within the repo
HANDOFFS_DIR = REPO_DIR / "handoffs"
CONTEXT_DIR = REPO_DIR / "context"
MEMORY_DIR = REPO_DIR / "memory"

mcp = FastMCP(
    "agent-handoff", host="127.0.0.1", port=PORT
)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    git_env = {**os.environ}
    if Path(DEPLOY_KEY).exists():
        git_env["GIT_SSH_COMMAND"] = f"ssh -i {DEPLOY_KEY} -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
    return subprocess.run(
        ["git", "-C", str(REPO_DIR), *args],
        capture_output=True,
        text=True,
        check=check,
        env=git_env,
    )


def _ensure_repo() -> None:
    """Clone the repo if it doesn't exist; pull if it does."""
    if not (REPO_DIR / ".git").exists():
        REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
        git_env = {**os.environ}
        if Path(DEPLOY_KEY).exists():
            git_env["GIT_SSH_COMMAND"] = f"ssh -i {DEPLOY_KEY} -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
        url = f"git@github.com:{GITHUB_REPO}.git"
        subprocess.run(
            ["git", "clone", url, str(REPO_DIR)],
            check=True,
            capture_output=True,
            text=True,
            env=git_env,
        )
    else:
        _git("pull", "--rebase", "--autostash", check=False)
    for d in (HANDOFFS_DIR, CONTEXT_DIR, MEMORY_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _commit_and_push(message: str) -> str:
    """Stage all changes, commit, push. Returns short sha."""
    _git("add", "-A")
    result = _git("diff", "--cached", "--quiet", check=False)
    if result.returncode == 0:
        # Nothing to commit
        return _git("rev-parse", "--short", "HEAD").stdout.strip()
    _git("commit", "-m", message)
    _git("push", check=False)  # non-fatal; GitHub is fallback, not primary
    return _git("rev-parse", "--short", "HEAD").stdout.strip()


def _stable_id(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

@mcp.tool()
def write_handoff(
    topic: str,
    content: str,
    project: str = "",
    workspace: str = "",
    agent: str = "",
    status: str = "",
    next_action: str = "",
    blockers: str = "",
    tags: str = "",
) -> dict[str, Any]:
    """Write a handoff document so any agent or interface can resume this work.

    Call this proactively — at decision gates, blockers, session end, or when
    context is getting heavy. Do not wait to be asked.

    Returns a handoff_id and a resume_prompt suitable for pasting into any
    agent session or sharing via /handoff in Slack.
    """
    _ensure_repo()
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    slug = topic.lower().replace(" ", "-")[:40]
    handoff_id = f"{ts}-{_stable_id(topic + ts)}"
    filename = f"{handoff_id}-{slug}.md"

    frontmatter = {
        "id": handoff_id,
        "topic": topic,
        "project": project,
        "workspace": workspace,
        "agent": agent,
        "status": status,
        "next_action": next_action,
        "blockers": blockers,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "written_at": now.isoformat(),
    }

    doc = f"""---
{json.dumps(frontmatter, indent=2)}
---

# Handoff: {topic}

**Written:** {now.strftime("%Y-%m-%d %H:%M UTC")}  
**Agent:** {agent or "unknown"}  
**Project:** {project or "—"}  
**Workspace:** {workspace or "—"}

## Status
{status or "—"}

## Next Action
{next_action or "—"}

## Blockers
{blockers or "None"}

---

{content}
"""

    path = HANDOFFS_DIR / filename
    path.write_text(doc, encoding="utf-8")
    sha = _commit_and_push(f"handoff: {topic[:60]}")

    resume_prompt = _build_resume_prompt(
        handoff_id=handoff_id,
        topic=topic,
        status=status,
        next_action=next_action,
        blockers=blockers,
        content=content,
        project=project,
        workspace=workspace,
    )

    return {
        "handoff_id": handoff_id,
        "file": filename,
        "sha": sha,
        "resume_prompt": resume_prompt,
        "resume_commands": _build_resume_commands(handoff_id, workspace),
    }


@mcp.tool()
def read_handoff(handoff_id: str) -> dict[str, Any]:
    """Read a specific handoff by ID. Use at session start when resuming work."""
    _ensure_repo()
    matches = list(HANDOFFS_DIR.glob(f"{handoff_id}*.md"))
    if not matches:
        raise ValueError(f"no handoff found with id prefix: {handoff_id}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous id prefix {handoff_id}: {[m.name for m in matches]}")
    path = matches[0]
    raw = path.read_text(encoding="utf-8")
    # Parse frontmatter
    meta: dict[str, Any] = {}
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end > 0:
            try:
                meta = json.loads(raw[4:end])
            except json.JSONDecodeError:
                pass
            raw = raw[end + 5:]
    return {"id": handoff_id, "meta": meta, "content": raw, "file": path.name}


@mcp.tool()
def list_handoffs(
    project: str = "",
    workspace: str = "",
    limit: int = 10,
    tags: str = "",
) -> list[dict[str, Any]]:
    """List recent handoffs, optionally filtered by project, workspace, or tags.

    Call at session start when working in a known project/workspace to
    automatically surface prior context without waiting for the user to ask.
    """
    _ensure_repo()
    tag_filter = {t.strip() for t in tags.split(",") if t.strip()}
    results = []
    for path in sorted(HANDOFFS_DIR.glob("*.md"), reverse=True):
        raw = path.read_text(encoding="utf-8")
        meta: dict[str, Any] = {}
        if raw.startswith("---\n"):
            end = raw.find("\n---\n", 4)
            if end > 0:
                try:
                    meta = json.loads(raw[4:end])
                except json.JSONDecodeError:
                    pass
        if project and meta.get("project", "").lower() != project.lower():
            continue
        if workspace and meta.get("workspace", "").lower() != workspace.lower():
            continue
        if tag_filter and not tag_filter.intersection(set(meta.get("tags", []))):
            continue
        results.append({
            "id": meta.get("id", path.stem),
            "topic": meta.get("topic", ""),
            "project": meta.get("project", ""),
            "workspace": meta.get("workspace", ""),
            "status": meta.get("status", ""),
            "next_action": meta.get("next_action", ""),
            "written_at": meta.get("written_at", ""),
            "tags": meta.get("tags", []),
        })
        if len(results) >= min(limit, 20):
            break
    return results


@mcp.tool()
def search_handoffs(query: str, project: str = "", limit: int = 8) -> list[dict[str, Any]]:
    """Full-text search across all handoffs. Use when you need prior context
    on a topic but don't know the exact handoff ID."""
    _ensure_repo()
    query_lower = query.lower()
    results = []
    for path in sorted(HANDOFFS_DIR.glob("*.md"), reverse=True):
        raw = path.read_text(encoding="utf-8")
        if query_lower not in raw.lower():
            continue
        meta: dict[str, Any] = {}
        if raw.startswith("---\n"):
            end = raw.find("\n---\n", 4)
            if end > 0:
                try:
                    meta = json.loads(raw[4:end])
                except json.JSONDecodeError:
                    pass
        if project and meta.get("project", "").lower() != project.lower():
            continue
        # Find the matching snippet
        idx = raw.lower().find(query_lower)
        snippet = raw[max(0, idx - 80):idx + 160].strip()
        results.append({
            "id": meta.get("id", path.stem),
            "topic": meta.get("topic", ""),
            "project": meta.get("project", ""),
            "status": meta.get("status", ""),
            "written_at": meta.get("written_at", ""),
            "snippet": snippet,
        })
        if len(results) >= min(limit, 20):
            break
    return results


@mcp.tool()
def get_resume_prompt(handoff_id: str) -> dict[str, Any]:
    """Generate a formatted resume prompt and CLI commands for a handoff.

    Use this when a user asks for /handoff or wants to continue work in a
    different interface. Returns the prompt text and ready-to-run commands
    for claude, codex, and pi.
    """
    result = read_handoff(handoff_id)
    meta = result["meta"]
    content = result["content"]
    return {
        "prompt": _build_resume_prompt(
            handoff_id=handoff_id,
            topic=meta.get("topic", ""),
            status=meta.get("status", ""),
            next_action=meta.get("next_action", ""),
            blockers=meta.get("blockers", ""),
            content=content,
            project=meta.get("project", ""),
            workspace=meta.get("workspace", ""),
        ),
        "commands": _build_resume_commands(handoff_id, meta.get("workspace", "")),
        "slack_block": _build_slack_block(handoff_id, meta),
    }


@mcp.tool()
def write_context(
    name: str,
    content: str,
    kind: str = "notes",
) -> dict[str, str]:
    """Write a named shared context document (architecture notes, decisions,
    running plans). Persistent across all agents and sessions.

    kind: notes | architecture | decisions | plan | reference
    """
    _ensure_repo()
    safe_name = name.replace(" ", "-").replace("/", "-")[:80]
    path = CONTEXT_DIR / f"{safe_name}.md"
    path.write_text(content, encoding="utf-8")
    sha = _commit_and_push(f"context: {name[:60]}")
    return {"name": name, "file": path.name, "sha": sha}


@mcp.tool()
def read_context(name: str) -> dict[str, str]:
    """Read a named shared context document by name or filename prefix."""
    _ensure_repo()
    safe_name = name.replace(" ", "-").replace("/", "-")
    matches = list(CONTEXT_DIR.glob(f"{safe_name}*.md"))
    if not matches:
        # Try partial match
        matches = [p for p in CONTEXT_DIR.glob("*.md") if safe_name.lower() in p.stem.lower()]
    if not matches:
        raise ValueError(f"no context document found for: {name}")
    path = sorted(matches)[0]
    return {"name": path.stem, "content": path.read_text(encoding="utf-8")}


@mcp.tool()
def list_context(kind: str = "") -> list[dict[str, str]]:
    """List available shared context documents."""
    _ensure_repo()
    results = []
    for path in sorted(CONTEXT_DIR.glob("*.md")):
        results.append({"name": path.stem, "file": path.name})
    return results


@mcp.tool()
def cos_update(
    status: str,
    message: str,
    agent: str = "claude-desktop",
) -> dict[str, Any]:
    """Send a status update to the Chief of Staff on ghost, which logs it to the Kanban.

    Use this at session start, on every significant decision or discovery,
    when blocked, when requesting review, and at session end. CoS is the
    single source of truth for all in-flight work — calling this keeps the
    Kanban accurate so no work is invisible or repeated.

    Args:
        status: One of: started, update, blocked, review, completed
        message: Human-readable update. Include what you did, why, links to
                 PRs/issues, what's next. Be specific — CoS uses this to
                 update the Kanban and brief other agents.
        agent:   Caller identity (default: claude-desktop). Used by CoS to
                 attribute the update and route to the right Kanban board.

    Returns a dict with 'sent', 'cos_reply', and 'kanban_hint' fields.
    """
    valid_statuses = {"started", "update", "blocked", "review", "completed"}
    if status not in valid_statuses:
        return {
            "error": f"Invalid status '{status}'. Must be one of: {', '.join(sorted(valid_statuses))}",
            "sent": False,
        }

    formatted = f"[{agent}] [{status}] {message}"

    # ssh concatenates every arg after the host into one string and hands it
    # to the remote login shell (zsh on ghost), which then glob-expands
    # anything unquoted - formatted's own "[...]" brackets triggered
    # "no matches found" there. Quote the remote command as a single shell
    # token so zsh sees it as one opaque argument.
    remote_cmd = " ".join(
        shlex.quote(part) for part in ("hermes", "peer", "dm", "ghost/chief-of-staff", formatted)
    )

    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=10",
                "ghost",
                remote_cmd,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        cos_reply = (result.stdout or "").strip()
        # Strip the sops helper line that always appears on ghost
        cos_reply = "\n".join(
            line for line in cos_reply.splitlines()
            if not line.startswith("Command helper:")
        ).strip()

        # Parse structured task_id from CoS reply JSON block if present
        task_id = None
        kanban_status = None
        board = None
        for line in cos_reply.splitlines():
            line = line.strip()
            if line.startswith("{") and "task_id" in line:
                try:
                    parsed = json.loads(line)
                    task_id = parsed.get("task_id")
                    kanban_status = parsed.get("status")
                    board = parsed.get("board")
                except (json.JSONDecodeError, ValueError):
                    pass

        return {
            "sent": result.returncode == 0,
            "cos_reply": cos_reply or "(no reply)",
            "task_id": task_id,
            "kanban_status": kanban_status,
            "board": board,
            "kanban_hint": (
                f"Task ID: {task_id} on {board}. Use this task_id in all subsequent cos_update calls this session."
                if task_id else
                "CoS has received this update and will log it to the Kanban."
            ),
            "error": result.stderr.strip() if result.returncode != 0 else None,
        }
    except subprocess.TimeoutExpired:
        return {"sent": False, "error": "SSH to ghost timed out after 30s", "cos_reply": None}
    except Exception as exc:  # noqa: BLE001
        return {"sent": False, "error": str(exc), "cos_reply": None}


@mcp.tool()
def health() -> dict[str, Any]:
    """Report agent-handoff service health."""
    repo_ok = (REPO_DIR / ".git").exists()
    handoff_count = len(list(HANDOFFS_DIR.glob("*.md"))) if HANDOFFS_DIR.exists() else 0
    context_count = len(list(CONTEXT_DIR.glob("*.md"))) if CONTEXT_DIR.exists() else 0
    return {
        "status": "ok" if repo_ok else "repo_missing",
        "repo": str(REPO_DIR),
        "github_repo": GITHUB_REPO,
        "handoffs": handoff_count,
        "context_docs": context_count,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_resume_prompt(
    handoff_id: str,
    topic: str,
    status: str,
    next_action: str,
    blockers: str,
    content: str,
    project: str,
    workspace: str,
) -> str:
    workspace_line = f"\nWorkspace: {workspace}" if workspace else ""
    project_line = f"\nProject: {project}" if project else ""
    blockers_section = f"\n\nBlockers:\n{blockers}" if blockers else ""

    # Truncate content if very long
    body = content.strip()
    if len(body) > 4000:
        body = body[:4000] + "\n\n[... truncated — read full handoff with handoff_id: " + handoff_id + "]"

    return textwrap.dedent(f"""\
        You are resuming work on: {topic}
        Handoff ID: {handoff_id}{project_line}{workspace_line}

        Current status: {status or "see handoff"}
        Immediate next action: {next_action or "see handoff"}{blockers_section}

        --- Full context ---
        {body}
        --- End context ---

        Read the above, confirm your understanding of where we are, then take the immediate next action.
        Do not ask me to repeat the context — it is all above.
    """).strip()


def _build_resume_commands(handoff_id: str, workspace: str) -> dict[str, str]:
    """Build ready-to-run CLI commands for each agent interface."""
    # The agent-resume shell alias (deployed via files.nix) fetches the prompt
    # from eros and prints it. Users can paste the output as the first message.
    base = f"agent-resume {handoff_id}"
    ws = workspace or "~/src/workspace"
    return {
        "claude": f'cd {ws} && claude "$(${base})"',
        "codex": f'cd {ws} && codex --instructions "$(${base})"',
        "pi": f'cd {ws} && pi "$(${base})"',
        "shell": f"{base}  # prints prompt — paste into any interface",
    }


def _build_slack_block(handoff_id: str, meta: dict[str, Any]) -> str:
    topic = meta.get("topic", handoff_id)
    status = meta.get("status", "")
    next_action = meta.get("next_action", "")
    project = meta.get("project", "")
    return (
        f"📎 *Handoff ready* — `{topic}`\n"
        + (f"*Project:* {project}\n" if project else "")
        + (f"*Status:* {status}\n" if status else "")
        + (f"*Next:* {next_action}\n" if next_action else "")
        + f"\n*Resume in terminal:*\n"
        f"```\nagent-resume {handoff_id}\n```\n"
        f"Then pipe to your agent of choice:\n"
        f"```\nclaude \"$(agent-resume {handoff_id})\"\n"
        f"pi \"$(agent-resume {handoff_id})\"\n"
        f"codex --instructions \"$(agent-resume {handoff_id})\"\n```"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["serve"])
    args = parser.parse_args()

    if args.command == "serve":
        _ensure_repo()
        mcp.run(transport="streamable-http")
