# Architecture

## System Architecture

This repository is a Nix flake monorepo that defines:

- NixOS host configurations
- nix-darwin host configurations
- Home Manager user configurations
- shared packages, scripts, and generated user files

The flake is the operational source of truth for personal infrastructure and developer tooling.

## Key Components

- `flake.nix`
  - Declares stable `nixpkgs` for system builds and `nixpkgs-unstable` for selected user-space tooling.
- `systems/`
  - Wires flake outputs for NixOS, darwin, and Home Manager.
- `hosts/`
  - Host-specific system configuration:
    - `hosts/nixos/nyx.nix`
    - `hosts/nixos/ghost.nix`
    - `hosts/nixos/ghost-bootstrap.nix`
    - `hosts/darwin/VNJTECMBCD.nix`
- `modules/`
  - Shared system and Home Manager modules.
- `modules/hm/users/cdenneen/ai/AGENTS.md`
  - Shared global instruction file installed to Codex and OpenCode locations.
- `pkgs/`
  - Custom packages such as the pinned `codex` and `opencode` CLI derivations.
- `secrets/`
  - SOPS-encrypted secrets and recipient configuration.

## Host Model

- `VNJTECMBCD`
  - Primary local Mac using nix-darwin plus Home Manager.
- `nyx`
  - Primary NixOS work/execution host.
- `ghost`
  - Personal NixOS control-plane/deployment host.
- Additional image/bootstrap/utility hosts exist for EC2, WSL, UTM, and bootstrapping.

## Data Flow

1. Edit flake repo locally.
2. Commit and push changes.
3. Pull the repo on the target host.
4. Apply with:
   - `sudo nixos-rebuild switch --flake .#<host>` on NixOS
   - `sudo darwin-rebuild switch --flake .#<host>` on macOS
   - `home-manager switch --flake .#cdenneen@<host>` when only HM scope is needed
5. Verify services, generated files, and connectivity.

## Major Dependencies

- `nixpkgs` / `nixpkgs-unstable`
- `home-manager`
- `nix-darwin`
- `sops-nix`
- `disko`
- `treefmt-nix`
- selected external flakes for platform-specific or workflow-specific modules

## Integration Points

- SOPS + age for secret materialization
- GitHub Actions for flake input and custom CLI update automation
- Home Manager for user-space file generation including:
  - `~/.codex/AGENTS.md`
  - `~/.config/opencode/AGENTS.md`
  - shell config, SSH config, app helpers, and agent tooling
- Host-specific services such as Jarvis, Cloudflare, Tailscale, happier, and deployment helpers

## Design Constraints

- Prefer declarative fixes over transient host changes.
- Keep changes conservative and auditable.
- Host deployment workflow is commit/push/pull/switch.
- Secrets must not be committed in plaintext.
- Durable project context must live in repo files, not only in chat history.
