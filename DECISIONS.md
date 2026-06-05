# Decisions

## 2026-06-05 — Shared agent policy uses file-based project memory

- Context
  - The shared global Codex/OpenCode `AGENTS.md` needed durable memory rules because chat history is ephemeral.
- Decision
  - Add persistent memory requirements and a startup/shutdown routine to the shared AI `AGENTS.md`.
- Rationale
  - Fresh agents need durable repo context to resume safely after context loss, compaction, or restarts.
- Alternatives considered
  - Keep relying on conversational context alone.
  - Use ad-hoc notes outside the repo.
- Consequences
  - Every substantial session should maintain `PROJECT_STATE.md`, `NEXT_STEPS.md`, `ARCHITECTURE.md`, `TASKS.md`, `DECISIONS.md`, and `HANDOFF.md`.
  - These files are now part of the deliverable, not optional notes.

## 2026-06-05 — Custom CLI package updates are separate from flake input updates

- Context
  - `nix flake lock --update-input nixpkgs` and `nixpkgs-unstable` were no-ops, but `codex` and `opencode` were still behind upstream releases.
- Decision
  - Keep flake input updates and custom package pin updates as separate automation paths.
- Rationale
  - `codex` and `opencode` are pinned in custom derivations under `pkgs/`, so flake input bumps alone cannot move them.
- Alternatives considered
  - Assume flake input updates are sufficient for all tool updates.
  - Manually bump the custom package pins when noticed.
- Consequences
  - GitHub Actions now need to maintain both flake input updates and custom CLI pin updates.

## 2026-06-05 — Host deployment remains commit/push/pull/switch

- Context
  - This repo manages multiple hosts and user environments; transient changes make state drift hard to reason about.
- Decision
  - Keep the deployment flow as local edit -> commit -> push -> remote pull -> rebuild/switch.
- Rationale
  - This preserves auditability and makes host state reproducible from git history.
- Alternatives considered
  - Ad-hoc remote edits.
  - One-off transient fixes on target hosts.
- Consequences
  - Remote changes should land through the repo unless emergency recovery requires otherwise.

## Failed or Rejected Approaches

- Treating flake input updates as the only path for newer `codex`/`opencode` versions.
  - Rejected because those packages are pinned in custom derivations.
- Relying on chat history alone for project continuation.
  - Rejected because context can be lost between sessions.
- Accepting stale SSH identity paths after host switches.
  - Rejected because it breaks normal verification and operational workflows.
- Accepting stale git signing key paths after host switches.
  - Rejected because it breaks normal signed commit workflows and hides declarative drift.
- Accepting broken flake-managed key symlinks that point into deleted temporary secret paths.
  - Rejected because it breaks both SSH access and signed commit workflows at once.

## 2026-06-05 — Git and SSH consumers should use stable `~/.ssh/*` paths

- Context
  - Mac git signing and SSH aliases broke after a flake switch because consumers referenced secret-materialization paths directly.
- Decision
  - Point git signing and SSH `IdentityFile` consumers at stable `~/.ssh/*` paths, and ensure SOPS secrets materialize to stable per-OS secret directories.
- Rationale
  - User-facing tools should not depend on transient secret directory layouts.
- Alternatives considered
  - Keep using `config.sops.secrets.*.path` directly in git and SSH consumer config.
  - Keep relying on fallback saved keys outside flake control.
- Consequences
  - The flake now treats `~/.ssh/*` as the stable consumer interface while SOPS remains the secret source of truth.

## 2026-06-05 — `nyx` OpenCode compaction must talk to the direct app API

- Context
  - `opencode-serve-compact` was querying `127.0.0.1:4096`, which is the OAuth proxy, and received sign-in HTML instead of JSON.
  - The main `opencode-serve` unit also was not exporting `OPENCODE_SERVER_PASSWORD`.
- Decision
  - Export `OPENCODE_SERVER_PASSWORD` in `opencode-serve`, use the direct app API on `127.0.0.1:4097` for compaction, and bound `curl` calls with timeouts.
- Rationale
  - The compaction helper is a machine-to-machine client and should bypass the human OAuth layer.
- Alternatives considered
  - Keep calling the OAuth proxy.
  - Leave the server unsecured and skip compaction.
- Consequences
  - `nyx` opencode is now secure again and the compact one-shot returns instead of wedging indefinitely.
