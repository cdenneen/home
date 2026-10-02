---
name: agent-handoff
description: >
  Use when starting, pausing, or ending any substantial work session.
  Proactively writes and reads session handoffs via eros MCP so context
  survives across agents, interfaces, and hosts without the user having to ask.
---

# Agent Handoff

Durable session context that follows you across Claude, Codex, pi, Claude
Desktop, ChatGPT, and Slack. Backed by `cdenneen/agent-context` on GitHub
via the `agent-handoff` MCP server on eros.

---

## When to act — do these without being asked

### Session start

1. Call `list_handoffs` with the current `project` and/or `workspace` values.
2. If results exist, call `read_handoff` on the most recent relevant one.
3. Summarize what you found in one paragraph, then immediately continue the
   work — do not wait for the user to confirm you read it.
4. If nothing exists, check `list_context` for any standing architecture or
   decisions docs for this project.

### During a session

- **Before a decision gate or blocker** — call `write_handoff` with the
  current state so the user can hand off to a different interface if needed.
- **At context pressure (>50% window)** — call `write_handoff` with a compact
  snapshot before continuing. Include the full current state in `content`.
- **When spawning subagents or workers** — write a handoff first so the
  subagent can read it rather than re-deriving context from scratch.
- **On any non-trivial decision** — call `write_context` to record it durably
  under the project name. Decisions in `write_context` survive session end;
  decisions only in the conversation do not.

### Session end

Always call `write_handoff` before ending a session that did real work.
Include in `content`:

- What was completed (with MR/issue links)
- What is in-flight and its exact state
- What is blocked and who owns the gate
- Any local files that only exist on this machine
- The exact command or step to resume

---

## Tool reference

### `write_handoff`

Write a handoff and get back a `handoff_id` and `resume_prompt`.

Required fields:

- `topic` — short name for this work, e.g. `"cadbury tenant migration"`
- `content` — the full state dump (markdown, no length limit)

Recommended fields:

- `project` — gitlab project slug or github repo, e.g. `"eks-platform"`
- `workspace` — local workspace path, e.g. `"~/src/workspace/eks"`
- `agent` — which agent is writing this, e.g. `"claude-code"`, `"pi"`, `"codex"`
- `status` — one-line current state
- `next_action` — the exact next step
- `blockers` — what is blocked and who owns it
- `tags` — comma-separated, e.g. `"cadbury,eks,prd-use1-400"`

Returns `handoff_id`, `resume_prompt`, and `resume_commands` for each CLI.

### `read_handoff(handoff_id)`

Read a specific handoff by ID prefix. Use at session start when resuming.

### `list_handoffs(project, workspace, tags, limit)`

List recent handoffs filtered by project/workspace/tags. Call at session start.

### `search_handoffs(query, project)`

Full-text search. Use when you need prior context but don't have an ID.

### `get_resume_prompt(handoff_id)`

Generate a formatted prompt + CLI commands for resuming in another interface.
Returns:

- `prompt` — paste into any agent session to restore context
- `commands` — ready-to-run `claude`, `codex`, `pi` commands using `agent-resume`
- `slack_block` — formatted message for posting to Slack `/handoff`

### `write_context(name, content, kind)`

Write a named shared doc (architecture, decisions, plan, notes, reference).
Persists across all sessions. Use for standing knowledge, not ephemeral state.

### `read_context(name)` / `list_context()`

Read or list shared context documents.

---

## /handoff in Slack

When working in Slack and the user types `/handoff` or asks to continue in a
terminal:

1. Call `write_handoff` with the current thread topic and state.
2. Call `get_resume_prompt` with the returned `handoff_id`.
3. Post the `slack_block` from the response to the thread.

The user then runs one of the printed commands in their terminal and the
new agent session starts with full context. No copy-pasting, no re-explaining.

---

## What goes in `write_handoff` vs `write_context`

| Use `write_handoff` for           | Use `write_context` for                |
| --------------------------------- | -------------------------------------- |
| Current session state snapshot    | Architecture decisions                 |
| Exactly what is blocked right now | Standing project conventions           |
| In-flight MR/issue links          | Long-lived plans and roadmaps          |
| Local-only files that matter      | Cross-session reference docs           |
| "Resume here" instructions        | Decisions that should outlive sessions |

---

## Pitfalls

- Do not write secrets, tokens, or raw credential values into handoffs.
- Do not replace `.ai/HANDOFF.md` — write both; the local `.ai/` files serve
  offline/air-gapped scenarios; the eros handoff serves cross-host/cross-agent.
- Do not use `write_handoff` for trivial read-only sessions — only sessions
  that modified state, made decisions, or hit a real blocker warrant a handoff.
- The `agent-resume <id>` shell alias on nyx and Mac fetches the prompt and
  prints it. Users pipe it directly to their agent: `claude "$(agent-resume id)"`.
