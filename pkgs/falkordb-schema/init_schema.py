#!/usr/bin/env python3
"""Initialize FalkorDB knowledge graph schema.

Creates constraints and indexes for the initial graph schema on first run.
Run manually: `python init_schema.py`

Environment variables:
- FALKORDB_HOST: FalkorDB host (default: 127.0.0.1)
- FALKORDB_PORT: FalkorDB port (default: 6380)
"""

from __future__ import annotations

import os
import sys

from redis import Redis

FALKORDB_HOST = os.environ.get("FALKORDB_HOST", "127.0.0.1")
FALKORDB_PORT = int(os.environ.get("FALKORDB_PORT", "6380"))


def init_schema() -> None:
    """Create constraints and indexes for the graph schema."""
    client = Redis(host=FALKORDB_HOST, port=FALKORDB_PORT, decode_responses=True)

    # FalkorDB uses GRAPH.QUERY with Cypher-like syntax
    constraints = [
        "CREATE CONSTRAINT task_id FOR (t:Task) REQUIRE t.id IS UNIQUE",
        "CREATE CONSTRAINT agent_id FOR (a:Agent) REQUIRE a.id IS UNIQUE",
        "CREATE CONSTRAINT file_path FOR (f:File) REQUIRE f.path IS UNIQUE",
        "CREATE CONSTRAINT repo_name FOR (r:Repo) REQUIRE r.name IS UNIQUE",
        "CREATE CONSTRAINT issue_id FOR (i:Issue) REQUIRE i.id IS UNIQUE",
        "CREATE CONSTRAINT decision_id FOR (d:Decision) REQUIRE d.id IS UNIQUE",
    ]

    print(f"Connecting to FalkorDB at {FALKORDB_HOST}:{FALKORDB_PORT}...")
    try:
        client.ping()
        print("Connected successfully.")
    except Exception as e:
        print(f"Failed to connect: {e}", file=sys.stderr)
        sys.exit(1)

    print("\nCreating constraints...")
    for constraint in constraints:
        try:
            client.execute_command("GRAPH.QUERY", "knowledge", constraint)
            print(f"  ✓ {constraint}")
        except Exception as e:
            # Constraint may already exist
            print(f"  ! {constraint}")
            print(f"    (possibly already exists: {e})")

    print("\nSchema initialization complete.")
    print("\nAvailable node types: Agent, Task, File, Decision, Repo, Issue")
    print("\nExample queries:")
    print('  GRAPH.QUERY knowledge "MATCH (t:Task {status: \\"completed\\"}) RETURN t LIMIT 10"')
    print('  GRAPH.QUERY knowledge "MATCH (a:Agent)-[:COMPLETED]->(t:Task) RETURN a.name, count(t) as completed ORDER BY completed DESC LIMIT 5"')


if __name__ == "__main__":
    init_schema()
