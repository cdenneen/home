# Decisions

## 2026-06-25 - Forward-port mbair instead of reviving old module graph

- Context: `origin/mbair` was an unrelated, old fork with legacy `systems/` and `modules/home` layouts.
- Decision: Merge current `main` into `mbair` with `--allow-unrelated-histories`, resolve conflicts to the modern structure, and add a minimal mbair host.
- Rationale: Chasing old input/module failures would be more fragile than using the maintained flake structure.
- Consequences: The branch is now a dedicated modern branch for mbair and includes a large merge commit.

## 2026-06-25 - Use existing personal age recipient for mbair

- Context: The provided mbair public age key is `age1txemjnq72tf7wx85a5klf8f72fgm3yll0pkpuuj79rwjfa4c8qtq9ww6u8`, already present as `personal_current`.
- Decision: Document mbair as using the existing personal recipient rather than adding a duplicate SOPS recipient.
- Rationale: Duplicate recipients are unnecessary; the important step is installing the matching private key on mbair.
- Consequences: Secrets should decrypt if the private key exists at `~/.config/sops/age/keys.txt`.

## 2026-06-25 - Keep mbair conservative for Big Sur

- Context: mbair is an older Intel MacBook Air capped at macOS Big Sur.
- Decision: Disable GUI/dev-heavy profiles, Podman, Homebrew auto-upgrades, and Ghostty; keep Alacritty/Kitty and local Codex/OpenCode.
- Rationale: Reduce incompatibility risk from modern packages and casks.
- Consequences: Additional packages can be re-enabled later after runtime verification.
