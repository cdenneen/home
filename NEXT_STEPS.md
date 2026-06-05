# Next Steps

## Immediate Next Task

- If `nyx` `opencode` feels stuck again, inspect session count and Playwright MCP child growth before restarting it.

## Ordered Task List

1. Inspect `nyx` `opencode-serve.service` task count, memory, and live session count if it appears sluggish again.
2. Decide whether session compaction behavior or Playwright MCP spawning needs another flake change.
3. Tackle the next user-requested host or workflow task.
4. Keep project memory files current as the next substantial task proceeds.

## Dependencies

- Local Mac must have access to the intended SSH private keys.
- Remote hosts must remain reachable over current network paths.
- The repo must stay on `main` or another known branch during SSH-config fixes.

## Validation Steps

- `git log --show-signature -1 --format=fuller`
- `ssh nyx 'systemctl --user status opencode-serve.service --no-pager -n 20'`
- `ssh nyx 'curl -fsS -u "opencode:$(tr -d "\n\r" </run/secrets/opencode_server_password)" http://127.0.0.1:4097/session | jq -r "length"'`
- `ssh nyx 'systemctl --user show opencode-serve-compact.service -p ActiveState -p Result -p ExecMainStatus --no-pager'`
- `ssh nyx 'rg -n "Persistent Project Memory Requirements" ~/.codex/AGENTS.md ~/.config/opencode/AGENTS.md'`
- `ssh ghost 'rg -n "Persistent Project Memory Requirements" ~/.codex/AGENTS.md ~/.config/opencode/AGENTS.md'`

## Recommended Next Session Starting Point

- Read `AGENTS.md`, then `HANDOFF.md`, `PROJECT_STATE.md`, `DECISIONS.md`, and this file.
- Start by checking whether `nyx` `opencode` still feels stuck to the user; if so, use the validation commands above before restarting anything.
