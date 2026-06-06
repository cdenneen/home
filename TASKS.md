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
- [x] Add stale-session deletion to the `nyx` OpenCode compaction flow and verify the historical session count drops.
- [x] Guard `restart-tmux` against reattaching OpenCode sessions whose stored directory belongs to another host root.
- [x] Stop persisting the literal OpenCode password in tmux snapshot commands.
- [x] Repair `nyx` `coding:8` by reattaching it to a nyx-native `k8s` session instead of the incompatible Mac-path session.
- [x] Reproduce the DuckDuckGo MCP `No valid session ID provided` failure against the shared `nyx` gateway.
- [x] Switch the shared DuckDuckGo gateway on `nyx` to stateless Streamable HTTP.
- [x] Verify DuckDuckGo MCP calls succeed from both `nyx` and the Mac even with a bogus stale session header.
- [x] Migrate Codex flake-managed profile config from legacy `profile`/`[profiles.*]` in `config.toml` to `~/.codex/<name>.config.toml` files.
- [x] Split Home Manager package set into core vs heavy groups and trim heavy remote-dev extras on `ghost`.

## Active

- [ ] Keep project memory files current as the next substantial work stream proceeds.
- [ ] Decide whether the other shared `nyx` MCP gateways should also move to stateless mode.
- [ ] Decide whether helpers besides `restart-tmux` should explicitly reject or remap foreign-host OpenCode session paths.
- [ ] Decide whether old standalone Playwright processes still visible on `nyx` should be cleaned up or can be ignored.

## Deferred

- [ ] Review whether additional repo-specific docs should link to the new project memory files.
- [ ] Decide whether cross-host OpenCode session portability deserves explicit documentation in user-facing workflow docs.
- [ ] Clean up deprecation warnings surfaced during `darwin-rebuild` and `nixos-rebuild`.

## Blocked

- [ ] No current blocked task.
