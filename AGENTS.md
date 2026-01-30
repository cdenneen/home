# AGENTS.md
## Build/Lint/Test
- Dev env: `nix develop`
- Format (required): `nix fmt` (uses `nixfmt-rfc-style`)
- Pre-commit formatter: `nix run .#pre-commit` (stashes unstaged, runs `nix fmt`, restages)
- GitHub rate limits: `export NIX_CONFIG="access-tokens = github.com=$GITHUB_TOKEN"` before `nix ...`
- Build all checks: `nix flake check -L` (add `--no-build` if you’re not on the target system)
- Run one check (example): `nix build .#checks.$(nix eval --raw --impure --expr builtins.currentSystem).nixosConfigurations-wsl -L`
- Build output (examples): `nix build .#nixosConfigurations.wsl.config.system.build.toplevel -L` or `nix build .#homeConfigurations.hm@linux-x86.activationPackage -L`
- Apply system config: `NIXNAME=wsl make switch` (host names in `systems/default.nix`)
- Apply Home Manager: `home-manager switch --flake .#hm@linux-x86`
- Test switch: `NIXNAME=wsl make test`
## Code Style (Nix)
- Formatting: don’t hand-align; rely on `nix fmt` and keep diffs minimal.
- Imports: group module args consistently (`{ lib, pkgs, config, ... }:`), keep lists sorted.
- Naming: `lowerCamelCase` for locals; files/dirs in `kebab-case`; match NixOS/HM option names.
- Modules: prefer `lib.mkIf`/`lib.mkMerge`/`lib.mkDefault`/`lib.optionals` over nested `if`s.
- Types: use `lib.mkOption` with explicit `type` + `default` when adding new options.
- Errors: prefer `assert` / `lib.assertMsg` / `throw` with actionable messages; avoid `builtins.trace`.
- Secrets: never commit plaintext; use sops-nix and keep secrets in `secrets/secrets.yaml`.
## Other Rules
- No `.cursor/rules`, `.cursorrules`, or `.github/copilot-instructions.md` found.
