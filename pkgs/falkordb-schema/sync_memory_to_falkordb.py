#!/usr/bin/env python3
"""Sync Hermes profile memory facts to FalkorDB knowledge graph.

Called as a Hermes context_engine post-memory-write hook, or manually.
Reads MEMORY.md and USER.md for the active profile and upserts key facts
as Agent/Decision nodes in FalkorDB so all agents (Paul, CoS, Claude Desktop)
can query them via the FalkorDB MCP on eros.

Usage:
    python sync_memory_to_falkordb.py [--profile <name>] [--hermes-home <path>]
    HERMES_HOME=~/.hermes/profiles/ops python sync_memory_to_falkordb.py

Environment variables:
    HERMES_HOME:     Path to the active Hermes profile directory
    FALKORDB_HOST:   FalkorDB host (default: 127.0.0.1 or eros.tail0e55.ts.net)
    FALKORDB_PORT:   FalkorDB port (default: 6380)
    AGENT_ID:        Agent identity for the node (default: derived from HERMES_HOME)
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import re
import sys
from pathlib import Path


FALKORDB_HOST = os.environ.get("FALKORDB_HOST", "eros.tail0e55.ts.net")
FALKORDB_PORT = int(os.environ.get("FALKORDB_PORT", "6380"))
GRAPH_NAME = "knowledge"


def escape_cypher(value: str) -> str:
    """Escape a string value for safe inclusion in a Cypher query."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def get_hermes_home() -> Path:
    """Resolve the active Hermes profile directory."""
    if env := os.environ.get("HERMES_HOME"):
        return Path(env).expanduser()
    # Fall back to default ops profile
    return Path("~/.hermes/profiles/ops").expanduser()


def derive_agent_id(hermes_home: Path) -> str:
    """Derive agent ID from profile path, e.g. ops@nyx or chief-of-staff@ghost."""
    profile = hermes_home.name
    hostname = os.uname().nodename.split(".")[0]
    return f"{profile}@{hostname}"


def read_memory_files(hermes_home: Path) -> dict[str, str]:
    """Read MEMORY.md and USER.md from the profile directory."""
    files = {}
    for name in ("MEMORY.md", "USER.md"):
        path = hermes_home / "memories" / name
        if path.exists():
            files[name] = path.read_text(encoding="utf-8").strip()
    return files


def parse_memory_entries(content: str) -> list[str]:
    """Split memory content on § separator into individual entries."""
    return [e.strip() for e in re.split(r"\s*§\s*", content) if e.strip()]


def upsert_agent_node(client: "Redis", agent_id: str, memory_text: str, now: str) -> None:  # type: ignore[name-defined]
    """Upsert an Agent node with current memory snapshot."""
    escaped_id = escape_cypher(agent_id)
    escaped_memory = escape_cypher(memory_text[:2000])  # cap at 2000 chars
    escaped_now = escape_cypher(now)

    query = f"""
MERGE (a:Agent {{id: "{escaped_id}"}})
ON CREATE SET
    a.name = "{escaped_id}",
    a.type = "ai",
    a.last_active = "{escaped_now}",
    a.memory_snapshot = "{escaped_memory}"
ON MATCH SET
    a.last_active = "{escaped_now}",
    a.memory_snapshot = "{escaped_memory}"
"""
    client.execute_command("GRAPH.QUERY", GRAPH_NAME, query.strip())


def upsert_human_node(client: "Redis", user_content: str, now: str) -> None:  # type: ignore[name-defined]
    """Upsert the cdenneen human Agent node from USER.md facts."""
    # Extract key facts from USER.md
    agent_id = "cdenneen"
    name = "Chris Denneen"

    # Try to extract title
    title_match = re.search(r"—\s*([^.]+?)\s+at\s+AP", user_content)
    title = title_match.group(1).strip() if title_match else "Director of Cloud and Infrastructure Engineering"

    escaped_id = escape_cypher(agent_id)
    escaped_name = escape_cypher(name)
    escaped_title = escape_cypher(title)
    escaped_snapshot = escape_cypher(user_content[:2000])
    escaped_now = escape_cypher(now)

    query = f"""
MERGE (a:Agent {{id: "{escaped_id}"}})
ON CREATE SET
    a.name = "{escaped_name}",
    a.type = "human",
    a.title = "{escaped_title}",
    a.last_active = "{escaped_now}",
    a.memory_snapshot = "{escaped_snapshot}"
ON MATCH SET
    a.name = "{escaped_name}",
    a.title = "{escaped_title}",
    a.last_active = "{escaped_now}",
    a.memory_snapshot = "{escaped_snapshot}"
"""
    client.execute_command("GRAPH.QUERY", GRAPH_NAME, query.strip())


def upsert_decision_nodes(client: "Redis", entries: list[str], agent_id: str, now: str) -> None:  # type: ignore[name-defined]
    """Upsert significant memory entries as Decision nodes linked to the agent."""
    escaped_agent = escape_cypher(agent_id)
    escaped_now = escape_cypher(now)

    decision_ids = []
    for entry in entries:
        if len(entry) < 30:
            continue  # skip trivial entries

        # Keep IDs readable while distinguishing entries with shared prefixes.
        slug = re.sub(r"[^a-z0-9]+", "-", entry[:40].lower()).strip("-")
        digest = hashlib.sha256(entry.encode()).hexdigest()[:16]
        decision_id = f"mem-{agent_id}-{slug}-{digest}"
        decision_ids.append(decision_id)

        # First line is the title; rest is description
        lines = entry.split("\n", 1)
        title = lines[0][:120]
        description = entry[:500]

        escaped_did = escape_cypher(decision_id)
        escaped_title = escape_cypher(title)
        escaped_desc = escape_cypher(description)

        query = f"""
MERGE (d:Decision {{id: "{escaped_did}"}})
ON CREATE SET
    d.title = "{escaped_title}",
    d.description = "{escaped_desc}",
    d.made_at = "{escaped_now}",
    d.author = "{escaped_agent}"
ON MATCH SET
    d.title = "{escaped_title}",
    d.description = "{escaped_desc}"
MERGE (a:Agent {{id: "{escaped_agent}"}})
MERGE (a)-[:KNOWS]->(d)
"""
        client.execute_command("GRAPH.QUERY", GRAPH_NAME, query.strip())

    keep_ids = ", ".join(f'"{escape_cypher(value)}"' for value in decision_ids)
    cleanup = f"""
MATCH (a:Agent {{id: "{escaped_agent}"}})-[r:KNOWS]->(d:Decision)
WHERE d.id STARTS WITH "mem-{escaped_agent}-"
  AND NOT (d.id IN [{keep_ids}])
DELETE r, d
"""
    client.execute_command("GRAPH.QUERY", GRAPH_NAME, cleanup.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Hermes memory to FalkorDB")
    parser.add_argument("--profile", help="Profile name (e.g. ops, chief-of-staff)")
    parser.add_argument("--hermes-home", help="Path to Hermes profile directory")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no writes")
    args = parser.parse_args()

    # Resolve hermes home
    if args.hermes_home:
        hermes_home = Path(args.hermes_home).expanduser()
    elif args.profile:
        hermes_home = Path(f"~/.hermes/profiles/{args.profile}").expanduser()
    else:
        hermes_home = get_hermes_home()

    agent_id = os.environ.get("AGENT_ID") or derive_agent_id(hermes_home)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    print(f"Syncing memory for agent: {agent_id}")
    print(f"  Profile dir: {hermes_home}")
    print(f"  FalkorDB:    {FALKORDB_HOST}:{FALKORDB_PORT}")

    memory_files = read_memory_files(hermes_home)
    if not memory_files:
        print("  No memory files found — nothing to sync.", file=sys.stderr)
        sys.exit(1)

    for fname, content in memory_files.items():
        entries = parse_memory_entries(content)
        print(f"  {fname}: {len(entries)} entries")

    if args.dry_run:
        print("Dry run — no writes performed.")
        return

    try:
        from redis import Redis
    except ImportError:
        print("Error: redis package not available. Install with: pip install redis", file=sys.stderr)
        sys.exit(1)

    try:
        client = Redis(host=FALKORDB_HOST, port=FALKORDB_PORT, decode_responses=True)
        client.ping()
        print("  Connected to FalkorDB ✓")
    except Exception as e:
        print(f"  Cannot connect to FalkorDB at {FALKORDB_HOST}:{FALKORDB_PORT}: {e}", file=sys.stderr)
        sys.exit(1)

    # Upsert agent node with MEMORY.md snapshot
    if "MEMORY.md" in memory_files:
        upsert_agent_node(client, agent_id, memory_files["MEMORY.md"], now)
        entries = parse_memory_entries(memory_files["MEMORY.md"])
        upsert_decision_nodes(client, entries, agent_id, now)
        print(f"  Agent node upserted: {agent_id}")
        print(f"  Decision nodes upserted: {len(entries)}")

    # Upsert human node from USER.md
    if "USER.md" in memory_files:
        upsert_human_node(client, memory_files["USER.md"], now)
        print("  Human agent node (cdenneen) upserted")

    print("Done.")


if __name__ == "__main__":
    main()
