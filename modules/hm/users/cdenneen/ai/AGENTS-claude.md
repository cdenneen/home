# Claude Agent Guide

<!-- Shared contract — read this first (auto-loaded by Claude Code at session start) -->
<!-- Full content at ~/.ai/AGENTS.md -->

@include ~/.ai/AGENTS.md

---

## Claude-Specific

### Token budget

This file is injected every session. Project context belongs in `.ai/HANDOFF.md` loaded via startup routine, not here. GitLab pipeline contract is in the `gitlab-pipelines` skill — load on demand.

### MCP tool names on eros (use these exact names — don't rely on search)

The eros MCP aggregate (`eros.tail0e55.ts.net:4000/mcp/`) proxies multiple servers. Semantic tool search may not surface all tools — call these directly by name:

| Contract name    | Actual MCP tool              | Server              |
| ---------------- | ---------------------------- | ------------------- |
| `write_handoff`  | `write_handoff`              | agent-handoff       |
| `read_handoff`   | `read_handoff`               | agent-handoff       |
| `list_handoffs`  | `list_handoffs`              | agent-handoff       |
| `store_context`  | `store_context`              | eros-context-shared |
| `recall`         | `recall`                     | eros-context-shared |
| `search_context` | `search_context`             | eros-context-shared |
| `cos_update`     | `cos_update`                 | agent-handoff       |
| Kanban read      | `kanban_list`, `kanban_show` | kanban-shim         |

If tool search returns `recallium-*` or `graphify-*` — those are valid for episodic memory and graph queries, but `write_handoff`, `store_context`, and `cos_update` are the canonical contract tools. Call them directly: `mcp__eros__mcp_tool_call` with `tool_name=<name>`.

### Session update (Claude equivalent of cos-update)

Claude has the `cos_update` MCP tool via eros — use it directly, same as any other agent:

1. `cos_update(status="started", ...)` at session start — **blocks on CoS reply**, returns `task_id`. Store it.
2. `cos_update(status="update"|"blocked"|"review", ..., task_id=<from start>)` continuously during work.
3. `store_context(content=<fact>, type="decision"|"fact", ...)` for durable knowledge.
4. `write_handoff(...)` at gates/blockers/completion.
5. `cos_update(status="completed", ..., task_id=<from start>)` at session end.
6. Print: `HANDOFF: <id> — Resume: tell @nyxops to resume handoff <id>`

**`/activate-workflow`** — if you see this in any message, immediately run the full session-start routine (recall → list_handoffs → cos_update started → read HANDOFF.md) even if the session is already running.

### Human-in-the-middle handoff to Slack

When you need human review or CoS dispatch before continuing:

```
write_handoff(..., next_action="awaiting approval: <what>")
cos_update(status="review", message="<MR/issue link>. Waiting on: Chris.", task_id=<session task_id>)
Print: "HANDOFF: <id> — In Slack: tell @nyxops to resume handoff <id>"
```
