# Project State

## Current Goals

- Keep this flake repo as the source of truth for macOS, NixOS, and Home Manager configuration.
- Make Codex/OpenCode behavior, tooling, secrets, and host deployment workflows reproducible across hosts.
- Preserve enough durable project context that a fresh agent can resume work without prior chat history.

## Current Status

- Shared global AI `AGENTS.md` now includes persistent project memory requirements and startup/shutdown routines.
- Required repo-root memory files have been added and initialized.
- Recent `codex` and `opencode` package bumps were merged and applied on Mac, `nyx`, and `ghost`.
- `nyx` and `ghost` successfully pulled the latest repo and completed `nixos-rebuild switch`.
- The local Mac completed `darwin-rebuild switch`.
- The local git signing and SSH key-path issue is fixed on the Mac and verified with a real signed temp commit.
- Git signing is confirmed working on `nyx` and `ghost`.
- `nyx` `opencode-serve` has been rebuilt, restarted, and is responding on the direct API again.

## Active Work Stream

- Establish durable repo memory and keep it current.
- Commit and push the verified SSH/git-signing path fix, then apply it on `nyx` and `ghost`.

## Recent Accomplishments

- Added persistent project memory guidance to `modules/hm/users/cdenneen/ai/AGENTS.md`.
- Added automation for custom-pinned `codex` and `opencode` package updates.
- Merged current `codex` and `opencode` update PRs after CI success.
- Applied the latest flake generation on Mac, `nyx`, and `ghost`.
- Fixed the Mac-side SSH and git signing config to use stable `~/.ssh/*` key paths again.
- Verified good signed commits on Mac, `nyx`, and `ghost`.
- Fixed `nyx` opencode password wiring and moved session compaction to the direct app API with bounded timeouts.

## Current Blockers

- No critical blocker is open for git signing now that the Mac fix is verified.
- The remaining work is to commit and propagate the verified signing-path fix to `nyx` and `ghost`.

## Known Risks

- Future sessions may repeat failed work if `DECISIONS.md` and `HANDOFF.md` are not kept current.
- Host verification can silently regress if SSH config and materialized key paths drift again.
- `nyx` opencode currently spawns many Playwright MCP child processes under load; memory should be watched even though the service is healthy again.
- The repo spans multiple host types and services; stale summaries become misleading quickly if not refreshed.

## Important Assumptions

- `main` is the active branch for normal flake work.
- Host changes should be committed and pushed locally before pulling and switching on remote hosts.
- Secrets remain managed with SOPS/age unless intentionally redesigned.
- Shared Codex/OpenCode global config continues to source `modules/hm/users/cdenneen/ai/AGENTS.md`.
