# Tasks

## Completed

- [x] Add persistent project memory requirements to the shared Codex/OpenCode `AGENTS.md`.
- [x] Add repo-root memory files required by the new global agent policy.
- [x] Automate custom pinned `codex` and `opencode` package update PRs.
- [x] Merge the current `codex` and `opencode` package update PRs after successful CI.
- [x] Apply the latest flake generation on Mac, `nyx`, and `ghost`.
- [x] Fix the Mac-side SSH alias and git signing path drift and verify a good signed commit locally.
- [x] Verify good signed commits on `nyx` and `ghost`.
- [x] Commit and push the verified SSH/git-signing path fix.
- [x] Apply the signing-path fix on `nyx` and `ghost`.
- [x] Re-verify shared Codex/OpenCode `AGENTS.md` content on `nyx` and `ghost` after the new generation went live.
- [x] Fix `nyx` opencode password wiring and direct session-compaction API routing.
- [x] Add bounded timeouts to `nyx` opencode compaction calls so the one-shot unit returns.
- [x] Re-verify `nyx` opencode auth and direct API health against the live `/run/secrets/opencode_server_password`.

## Active

- [ ] Keep project memory files current as the next substantial work stream proceeds.
- [ ] Revisit whether `nyx` opencode should prune or limit Playwright MCP child processes more aggressively.

## Deferred

- [ ] Review whether additional repo-specific docs should link to the new project memory files.
- [ ] Clean up deprecation warnings surfaced during `darwin-rebuild` and `nixos-rebuild`.

## Blocked

- [ ] No current blocked task.
