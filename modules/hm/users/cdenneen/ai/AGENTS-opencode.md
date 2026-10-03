# OpenCode Agent Guide

<!-- Shared contract — read this first -->

@include ~/.ai/AGENTS.md

---

## OpenCode-Specific

### Session update (OpenCode equivalent of cos-update)

OpenCode does not have the `cos-update` Hermes skill. Use the MCP equivalent:

1. `store_context(content=<update>, type="session", agent_id="opencode@<host>", project=<slug>)`
2. `write_handoff` at gates/blockers/completion.
3. At session end: print the HANDOFF resume prompt so the human or CoS can resume.

### Agent commands

See `~/.config/opencode/docs/agent-commands.md` for opencode-specific slash commands and shortcuts.
