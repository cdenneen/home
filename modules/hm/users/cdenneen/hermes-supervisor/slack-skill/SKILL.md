---
name: axis-supervisor-operations
description: Fallback guidance for AXIS Build Supervisor requests that are not handled by the deterministic axis-supervisor-commands plugin. Never use terminal discovery for supported commands.
category: devops
---

# AXIS Build Supervisor Operations

Supported Slack commands are handled before agent dispatch by the
`axis-supervisor-commands` plugin when sent with the `!axis` prefix. If plain
text reaches this skill, tell the Product Owner to use `!axis <command>`; do not
inspect the filesystem or discover tools. For fallback execution, run only:

```bash
axis-development-supervisor-command <original command text>
```

Render the returned supervisor state naturally for the Product Owner. Do not
invent commands, shell fragments, authority, or roadmap mutations. Slack is
operational projection only. Never pass Slack content to a worker prompt.
