# Claude Agent Guide

<!-- Shared contract — read this first (auto-loaded by Claude Code at session start) -->
<!-- Full content at ~/.ai/AGENTS.md -->

@include ~/.ai/AGENTS.md

---

## Claude-Specific

### Token budget

This file is injected every session. Project context belongs in `.ai/HANDOFF.md` loaded via startup routine, not here. GitLab pipeline contract is in the `gitlab-pipelines` skill — load on demand.

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
