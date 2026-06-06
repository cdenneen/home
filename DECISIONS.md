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
- Reusing OpenCode session IDs across hosts with different absolute workspace roots.
  - Rejected because it can degenerate into repeated broad filesystem permission prompts.
- Persisting the literal OpenCode password inside tmux snapshot files.
  - Rejected because snapshots are operational state, not secret storage.
- Keeping the shared DuckDuckGo gateway in stateful Streamable HTTP mode.
  - Rejected because stale client session IDs produced repeated `No valid session ID provided` failures after gateway restarts.

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

## 2026-06-05 — `nyx` OpenCode health checks should use the live system secret path

- Context
  - A manual auth check against `nyx` opencode initially used a guessed per-user password file and returned `401`.
  - The live service actually reads `config.sops.secrets.opencode_server_password.path`, which materializes as `/run/secrets/opencode_server_password`.
- Decision
  - Treat `/run/secrets/opencode_server_password` as the canonical runtime auth source for `nyx` opencode diagnostics.
- Rationale
  - Debug commands should validate the live deployed path, not an assumed user-local cache file.
- Alternatives considered
  - Keep probing guessed files under `~/.local/share` or `~/.config`.
- Consequences
  - Future opencode checks can prove auth and API health directly against the same secret path the unit uses.

## 2026-06-05 — `nyx` should isolate Playwright MCP from `opencode-serve`

- Context
  - `nyx` `opencode-serve` accumulated many `npm exec @playwright/mcp` child processes and became heavy and sluggish.
- Decision
  - Run Playwright as its own shared `nyx-mcp-playwright.service` on `127.0.0.1:18107` via `supergateway`, and point only `nyx` Codex/OpenCode configs at that shared gateway.
- Rationale
  - A shared gateway keeps browser automation available on `nyx` without bloating each OpenCode server process.
- Alternatives considered
  - Keep Playwright as a per-session local MCP command on `nyx`.
  - Move Playwright to the shared `nyx` gateway for all hosts, including the Mac.
- Consequences
  - `nyx` OpenCode stays smaller and simpler.
  - The Mac keeps local Playwright, which preserves local-browser behavior there.

## 2026-06-05 — Fresh `nyx` shells should preload OpenCode server auth

- Context
  - Direct `opencode attach http://127.0.0.1:4097 ...` on `nyx` returned `401 Unauthorized` because the CLI only sends basic auth when `OPENCODE_SERVER_PASSWORD` or `--password` is provided.
- Decision
  - Export `OPENCODE_SERVER_PASSWORD` from `/run/secrets/opencode_server_password` in fresh `nyx` login shells and update the local helper scripts to pass auth explicitly.
- Rationale
  - The protected local server should remain protected, but normal local attach workflows should still work out of the box.
- Alternatives considered
  - Remove password protection from the local server.
  - Require manual `--password` or manual env export every time.
- Consequences
  - Fresh `nyx` shells and flake-managed helpers can attach directly without 401s.
  - Existing long-lived shells may need `exec zsh -il` or a new pane to pick up the env export.

## 2026-06-05 — `restart-tmux` must not reuse OpenCode session IDs across foreign host paths

- Context
  - `nyx` `coding:8` was stuck in a permission loop after reattaching session `ses_1e88f80d2ffe5gZIW1wrs5QeIJ`.
  - That session’s stored OpenCode directory was `/Users/cdenneen/code/workspace/k8s`, but the live nyx pane path was `/home/cdenneen/src/workspace/k8s`.
  - The mismatched absolute roots caused OpenCode to request broad `/` filesystem access instead of resuming cleanly.
- Decision
  - Teach `restart-tmux` to query the stored OpenCode session directory and skip that session ID when it does not match the local pane path.
- Rationale
  - Cross-host session IDs are not portable when the server stores absolute working directories from different hosts.
- Alternatives considered
  - Keep reusing the captured session ID and accept the permission loop.
  - Disable permissions or broadly approve `/` access.
  - Manually repair each stuck pane without fixing the helper.
- Consequences
  - `restart-tmux` now falls back to the latest host-native session for the pane path or `--continue`.
  - Operators should expect Mac-created session IDs and nyx-created session IDs to behave differently when the workspace roots differ.

## 2026-06-05 — tmux snapshots must not store literal OpenCode passwords

- Context
  - The original `restart-tmux` auth flags embedded the resolved OpenCode password directly into the snapshot command line.
- Decision
  - Keep the auth flags dynamic so restored panes read `OPENCODE_SERVER_PASSWORD` or `/run/secrets/opencode_server_password` at runtime instead of persisting the secret value.
- Rationale
  - tmux snapshot files should not become plaintext secret material.
- Alternatives considered
  - Keep snapshotting the literal password.
  - Remove auth flags entirely and break protected-local-server restores.
- Consequences
  - Restored panes still authenticate cleanly.
  - Snapshot files no longer expose the actual OpenCode password.

## 2026-06-05 — DuckDuckGo shared MCP should use stateless Streamable HTTP

- Context
  - DuckDuckGo MCP calls from Codex/OpenCode were failing with `Bad Request: No valid session ID provided`.
  - A clean direct handshake against the `nyx` DuckDuckGo gateway succeeded, which showed the gateway itself was healthy but stateful and expecting a valid MCP session.
  - After the gateway had been restarted, long-lived clients could still hold stale session IDs.
- Decision
  - Keep the shared DuckDuckGo gateway on `nyx`, but run it in stateless Streamable HTTP mode instead of stateful mode.
- Rationale
  - DuckDuckGo search calls are stateless and do not need persistent child-process sessions.
  - Stateless mode tolerates stale or missing `mcp-session-id` headers and auto-initializes per request.
- Alternatives considered
  - Leave DuckDuckGo stateful and require restarting all affected Codex/OpenCode sessions after every gateway restart.
  - Move DuckDuckGo back to a local per-client stdio command on each host.
  - Switch all shared MCP gateways to stateless immediately.
- Consequences
  - DuckDuckGo searches continue working even for clients that still send an old session header.
  - Other shared gateways still use their previous stateful behavior and may need similar treatment later if they show the same symptom.

## 2026-06-06 — Codex profiles must be file-layered, not embedded in `config.toml`

- Context
  - After the Codex update, `codex resume ...` failed because the generated `~/.codex/config.toml` still contained legacy `profile = "safe-relaxed"` and `[profiles.*]` settings.
- Decision
  - Remove legacy profile selector/tables from the base generated config and generate standalone profile files: `~/.codex/safe-relaxed.config.toml`, `~/.codex/ci-runner.config.toml`, `~/.codex/fast-triage.config.toml`, and `~/.codex/strict.config.toml`.
- Rationale
  - Codex 0.134+ only supports `--profile <name>` with `~/.codex/<name>.config.toml`; legacy in-file profile definitions are explicitly rejected.
- Alternatives considered
  - Keep legacy profile settings in `config.toml` and try to continue using `--profile`.
  - Drop profiles entirely and rely only on one-off `--config` flags.
- Consequences
  - Flake-generated config now matches current Codex expectations.
  - Existing profile-oriented workflows continue to work with `--profile` once the new Home Manager generation is applied.

## 2026-06-06 — `ghost` should use a lean Home Manager package set

- Context
  - `ghost` is primarily a remote services and remote development host, and store usage should prioritize essential CLI workflows over optional local heavy tooling.
- Decision
  - Split Home Manager packages into core and heavy groups and exclude the heavy group when `hostName == "ghost"`.
- Rationale
  - This keeps common remote workflows available (`git`, secrets, SSM/OCI access, workspace tooling) while reducing package closure size on `ghost`.
- Alternatives considered
  - Disable Home Manager integration entirely on `ghost`.
  - Keep the same full package set across all hosts.
- Consequences
  - `ghost` no longer gets heavy extras like Kubernetes/GitOps helper bundle, browser/runtime extras, and clipboard bridge tooling from the HM package list.
  - Re-adding any removed tool later is straightforward by moving it back into core or adding a ghost override.
