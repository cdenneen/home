# Next Steps

## Immediate Next Task

- Create a signed commit for the verified SSH/git-signing path fix, push it, and apply it on `nyx` and `ghost`.

## Ordered Task List

1. Stage and sign-commit the verified fixes in:
   - `modules/hm/users/cdenneen/secrets.nix`
   - `modules/hm/users/cdenneen/git.nix`
   - `modules/hm/users/cdenneen/programs.nix`
2. Push the signing-path fix to `main`.
3. Pull and apply the updated flake on `nyx`.
4. Pull and apply the updated flake on `ghost`.
5. Re-verify `ssh nyx`, `ssh ghost`, and signed temp commits on both hosts after the new generation is live.
6. Keep project memory files current as the next substantial task proceeds.

## Dependencies

- Local Mac must have access to the intended SSH private keys.
- Remote hosts must remain reachable over current network paths.
- The repo must stay on `main` or another known branch during SSH-config fixes.

## Validation Steps

- `readlink ~/.ssh/github_ed25519`
- `readlink ~/.ssh/cdenneen_ed25519_2024`
- `ssh -G nyx | rg IdentityFile`
- `ssh -G ghost | rg IdentityFile`
- `git config --show-origin --get user.signingkey`
- `ssh nyx true`
- `ssh ghost true`
- `git commit --allow-empty -m "signing-path-check"`
- `ssh nyx 'rg -n "Persistent Project Memory Requirements" ~/.codex/AGENTS.md ~/.config/opencode/AGENTS.md'`
- `ssh ghost 'rg -n "Persistent Project Memory Requirements" ~/.codex/AGENTS.md ~/.config/opencode/AGENTS.md'`
- `ssh nyx 'tmpdir=$(mktemp -d /tmp/git-sign-check.XXXXXX) && cd "$tmpdir" && git init -q && git config user.name "Chris Denneen" && git config user.email "cdenneen@gmail.com" && printf "ok\n" > README && git add README && git commit -m "signing-path-check" >/dev/null && git log --show-signature -1 --format=fuller | sed -n "1,8p"'`
- `ssh ghost 'tmpdir=$(mktemp -d /tmp/git-sign-check.XXXXXX) && cd "$tmpdir" && git init -q && git config user.name "Chris Denneen" && git config user.email "cdenneen@gmail.com" && printf "ok\n" > README && git add README && git commit -m "signing-path-check" >/dev/null && git log --show-signature -1 --format=fuller | sed -n "1,8p"'`

## Recommended Next Session Starting Point

- Read `AGENTS.md`, then `HANDOFF.md`, `PROJECT_STATE.md`, `DECISIONS.md`, and this file.
- Start with committing and propagating the already-verified signing-path fix described in `PROJECT_STATE.md` and `HANDOFF.md`.
