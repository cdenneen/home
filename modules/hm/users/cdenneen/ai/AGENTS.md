## Table of Contents

- General Execution Contract
- GitLab IaC Pipelines (glab + Terraform/OpenTofu/Terragrunt + AWS OIDC)
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

## GitLab IaC Pipelines (glab + Terraform/OpenTofu/Terragrunt + AWS OIDC)

This section applies only when the task concerns a GitLab pipeline, job, artifact, child pipeline, or GitLab-driven infrastructure deployment. It does not impose output or stopping requirements on unrelated work.

### Quick Reference

- Never claim ongoing monitoring; use bounded polling and finish in-run.
- Always check pipeline + child pipelines + artifacts; report job URLs.
- Manual job blocks unless the current request explicitly names and authorizes that job; otherwise stop and request human action with the exact `glab` command.
- End every GitLab pipeline/deployment response with the “Run Summary” block.

### 0) Execution Contract (NO fake monitoring)

- The agent must NOT say: “I will monitor”, “I’ll keep an eye on it”, “I’ll report back later”.
- The agent operates in single-run mode: it must do all polling/retries NOW, inside this run.
- If a task requires waiting on external state (pipeline/jobs), the agent must implement a bounded polling loop (see §3).
- If the pipeline is blocked on a manual job not explicitly named and authorized in the current request, stop polling and request the required human action with exact `glab` commands/URL. If it is pre-authorized, play it and continue polling.

### 1) Definition of Done (DoD)

A deployment task is only “done” when:

1. The target pipeline reaches a terminal state (success/failed/canceled), AND
2. All relevant child/bridge pipelines are also terminal, AND
3. If the workflow depends on artifacts, the agent has checked whether artifacts exist and were produced by the expected job(s), AND
4. The agent prints a final status summary including:
   - pipeline ID + URL
   - failing job(s) with stage
   - child pipeline status (if any)
   - next action (none / manual play / patch + rerun)

### 2) Tooling rules (glab is source of truth)

Use `glab` CLI to query:

- pipeline status
- jobs list (including manual jobs)
- job logs
- artifacts
- downstream/child pipelines (bridges)

Never assume a pipeline is “running normally” — always check for manual jobs or stalled stages.

Auth preference:

- Prefer `glab` host auth from `~/.config/glab-cli/config.yml` (managed as secret).
- Do not require `GITLAB_TOKEN` for `glab` workflows.

### 3) Polling / Monitoring MUST be executed (evidence required)

If the pipeline is not in a terminal state (success/failed/canceled) AND not blocked on a manual job,
the agent MUST run an actual polling loop inside this run.

Compliance requirements:

- The agent MUST perform at least 3 polling iterations before returning (unless it reaches terminal state earlier).
- The agent MUST print a "Poll Log" table with one row per poll iteration:
  - poll number
  - timestamp (UTC or local)
  - parent pipeline status
  - child pipeline statuses (if any)
  - any newly failed jobs
  - any manual jobs detected

If the agent cannot run a loop in the current environment, it MUST say:
"Loop execution unavailable in this runtime" and then output a standalone shell script the user can run
to monitor the pipeline, including child pipelines and manual-job detection.

The agent must never output "Next action: wait" without having executed the minimum poll iterations.

## Non-terminal states are not an acceptable stopping point

The agent must NOT end a run with pipeline status in {running, pending} unless:

- the pipeline is blocked by a manual job not explicitly authorized in the current request (then exit as "Requires human action"), OR
- the polling loop reached TIMEOUT and a Poll Log is printed.

### Example polling monitor (standalone script)

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-:id}"      # glab supports :id in many endpoints when in repo
PIPELINE_ID="${1:?usage: $0 <pipeline_id>}"
SLEEP="${SLEEP:-25}"
MAX_POLLS="${MAX_POLLS:-30}"
AUTHORIZED_MANUAL_JOBS="${AUTHORIZED_MANUAL_JOBS:-}" # comma-separated exact job names

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

get_pipeline_status() {
  glab api "projects/$PROJECT_ID/pipelines/$PIPELINE_ID" | jq -r '.status'
}

get_child_pipelines() {
  # bridges endpoint; if your GitLab differs, adjust here
  glab api "projects/$PROJECT_ID/pipelines/$PIPELINE_ID/bridges" --paginate 2>/dev/null \
    | jq -r '.[] | "\(.downstream_pipeline.id)\t\(.downstream_pipeline.status)"' || true
}

get_jobs() {
  glab api "projects/$PROJECT_ID/pipelines/$PIPELINE_ID/jobs" --paginate \
    | jq -r '.[] | "\(.id)\t\(.name)\t\(.stage)\t\(.status)\t\(.web_url)"'
}

manual_jobs() {
  get_jobs | awk -F'\t' '$4=="manual"{print}'
}

is_authorized_manual_job() {
  [[ ",${AUTHORIZED_MANUAL_JOBS}," == *",$1,"* ]]
}

play_authorized_manual_jobs() {
  played_authorized_manual_job=false
  while IFS=$'\t' read -r id name stage status url; do
    [[ -n "$id" ]] || continue
    if is_authorized_manual_job "$name"; then
      echo "PLAYING AUTHORIZED MANUAL JOB: $name ($url)"
      glab api --method POST "projects/$PROJECT_ID/jobs/$id/play" >/dev/null
      played_authorized_manual_job=true
    fi
  done < <(manual_jobs)
}

detect_manual_blockers() {
  while IFS=$'\t' read -r id name stage status url; do
    [[ -n "$id" ]] || continue
    if ! is_authorized_manual_job "$name"; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$id" "$name" "$stage" "$status" "$url"
    fi
  done < <(manual_jobs)
}

detect_failed_jobs() {
  get_jobs | awk -F'\t' '$4=="failed"{print}'
}

echo -e "poll\ttime\tparent_status\tchild_pipelines\tmanual_jobs\tfailed_jobs"
played_authorized_manual_job=false

for ((i=1;i<=MAX_POLLS;i++)); do
  play_authorized_manual_jobs
  parent="$(get_pipeline_status || echo unknown)"
  child="$(get_child_pipelines | paste -sd',' -)"
  manual_cnt="$(detect_manual_blockers | wc -l | tr -d ' ')"
  failed_cnt="$(detect_failed_jobs | wc -l | tr -d ' ')"

  echo -e "${i}\t$(ts)\t${parent}\t${child:-none}\t${manual_cnt}\t${failed_cnt}"

  if [[ "$failed_cnt" -gt 0 ]]; then
    echo "FAILED JOBS:"
    detect_failed_jobs
    exit 2
  fi

  if [[ "$manual_cnt" -gt 0 ]]; then
    echo "MANUAL JOBS BLOCKING:"
    detect_manual_blockers
    exit 3
  fi

  if [[ "$played_authorized_manual_job" == true ]]; then
    sleep "$SLEEP"
    continue
  fi

  if [[ "$parent" =~ ^(success|failed|canceled|skipped)$ ]]; then
    exit 0
  fi

  sleep "$SLEEP"
done

echo "TIMEOUT after $MAX_POLLS polls"
exit 4
```

### 4) Manual jobs (pipelines that are “running” but paused)

If any required job is manual:

- Identify job name, stage, and URL.
- If the current request explicitly names and authorizes the job, play it and continue through terminal verification.
- Provide one of:
  - the exact “play” instruction (GitLab UI path), AND
  - the exact `glab` command (if supported in environment) OR the pipeline/job URL
- Explain what will happen after the manual job is played (next stage/child pipeline).

When the job is not pre-authorized, label the outcome as “Requires human action” and stop.

### 5) Debugging policy (multi-stage + artifacts + early-stage failures)

When any job fails OR downstream stages misbehave:

- Always pull logs for:
  - the first failing job
  - any upstream jobs that produce artifacts or state used downstream
  - terraform/tofu/terragrunt plan/apply jobs in earlier stages
- If artifacts influence behavior:
  - confirm artifact existence (download/list if feasible)
  - confirm artifact was produced by the expected commit/job

The agent must not focus only on the latest stage if earlier stage created the artifact/state.

### 6) Child/bridge pipeline policy

If pipelines trigger child/bridge pipelines:

- The agent must discover and report the child pipeline IDs and statuses.
- Failures in child pipelines are treated as failures of the overall deployment.
- Debugging must include failed child pipeline jobs and their logs.

### 7) Terraform/OpenTofu/Terragrunt workflow rules

- Prefer `plan` -> review -> `apply` discipline when pipeline uses manual apply.
- When diagnosing failures, extract and summarize:
  - provider errors (AWS auth/assume role/OIDC)
  - dependency graph issues (missing outputs/artifacts)
  - state lock issues
  - drift / import needs

If proposing a patch:

- Show exact file changes or commands.
- Explain why the change addresses the log evidence.
- Re-run pipeline and verify status (bounded polling).

### 8) AWS OIDC / role assumption checks (common failure class)

If logs include AssumeRoleWithWebIdentity/OIDC errors:

- Verify job has expected env vars and token file path (as shown in logs/CI config).
- Verify audience/subject/role ARN referenced.
- Check for expired token / incorrect AWS region / missing permissions in role policy.

Do not guess — cite exact log lines.

### 9) Output format (required for GitLab pipeline/deployment tasks)

Every GitLab pipeline/deployment response must end with a “Run Summary” block:

- Pipeline: <id> <url>
- Status: <success|failed|requires manual|timeout>
- Manual jobs blocking: <list or none>
- Failed jobs: <list or none>
- Child pipelines: <list>
- Next action: <none|play job X|apply patch Y|rerun pipeline>

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
