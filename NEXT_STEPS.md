# Next Steps

## Immediate Next Task

- Fix the generated SSH identity paths so `ssh nyx` and `ssh ghost` use valid materialized key locations again.

## Ordered Task List

1. Inspect the SSH config generation in `modules/hm/users/cdenneen/programs.nix` and related SSH modules.
2. Change host entries to use the actual materialized key paths under `~/.ssh/*` or another stable declarative location.
3. Apply the updated flake on the Mac with `sudo darwin-rebuild switch --flake .#VNJTECMBCD`.
4. Re-test `ssh nyx` and `ssh ghost`.
5. Verify `~/.codex/AGENTS.md` and `~/.config/opencode/AGENTS.md` on `nyx` and `ghost` contain the persistent memory section.
6. Keep these memory files current as the next substantial task proceeds.

## Dependencies

- Local Mac must have access to the intended SSH private keys.
- Remote hosts must remain reachable over current network paths.
- The repo must stay on `main` or another known branch during SSH-config fixes.

## Validation Steps

- `ssh -G nyx | rg IdentityFile`
- `ssh -G ghost | rg IdentityFile`
- `ssh nyx true`
- `ssh ghost true`
- `ssh nyx 'rg -n "Persistent Project Memory Requirements" ~/.codex/AGENTS.md ~/.config/opencode/AGENTS.md'`
- `ssh ghost 'rg -n "Persistent Project Memory Requirements" ~/.codex/AGENTS.md ~/.config/opencode/AGENTS.md'`

## Recommended Next Session Starting Point

- Read `AGENTS.md`, then `HANDOFF.md`, `PROJECT_STATE.md`, `DECISIONS.md`, and this file.
- Start with the SSH identity path drift described in `PROJECT_STATE.md` and `HANDOFF.md`.
