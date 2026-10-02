#!/usr/bin/env python3
"""Record a completed task in FalkorDB knowledge graph.

This script is called by the kanban_complete hook to write task completion
events to the FalkorDB knowledge graph.

Usage:
    # Via stdin (JSON):
    echo '{"task_id": "maw-p3", "title": "Multi-Agent Workflow Phase 3", "assignee": "cdenneen", ...}' | python record_completed_task.py

    # Or via environment variables:
    export TASK_ID=maw-p3
    export TASK_TITLE="Multi-Agent Workflow Phase 3"
    export TASK_ASSIGNEE=cdenneen
    python record_completed_task.py

Environment variables:
    FALKORDB_HOST: FalkorDB host (default: 127.0.0.1)
    FALKORDB_PORT: FalkorDB port (default: 6380)
    TASK_ID: Task ID from Ghost Kanban
    TASK_TITLE: Task title
    TASK_ASSIGNEE: Agent ID assigned to task
    TASK_REPO: Repository name (optional)
    TASK_FILES: Comma-separated list of file paths (optional)
    TASK_DECISIONS: Comma-separated list of decision IDs (optional)
    TASK_ISSUES: Comma-separated list of issue IDs (optional)
"""

from __future__ import annotations

import json
import os
import sys

from redis import Redis

FALKORDB_HOST = os.environ.get("FALKORDB_HOST", "127.0.0.1")
FALKORDB_PORT = int(os.environ.get("FALKORDB_PORT", "6380"))


def get_task_metadata():
    """Read task metadata from stdin or environment."""
    metadata = {}

    # Try stdin first
    if not sys.stdin.isatty():
        try:
            data = json.load(sys.stdin)
            metadata.update(data)
        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse JSON from stdin: {e}", file=sys.stderr)

    # Override with environment variables if present
    env_vars = [
        "TASK_ID",
        "TASK_TITLE",
        "TASK_ASSIGNEE",
        "TASK_REPO",
        "TASK_FILES",
        "TASK_DECISIONS",
        "TASK_ISSUES",
    ]

    for var in env_vars:
        value = os.environ.get(var)
        if value is not None:
            metadata[var.lower()] = value

    return metadata


def record_completed_task(metadata: dict) -> None:
    """Write task completion to FalkorDB knowledge graph.

    Creates:
    - Task node with status=completed
    - Agent-TASK_COMPLETED relationship
    - Task-TOUCHED relationships for files/repos/decisions/issues
    """
    from redis import Redis

    client = Redis(host=FALKORDB_HOST, port=FALKORDB_PORT, decode_responses=True)

    task_id = metadata.get("task_id")
    title = metadata.get("task_title", "Unknown Task")
    assignee = metadata.get("task_assignee", "unknown")
    repo = metadata.get("task_repo")
    files = metadata.get("task_files", "")
    decisions = metadata.get("task_decisions", "")
    issues = metadata.get("task_issues", "")

    if not task_id:
        print("Error: TASK_ID is required", file=sys.stderr)
        sys.exit(1)

    now = os.popen("date -Iseconds").read().strip()

    # Build the Cypher query
    query = f"""
    MERGE (a:Agent {{id: "{assignee}"}})
    ON CREATE SET a.last_active = "{now}"

    MERGE (t:Task {{id: "{task_id}"}})
    ON CREATE SET
        t.title = "{title}",
        t.status = "completed",
        t.created_at = "{now}",
        t.completed_at = "{now}"
    ON MATCH SET
        t.status = "completed",
        t.completed_at = "{now}"

    MERGE (a)-[:COMPLETED]->(t)
    """

    # Add file relationships
    for file_path in [f.strip() for f in files.split(",") if f.strip()]:
        # Escape quotes in file path
        escaped_path = file_path.replace('"', '\\"')
        query += f"""
        MERGE (f:File {{path: "{escaped_path}"}})
        ON CREATE SET f.last_modified = "{now}"
        MERGE (t)-[:TOUCHED]->(f)
        """

    # Add repo relationship
    if repo:
        escaped_repo = repo.replace('"', '\\"')
        query += f"""
        MERGE (r:Repo {{name: "{escaped_repo}"}})
        ON CREATE SET r.last_sync = "{now}"
        MERGE (t)-[:TOUCHED]->(r)
        """

    # Add decision relationships
    for decision_id in [d.strip() for d in decisions.split(",") if d.strip()]:
        escaped_dec = decision_id.replace('"', '\\"')
        query += f"""
        MERGE (d:Decision {{id: "{escaped_dec}"}})
        ON CREATE SET d.made_at = "{now}"
        MERGE (t)-[:MADE]->(d)
        """

    # Add issue relationships
    for issue_id in [i.strip() for i in issues.split(",") if i.strip()]:
        escaped_issue = issue_id.replace('"', '\\"')
        query += f"""
        MERGE (i:Issue {{id: "{escaped_issue}"}})
        ON CREATE SET i.created_at = "{now}"
        MERGE (t)-[:RESOLVED]->(i)
        """

    print(f"Recording completed task: {task_id}")
    print(f"  Assignee: {assignee}")
    print(f"  Title: {title}")
    if repo:
        print(f"  Repository: {repo}")
    if files:
        print(f"  Files touched: {files}")
    if decisions:
        print(f"  Decisions: {decisions}")
    if issues:
        print(f"  Issues resolved: {issues}")

    try:
        result = client.execute_command("GRAPH.QUERY", "knowledge", query.strip())
        print(f"\nQuery executed successfully.")
        print(f"  Nodes created: {result[1][0] if len(result) > 1 else 'N/A'}")
        print(f"  Relationships created: {result[2][0] if len(result) > 2 else 'N/A'}")
    except Exception as e:
        print(f"Error executing query: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """Main entry point."""
    metadata = get_task_metadata()

    if not metadata:
        print("Error: No task metadata provided", file=sys.stderr)
        print("Usage: echo '{...}' | python record_completed_task.py", file=sys.stderr)
        print("   or: export TASK_ID=... && python record_completed_task.py", file=sys.stderr)
        sys.exit(1)

    record_completed_task(metadata)


if __name__ == "__main__":
    main()
