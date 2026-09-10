---
name: graphify
description: Search and update the shared Graphify knowledge base for durable decisions, summaries, prompts, and notes across agent sessions.
---

# Graphify

## When to Use

Use Graphify at the start of a non-trivial task when prior decisions or session context may help, and after substantial work when a durable decision or summary should survive across sessions.

## Procedure

1. Call the Graphify MCP `search_knowledge` tool with the task topic before re-reading broad parts of the workspace.
2. Treat current repository files and live system state as authoritative when they conflict with stored memory.
3. Call `save_memory` only for concise, durable decisions, summaries, prompts, or notes; do not store secrets or temporary task state.
4. Use `delete_memory` when an entry is confirmed stale or wrong.

## Pitfalls

- Do not save credentials, tokens, private keys, or raw secret-bearing configuration.
- Do not replace project documentation or source control with Graphify.
- Avoid duplicate or low-value memories.

## Verification

- Confirm the MCP call succeeds and the returned project/title matches the intended scope.
- After saving important memory, search for its title once to verify retrieval.
