# Unified Memory Router — Design Document

**Status:** Proposed  
**Author:** ops@nyx  
**Date:** 2026-10-03  
**Kanban:** maw-p6 (work-ops board, ghost)

---

## Problem

Every agent in the fleet (Hermes profiles, CoS, Claude Desktop, Codex, pi) has
a different memory ceiling and a disconnected store:

| Agent                      | Current memory                | Limit               | Shared?          |
| -------------------------- | ----------------------------- | ------------------- | ---------------- |
| Hermes (any profile)       | MEMORY.md + USER.md           | 2,200 + 1,375 chars | No — per-profile |
| CoS (chief-of-staff@ghost) | Same MEMORY.md                | Same                | No               |
| Claude Desktop             | Project notes (local)         | Varies              | No               |
| Codex                      | None (stateless)              | —                   | No               |
| pi/Paul                    | Per-project memory tool       | Unknown             | No               |
| Recallium                  | Episodic sessions (nyx:18001) | Large               | MCP-accessible   |
| Qdrant (eros)              | Vector collections            | Large               | MCP-accessible   |
| FalkorDB (eros)            | Knowledge graph               | Large               | MCP-accessible   |
| agent-context repo         | Handoff markdown files        | Git-bounded         | Git-accessible   |

The result: agents constantly re-derive context, LLM calls are made for things
already known, and no agent has visibility into what others have learned.

Hermes `write_approval` queues memory writes for human review but the approval
path only touches MEMORY.md — there is no fan-out to shared stores on write,
and no hook to extend this behavior without patching Hermes source.

---

## Goals

1. **Single write path** — one MCP tool call (`store_context`) fans out to all
   appropriate stores. Callers do not need to know which store to use.

2. **Single read path** — one MCP tool call (`recall`) searches across all
   stores, merges results by relevance, and returns ranked context. Eliminates
   per-store query logic from every agent.

3. **No redundant re-derivation** — Qdrant semantic search plus FalkorDB graph
   queries together cover all hot-context needs for Phase 1. A dedicated cache
   tier may be added in a later phase once Phase 1 performance is validated.

4. **Universal client support** — every agent that can reach the eros MCP
   aggregate gets the full memory system: Hermes, CoS, Claude Desktop (via
   mcp-remote), Codex, pi, ChatGPT (via kanban-shim pattern).

5. **Budget escape hatch** — MEMORY.md stays as the compact survival hint
   (session bootstrap only). All durable facts live in the shared stores.

---

## Architecture

```
Agent (any)
    |
    | MCP tool call
    v
eros context broker (pkgs/eros-context-broker/server.py)
    |
    +-- store_context(content, type, agent_id, project, tags)
    |       |
    |       +-- Qdrant (embed+upsert)   <- vector / semantic search
    |       +-- FalkorDB (MERGE node)   <- knowledge graph / relationships
    |       +-- agent-context repo      <- durable markdown (git commit)
    |
    +-- recall(query, agent_id, project, limit)
            |
            +-- Qdrant semantic search  <- embed query -> top-K results
            +-- FalkorDB graph query    <- related nodes by agent/project
            +-- merge_ranked()          <- existing broker merge logic
            +-- return top results
```

The broker already has `embed()`, `qdrant_upsert()`, `qdrant_search()`,
`merge_ranked()`, and the MCP server infrastructure. This extends it with
two new tools and the FalkorDB + Qdrant write paths.

Note: a Redis/cache tier (short-term exact-hit lookups) was considered for
Phase 1 but deferred. FalkorDB already exposes the Redis protocol on port 6380
(tailnet-only, used by `pkgs/falkordb-schema/record_completed_task.py` for
graph writes). Placing plain SET/GET/EXPIRE cache keys alongside live Cypher
graph data on the same FalkorDB instance adds key-namespace coupling and
unauthenticated tailnet exposure (flagged by Greptile) with no validated
performance baseline to justify it. The cache-tier decision is deferred to
Phase 2 after Phase 1 store latency is measured.

---

## Memory Type Routing

`store_context` accepts a `type` hint that controls routing:

| Type         | Qdrant                 | FalkorDB        | agent-context |
| ------------ | ---------------------- | --------------- | ------------- |
| `fact`       | ✓ `shared_knowledge`   | ✓ Decision node | ✓ commit      |
| `decision`   | ✓ `shared_knowledge`   | ✓ Decision node | ✓ commit      |
| `session`    | ✓ `shared_memory`      | —               | ✓ handoff     |
| `topology`   | ✓ `shared_knowledge`   | ✓ Agent node    | ✓ commit      |
| `task`       | —                      | ✓ Task node     | —             |
| `capability` | ✓ `eros_capability_v1` | ✓ Agent node    | —             |

Default type is `fact` when unspecified.

---

## New MCP Tools

### `store_context`

```json
{
  "name": "store_context",
  "description": "Store a fact, decision, or session note in the shared memory system. Fans out to Qdrant (vector), FalkorDB (graph), and agent-context repo (durable) based on type. Use instead of writing directly to MEMORY.md for anything that should be accessible to other agents.",
  "inputSchema": {
    "type": "object",
    "required": ["content"],
    "properties": {
      "content": {
        "type": "string",
        "description": "The fact, decision, or note to store"
      },
      "type": {
        "type": "string",
        "enum": [
          "fact",
          "decision",
          "session",
          "topology",
          "task",
          "capability"
        ],
        "default": "fact"
      },
      "agent_id": {
        "type": "string",
        "description": "Calling agent identity (e.g. ops@nyx). Defaults to trust domain owner."
      },
      "project": {
        "type": "string",
        "description": "Project slug for scoping (e.g. eks-platform, multi-agent-workflow)"
      },
      "tags": {
        "type": "array",
        "items": { "type": "string" },
        "description": "Optional tags for filtering"
      }
    }
  }
}
```

### `recall`

```json
{
  "name": "recall",
  "description": "Retrieve relevant context from shared memory. Searches Qdrant (semantic) and FalkorDB (graph) in parallel, merges results by relevance, and returns ranked context.",
  "inputSchema": {
    "type": "object",
    "required": ["query"],
    "properties": {
      "query": {
        "type": "string",
        "description": "Natural language query or keyword"
      },
      "project": {
        "type": "string",
        "description": "Scope to a specific project"
      },
      "agent_id": {
        "type": "string",
        "description": "Scope to a specific agent's knowledge"
      },
      "type": {
        "type": "string",
        "enum": [
          "fact",
          "decision",
          "session",
          "topology",
          "task",
          "capability"
        ]
      },
      "limit": { "type": "integer", "default": 10 }
    }
  }
}
```

---

## Integration Points

### cos-update → store_context fan-out

When any agent calls `cos-update`, CoS:

1. Updates the Kanban task
2. Calls `store_context(content=message, type="session", agent_id=caller)`

This means every cos-update automatically flows into the shared memory system
without agents needing to make a separate call.

### Hermes memory.write_approval → store_context

No Hermes source patch needed. The approach:

- MEMORY.md remains the survival hint (built-in Hermes mechanism)
- Agents are instructed in their SOUL to call `store_context` for anything
  durable, instead of relying on the Hermes memory tool for shared facts
- The curator background process (which generates pending writes) is
  supplemented: agents explicitly call `store_context` at decision points

### Session start context injection

Agents call `recall(query="active context", project=<current>)` at session
start instead of only reading MEMORY.md. The broker returns the most relevant
recent context across all stores. This replaces the cold-start re-derivation
problem.

---

## Implementation Plan

### Phase 1 — Core broker extension (this PR)

- Add FalkorDB write path (reuse `record_completed_task.py` client pattern:
  `Redis(host=FALKORDB_HOST, port=6380)` + `execute_command("GRAPH.QUERY", ...)`)
- Add `store_context` tool with Qdrant + FalkorDB + agent-context repo fan-out
- Add `recall` tool with merged Qdrant + FalkorDB results
- Wire `store_context` call into `write_handoff` in agent-handoff service so
  every handoff is also indexed

### Phase 2 — Performance + cache-tier decision (follow-on)

- Measure Phase 1 recall latency under load
- Evaluate adding a short-term cache tier: options include a distinct key
  namespace on FalkorDB:6380 (Redis protocol, SET/GET/EXPIRE — key-prefix
  `ctx:` to avoid collision with GRAPH.QUERY-managed keys), a standalone
  keydb/redis on a separate port, or accepting Qdrant latency as sufficient
- Update all nyx+ghost profile SOULs: call `recall` at session start
- Update cos-update flow: CoS calls `store_context` on every received update
- Add `store_context` call to the post-merge hook script

### Phase 3 — Recallium integration (follow-on)

- Recallium (nyx:18001) is already running and MCP-accessible
- Evaluate: add Recallium as a fifth store in the fan-out, OR use it as the
  episodic layer that the broker queries (not writes to directly)
- Decision deferred until Phase 1 is validated

---

## What This Is NOT

- Not a replacement for MEMORY.md — that stays as session bootstrap
- Not a new service — extends the existing context broker in-place
- Not a Recallium replacement — Recallium handles episodic session replay;
  this handles durable cross-agent facts
- Not a requirement to change Hermes source — works via SOUL instructions
  and MCP tool calls

---

## Files Changed

- `pkgs/eros-context-broker/server.py` — add `store_context`, `recall` tools,
  FalkorDB write path (Redis protocol client to port 6380)
- `hosts/nixos/eros.nix` — add `FALKORDB_HOST` env to context broker systemd
  services
- Profile SOULs (nyx-home.nix, ghost-home.nix) — add `recall` at session
  start instruction (Phase 2, separate PR)

---

## Dependencies

- FalkorDB on eros, port 6380 (tailnet-only) — already deployed, used by
  `pkgs/falkordb-schema/record_completed_task.py` (#859)
- Qdrant on eros — already deployed and used by context broker
- agent-context repo — already in use for handoffs

No new services required for Phase 1.

---

## Success Criteria

- `store_context("OmniRoute is on port 20130", type="fact", agent_id="ops@nyx")`
  from nyx returns a hit on `recall("OmniRoute port")` from a Claude Desktop
  session on Mac without a separate LLM call
- All cos-update messages queryable via `recall` within 1 minute of receipt
- MEMORY.md budget pressure eliminated as a blocker for agent memory
