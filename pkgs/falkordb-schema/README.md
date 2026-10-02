# FalkorDB Schema Package

This package contains the FalkorDB knowledge graph schema and utilities for the home repository.

## Files

- `SCHEMA.md` - Graph schema documentation (node labels, relationships, constraints)
- `init_schema.py` - One-shot script to create constraints and indexes
- `record_completed_task.py` - Hook script to record task completions

## Usage

### Initialize Schema (one-time setup)

Run this once on Eros after FalkorDB is deployed:

```bash
python /nix/store/...-falkordb-schema-init_schema.py
```

Or manually against the container:

```bash
# Get container IP (loopback)
FALKORDB_HOST=127.0.0.1
FALKORDB_PORT=6380

python init_schema.py
```

### Record Task Completion

Called by the kanban_complete hook when a task is completed:

```bash
# Via stdin (JSON)
echo '{"task_id": "maw-p3", "title": "FalkorDB Phase 3", "assignee": "cdenneen", "task_repo": "cdenneen/home", "task_files": "hosts/nixos/eros.nix,pkgs/falkordb-schema/SCHEMA.md"}' | python record_completed_task.py

# Via environment variables
export TASK_ID=maw-p3
export TASK_TITLE="FalkorDB Phase 3"
export TASK_ASSIGNEE=cdenneen
export TASK_REPO=cdenneen/home
export TASK_FILES=hosts/nixos/eros.nix,pkgs/falkordb-schema/SCHEMA.md
python record_completed_task.py
```

### Future Integration Points

Eventually, this script should be called by:

1. **kanban_complete hook**: When `hermes kanban complete` is run
2. **git post-merge hook**: When a PR is merged

### Example Cypher Queries

```cypher
# What repos has a specific agent touched?
GRAPH.QUERY knowledge "MATCH (a:Agent {id: 'cdenneen'})-[:COMPLETED]->(t:Task)-[:TOUCHED]->(r:Repo) RETURN r.name, count(t) as tasks"

# What files were touched in completed tasks?
GRAPH.QUERY knowledge "MATCH (t:Task {status: 'completed'})-[:TOUCHED]->(f:File) RETURN f.path, t.id, t.title ORDER BY t.completed_at DESC LIMIT 25"

# What decisions were made for a specific topic?
GRAPH.QUERY knowledge "MATCH (d:Decision {context: 'maw-p3'})-[:MADE]->(t:Task) RETURN d.title, d.description, t.id"
```
