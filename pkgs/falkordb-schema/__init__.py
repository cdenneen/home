"""FalkorDB knowledge graph schema initialization.

Initialize the graph schema with constraints and indexes.
"""

from __future__ import annotations

import os

FALKORDB_HOST = os.environ.get("FALKORDB_HOST", "127.0.0.1")
FALKORDB_PORT = int(os.environ.get("FALKORDB_PORT", "6380"))

def init_schema(client=None):
    """Create constraints and indexes for the graph schema.

    Args:
        client: Optional Redis client. If None, creates one using FALKORDB_HOST/PORT.
    """
    from redis import Redis

    if client is None:
        client = Redis(host=FALKORDB_HOST, port=FALKORDB_PORT, decode_responses=True)

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
        raise ConnectionError(f"Failed to connect to FalkorDB: {e}") from e

    print("\nCreating constraints...")
    for constraint in constraints:
        try:
            client.execute_command("GRAPH.QUERY", "knowledge", constraint)
            print(f"  OK: {constraint}")
        except Exception as e:
            # Constraint may already exist
            print(f"  SKIP (may already exist): {constraint}")
            print(f"    Error: {e}")

    print("\nSchema initialization complete.")
    return True
