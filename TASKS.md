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
- [x] Move `nyx` Playwright MCP off the `opencode-serve` process tree into a shared `nyx-mcp-playwright` gateway.
- [x] Export `OPENCODE_SERVER_PASSWORD` in fresh `nyx` login shells so direct local `opencode attach` works again.
- [x] Patch `opencode-attach-latest` and `restart-tmux` to pass auth to the protected local OpenCode server.
- [x] Re-verify that direct `opencode attach http://127.0.0.1:4097 ...` on `nyx` no longer returns `401`.

## Active

- [ ] Keep project memory files current as the next substantial work stream proceeds.
- [ ] Decide whether `nyx` needs true stale-session deletion in addition to bounded compaction.
- [ ] Decide whether old standalone Playwright processes still visible on `nyx` should be cleaned up or can be ignored.

## Deferred

- [ ] Review whether additional repo-specific docs should link to the new project memory files.
- [ ] Clean up deprecation warnings surfaced during `darwin-rebuild` and `nixos-rebuild`.

## Blocked

- [ ] No current blocked task.
