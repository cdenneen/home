# Agent Guide for `home` Nix Flake Repository

This document is intended for **agentic coding assistants** (LLMs, Cursor, Copilot, etc.) working in this repository. It summarizes how to build, test, lint, and modify the codebase safely, and documents the architectural and style conventions already in use.

The goal is to keep changes **boring, reproducible, and reviewable**. This file is written for
agentic coding assistants (LLMs, Cursor, Copilot) operating autonomously in this repo.

---

## Repository Overview

- This is a **Nix flake–based mono‑repo** for:
  - nix‑darwin (macOS)
  - NixOS (Linux, incl. WSL)
  - Home Manager (integrated via `useGlobalPkgs = true`)
- Uses **flake‑parts** to structure outputs.
- Targets **ARM only**:
  - `aarch64-darwin`
  - `aarch64-linux`
- No x86_64 support is expected or tested.

Key principles:

- One nixpkgs import per system
- No ad‑hoc `import nixpkgs` inside `perSystem`
- Home Manager is declarative but may _invoke_ imperative scripts

Non-goals:
- Supporting x86_64 platforms
- Adding imperative state ownership to Home Manager
- Mutating files during evaluation, activation, or git hooks

---

## Common Commands

### Flake evaluation

```sh
nix flake show
nix flake check
```

Note:

- `nix flake check` is **not filtered by system**.
- CI explicitly builds `checks.aarch64-linux` and `checks.aarch64-darwin` instead.

### Build individual checks (single check)

Use this when iterating on one failing check:

```sh
nix build .#checks.aarch64-darwin.<check-name>
nix build .#checks.aarch64-linux.<check-name>
```

List available checks:

```sh
nix flake show | sed -n '/checks/,$p'
```

### Build NixOS system (Linux)

```sh
nix build .#nixosConfigurations.eros.config.system.build.toplevel
```

### Build nix-darwin system (macOS)

```sh
nix build .#darwinConfigurations.VNJTECMBCD.system
```

### Switch system (local machine only)

- macOS:

```sh
sudo darwin-rebuild switch --flake .
```

- NixOS:

```sh
sudo nixos-rebuild switch --flake .
```

### Home Manager only (when available)

```sh
home-manager switch --flake .
```

### Linting & formatting

Formatting is enforced via **treefmt** and checked in CI.

Run formatter locally:

```sh
treefmt
```

Check formatting only (used in pre-commit):

```sh
treefmt --check
```

---

## Devshell

This repo supports a **flake devshell**.

Enter it with:

```sh
nix develop
```

The devshell:

- Uses the same nixpkgs pin as the rest of the flake
- Should not override or re‑import nixpkgs
- Is intentionally minimal

Do **not** add heavy tooling here unless it is universally useful.

---

## CI (GitHub Actions)

Workflow lives at:

```
.github/workflows/ci.yml
```

Important constraints:

- Linux jobs run on `ubuntu-latest`
- Darwin jobs run on `macos-latest`
- No cross‑OS builds (Linux never builds Darwin)
- Cachix publishing is **not** part of this workflow (handled separately)

When editing CI:

- Never reference `secrets.*` at job level
- Use **step‑level guards** only
- Avoid conditional `needs:` relationships

### Running CI logic locally

CI logic should be reproducible via `nix build` / `nix flake check`.
Do **not** add GitHub-only logic that cannot be exercised locally.

---

## Code Style – Nix

### Formatting

- Follow standard nixpkgs style
- 2‑space indentation
- No trailing whitespace
- Attribute sets sorted logically, not alphabetically
- Prefer explicit parentheses for conditionals over clever expressions

### Imports

- Prefer `imports = [ ./foo.nix ./bar.nix ];`
- Avoid deep relative paths in system modules
- One nixpkgs import per system (enforced)
- Never import nixpkgs inside `perSystem`
- Home Manager user modules live under:
  ```
  modules/home/users/<username>/
  ```

### nixpkgs usage

- `home-manager.useGlobalPkgs = true` is **required**
- `allowUnfree`, overlays, and config belong to the **system nixpkgs import**
- Never set `nixpkgs.*` options inside Home Manager modules
- Prefer `lib.mkDefault` for host-specific overrides
- Use `lib.mkForce` only when correcting an upstream or invariant violation

### Assertions & guards

- Prefer failing early with clear assertions
- Avoid silent fallbacks
- CI should fail loudly if invariants are broken

### Types & option hygiene

- Define options with accurate types and descriptions
- Avoid `types.anything` unless strictly necessary
- Prefer smaller, composable modules over large option bags

### Naming conventions

- Files: `kebab-case.nix`
- Modules: descriptive and scoped (`git.nix`, `zsh.nix`)
- Options: explicit and namespaced
- Scripts: verb-first (`update-secrets`, `sync-keys`)

---

## Shell Scripts & Helpers

### Location

- User scripts live under:

  ```
  modules/home/users/<username>/files/
  ```

- They are installed via `home.file` into `$HOME/.local/bin`

### Requirements

- Scripts must be **self‑contained**
- Do not rely on interactive shell functions
- Must work in non‑interactive contexts (HM activation, CI, cron)

### Error handling

- Use `set -euo pipefail` (or zsh equivalent)
- Fail fast on missing inputs
- Never silently emit empty secrets or config

Scripts should be:
- Non-interactive
- Deterministic
- Safe to run multiple times

---

## Secrets & sops-nix Workflow

This repository uses **sops-nix with AGE encryption**.

### Files
- Encrypted secrets: `secrets/secrets.yaml`
- Human registry of AGE recipients: `pub/age-recipients.txt`

Public keys are documented for humans only; sops does not consume the registry directly.

### Helper commands (available in devshell)
- `sops-edit` – edit secrets safely
- `sops-diff-keys` – preview recipient changes with context
- `sops-update-keys` – re-encrypt with updated recipients
- `sops-check` – show current recipients and registry
- `sops-verify-keys` – enforce that all recipients are documented
- `sops-bootstrap-host` – generate host AGE key on a new machine

### Safe update procedure
1. Generate host key: `sops-bootstrap-host`
2. Add public key to `pub/age-recipients.txt`
3. Review changes: `sops-diff-keys`
4. Re-encrypt: `sops-update-keys`
5. Verify: `sops-verify-keys`

Never approve recipient changes unless every key is understood and documented.

---

## Naming Conventions

- Files: `kebab-case.nix`
- Modules: descriptive, not generic (`zsh.nix`, not `shell.nix`)
- Options: scoped and explicit
- Scripts: verbs preferred (`update-secrets`, `sync-keys`)

---

## What NOT to Do

- Do not add x86_64 support
- Do not import nixpkgs manually inside `perSystem`
- Do not add secrets to the repo (even encrypted) without review
- Do not rely on Home Manager to _own_ imperative state
- Do not "fix" CI by disabling jobs or checks
- Do not introduce `xdg.configFile` duplication in Home Manager modules
- Do not manage Starship or similar tools via ad-hoc file writes

---

## Agent Expectations

When making changes:

- Keep diffs minimal
- Preserve existing structure
- Prefer small, composable modules
- Explain _why_ a change is needed, not just _what_
- Assume CI will catch regressions

If unsure, choose the **most conservative** option.

---

## Cursor / Copilot Notes

No explicit `.cursor/rules` or `.cursorrules` files are present.
No `.github/copilot-instructions.md` is present.

Agents should treat **this file** as the canonical instruction source.

---

## Quick Agent Checklist

- Can this be built with `nix build`?
- Does it respect ARM-only constraints?
- Does it avoid imperative mutation?
- Does Home Manager remain declarative?
- Will CI fail loudly if broken?

If any answer is "no", rethink the change.

---

End of AGENTS.md
