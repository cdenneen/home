# Agent Guide (AGENTS.md)

This repo is a Nix flake monorepo for NixOS, nix-darwin, and Home Manager. These notes guide agentic tooling.

## How to read context

- Read this file first.
- Then read any referenced docs (kept short and focused).
- Always read workspace-level `AGENTS.md` and repo-level `AGENTS.md` when present.

## Quick facts

- Flake wiring lives under `systems/`, hosts under `hosts/`, modules under `modules/`.
- Home Manager is integrated by default and uses its own nixpkgs (unstable) via per-user HM config.
- `secrets/secrets.yaml` is encrypted with sops + age; recipients are managed via `.sops.yaml`.
- Prefer conservative changes; keep diffs small and readable.
- Prefer OpenTofu (`tofu`) over `terraform` for all Terraform commands.

## Workspace roots

- Linux WORKSPACE_ROOT: `$HOME/src/workspace`
- Darwin WORKSPACE_ROOT: `$HOME/code/workspace`

Each workspace lives under WORKSPACE_ROOT (e.g. `gitlab`, `infra`, `eks`, `backstage`, `work`).
OpenCode sessions should save durable context to a workspace-level `AGENTS.md`
inside the workspace folder (e.g. `$WORKSPACE_ROOT/gitlab/AGENTS.md`).
Repo-level `AGENTS.md` files can be used for more granular context.

If a section of this file grows too large or complex, move it to a dedicated
markdown doc and reference it here.

## Build / lint / test commands

### System builds

```sh
# NixOS (single host)
nix build .#nixosConfigurations.<host>.config.system.build.toplevel
sudo nixos-rebuild switch --flake .#<host>

# nix-darwin (single host)
nix build .#darwinConfigurations.<host>.system
sudo darwin-rebuild switch --flake .#<host>
```

### Home Manager

```sh
home-manager switch --flake .#cdenneen@<host>
```

### Lint / format

```sh
nix fmt
nix develop -c treefmt
nix develop -c treefmt --check
```

### Single test equivalent

There are no unit tests; build a derivation instead:

```sh
# One check
nix build .#checks.<system>.<check>

# One host
nix build .#nixosConfigurations.<host>.config.system.build.toplevel
```

### Eval helpers

```sh
nix eval --impure .#nixosConfigurations.<host>.config.system.build.toplevel
nix eval --impure .#darwinConfigurations.<host>.system
```

### Flake input update workflow

- Flake input updates are opened as one PR per input (`Update flake input <name>`).
- Auto-merge is allowed only after CI evals pass.
- CI evaluates: `nixosConfigurations.nyx`, `nixosConfigurations.MacBook-Pro-NixOS`, and `darwinConfigurations.VNJTECMBCD`.

## Code style guidelines

### Nix

- Formatting: 2-space indent; let `nix fmt` decide details. Run `nix fmt` before commits.
- Imports: prefer `imports = [ ./a.nix ./b.nix ];` and keep modules small.
- One nixpkgs import per system; avoid `import nixpkgs` inside random modules.
- Home Manager with `useGlobalPkgs`: do not set `nixpkgs.config` / `nixpkgs.overlays` in HM modules.
- Options: define `type`, `default`, and `description` for new options.
- Defaults: use `mkDefault` for defaults; `mkForce` only for invariants/breakage.
- Merges: prefer `mkIf`, `mkAfter`, `mkBefore`; avoid manual deep merges.
- Hyprland: avoid `windowrulev2` (deprecated); use `windowrule` syntax.

### Naming / structure

- Nix files: prefer `kebab-case.nix` for new files.
- User HM modules: `modules/hm/users/<user>/...`.
- Host configs: `hosts/nixos/<host>.nix` and `hosts/darwin/<host>.nix`.
- Scripts: verb-first (e.g. `sops-bootstrap-host`, `update-secrets`).

### Shell scripts

- Use `set -euo pipefail`.
- Be non-interactive and idempotent.
- Never silently write empty/partial output or secrets.
- Prefer absolute paths or `${pkgs.<tool>}/bin/<tool>` in Nix scripts.

### Python

- Imports: stdlib first, third-party next, then local modules.
- Add type hints for new functions or public interfaces.
- Raise explicit, actionable errors; keep retry/backoff bounded.
- Avoid new dependencies unless packaging is updated.

### Error handling

- Fail fast on missing files, invalid config, or empty secrets.
- Emit actionable errors to stderr and return non-zero exit codes.
- Avoid partial state changes; keep operations atomic.

## Telegram bridge

- Managed via `programs.telegram-bridge` (HM module).
- Legacy per-user `opencode-telegram-bridge.nix` is removed.
- Use `pkgs/opencode-telegram-bridge.nix` and configure through
  `modules/hm/programs/telegram-bridge.nix`.

## Secrets (sops-nix + age)

See `docs/agent-secrets.md` (relative to `~/.config/opencode/`).

## GC / maintenance

- NixOS: `nix.gc.automatic = true` with weekly schedule.
- nix-darwin: GC interval configured in `modules/darwin/default.nix`.
- Store optimization: `nix.settings.auto-optimise-store = true`.

Note: avoid enabling both `programs.nh.clean.enable` and `nix.gc.automatic`.

## Cursor / Copilot rules

- No `.cursor/rules/`, `.cursorrules`, or `.github/copilot-instructions.md` present.
- Treat this file as the canonical agent instruction.
