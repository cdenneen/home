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
- `nyx` opencode is active, the direct API responds with auth via `/run/secrets/opencode_server_password`, the compaction unit now returns successfully, and Playwright is isolated in `nyx-mcp-playwright.service`.
- Fresh `nyx` login shells can now run direct `opencode attach http://127.0.0.1:4097 ...` without `401`.

## What Was Completed

- Added memory-policy guidance to `modules/hm/users/cdenneen/ai/AGENTS.md`.
- Applied the updated flake on Mac, `nyx`, and `ghost`.
- Signed and pushed the stable SSH/git-signing path fix, then switched `nyx` and `ghost` onto it.
- Re-verified signed temp commits and shared `AGENTS.md` memory guidance on `nyx` and `ghost`.
- Re-verified `nyx` opencode auth and API health against the live secret path.
- Added commits `2263467f` and `fe5dd74f` to stabilize `nyx` OpenCode runtime:
  - shared Playwright gateway on `18107`
  - bounded 30-minute compaction runs
  - `nyx` shell/helper auth for direct local attach
- Re-verified on live `nyx` that:
  - `opencode-serve` restarted onto the new config
  - `nyx-mcp-playwright.service` is active
  - direct `opencode attach http://127.0.0.1:4097 ...` no longer returns `401`
- Added these files:
  - `PROJECT_STATE.md`
  - `NEXT_STEPS.md`
  - `ARCHITECTURE.md`
  - `TASKS.md`
  - `DECISIONS.md`
  - `HANDOFF.md`

## What Remains

- Decide whether `nyx` needs true stale-session deletion in addition to bounded compaction.
- Decide whether the remaining old standalone Playwright processes on `nyx` are harmless or should be cleaned up.
- Keep the memory files updated during the next substantial task.

## Open Issues

- `nyx` opencode is healthy again and much lighter, but the live session count is still high (`91` during the last check).
- A small number of old standalone Playwright processes are still visible on `nyx`, but they are no longer children of `opencode-serve`.

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

If `nyx` opencode feels slow again, start by checking live session count, `nyx-mcp-playwright.service`, and whether true stale-session deletion is needed before making more architecture changes.
