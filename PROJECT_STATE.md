# Project State

## Current Goals

- Keep the `mbair` branch usable for the older Intel MacBook Air on macOS Big Sur.
- Preserve current repo updates while keeping the mbair host conservative and evaluation-safe.

## Current Status

- `mbair` branch was forward-ported to current `main` via merge.
- Added modern Darwin host `darwinConfigurations.mbair` using `hosts/darwin/mbair.nix`.
- `nix eval --impure .#darwinConfigurations.mbair.system` succeeds.
- Removed NUR wiring from the branch to reduce stale dependency surface.
- Added mbair age recipient documentation; mbair uses the existing `personal_current` age recipient.
- Configured mbair Homebrew to use `cdenneen/taps/tailscale-app@1.70.0` and disable auto-update/upgrade/cleanup.
- Kept mbair lean: GUI/dev profiles off, Podman off, Alacritty/Kitty enabled, Ghostty avoided.
- Codex is available through the system package set; OpenCode remains enabled via Home Manager.

## Current Blockers

- No current eval blocker.
- Big Sur runtime compatibility for the custom Tailscale cask is not fully verified on the machine.

## Known Risks

- Nixpkgs warns that `x86_64-darwin` support ends after 26.05.
- Modern package selections may still include binaries that do not run on Big Sur even if eval succeeds.
- Secrets require the private key for `age1txemjnq72tf7wx85a5klf8f72fgm3yll0pkpuuj79rwjfa4c8qtq9ww6u8` at `~/.config/sops/age/keys.txt` before first switch.

## Important Assumptions

- `mbair` is a dedicated branch for the older MacBook Air and does not need to stay mergeable back to `main`.
- The host name for the system is `mbair`.
