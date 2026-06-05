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
