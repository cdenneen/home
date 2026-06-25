# Architecture

## Branch Purpose

`mbair` is a dedicated branch of the Nix flake for an older Intel MacBook Air running macOS Big Sur.

## Host Wiring

- Host catalog: `hosts/default.nix`
- Host module: `hosts/darwin/mbair.nix`
- System output: `darwinConfigurations.mbair`
- System: `x86_64-darwin`

## Compatibility Strategy

- Use the modern repo module graph from `main`.
- Keep mbair host overrides conservative.
- Disable Podman/vfkit and GUI/dev-heavy defaults.
- Use Homebrew only for the legacy Tailscale cask from `cdenneen/taps`.
- Use the existing personal age recipient for secrets bootstrap.

## Key Integration Points

- SOPS key path on Darwin: `~/.config/sops/age/keys.txt`
- Legacy Tailscale cask: `cdenneen/taps/tailscale-app@1.70.0`
- Local AI tools: Codex system package and OpenCode Home Manager module.
