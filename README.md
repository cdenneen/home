# home — upstream-based Nix flake fork

This repository is a **conservative fork** of an upstream Nix flake, rebased and maintained to support a clean, ARM-first developer workflow across **nix-darwin**, **NixOS**, and **Home Manager**.

The guiding principle is boring correctness: reproducible builds, declarative state, and loud failures when invariants are broken.

---

## Fork delta (what differs from upstream)

This fork intentionally keeps the surface area small. The changes below are the *entire* reason the fork exists.

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

### CI and evaluation
- ARM-only evaluation (aarch64-darwin, aarch64-linux)
- Simplified GitHub Actions matrix
- CI behavior reproducible locally via `nix build` / `nix flake check`

---

## What this fork does *not* do

- Support x86_64 systems
- Own imperative state in Home Manager
- Mutate files during evaluation, activation, or git hooks
- Diverge stylistically from nixpkgs conventions

---

## Working with this repo

See `AGENTS.md` for:
- build / lint / check commands
- code style and naming rules
- invariants that must not be violated

If a change does not clearly fit the fork delta above, it likely does not belong here.
