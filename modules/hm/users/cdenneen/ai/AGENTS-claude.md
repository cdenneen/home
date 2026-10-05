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
| Kanban read      | `kanban_list`, `kanban_show` | kanban-shim         |

If tool search returns `recallium-*` or `graphify-*` — those are valid for episodic memory and graph queries, but `write_handoff` and `store_context` are the canonical contract tools. Search for them specifically: `mcp__eros__mcp_tool_call` with tool_name `write_handoff`.

### Session update (Claude equivalent of cos-update)

Claude Code does not have the Hermes `cos-update` skill. Use the MCP equivalent:

1. `store_context(content=<update>, type="session", agent_id="claude@<host>", project=<slug>)`
2. `write_handoff` at gates/blockers/completion.
3. At session end: print `HANDOFF: <id> — Resume: tell @nyxops to resume handoff <id>`

### Human-in-the-middle handoff to Slack

When you need human review or CoS dispatch before continuing:

```
write_handoff(..., next_action="awaiting approval: <what>")
Print: "HANDOFF: <id> — In Slack: tell @nyxops to resume handoff <id>"
```
