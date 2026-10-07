## Table of Contents

- General Execution Contract
- GitLab IaC Pipelines (see `gitlab-pipelines` skill)
- Workspace Git Workflow (Cache + Worktrees)
- Persistent Project Memory Requirements
- Tooling Preferences and Fallbacks
- MCP and Skills
- Git Workflow
- Response Style

## General Execution Contract

- Infer execution intent from the request. Questions, reviews, audits, research, and explicit planning requests are read-only. Requests to fix, implement, update, deploy, clean up, or "proceed" authorize the required edits and non-destructive validation.
- For execution tasks, continue end-to-end in the current run: investigate, implement, validate, commit/push/open or merge a PR when requested, and verify the resulting automation. Do not stop after diagnosis, a plan, a progress update, or a delegated subagent result.
- Progress updates communicate status; they are not approval checkpoints. Continue without waiting unless a material ambiguity changes correctness, a required credential or manual action is unavailable, or the next operation is destructive or an infrastructure apply that requires explicit confirmation.
- When delegating, state whether the subagent should execute or review. The primary agent owns completion and must resume after the subagent returns.
- Resolve routine blockers independently using available tools and bounded retries. If genuinely blocked, report the evidence and the single concrete user action required.
- Preserve explicit safety gates for deleting data, rewriting shared history, production/cloud applies, and manual pipeline jobs.
- An explicit request such as "deploy/apply to <named target>" satisfies the target confirmation gate. A generic request to fix code or configuration does not authorize remote mutation.

## GitLab IaC Pipelines

GitLab pipeline/job/artifact/child-pipeline debugging and GitLab-driven
Terraform/OpenTofu/Terragrunt deployment have their own contract: bounded
polling with an evidence Poll Log, manual-job authorization rules, AWS OIDC
failure triage, and a required Run Summary block. That contract is long and
applies to a minority of tasks, so it lives in the `gitlab-pipelines` skill
rather than in every context window. Load it before starting any such task.

## Workspace Git Workflow (Cache + Worktrees)

This setup uses a "single bare cache + many worktrees" Git workflow.

Goals:

- Keep exactly one checked-out copy of each repo per workspace.
- Avoid duplicate Git objects and repeated clones.
- Allow multiple independent workspaces to operate on the same repo/branch without checkout conflicts.

### Concepts

#### Bare cache repos

Each remote repo is cloned once as a _bare_ repository under `CACHE_ROOT`.

- Defaults:
  - Linux: `/home/cdenneen/src/cache`
  - Darwin: `/Users/cdenneen/code/cache`
  - Verify current shell: `echo $CACHE_ROOT`
- Cache layout: flat key per repo:
  - `$CACHE_ROOT/<host>_<path>.git` (slashes in `<path>` become `_`)
  - Example: `~/src/cache/git.ap.org_gitops_infra_eks-apps.git`

This bare repo is not used directly for day-to-day work; it backs worktrees.

#### Workspace worktrees

Workspaces typically live under:

- Defaults:
  - Linux: `/home/cdenneen/src/workspace`
  - Darwin: `/Users/cdenneen/code/workspace`
  - Verify current shell: `echo $WORKSPACE_ROOT`

When you "clone" a repo into a workspace, you actually add a Git worktree backed by the bare cache.

### Synthetic branches

Git worktrees cannot check out the same local branch name at the same time. To avoid conflicts, this workflow
uses synthetic local branches:

- Local synthetic branch: `<base-branch>@<workspace>`
  - Examples: `main@infra`, `master@projectA`

Remote branch names remain normal (no `@workspace` suffix).

#### Tracking

- `setup_repo` chooses the base branch from `origin/HEAD` when you do not specify one.
- The synthetic branch is configured to track the corresponding remote branch (e.g. `master@infra` -> `origin/master`).

### Commands

#### setup_repo

Preferred way to bring a repo into the current directory as a worktree:

```
setup_repo <git-url> [branch]
```

- If `[branch]` is omitted, it uses the remote default branch (`origin/HEAD`).
  - Repos whose default branch is `master` will use `master@<workspace>`.
- Ensures the bare cache exists and is fetched.
- Adds a worktree at `./<repo>`.
- Checks out `<branch>@<workspace>`.

#### update_workspace

If you have older worktrees that still point at the old cache layout, migrate them:

```
update_workspace
update_workspace --migrate
```

- Dry run shows mismatches.
- `--migrate` snapshots local changes and local commits, retargets the worktree to the new flat cache.
- Migration is non-destructive and keeps a backup as `./<repo>.bak.<timestamp>`.

#### ws-branch

Create a feature branch for the current workspace with upstream set so `git push` works:

```
git ws-branch feat/my-branch [start-point]
```

- Creates `feat/my-branch@<workspace>` locally.
- Pushes to `origin/feat/my-branch`.
- Sets upstream accordingly.

#### Repo setup (required)

- Always use `setup_repo` or `git clone` (alias) so repos are created as worktrees from the cache.
- Never run plain `git clone` without the alias; it breaks the cache/worktree workflow.

## Persistent Project Memory Requirements

This repository uses file-based project memory. Conversation history is ephemeral and may be lost due to context limits, compaction, crashes, model changes, or agent restarts.

### Startup Routine

When a new session begins, read in this order:

1. `AGENTS.md`
2. `.ai/HANDOFF.md` (fallback: `HANDOFF.md`)
3. `.ai/PROJECT_STATE.md` (fallback: `PROJECT_STATE.md`)
4. `.ai/DECISIONS.md` (fallback: `DECISIONS.md`)
5. `.ai/NEXT_STEPS.md` (fallback: `NEXT_STEPS.md`)

Briefly summarize understanding in a progress update, then continue with the requested work without waiting for confirmation.

### Required Project Memory Files

Maintain these files under `.ai/` for execution sessions that modify project state. Explicitly read-only review/research tasks must not create or update them unless the user asks or a durable blocker/decision must be recorded:

- `.ai/PROJECT_STATE.md`
- `.ai/NEXT_STEPS.md`
- `.ai/ARCHITECTURE.md`
- `.ai/TASKS.md`
- `.ai/DECISIONS.md`
- `.ai/HANDOFF.md`

Legacy root-level memory files with the same names may exist in older repos. If present, move them into `.ai/` before making further updates:

```sh
mkdir -p .ai
for f in PROJECT_STATE.md NEXT_STEPS.md ARCHITECTURE.md TASKS.md DECISIONS.md HANDOFF.md; do
  [ -f "$f" ] && [ ! -f ".ai/$f" ] && mv "$f" ".ai/$f"
done
```

If both root and `.ai/` versions exist, read both, preserve the newest useful information in `.ai/`, then remove the root copy.

### Update Triggers

The agent MUST update project memory when any of the following occur:

- A feature is completed.
- A significant implementation decision is made.
- An architectural change is introduced.
- A bug is fixed.
- A new blocker is discovered.
- A task is abandoned.
- A task is reprioritized.
- More than 30 minutes of work has elapsed.
- More than 10 files have been modified.
- Before ending a session.
- Before requesting user review.
- Before any potentially disruptive refactor.
- When context usage appears high.

Read-only review/research tasks should not modify project memory unless they produce a durable decision, blocker, or explicit user-requested record.

### Context Pressure

- If estimated context usage exceeds 50%, update all memory files before continuing.
- If estimated context usage exceeds 75%, perform a full handoff refresh before continuing.

### File Responsibilities

#### `.ai/PROJECT_STATE.md`

Current project snapshot. Keep it concise and factual. Include:

- Current goals
- Current status
- Active work stream
- Recent accomplishments
- Current blockers
- Known risks
- Important assumptions

#### `.ai/NEXT_STEPS.md`

Actionable continuation plan. Include:

- Immediate next task
- Ordered task list
- Dependencies
- Validation steps
- Recommended next-session starting point

#### `.ai/ARCHITECTURE.md`

Long-term technical reference. Include:

- System architecture
- Key components
- Data flow
- Major dependencies
- Integration points
- Design constraints

#### `.ai/TASKS.md`

Working task tracker. Use checkbox format and keep these sections current:

- Completed tasks
- Active tasks
- Deferred tasks
- Blocked tasks

#### `.ai/DECISIONS.md`

Persistent engineering journal. Keep this file forever. Record:

- Date
- Context
- Decision
- Rationale
- Alternatives considered
- Consequences

Also record:

- Failed approaches
- Rejected designs
- Things that should not be attempted again

#### `.ai/HANDOFF.md`

Session recovery document optimized for a fresh agent with no prior context. Keep it small (roughly 1–3 pages) and include:

- Project summary
- Current status
- What was completed
- What remains
- Open issues
- Important files
- Current branch information
- Exact next action

### Continuation Rule

A new agent should be able to continue the project successfully using only:

- repository contents
- `AGENTS.md`
- `.ai/HANDOFF.md`
- `.ai/PROJECT_STATE.md`
- `.ai/DECISIONS.md`

without access to prior conversation history.

### Memory Quality Rule

When updating memory files:

- Prefer concise factual summaries.
- Remove stale information.
- Avoid duplicate content.
- Preserve important rationale.
- Preserve lessons learned.
- Preserve rejected approaches.
- Preserve blocker information.

Project memory files are local agent state, not normal source deliverables. Keep them current under `.ai/`, which is globally ignored by Git.

### Shutdown Routine

Before ending any substantial execution session that modified code, state, tasks, or decisions:

1. Update `.ai/TASKS.md`
2. Update `.ai/PROJECT_STATE.md`
3. Update `.ai/DECISIONS.md`
4. Refresh `.ai/NEXT_STEPS.md`
5. Regenerate `.ai/HANDOFF.md`
6. Verify another agent could continue successfully

## Tooling Preferences and Fallbacks

### Preferred tools (use when available)

- `rg` (ripgrep) over `grep`: faster, better default regex handling, and clearer output on large repos.
- `bat` over `cat`: syntax highlighting and line numbers make reviews faster.
- `httpie` (`http`) over `curl`: more readable requests and responses with sane defaults.
- `gh` or `glab` over raw `git` API calls: clearer intent and better defaults for GitHub/GitLab.
- `git-delta` (`delta`) over raw `git diff`: clearer diffs with syntax highlighting.
- `jq` for JSON processing: safe, explicit parsing instead of brittle text manipulation.
- `fd` over `find`: faster and simpler file discovery.

### Fallbacks (use if preferred tool is missing or fails)

- `grep` if `rg` is unavailable or its regex behavior blocks progress.
- `cat` if `bat` is unavailable or output must be raw.
- `curl` if `httpie` is unavailable or a request requires unusual flags.
- `git` (or direct API calls) if `gh`/`glab` are unavailable.
- `git diff` if `delta` is unavailable or raw patches are required.
- `find` if `fd` is unavailable.

### Tool availability

- If a tool is missing and `nixpkgs` is available, prefer running via Nix to avoid manual installs.
- Example: `nix run nixpkgs#rg -- --help`
- Example: `nix shell nixpkgs#jq -c jq -- --version`
- Example: `nix shell nixpkgs#httpie -c http -- --version`
- If Nix is not available but the system has `brew`/`apt`/`yum`/etc, suggest installing the tool or use a fallback.

### Codex RTK reference

- RTK guidance lives in `~/.codex/RTK.md`.
- In Codex sessions, prefer `rtk <command>` for tests, builds, installs, and verbose `git`/log commands when the wrapper is available.
- Use raw commands for tiny output or interactive flows when `rtk` would get in the way.

### Codebase memory

- When `ccc` / the `cocoindex-code` MCP is available, prefer it for semantic code discovery before broad repo-wide scans.
- Use `ccc search` for concept lookup and `ccc grep` for structural or AST-style matching.
- Fall back to `rg` once you know the file or symbol range you need to inspect directly.

## MCP and Skills

### MCP servers

- MCP tools are available by default; use them automatically when relevant.
- Prefer read-only tools during discovery. When request intent authorizes execution, use the required write tools without asking again, subject to the safety gates above.
- If a specific MCP is requested, use it explicitly.

### Skills

- Skills are discovered from:
  - `~/.agents/skills/<name>/SKILL.md`
  - `~/.opencode/skills/<name>/SKILL.md`
- Load skills on demand using the `skill` tool.
- FluxCD skills installed globally:
  - `gitops-knowledge`
  - `gitops-repo-audit`
  - `gitops-cluster-debug`
- Repo-managed skills installed globally:
  - `cocoindex-code`
  - `rtk-workflow`
  - `graphify`
  - `gitlab-pipelines`

## Codex Subagent Routing

- When a task is primarily Kubernetes cluster/manifests/troubleshooting, delegate to `kubernetes-expert`.
- When a task is primarily Terraform/OpenTofu/Terragrunt planning/state/module work, delegate to `terraform-expert`.
- When a task is primarily GitLab CI pipeline/job/artifact/bridge debugging, delegate to `gitlab-ci-expert`.
- When a task is primarily AWS IAM/OIDC/STS/service diagnostics, delegate to `aws-expert`.
- When a task is primarily Nix/NixOS/nix-darwin/Home Manager/flake design or debugging, delegate to `nix-expert`.
- When a task is primarily FluxCD/Flux Operator/GitOps reconciliation or topology design, delegate to `flux-expert`.
- For mixed tasks, split by domain to the matching subagents and synthesize a single final answer.

## Git Workflow

- Always run `git pull --rebase` before `git push` to avoid remote divergence.

## Response Style

- Include suggestions only when useful work remains outside the completed request.
- Keep suggestions simple, direct, and actionable.
- Do not overcomplicate wording or steps.

## Shared Memory Contract

Full spec: `~/.ai/AGENTS.md` + `docs/architecture/agent-workflow-contract.md`.

**This contract is self-executing.** Read it once at session start and autonomously follow it for the entire session — no human prompting required. Every backend listed here is actively used — if any is skipped, context is lost and prior work is repeated.

### Trigger phrase: `/activate-workflow`

If this phrase appears in any message, immediately run the full Session Start routine below as if it is a fresh session start, even mid-session.

### Memory backends — all must be written to

| Backend | MCP tool | What it stores | Why |
|---|---|---|---|
| Qdrant + FalkorDB | `store_context` (eros-context-shared) | Vector + graph searchable facts | Cross-session semantic recall |
| agent-context repo | `agent_handoff-write_context` (agent-handoff) | Structured handoff context | Cold-resume, human-readable |
| agent-context repo | `agent_handoff-write_handoff` (agent-handoff) | Full session state | Resume from any agent |
| Ghost Kanban | `hermes peer dm` via cos-update skill | Task tracking | CoS awareness |

**Writing to only one is not enough.** `agent_handoff-write_context` does NOT write to Qdrant/FalkorDB. `store_context` does NOT write to the GitHub repo. Both must be called.

### Session start — before any other action

1. `recall(query="<inferred topic> recent context decisions")` via eros-context-shared — loads prior facts from Qdrant/FalkorDB. Never ask Chris to re-explain context found here.
2. `agent_handoff-list_handoffs(project=<inferred slug>, limit=5)` — load prior session handoffs. If a `handoff_id` was provided, `agent_handoff-read_handoff(id)` first.
3. Read `.ai/HANDOFF.md` in the repo if present.
4. `hermes peer dm ghost/chief-of-staff "[<profile>@<host>] [started] Working on: <topic>. Prior handoffs: <count>."` via cos-update skill/terminal.
5. Begin work.

### During work — autonomous, every 10 turns AND on every significant event

**Every 10 turns** (regardless of significance — like recallium):
- `store_context(content=<session progress summary>, type="session", agent_id="<agent>@<host>", project=<project>)`

**On every significant decision, discovery, blocker, or completed unit**:
- `hermes peer dm ghost/chief-of-staff "[<profile>@<host>] [<update|blocked|review>] <message>"` via cos-update skill
- `store_context(content=<fact/decision with rationale>, type="decision"|"fact", agent_id=..., project=...)` → Qdrant + FalkorDB
- `agent_handoff-write_context(content=<same>, type=..., agent_id=..., project=...)` → agent-context repo

**Context >50%**: run the full session-end sequence, then continue in a new context.

### Session end / handoff

1. `agent_handoff-write_handoff(topic, content, project, next_action)` → get `handoff_id`
2. `store_context` for all key facts/decisions not yet written this session
3. `agent_handoff-write_context` for the same facts (both backends)
4. Update `.ai/HANDOFF.md` in the repo
5. `hermes peer dm ghost/chief-of-staff "[<profile>@<host>] [completed] <summary>"` via cos-update skill
6. Print: `HANDOFF: <handoff_id> — Resume: tell @nyxops to resume handoff <handoff_id>`

### MCP tool names (via eros.tail0e55.ts.net:4000/mcp/)

| Tool | Actual MCP name | Server | Writes to |
|---|---|---|---|
| `recall` | `recall` | eros-context-shared | reads Qdrant+FalkorDB |
| `store_context` | `store_context` | eros-context-shared | Qdrant + FalkorDB |
| `write_handoff` | `agent_handoff-write_handoff` | agent-handoff | GitHub repo |
| `read_handoff` | `agent_handoff-read_handoff` | agent-handoff | — |
| `list_handoffs` | `agent_handoff-list_handoffs` | agent-handoff | — |
| `write_context` | `agent_handoff-write_context` | agent-handoff | GitHub repo |
| CoS updates | `hermes peer dm` via cos-update skill | terminal | Ghost Kanban |

### cos-update skill — how to call it

Load with skill tool or read `~/.pi/agent/skills/cos-update/SKILL.md`. All agent types use terminal:
`hermes peer dm ghost/chief-of-staff "[<profile>@<host>] [<started|update|blocked|review|completed>] <message>"`
