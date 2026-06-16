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
  - Hosts shared MCP gateways for remote tools, including the `recallium` API and the shared Playwright gateway on `127.0.0.1:18107`.
  - Runs `opencode-serve` on `127.0.0.1:4097` behind the OAuth proxy on `127.0.0.1:4096`.
- `ghost`
  - Personal NixOS control-plane/deployment host.
  - Runs local AI/data services via podman-backed systemd oci-containers on `127.0.0.1` with persistence under `/var/lib/<component>`.
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
- `nyx` shared MCP gateways exposed over localhost/Tailscale via `supergateway`
- `opencode` local attach/auth helpers that read the live password secret from `/run/secrets/opencode_server_password` on `nyx`
- `restart-tmux` snapshot/restore logic that detects when an OpenCode session was created with a foreign absolute workspace root and falls back to a host-native restore path
- shared `nyx` MCP gateways can choose stateful or stateless Streamable HTTP per service; DuckDuckGo now runs stateless on port `18105`
- Host-specific services such as Cloudflare, Tailscale, happier, and deployment helpers

## Design Constraints

- Prefer declarative fixes over transient host changes.
- Keep changes conservative and auditable.
- Host deployment workflow is commit/push/pull/switch.
- Secrets must not be committed in plaintext.
- Durable project context must live in repo files, not only in chat history.
- OpenCode session metadata stores absolute working directories, so session IDs are not safely portable across hosts that use different workspace roots (for example `/Users/cdenneen/code/workspace` vs `/home/cdenneen/src/workspace`).
- Stateful Streamable HTTP MCP gateways preserve per-client sessions, but stateless mode is a better fit for simple search-style tools that should survive stale or missing session IDs.
