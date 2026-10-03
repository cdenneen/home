# Agent Workflow Contract

**Version:** 1.0  
**Status:** NORMATIVE — all agents must implement this  
**Author:** ops@nyx + Chris Denneen  
**Date:** 2026-10-03  
**Kanban:** enforced by CoS, violations are drift

---

## Purpose

This document is the authoritative contract for how every agent in the fleet —
Hermes profiles, CoS, coding CLIs (Codex, Claude, pi/Paul, OpenCode), and
human-in-the-middle interfaces — handles context, memory, task continuity, and
handoffs. No agent may deviate from this contract. Drift from this spec is a
bug, not a style choice.

---

## The Problem This Solves

Without this contract:

- Agents re-derive known facts via LLM calls (wastes tokens, costs money)
- Sessions start cold — agent has no awareness of prior work
- Work gets lost at session boundaries
- Human has to re-explain context that was already captured
- CoS has no continuous awareness of what workers are doing
- Coding CLIs (Claude, Codex, pi) operate in isolation with no shared context
- Memory budget fills up and blocks new facts from being recorded

---

## Memory Hierarchy

Every agent has access to all layers. The rule is: **cheapest read first**.

```
1. Redis (eros:6379)         — hot cache, <5ms, no LLM call
                               TTL: session=4h, task=7d, fact=7d, decision=30d
2. MEMORY.md / USER.md       — session bootstrap hint, injected automatically
                               2200/1375 char budget — survival hints ONLY
3. Qdrant (eros:6333)        — semantic/vector search via context broker
                               collections: shared_memory, shared_knowledge
4. FalkorDB (eros:6380)      — knowledge graph, relationships, decisions
                               Agent/Task/Decision/File/Repo/Issue nodes
5. agent-context repo        — durable handoffs, git-backed, human-readable
                               eros:/var/lib/agent-handoff/agent-context/
6. Recallium (nyx:18001)     — episodic session replay, long-term memory
                               MCP-accessible via eros aggregate
```

**MEMORY.md is not the memory system.** It is a 2200-char bootstrap hint.
All durable facts belong in the shared stores (layers 1-6).

---

## Session Lifecycle (MANDATORY for all agents)

### On EVERY session start — before any other action

```
1. recall(query="active context <current project/task>")
   → hits Redis first (hot cache), falls back to Qdrant+FalkorDB
   → load top results into working context

2. list_handoffs(project=<current>, limit=5)
   → load most recent handoffs for this project/workspace
   → if a handoff_id is in the task instructions, read_handoff(id) first

3. Check Ghost Kanban for assigned/in-progress tasks
   → hermes --peer ghost kanban list (Hermes profiles)
   → hermes kanban list (CoS on ghost)
   → coding CLIs: read HANDOFF.md / .ai/ files in repo

4. THEN begin work — never ask the human to re-explain context
   that exists in the above sources
```

### During work — continuous cos-update (NOT just end-of-session)

Trigger `cos-update` (Hermes) or equivalent at:

- Every significant decision ("I'm going to approach this by...")
- Every tool/file/resource discovery ("The relevant file is X")
- Every blocker ("Can't proceed without Y")
- Every completion of a discrete unit ("Implemented X, tests pass")
- Context pressure (>50% context window used)
- Before any destructive or production-touching operation
- When switching approach or direction

The goal: CoS can reconstruct full session state from cos-update history
alone, without reading the conversation transcript.

### On session end / handoff

```
1. write_handoff(topic, content, project, workspace, next_action)
   → full state: what was done, exact next step, blockers, file paths, links

2. store_context(<key facts discovered this session>, type="fact")
   → fans out to Redis+Qdrant+FalkorDB+agent-context

3. cos-update completed "<summary with links, what remains, next action>"

4. Update .ai/HANDOFF.md in the repo (coding CLIs)
   → another agent must be able to resume from this file alone
```

---

## cos-update Contract (Hermes agents — nyx and ghost workers)

The `cos-update` skill calls `hermes peer dm ghost/chief-of-staff`.

**Trigger points (non-negotiable):**

| Event            | Message format                                                          |
| ---------------- | ----------------------------------------------------------------------- |
| Session start    | `[started] Working on: <topic>. Context loaded from: <sources>.`        |
| Decision made    | `[update] Decision: <what and why>. Impact: <files/tasks affected>.`    |
| Discovery        | `[update] Found: <what>. Relevant to: <task/project>.`                  |
| Blocked          | `[blocked] Gate: <exact blocker>. Owner: <who>. Parallel work: <what>.` |
| Unit complete    | `[update] Done: <what>. Evidence: <link/file>. Next: <step>.`           |
| Context pressure | `[update] Context at ~N%. State snapshot: <summary>.`                   |
| Review needed    | `[review] <PR/MR link>. Waiting on: <who>.`                             |
| Session end      | `[completed] <done, links, remains, next action>.`                      |

**Format rule:** One paragraph, factual, no padding. Always include links when
available (GitLab full path, GitHub PR, file paths). CoS uses this to update
the Kanban — make it machine-parseable.

---

## Coding CLI Contract (Claude, Codex, pi/Paul, OpenCode)

These agents do not have `cos-update` natively. They use the equivalent:

### Session start

```
1. Read AGENTS.md (this file or equivalent)
2. Read .ai/HANDOFF.md (or HANDOFF.md at repo root)
3. Read .ai/PROJECT_STATE.md, .ai/NEXT_STEPS.md
4. Call recall MCP tool via eros aggregate:
   recall(query="<current task>", project="<slug>")
5. Call list_handoffs via agent-handoff MCP:
   list_handoffs(project="<slug>", limit=5)
6. Summarize loaded context in one line, then begin work
   DO NOT ask "what should I work on?" if context answers it
```

### During work

- Update .ai/TASKS.md, .ai/PROJECT_STATE.md continuously
- Call store_context for every significant discovery or decision
- Call write_handoff at every gate, blocker, or decision point
- If a supervisor/CoS bridge is available: contact_supervisor on blockers
  requiring human approval, not silently stopping

### Session end / handoff back to CoS or human

```
1. Update all .ai/ files (TASKS, PROJECT_STATE, DECISIONS, NEXT_STEPS)
2. Regenerate .ai/HANDOFF.md — must be self-contained for cold resume
3. write_handoff with full state + handoff_id for CoS to pick up
4. If in an active CoS-dispatched task:
   - Call kanban_complete or equivalent with result summary
   - Do NOT just stop — explicitly close the dispatch loop
5. Print resume prompt:
   "To resume: load handoff <id> via agent-handoff MCP, or paste .ai/HANDOFF.md"
```

### The human-in-the-middle handoff pattern

When a coding CLI session needs human review or approval before continuing:

```
Agent:
  write_handoff(topic="<task>", content="<state>", next_action="awaiting approval: <what>")
  Print: "HANDOFF: <handoff_id> — awaiting your review of <X>.
          Resume in Slack: tell @nyxops to resume handoff <handoff_id>
          Resume in CLI: hermes peer run nyx/coder --resume <handoff_id>"

Human (in Slack):
  @nyxops resume handoff <handoff_id>
  → CoS reads handoff, dispatches worker with context pre-loaded

Human (in CLI):
  /handoff <id>  (future slash command, or paste HANDOFF.md as first prompt)
```

---

## CoS Contract (chief-of-staff@ghost)

### Session start

```
1. list_handoffs(limit=10) — no filter, get all recent unresolved handoffs
2. hermes kanban list — read all work boards (work-ops, work-eks-platform, work-gitlab)
3. recall(query="active work context") — load hot cache
4. Identify: what's in-progress, what's blocked, what needs dispatch
5. Do NOT start dispatching until context is fully loaded
```

### On receipt of cos-update from a worker

```
1. Parse: [profile@host] [event] <message>
2. Update Kanban task status (started→in_progress, blocked→blocked, etc.)
3. store_context(content=message, type="session", agent_id=caller)
   → indexes the update for future recall
4. write_handoff capturing new state so next dispatch has full context
5. If event=blocked AND blocked>4h: escalate to Chris via Slack
6. If event=completed: verify evidence (gh pr view, git log) before closing task
```

### Dispatch protocol

```
1. recall(query="<task context>", project="<slug>") — load prior context
2. list_handoffs(project="<slug>") — find most recent handoff
3. write_handoff(topic, content, project, next_action) — capture dispatch state
4. hermes-peer-dispatch start <host>/<role> \
     --idempotency-key <stable-task-key> \
     --board <board> \
     --task <task-id> \
     "Context: handoff_id=<id>. Load it first via agent-handoff MCP. <instructions>"
5. Record dispatch: task comment with run_id, handoff_id, worker, timestamp
```

### Kanban sweep (4x/day, weekdays)

```
For each in-progress task:
- Last cos-update > 4h ago? → ping worker
- Last cos-update > 8h ago? → mark drift, notify Chris
- Blocked > 24h with no owner action? → escalate to Chris
- No activity > 48h? → mark stale, ask Chris to close or reassign
```

---

## MCP Tools Available to All Agents (via eros aggregate)

All agents reach these via `eros.tail0e55.ts.net:4000/mcp/`:

| Tool             | Service             | Purpose                             |
| ---------------- | ------------------- | ----------------------------------- |
| `recall`         | eros-context-broker | Cache-first ranked memory retrieval |
| `store_context`  | eros-context-broker | Fan-out write to all memory stores  |
| `search_context` | eros-context-broker | Semantic search (existing)          |
| `write_handoff`  | agent-handoff       | Write durable session handoff       |
| `read_handoff`   | agent-handoff       | Load prior session handoff          |
| `list_handoffs`  | agent-handoff       | Find relevant handoffs              |
| `graph.query`    | falkordb-mcp        | Direct FalkorDB Cypher query        |
| Kanban tools     | kanban-shim         | Read/write ghost Kanban             |

Coding CLIs (Claude Desktop, Codex, pi) reach these via `mcp-remote` to
`eros.tail0e55.ts.net:4000/mcp/` with appropriate trust domain headers.

---

## Anti-Patterns (these are bugs, not style choices)

- Starting a session without loading prior context → cold-start waste
- cos-update only at session end → CoS has no continuous visibility
- Storing facts only in MEMORY.md → budget fills, facts lost to other agents
- Stopping at a blocker without a cos-update → CoS can't see the gate
- Completing a task without evidence verification → ghost completions
- Dispatching without a handoff_id in the task body → worker re-derives context
- Asking the human to re-explain something captured in a handoff → unacceptable
- "I'll remember that for next time" without calling store_context → it won't

---

## Enforcement

CoS enforces this contract via the 4x/day Kanban sweep. Agents that
consistently drift from the contract (no cos-updates, no handoffs, cold starts)
will be flagged to Chris for SOUL/AGENTS.md correction.

This document is the ground truth. When in doubt, follow it exactly.
