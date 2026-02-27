## Workspace Git Workflow (Cache + Worktrees)

This setup uses a "single bare cache + many worktrees" Git workflow.

Goals:

- Keep exactly one checked-out copy of each repo per workspace.
- Avoid duplicate Git objects and repeated clones.
- Allow multiple independent workspaces to operate on the same repo/branch without checkout conflicts.

### Concepts

#### Bare cache repos

Each remote repo is cloned once as a _bare_ repository under `CACHE_ROOT`.

- Default varies by host; always check:
  - `echo $CACHE_ROOT`
- Cache layout: flat key per repo:
  - `$CACHE_ROOT/<host>_<path>.git` (slashes in `<path>` become `_`)
  - Example: `~/src/cache/git.ap.org_gitops_infra_eks-apps.git`

This bare repo is not used directly for day-to-day work; it backs worktrees.

#### Workspace worktrees

Workspaces typically live under:

- Default varies by host; always check:
  - `echo $WORKSPACE_ROOT`

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

## MCP and Skills

### MCP servers

- MCP tools are available by default; use them automatically when relevant.
- Prefer read-only tools unless the user explicitly asks to write or mutate.
- If a specific MCP is requested, use it explicitly.

### Skills

- Skills are discovered from:
  - `~/.agents/skills/<name>/SKILL.md`
  - `~/.opencode/skills/<name>/SKILL.md`
- Load skills on demand using the `skill` tool.

## Git Workflow

- Always run `git pull --rebase` before `git push` to avoid remote divergence.

## Response Style

- Always include 1 or 2 suggestions in responses.
- Keep suggestions simple, direct, and actionable.
- Do not overcomplicate wording or steps.
