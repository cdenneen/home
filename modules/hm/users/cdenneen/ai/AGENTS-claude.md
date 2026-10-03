# Claude Agent Guide

<!-- Shared contract — read this first -->

@include ~/.ai/AGENTS.md

---

## Claude-Specific

### Token budget awareness

- This file is injected at the start of every session. Keep project-specific context in `.ai/HANDOFF.md` and load it via the startup routine, not by expanding this file.
- GitLab pipeline/IaC contract is in the `gitlab-pipelines` skill — load it on demand, not every session.

### CLAUDE.md conventions

- This file serves as CLAUDE.md. Claude Code reads it automatically at session start.
- Project-level CLAUDE.md files in repos override or extend this global config.

### Session update (Claude equivalent of cos-update)

Claude Code does not have the `cos-update` Hermes skill. Use the MCP equivalent:

1. `store_context(content=<update>, type="session", agent_id="claude@<host>", project=<slug>)`
2. `write_handoff` at gates/blockers/completion.
3. At session end: print the HANDOFF resume prompt so the human or CoS can resume.

### Human-in-the-middle handoff to Slack/Hermes

When you need human review or CoS dispatch:

```
write_handoff(..., next_action="awaiting approval: <what>")
Print: "HANDOFF: <id> — In Slack: tell @nyxops to resume handoff <id>"
```
