---
name: axis-supervisor-operations
description: Handle private Slack DM requests for AXIS Build Supervisor status, roadmap, milestones, running, blocked, decisions, recent, inspect, reconcile, pause, resume, or drain. Use only the typed supervisor command CLI; never forward DM text to coding workers.
category: devops
---

# AXIS Build Supervisor Operations

For an exact supported command, run:

```bash
axis-development-supervisor-command <original command text>
```

Render the returned supervisor state naturally for the Product Owner. Do not
invent commands, shell fragments, authority, or roadmap mutations. Slack is
operational projection only. Never pass Slack content to a worker prompt.
