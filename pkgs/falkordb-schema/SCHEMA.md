# FalkorDB Graph Schema

This document describes the initial graph schema for the FalkorDB knowledge graph on Eros.

## Node Labels

### Agent
Represents a human or AI agent that performs tasks.

- `id`: Unique identifier (e.g., "cdenneen", "hermes-coder@nyx")
- `name`: Display name
- `type`: "human" or "ai"
- `email`: Contact email (for humans)
- `last_active`: ISO-8601 timestamp

### Task
Represents a unit of work from Ghost Kanban.

- `id`: Kanban task ID (e.g., "maw-p3")
- `title`: Task title
- `status`: "open", "in-progress", "completed", "blocked"
- `assignee`: Agent ID
- `created_at`: ISO-8601 timestamp
- `completed_at`: ISO-8601 timestamp (when status=completed)
- `board`: Kanban board name

### File
Represents a file in the repository.

- `path`: Full file path (e.g., "hosts/nixos/eros.nix")
- `repo`: Repository name (e.g., "cdenneen/home")
- `sha`: Commit SHA where file was last touched
- `last_modified`: ISO-8601 timestamp
- `summary`: Brief description of file purpose

### Decision
Represents a significant decision recorded during task execution.

- `id`: Decision identifier (can be task ID + suffix)
- `context`: Related task ID(s)
- `title`: Decision title
- `description`: Detailed explanation
- `made_at`: ISO-8601 timestamp
- `author`: Agent ID

### Repo
Represents a GitHub repository.

- `name`: Full repo name (e.g., "cdenneen/home")
- `url`: GitHub URL
- `default_branch`: Default branch name
- `last_sync`: ISO-8601 timestamp

### Issue
Represents a GitLab issue.

- `id`: Issue identifier (e.g., "gitlab!123")
- `repo`: Repo name
- `title`: Issue title
- `status`: "open", "closed", "merged"
- `author`: Agent ID
- `created_at`: ISO-8601 timestamp
- `closed_at`: ISO-8601 timestamp (when status=closed/merged)

## Relationship Types

- `(:Agent)-[:CREATED]->(:Task)` - Agent created the task
- `(:Agent)-[:ASSIGNED]->(:Task)` - Agent is assigned to the task
- `(:Agent)-[:COMPLETED]->(:Task)` - Agent completed the task
- `(:Task)-[:TOUCHED]->(:File)` - Task modified this file
- `(:Task)-[:MADE]->(:Decision)` - Task resulted in this decision
- `(:Task)-[:RESOLVED]->(:Issue)` - Task resolved this issue
- `(:Task)-[:TOUCHED]->(:Repo)` - Task touched this repo
- `(:Issue)-[:BELONGS_TO]->(:Repo)` - Issue belongs to repository

## Indexes and Constraints

Create these on first run via `init_schema.py`:

```cypher
CREATE CONSTRAINT task_id IF NOT EXISTS FOR (t:Task) REQUIRE t.id IS UNIQUE;
CREATE CONSTRAINT agent_id IF NOT EXISTS FOR (a:Agent) REQUIRE a.id IS UNIQUE;
CREATE CONSTRAINT file_path IF NOT EXISTS FOR (f:File) REQUIRE f.path IS UNIQUE;
CREATE CONSTRAINT repo_name IF NOT EXISTS FOR (r:Repo) REQUIRE r.name IS UNIQUE;
CREATE CONSTRAINT issue_id IF NOT EXISTS FOR (i:Issue) REQUIRE i.id IS UNIQUE;
CREATE CONSTRAINT decision_id IF NOT EXISTS FOR (d:Decision) REQUIRE d.id IS UNIQUE;
```
