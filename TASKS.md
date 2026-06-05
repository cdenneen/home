# Tasks

## Completed

- [x] Add persistent project memory requirements to the shared Codex/OpenCode `AGENTS.md`.
- [x] Add repo-root memory files required by the new global agent policy.
- [x] Automate custom pinned `codex` and `opencode` package update PRs.
- [x] Merge the current `codex` and `opencode` package update PRs after successful CI.
- [x] Apply the latest flake generation on Mac, `nyx`, and `ghost`.

## Active

- [ ] Fix SSH alias identity paths so `nyx` and `ghost` use valid local key locations after `darwin-rebuild`.
- [ ] Re-verify shared Codex/OpenCode `AGENTS.md` content on `nyx` and `ghost`.
- [ ] Keep project memory files current as the next substantial work stream proceeds.

## Deferred

- [ ] Review whether additional repo-specific docs should link to the new project memory files.
- [ ] Clean up deprecation warnings surfaced during `darwin-rebuild` and `nixos-rebuild`.

## Blocked

- [ ] Remote verification through `ssh nyx` and `ssh ghost` is blocked until the generated SSH identity paths are corrected.
