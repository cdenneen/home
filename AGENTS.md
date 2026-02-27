# Agent Guide (AGENTS.md)

This repo is a Nix flake monorepo for NixOS, nix-darwin, and Home Manager. These notes are for agentic coding tools working autonomously here.

## How to read context

- Read this file first.
- Then read any referenced docs in this file (kept short and focused).
- Always read workspace-level `AGENTS.md` and repo-level `AGENTS.md` when present.

## Quick Facts

- Flake wiring lives under `systems/`, hosts under `hosts/`, modules under `modules/`.
- Home Manager is integrated by default and uses its own nixpkgs (unstable) via per-user HM config.
- `secrets/secrets.yaml` is encrypted with sops + age; recipients are managed via `.sops.yaml`.
- Prefer conservative changes; keep diffs small and readable.
- Prefer OpenTofu (`tofu`) over `terraform` for all Terraform commands.

## Workspace Roots

- Linux WORKSPACE_ROOT: `$HOME/src/workspace`
- Darwin WORKSPACE_ROOT: `$HOME/code/workspace`

Each workspace lives under WORKSPACE_ROOT (e.g. `gitlab`, `infra`, `eks`, `backstage`, `work`).
OpenCode sessions should save durable context to a workspace-level `AGENTS.md`
inside the workspace folder (e.g. `$WORKSPACE_ROOT/gitlab/AGENTS.md`).
Repo-level `AGENTS.md` files can be used for more granular context.

If a section of this file grows too large or complex, move it to a dedicated
markdown doc and reference it here.

## Commands (Build / Lint / Test)

See `docs/agent-commands.md` (relative to `~/.config/opencode/`).

## Nix Style Guidelines

- Formatting: 2-space indent; let `nix fmt` decide details.
- Imports: prefer `imports = [ ./a.nix ./b.nix ];` and keep modules small.
- One nixpkgs import per system: do not `import nixpkgs` inside random modules.
- Home Manager with `useGlobalPkgs`: do not set `nixpkgs.config` / `nixpkgs.overlays` in HM modules.
- `mkDefault` for defaults; `mkForce` only for invariants or upstream breakage.
- Keep option schemas accurate (types + descriptions) when adding new options.
- Hyprland: avoid `windowrulev2` (deprecated); use `windowrule` syntax.

## Naming / Structure

- Nix files: prefer `kebab-case.nix` for new files.
- User HM modules: `modules/hm/users/<user>/...`.
- Host configs: `hosts/nixos/<host>.nix` and `hosts/darwin/<host>.nix`.
- Scripts: verb-first (e.g. `sops-bootstrap-host`, `update-secrets`).
- Telegram bridge is managed via `programs.telegram-bridge` (HM module). The legacy
  per-user `opencode-telegram-bridge.nix` module is removed. Use the packaged bridge
  at `pkgs/opencode-telegram-bridge.nix` and configure through
  `modules/hm/programs/telegram-bridge.nix`.

## Shell Script Expectations

- Use `set -euo pipefail`.
- Non-interactive and idempotent.
- Never silently write empty/partial secrets.

## Secrets (sops-nix + age)

See `docs/agent-secrets.md` (relative to `~/.config/opencode/`).

## GC / Maintenance

- NixOS: `nix.gc.automatic = true` with weekly schedule.
- nix-darwin: GC interval configured in `modules/darwin/default.nix`.
- Store optimization: `nix.settings.auto-optimise-store = true`.

Note: avoid enabling both `programs.nh.clean.enable` and `nix.gc.automatic`.

## Cursor / Copilot Rules

- No `.cursor/rules/`, `.cursorrules`, or `.github/copilot-instructions.md` present.
- Treat this file as the canonical agent instruction.
