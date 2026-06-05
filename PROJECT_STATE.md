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

## Active Work Stream

- Establish durable repo memory and keep it current.
- Clean up local SSH config drift that is preventing reliable post-switch verification of `nyx` and `ghost` via the usual aliases.

## Recent Accomplishments

- Added persistent project memory guidance to `modules/hm/users/cdenneen/ai/AGENTS.md`.
- Added automation for custom-pinned `codex` and `opencode` package updates.
- Merged current `codex` and `opencode` update PRs after CI success.
- Applied the latest flake generation on Mac, `nyx`, and `ghost`.

## Current Blockers

- Local SSH host aliases for `nyx` and `ghost` currently reference stale `IdentityFile` paths under `~/.config/sops-nix/secrets/*`.
- The actual materialized keys are present under `~/.ssh/*`, so alias-based remote verification is currently unreliable until the SSH config is corrected.

## Known Risks

- Future sessions may repeat failed work if `DECISIONS.md` and `HANDOFF.md` are not kept current.
- Host verification can silently regress if SSH config and materialized key paths drift again.
- The repo spans multiple host types and services; stale summaries become misleading quickly if not refreshed.

## Important Assumptions

- `main` is the active branch for normal flake work.
- Host changes should be committed and pushed locally before pulling and switching on remote hosts.
- Secrets remain managed with SOPS/age unless intentionally redesigned.
- Shared Codex/OpenCode global config continues to source `modules/hm/users/cdenneen/ai/AGENTS.md`.
