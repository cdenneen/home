# Paul AGENTS.md Update Snippet

## Target File
`~/.pi/agent/AGENTS.md` (Paul's orchestrator AGENTS.md on nyx)

## Suggested Addition

Add this paragraph after the final "Tone" section (after line 141, before any new section):

```
## FalkorDB Knowledge Graph (maw-p3)

When asked to research or verify work on a task, query the FalkorDB MCP tools first for "what has been done on X" (repos/files/decisions relevant to the topic) before re-reading GitLab issues from scratch or launching a GitLab scout subagent. The graph is authoritative for completed task metadata and related artifacts.
```

---

**Note for cdenneen**: This PR does NOT modify `~/.pi/agent/AGENTS.md` directly. The protected instruction file approval gate requires manual human application outside of normal PR review. This snippet is provided for easy copy-paste when applying the change manually.
