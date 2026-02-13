# Workspace Git Workflow (Cache + Worktrees)

This setup uses a "single bare cache + many worktrees" Git workflow.

Goals:

- Keep exactly one checked-out copy of each repo per workspace.
- Avoid duplicate Git objects and repeated clones.
- Allow multiple independent workspaces to operate on the same repo/branch without checkout conflicts.

## Concepts

### Bare cache repos

Each remote repo is cloned once as a _bare_ repository under `CACHE_ROOT`.

- Default: `CACHE_ROOT="$HOME/src/cache"`
- Cache layout: flat key per repo:
  - `$CACHE_ROOT/<host>_<path>.git` (slashes in `<path>` become `_`)
  - Example: `~/src/cache/git.ap.org_gitops_infra_eks-apps.git`

This bare repo is not used directly for day-to-day work; it backs worktrees.

### Workspace worktrees

Workspaces typically live under:

- Default: `WORKSPACE_ROOT="$HOME/src/workspace"`

When you "clone" a repo into a workspace, you actually add a Git worktree backed by the bare cache.

## Synthetic branches

Git worktrees cannot check out the same local branch name at the same time. To avoid conflicts, this workflow
uses synthetic local branches:

- Local synthetic branch: `<base-branch>@<workspace>`
  - Examples: `main@infra`, `master@projectA`

Remote branch names remain normal (no `@workspace` suffix).

### Tracking

- `setup_repo` chooses the base branch from `origin/HEAD` when you do not specify one.
- The synthetic branch is configured to track the corresponding remote branch (e.g. `master@infra` -> `origin/master`).

## Commands

### setup_repo

Preferred way to bring a repo into the current directory as a worktree:

```
setup_repo <git-url> [branch]
```

- If `[branch]` is omitted, it uses the remote default branch (`origin/HEAD`).
- Ensures the bare cache exists and is fetched.
- Adds a worktree at `./<repo>`.
- Checks out `<branch>@<workspace>`.

### update_workspace

If you have older worktrees that still point at the old cache layout, migrate them:

```
update_workspace
update_workspace --migrate
```

- Dry run shows mismatches.
- `--migrate` snapshots local changes and local commits, retargets the worktree to the new flat cache.
- Keeps backups as `./<repo>.bak.<timestamp>`.

### ws-branch

Create a feature branch for the current workspace with upstream set so `git push` works:

```
git ws-branch feat/my-branch [start-point]
```

- Creates `feat/my-branch@<workspace>` locally.
- Pushes to `origin/feat/my-branch`.
- Sets upstream accordingly.

## Tailscale note (nyx)

On nyx, use `tsup` (alias) to avoid Tailscale taking over DNS:

- `tsup` -> `tailscale up --accept-dns=false`
