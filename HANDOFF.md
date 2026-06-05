# Handoff

## Project Summary

This repo is the flake monorepo for personal NixOS, nix-darwin, and Home Manager systems. It manages host configs, shared modules, packages, secrets, and generated agent/tooling files.

## Current Status

- Shared global Codex/OpenCode `AGENTS.md` now includes persistent project memory requirements.
- Repo-root memory files now exist and are initialized.
- Recent `codex` and `opencode` package updates were merged and applied on Mac, `nyx`, and `ghost`.
- Local branch is `main` and the worktree was clean when this handoff was written.
- The Mac-side SSH/git-signing path fix is live locally and verified with a good signed commit.
- `nyx` and `ghost` both also produce good signed commits.
- `nyx` opencode has been rebuilt and restarted; the direct app API is healthy and the compaction unit now returns successfully.

## What Was Completed

- Added memory-policy guidance to `modules/hm/users/cdenneen/ai/AGENTS.md`.
- Applied the updated flake on Mac, `nyx`, and `ghost`.
- Added these files:
  - `PROJECT_STATE.md`
  - `NEXT_STEPS.md`
  - `ARCHITECTURE.md`
  - `TASKS.md`
  - `DECISIONS.md`
  - `HANDOFF.md`

## What Remains

- Commit and push the verified signing-path fix.
- Apply the signing-path fix generation on `nyx` and `ghost`.
- Re-verify `~/.codex/AGENTS.md` and `~/.config/opencode/AGENTS.md` on `nyx` and `ghost` after that new generation is live.
- Keep the memory files updated during the next substantial task.

## Open Issues

- The verified signing-path fix exists locally but is not yet committed and propagated to `nyx` and `ghost`.
- `nyx` opencode is healthy again, but it still accumulates many Playwright MCP child processes under load and may need future tuning.

## Important Files

- `AGENTS.md`
- `modules/hm/users/cdenneen/ai/AGENTS.md`
- `hosts/nixos/nyx.nix`
- `modules/hm/users/cdenneen/programs.nix`
- `modules/hm/users/cdenneen/git.nix`
- `modules/hm/users/cdenneen/secrets.nix`
- `modules/hm/programs/ssh.nix`
- `PROJECT_STATE.md`
- `DECISIONS.md`
- `NEXT_STEPS.md`

## Current Branch

- `main`

## Exact Next Action

Create a signed commit for the verified SSH/git-signing path fix, push it to `main`, then apply it on `nyx` and `ghost` and re-verify the shared Codex/OpenCode `AGENTS.md` content there.
