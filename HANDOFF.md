# Handoff

## Project Summary

This repo is the flake monorepo for personal NixOS, nix-darwin, and Home Manager systems. It manages host configs, shared modules, packages, secrets, and generated agent/tooling files.

## Current Status

- Shared global Codex/OpenCode `AGENTS.md` now includes persistent project memory requirements.
- Repo-root memory files now exist and are initialized.
- Recent `codex` and `opencode` package updates were merged and applied on Mac, `nyx`, and `ghost`.
- The stable SSH/git-signing path fix is committed as `a3006aa0`, pushed to `main`, and applied on `nyx` and `ghost`.
- Mac, `nyx`, and `ghost` all produce good signed commits.
- Shared Codex/OpenCode memory guidance is confirmed present on `nyx` and `ghost`.
- `nyx` opencode is active, the direct API responds with auth via `/run/secrets/opencode_server_password`, and the compaction unit now returns successfully.

## What Was Completed

- Added memory-policy guidance to `modules/hm/users/cdenneen/ai/AGENTS.md`.
- Applied the updated flake on Mac, `nyx`, and `ghost`.
- Signed and pushed the stable SSH/git-signing path fix, then switched `nyx` and `ghost` onto it.
- Re-verified signed temp commits and shared `AGENTS.md` memory guidance on `nyx` and `ghost`.
- Re-verified `nyx` opencode auth and API health against the live secret path.
- Added these files:
  - `PROJECT_STATE.md`
  - `NEXT_STEPS.md`
  - `ARCHITECTURE.md`
  - `TASKS.md`
  - `DECISIONS.md`
  - `HANDOFF.md`

## What Remains

- Decide whether `nyx` opencode needs another tuning pass for large session counts and Playwright MCP child buildup.
- Keep the memory files updated during the next substantial task.

## Open Issues

- `nyx` opencode is healthy again, but it currently reports a large session count and many Playwright MCP child processes under load.

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

If `nyx` opencode feels stuck again, start by checking its live session count and Playwright MCP child buildup before doing any restart or further flake changes.
