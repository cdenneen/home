---
name: cocoindex-code
description: Use CocoIndex Code for semantic codebase discovery, structural grep, and repo-local MCP search before falling back to broad file scans. Best for large repos, symbol hunting, concept tracing, and AST-style matching.
---

# CocoIndex Code

## Default Workflow

1. Start with `ccc status` to see whether the repo is already initialized and indexed.
2. If the repo is not initialized and repo-local metadata is acceptable, run `ccc init`.
3. Build or refresh the index with `ccc index` before semantic search.
4. Use `ccc search` for concepts, behavior, ownership, or fuzzy symbol discovery.
5. Use `ccc grep` when you need structural matching by code example instead of plain text.
6. Once you know the right files, switch to targeted reads with `sed`, `bat`, or narrow `rg`.

## Commands

Check readiness:

```bash
ccc status
ccc doctor
```

Initialize and index:

```bash
ccc init
ccc index
```

Semantic search:

```bash
ccc search "where do we configure MCP servers for Codex and Claude?"
ccc search "Open WebUI cloudflare tunnel wiring"
```

Structural grep:

```bash
ccc grep 'home.file."$PATH".source = $VALUE;'
ccc grep 'mcpServers = { $SERVER = $CONFIG; }'
```

Run through MCP clients with the `cocoindex-code` server when the agent can call MCP tools directly.

## When To Prefer It

- Large monorepos where naive file dumping wastes tokens.
- You know the behavior you need, but not the exact filename.
- You need structure-aware matching that plain `rg` cannot express cleanly.

## When To Skip It

- One-file or very small repos.
- Fast exact-match searches where `rg` is already precise.
- Situations where the repo must remain untouched and `ccc init` would be undesirable.
