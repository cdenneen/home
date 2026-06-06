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
- `nyx` `restart-tmux` now detects foreign-host OpenCode session directories and avoids restoring those incompatible session IDs.
- `nyx` `coding:8` was repaired by reattaching it to nyx-native session `ses_1af7abd07ffeZXOtNe8WoLiGNB`.
- The shared DuckDuckGo MCP on `nyx` now runs stateless, so stale client session IDs no longer break searches.
- Codex config generation now targets the new profile model: no legacy `profile`/`[profiles.*]` in base `~/.codex/config.toml`, with dedicated `~/.codex/<name>.config.toml` files for `safe-relaxed`, `ci-runner`, `fast-triage`, and `strict`.
- `ghost` Home Manager package composition now separates core vs heavy tools and excludes heavy extras on `ghost` to reduce store usage.

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
- Added commit `ca182520` to guard `restart-tmux` against foreign OpenCode session directories.
- Added commit `123d6eff` so tmux snapshots no longer store literal OpenCode passwords.
- Applied both commits on `nyx` and confirmed `coding:8` no longer loops on the repeated permission prompt.
- Added commit `a9b73797` to make DuckDuckGo MCP stateless on `nyx`.
- Added commit `3d82967b` to add restart triggers for shared `nyx` MCP gateway wrappers.
- Rebuilt `nyx`, then manually restarted `nyx-mcp-duckduckgo.service` once because the old stateful process was still running from the prior generation.
- Verified DuckDuckGo searches now succeed with no session ID and with a bogus stale `mcp-session-id` from both `nyx` and the Mac.
- Re-verified on live `nyx` that:
  - `opencode-serve` restarted onto the new config
  - `nyx-mcp-playwright.service` is active
  - direct `opencode attach http://127.0.0.1:4097 ...` no longer returns `401`
- Updated Codex flake config to remove legacy embedded profile keys/tables and generate standalone profile-layer files compatible with Codex 0.134+.
- Refactored `modules/hm/users/cdenneen/programs.nix` so `ghost` keeps a lean core package set while non-ghost hosts keep the heavier Kubernetes/GitOps/browser/runtime extras.
- Added these files:
  - `PROJECT_STATE.md`
  - `NEXT_STEPS.md`
  - `ARCHITECTURE.md`
  - `TASKS.md`
  - `DECISIONS.md`
  - `HANDOFF.md`

## What Remains

- Decide whether other shared MCP gateways besides DuckDuckGo should become stateless too.
- Decide whether helpers besides `restart-tmux` should explicitly reject or remap foreign-host OpenCode session paths.
- Decide whether the remaining old standalone Playwright processes on `nyx` are harmless or should be cleaned up.
- Apply the new Home Manager generation on affected hosts to materialize the new Codex profile-layer files under `~/.codex`.
- Apply `ghost` and confirm no required day-to-day remote workflow tool was trimmed; re-add any missing tool to the HM core group if needed.
- Keep the memory files updated during the next substantial task.

## Open Issues

- `nyx` opencode is healthy again and much lighter, and stale-session deletion dropped the live session count from `91` to `75`.
- A small number of old standalone Playwright processes are still visible on `nyx`, but at least one belongs to a still-running user-attached session rather than a dead orphan.
- OpenCode session IDs created from Mac workspace roots are not safe to reattach inside nyx tmux panes.
- `nyx` user MCP services did not automatically replace the already-running DuckDuckGo process during this switch, so one manual restart was needed after the declarative change went live.

## Important Files

- `AGENTS.md`
- `modules/hm/users/cdenneen/ai/AGENTS.md`
- `hosts/nixos/nyx.nix`
- `modules/hm/users/cdenneen/programs.nix`
- `modules/hm/users/cdenneen/git.nix`
- `modules/hm/users/cdenneen/secrets.nix`
- `modules/hm/programs/ssh.nix`
- `modules/hm/users/cdenneen/files/restart-tmux`
- `PROJECT_STATE.md`
- `DECISIONS.md`
- `NEXT_STEPS.md`

## Current Branch

- `main`

## Exact Next Action

Build/apply `ghost`, validate the lean HM package list still supports expected remote workflows, then re-test Codex profile resume on the target host.
