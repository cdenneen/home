# Handoff

## Summary

The `mbair` branch has been forward-ported from an old unrelated fork to the current repo structure. It now defines `darwinConfigurations.mbair` for an Intel MacBook Air on Big Sur.

## Completed

- Created `../home-mbair` worktree on branch `mbair`.
- Merged current `main` into the old `mbair` history.
- Added `hosts/darwin/mbair.nix`.
- Added `mbair` to `hosts/default.nix`.
- Removed NUR flake/module/overlay wiring from this branch.
- Guarded missing `happier` package access on `x86_64-darwin`.
- Disabled automatic Nix GC for mbair because nix-darwin has `nix.enable = false` under Determinate Nix.
- Configured Homebrew tap/cask for `cdenneen/taps/tailscale-app@1.70.0`.
- Documented that mbair uses the existing personal age recipient.

## Current Status

- `nix eval --impure .#darwinConfigurations.mbair.system` succeeds.
- Branch pushed to `origin/mbair`.

## Open Issues

- Runtime Big Sur compatibility still needs verification on mbair.
- Tailscale 1.70.0 cask is known not 100% verified.

## Exact Next Action

On mbair, restore the age private key at `~/.config/sops/age/keys.txt`, pull branch `mbair`, then run:

```sh
darwin-rebuild switch --flake .#mbair
```
