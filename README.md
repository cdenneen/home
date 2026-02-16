# home — upstream-based Nix flake fork

This repository is a **conservative fork** of an upstream Nix flake, rebased and maintained to support a clean developer workflow across **nix-darwin**, **NixOS**, and **Home Manager**.

The guiding principle is boring correctness: reproducible builds, declarative state, and loud failures when invariants are broken.

---

## Fork delta (what differs from upstream)

This fork intentionally keeps the surface area small. The changes below are the _entire_ reason the fork exists.

### Home Manager invariants

- Enforced single `xdg.configFile` definition per module
- Removed destructive or mutating activation-time behavior
- Home Manager remains declarative; imperative scripts are invoked, not owned

### Git, GPG, and hooks

- Restored **OpenPGP** commit signing (no SSH signing misuse)
- Pre-commit hooks are **check-only** (no commits, no stashing, no mutation)
- Formatting enforced via `treefmt --check`

### Theming (Catppuccin)

- Per-host defaults:
  - macOS (nix-darwin): `latte`
  - Linux (NixOS): `mocha`
- Only flake-supported integrations enabled (starship, bat, fzf, tmux, nvim)
- No hard-coded color palettes in program configs

### Starship

- Fully managed via Home Manager
- Canonical config path only (`~/.config/starship.toml`)
- Legacy paths intentionally ignored, not deleted

### Secrets (sops-nix / AGE)

- Reliable AGE key handling on macOS and Linux
- Secrets decrypt correctly without activation-time hacks

Note: on NixOS, if you add a user to the `sops` group after login, you may need to re-login or restart the user manager for user services (like `sops-nix.service`) to pick up the new group:

```sh
sudo systemctl restart user@$(id -u <user>).service
```

### CI and evaluation

- Fast checks by default; full checks are opt-in in CI
- Simplified GitHub Actions matrix
- CI behavior reproducible locally via `nix build` / `nix flake check`

---

## What this fork does _not_ do

- Hide or mutate state during activation
- Own imperative state in Home Manager
- Mutate files during evaluation, activation, or git hooks
- Diverge stylistically from nixpkgs conventions

---

## Working with this repo

See `AGENTS.md` for:

- build / lint / check commands (system + Home Manager)
- code style and naming rules
- invariants that must not be violated

This repo also includes a repo-scoped OpenCode agent at `/.opencode/agents/nixos-expert.md` for NixOS/nix-darwin/Home Manager work.

If a change does not clearly fit the fork delta above, it likely does not belong here.

---

## Usage

### System builds

```sh
# NixOS
nix build .#nixosConfigurations.<host>.config.system.build.toplevel
sudo nixos-rebuild switch --flake .#<host>

# nix-darwin
nix build .#darwinConfigurations.<host>.system
sudo darwin-rebuild switch --flake .#<host>
```

### Home Manager

Home Manager configs are keyed by username (e.g. `cdenneen`) and target the current system.

```sh
home-manager switch --flake .#cdenneen
```

### HM integration in system builds

Home Manager is integrated into system builds by default. Disable per-host if you want
standalone HM only:

```nix
profiles.hmIntegrated.enable = false;
```

### Bootstrap with a minimal flake

Example `/etc/nixos/flake.nix` that delegates to this repo and sets host params:

```nix
{
  description = "System bootstrap";

  inputs.home.url = "github:cdenneen/home";

  outputs = { home, ... }:
    let
      host = "foobar";
    in
    home.lib.bootstrap {
      hostName = host;
      kind = "nixos"; # nixos or darwin
      system = "x86_64-linux";
      tags = [ "crostini" ];
      users = [ "cdenneen" ];
      nixosModules = [ ./configuration.nix ];
    };
}
```

Then:

```sh
sudo nixos-rebuild switch --flake /etc/nixos#foobar
home-manager switch --flake /etc/nixos#cdenneen
```

Tags can include `ec2`, `amazon-ami`, `qemu-guest`, `wsl`, and `crostini`.

### Structure

- `hosts/` host definitions
- `systems/` flake wiring
- `modules/system/` OS modules (shared + platform)
- `modules/hm/` Home Manager modules
- `modules/shared/` shared helpers
