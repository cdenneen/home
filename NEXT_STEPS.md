# Next Steps

1. On mbair, install/restore the private age key matching `age1txemjnq72tf7wx85a5klf8f72fgm3yll0pkpuuj79rwjfa4c8qtq9ww6u8` at `~/.config/sops/age/keys.txt`.
2. On mbair, pull the `mbair` branch and run `darwin-rebuild switch --flake .#mbair`.
3. Verify `tailscale-app@1.70.0` from `cdenneen/taps` installs and starts on Big Sur.
4. Verify `codex` and `opencode` run locally on mbair.
5. If Big Sur runtime issues appear, trim more packages from `hosts/darwin/mbair.nix` or host-specific Home Manager overrides.

## Validation Commands

```sh
nix eval --impure .#darwinConfigurations.mbair.system
darwin-rebuild switch --flake .#mbair
```
