# Agent Guide (AGENTS.md)

This repo is a Nix flake monorepo for NixOS, nix-darwin, and Home Manager. These notes are for agentic coding tools working autonomously here.

## Quick Facts

- Flake outputs live under `systems/` and `modules/`.
- Home Manager is integrated (system-managed) and generally uses `home-manager.useGlobalPkgs = true`.
- `secrets/secrets.yaml` is encrypted with sops + age; recipients are managed via `.sops.yaml`.
- Prefer conservative changes; keep diffs small and readable.

## Commands (Build / Lint / Test)

### Evaluate the flake

```sh
nix flake show
```

### Build a single thing (fast iteration)

- Build one check:

```sh
nix build .#checks.aarch64-darwin.<check>
nix build .#checks.aarch64-linux.<check>
```

- List check names:

```sh
nix flake show | sed -n '/checks/,$p'
```

- Build one host system output:

```sh
nix build .#darwinConfigurations.<host>.system
nix build .#nixosConfigurations.<host>.config.system.build.toplevel
```

### Switch (only on the target machine)

```sh
sudo darwin-rebuild switch --flake .
sudo nixos-rebuild switch --flake .
```

### Formatting / lint

- Repo formatter (treefmt via flake):

```sh
nix fmt
```

- If you need the wrapper directly:

```sh
nix develop -c treefmt
nix develop -c treefmt --check
```

Rule: run `nix fmt` before committing.

### “Single test” equivalents

There aren’t unit tests in the usual sense; the closest is building one derivation:

- One check: `nix build .#checks.<system>.<check>`
- One host: `nix build .#(darwin|nixos)Configurations.<host>...`

## Nix Style Guidelines

- Formatting: 2-space indent; let `nix fmt` decide details.
- Imports: prefer `imports = [ ./a.nix ./b.nix ];` and keep modules small.
- One nixpkgs import per system: do not `import nixpkgs` inside random modules.
- Home Manager with `useGlobalPkgs`: do not set `nixpkgs.config` / `nixpkgs.overlays` in HM modules.
- `mkDefault` for defaults; `mkForce` only for invariants or upstream breakage.
- Keep option schemas accurate (types + descriptions) when adding new options.

## Naming / Structure

- Nix files: `kebab-case.nix`.
- User HM modules: `modules/home/users/<user>/...`.
- Host configs: `systems/<host>.nix`.
- Scripts: verb-first (e.g. `sops-bootstrap-host`, `update-secrets`).

## Shell Script Expectations

- Use `set -euo pipefail`.
- Non-interactive and idempotent.
- Never silently write empty/partial secrets.

## Secrets (sops-nix + age)

Files:

- Encrypted: `secrets/secrets.yaml`
- Recipients config: `.sops.yaml`
- Human registry: `pub/age-recipients.txt`

Devshell helpers (preferred):

```sh
nix develop
sops-edit
sops-diff-keys
sops-update-keys   # non-interactive
sops-verify-keys
sops-bootstrap-host
```

Safe recipient rotation:

1. Bootstrap host key: `sops-bootstrap-host`
2. Update `pub/age-recipients.txt` and `.sops.yaml`
3. Re-encrypt: `sops-update-keys`
4. Verify registry: `sops-verify-keys`

Host key conventions (Linux):

- System key: `/var/sops/age/keys.txt`
- Permissions: `root:sops` + directory `0750`, file `0440` so user sops-nix can read.

If you add a user to the `sops` group after login, the systemd user manager may not pick up the new supplementary groups. Fix by re-logging in or restarting the user manager:

```sh
sudo systemctl restart user@$(id -u <user>).service
```

## GC / Maintenance

- NixOS: `nix.gc.automatic = true` with weekly schedule.
- nix-darwin: GC interval configured in `modules/darwin/default.nix`.
- Store optimization: `nix.settings.auto-optimise-store = true`.

Note: avoid enabling both `programs.nh.clean.enable` and `nix.gc.automatic`.

## Cursor / Copilot Rules

- No `.cursor/rules/`, `.cursorrules`, or `.github/copilot-instructions.md` present.
- Treat this file as the canonical agent instruction.
