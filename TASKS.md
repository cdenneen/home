# Tasks

## Completed

- [x] Create isolated `mbair` worktree.
- [x] Forward-port `mbair` branch to current `main` structure.
- [x] Add modern `darwinConfigurations.mbair` host config.
- [x] Remove NUR input/module/overlay wiring from the `mbair` branch.
- [x] Guard unsupported `happier` package access for `x86_64-darwin`.
- [x] Disable automatic Nix GC on mbair while `nix.enable = false` under Determinate Nix.
- [x] Configure mbair to use `cdenneen/taps/tailscale-app@1.70.0`.
- [x] Keep mbair on Alacritty/Kitty and avoid Ghostty.
- [x] Verify `nix eval --impure .#darwinConfigurations.mbair.system` succeeds.
- [x] Pin mbair release inputs to 25.05 and add Big Sur/HM compatibility shims.

## Active

- [ ] Run `darwin-rebuild switch --flake .#mbair` on the actual mbair machine.
- [ ] Verify Tailscale 1.70.0 works on Big Sur.
- [ ] Verify Codex/OpenCode run locally on mbair.

## Deferred

- [x] Decide whether to pin mbair to a narrower nixpkgs branch for long-term Big Sur compatibility.

## Blocked

- [ ] No current blocked task.
