# Handoff

## Project Summary

This repo is the flake monorepo for personal NixOS, nix-darwin, and Home Manager systems. It manages host configs, shared modules, packages, secrets, and generated agent/tooling files.

## Current Status

- Shared global Codex/OpenCode `AGENTS.md` now includes persistent project memory requirements.
- Repo-root memory files now exist and are initialized.
- Recent `codex` and `opencode` package updates were merged and applied on Mac, `nyx`, and `ghost`.
- Local branch is `main` and the worktree was clean when this handoff was written.

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

- Fix the SSH config identity path drift on the Mac.
- Re-verify `~/.codex/AGENTS.md` and `~/.config/opencode/AGENTS.md` on `nyx` and `ghost` using corrected SSH access.
- Keep the memory files updated during the next substantial task.

## Open Issues

- `ssh nyx` and `ssh ghost` aliases currently point at stale `IdentityFile` paths under `~/.config/sops-nix/secrets/*`.
- Real materialized keys are present in `~/.ssh/*`, so the SSH config generation likely needs to be updated.

## Important Files

- `AGENTS.md`
- `modules/hm/users/cdenneen/ai/AGENTS.md`
- `modules/hm/users/cdenneen/programs.nix`
- `modules/hm/programs/ssh.nix`
- `PROJECT_STATE.md`
- `DECISIONS.md`
- `NEXT_STEPS.md`

## Current Branch

- `main`

## Exact Next Action

Inspect the SSH host config generation for `nyx` and `ghost`, change it to use valid materialized key paths, apply `sudo darwin-rebuild switch --flake .#VNJTECMBCD`, then verify the shared Codex/OpenCode `AGENTS.md` content on both remote hosts.
