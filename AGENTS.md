# AGENTS.md
## Build/Lint/Test
- Dev env: `nix develop` (rate limits: `export NIX_CONFIG="access-tokens = github.com=$GITHUB_TOKEN"`)
- Format (required): `nix fmt` (uses `nixfmt-rfc-style`)
- Pre-commit formatter: `nix run .#pre-commit` (stashes unstaged, runs `nix fmt`, restages)
- Build all checks: `nix flake check -L` (add `--no-build` if you’re not on the target system)
- Run single check/test: `nix build .#checks.$(nix eval --raw --impure --expr builtins.currentSystem).<checkName> -L`
- Build outputs: `nix build .#nixosConfigurations.<host>.config.system.build.toplevel -L` or `nix build .#homeConfigurations.hm@linux-x86.activationPackage -L`
- Apply system config: `NIXNAME=<host> make switch` (hosts in `systems/default.nix`); test: `NIXNAME=<host> make test`
- Apply Home Manager: `home-manager switch --flake .#hm@linux-x86`
## Code Style (Nix)
- Formatting: don’t hand-align; rely on `nix fmt`; keep diffs minimal.
- Imports: module args as `{ lib, pkgs, config, ... }:`; keep lists/attrsets sorted.
- Naming: locals `lowerCamelCase`; files/dirs `kebab-case`; match NixOS/HM option names.
- Modules: prefer `lib.mkIf`/`lib.mkMerge`/`lib.mkDefault`/`lib.optionals` over nested `if`s.
- Types: use `lib.mkOption` with explicit `type` + `default` when adding new options.
- Errors: prefer `assert`/`lib.assertMsg`/`throw` with actionable messages; avoid `builtins.trace`.
- Secrets: never commit plaintext; use sops-nix; keep encrypted values in `secrets/secrets.yaml`.
## Other Rules
- No `.cursor/rules`, `.cursorrules`, or `.github/copilot-instructions.md` in this repo.
