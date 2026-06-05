# Next Steps

## Immediate Next Task

- If a resumed `nyx` OpenCode pane loops on permissions again, check whether the session ID was created from a foreign host path before changing the server.

## Ordered Task List

1. Inspect `nyx` `opencode-serve.service`, `nyx-mcp-playwright.service`, and live session count if responsiveness regresses again.
2. Decide whether shared MCP gateways besides DuckDuckGo should also switch to stateless Streamable HTTP.
3. Decide whether helpers besides `restart-tmux` should explicitly reject or remap foreign-host OpenCode session paths.
4. Decide whether the remaining old standalone Playwright processes on `nyx` are harmless or need explicit cleanup.
5. Tackle the next user-requested host or workflow task.
6. Keep project memory files current as the next substantial task proceeds.

## Dependencies

- Local Mac must have access to the intended SSH private keys.
- Remote hosts must remain reachable over current network paths.
- The repo must stay on `main` or another known branch during SSH-config fixes.

## Validation Steps

- `git log --show-signature -1 --format=fuller`
- `ssh nyx 'systemctl --user status nyx-mcp-playwright.service --no-pager -n 20'`
- `ssh nyx 'systemctl --user status opencode-serve.service --no-pager -n 20'`
- `ssh nyx 'curl -fsS -u "opencode:$(tr -d "\n\r" </run/secrets/opencode_server_password)" http://127.0.0.1:4097/session | jq -r "length"'`
- `ssh nyx 'curl -fsS http://127.0.0.1:18107/healthz'`
- `ssh nyx 'systemctl --user show opencode-serve-compact.service -p ActiveState -p Result -p ExecMainStatus --no-pager'`
- `ssh nyx 'zsh -lic '\''sid=$(curl -fsS -u "opencode:$OPENCODE_SERVER_PASSWORD" http://127.0.0.1:4097/session | jq -r ".[0].id"); dir=$(curl -fsS -u "opencode:$OPENCODE_SERVER_PASSWORD" http://127.0.0.1:4097/session | jq -r ".[0].directory"); timeout 5 opencode attach http://127.0.0.1:4097 --session "$sid" --dir "$dir" >/tmp/opencode-attach.log 2>&1 || true; ! rg -n "401 Unauthorized|401" /tmp/opencode-attach.log'\'''
- `ssh nyx '~/.local/bin/restart-tmux coding --snapshot >/tmp/coding.snapshot && snapshot=$(cat /tmp/coding.snapshot); awk -F "\t" '\''$1==8 {print $4}'\'' "$snapshot"'`
- `ssh nyx 'tmux capture-pane -pt coding:8 -S -40 | tail -n 40'`
- `python3 - <<'\''PY'\'' ... POST http://nyx.tail0e55.ts.net:18105/mcp with a bogus mcp-session-id and confirm DuckDuckGo still returns results ... PY`
- `ssh nyx 'rg -n "Persistent Project Memory Requirements" ~/.codex/AGENTS.md ~/.config/opencode/AGENTS.md'`
- `ssh ghost 'rg -n "Persistent Project Memory Requirements" ~/.codex/AGENTS.md ~/.config/opencode/AGENTS.md'`

## Recommended Next Session Starting Point

- Read `AGENTS.md`, then `HANDOFF.md`, `PROJECT_STATE.md`, `DECISIONS.md`, and this file.
- Start by checking `nyx` session count, the shared Playwright gateway, and whether the affected session was created from the same host path before changing anything else in the opencode stack.
