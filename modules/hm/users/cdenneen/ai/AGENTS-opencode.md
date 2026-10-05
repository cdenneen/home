# OpenCode Agent Guide

<!-- Shared contract — read this first -->

@include ~/.ai/AGENTS.md

---

## OpenCode-Specific

### Session update

1. `store_context(content=<update>, type="session", agent_id="opencode@<host>", project=<slug>)`
2. `write_handoff` at gates/blockers/completion.
3. At session end: print `HANDOFF: <id> — Resume: tell @nyxops to resume handoff <id>`

### Agent commands

See `~/.config/opencode/docs/agent-commands.md` for opencode-specific slash commands.
