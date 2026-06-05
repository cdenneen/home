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
- The stable SSH/git-signing path fix is committed as `a3006aa0`, pushed to `main`, and applied on `nyx` and `ghost`.
- Git signing is confirmed working on Mac, `nyx`, and `ghost` with real signed temp commits after the live generations were switched.
- Shared Codex/OpenCode `AGENTS.md` memory guidance is confirmed present on `nyx` and `ghost`.
- `nyx` `opencode-serve` is active again, the direct API is responding with auth, and the compaction one-shot now exits successfully.
- `nyx` now isolates Playwright MCP into `nyx-mcp-playwright.service` on `127.0.0.1:18107`, and `opencode-serve` is down to a small process tree again.
- Fresh `zsh -l` shells on `nyx` export `OPENCODE_SERVER_PASSWORD`, so direct `opencode attach http://127.0.0.1:4097 ...` no longer fails with `401`.
- `nyx` `restart-tmux` now detects OpenCode sessions whose stored working directory belongs to another host root and skips reattaching those incompatible session IDs.
- `nyx` `coding:8` was repaired by switching it from Mac-path session `ses_1e88f80d2ffe5gZIW1wrs5QeIJ` to nyx-native session `ses_1af7abd07ffeZXOtNe8WoLiGNB`.
- `restart-tmux` no longer snapshots the literal OpenCode password into tmux snapshot files.

## Active Work Stream

- Establish durable repo memory and keep it current.
- Watch `nyx` `opencode` session count and cross-host attach behavior over time now that Playwright is isolated, stale-session deletion is enabled, and `restart-tmux` guards against foreign session paths.

## Recent Accomplishments

- Added persistent project memory guidance to `modules/hm/users/cdenneen/ai/AGENTS.md`.
- Added automation for custom-pinned `codex` and `opencode` package updates.
- Merged current `codex` and `opencode` update PRs after CI success.
- Applied the latest flake generation on Mac, `nyx`, and `ghost`.
- Fixed the Mac-side SSH and git signing config to use stable `~/.ssh/*` key paths again.
- Signed and pushed the stable SSH/signing-path fix, then applied it on `nyx` and `ghost`.
- Verified good signed commits on Mac, `nyx`, and `ghost` after the live generations were updated.
- Re-verified the shared Codex/OpenCode memory guidance on `nyx` and `ghost`.
- Fixed `nyx` opencode password wiring and moved session compaction to the direct app API with bounded timeouts.
- Confirmed `nyx` opencode auth reads from `/run/secrets/opencode_server_password` and the live API responds again.
- Added `nyx-mcp-playwright.service` and pointed only `nyx` Codex/OpenCode configs at the shared gateway instead of spawning Playwright inside each session.
- Added `nyx` shell and helper-script auth wiring so direct `opencode attach` and `opencode-attach-latest` both work against the protected local server.
- Verified post-restart live state on `nyx`: `opencode-serve` at about `7` tasks and about `173M` memory, shared Playwright active separately, direct attach succeeds, and compaction now logs bounded `12`-session runs.
- Verified stale-session deletion on `nyx`, reducing the historical session count from `91` to `75`.
- Identified the window-8 permission loop root cause: the live session was created with Mac directory `/Users/cdenneen/code/workspace/k8s`, so local `nyx` reattach tried to request broad `/` access.
- Patched `restart-tmux` to fall back to a host-native session or `--continue` when the stored OpenCode session directory does not match the local pane path.
- Removed the OpenCode password from tmux snapshot command lines.
- Reattached `nyx` `coding:8` to the latest nyx-native `k8s` session and confirmed the permission prompt no longer reappears.

## Current Blockers

- No critical blocker is open for git signing.
- No current hard blocker is open for `nyx` `opencode`; the remaining concerns are whether other helpers besides `restart-tmux` need cross-host session-path guardrails and whether long-lived session count keeps growing.

## Known Risks

- Future sessions may repeat failed work if `DECISIONS.md` and `HANDOFF.md` are not kept current.
- Host verification can silently regress if SSH config and materialized key paths drift again.
- `nyx` may still accumulate a large historical session count; if responsiveness regresses again, the next step is session-prune strategy rather than more Playwright isolation.
- OpenCode session IDs are not portable across Mac and Linux hosts when the stored session directory uses different absolute workspace roots.
- The repo spans multiple host types and services; stale summaries become misleading quickly if not refreshed.

## Important Assumptions

- `main` is the active branch for normal flake work.
- Host changes should be committed and pushed locally before pulling and switching on remote hosts.
- Secrets remain managed with SOPS/age unless intentionally redesigned.
- Shared Codex/OpenCode global config continues to source `modules/hm/users/cdenneen/ai/AGENTS.md`.
- Sessions that should be resumable inside `nyx` tmux should be created or continued from nyx-native workspace paths under `/home/cdenneen/src/workspace`.
