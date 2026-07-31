---
name: rtk-workflow
description: Use RTK to wrap noisy shell commands so agent sessions waste fewer tokens on logs and repetitive command output. Best for tests, builds, installs, linters, and verbose git inspection.
---

# RTK Workflow

## Default Workflow

1. Prefer `rtk <command>` for noisy commands first.
2. Use plain commands for tiny outputs, interactive TUIs, or cases where RTK breaks the workflow.
3. After a work session, use `rtk gain` to check whether the wrapper is actually helping.

## Commands

Typical high-value cases:

```bash
rtk git status
rtk git log --oneline -20
rtk npm test
rtk pytest -q
rtk cargo test
rtk nix build .#nixosConfigurations.ghost.config.system.build.toplevel
```

Bypass filtering when needed:

```bash
rtk proxy journalctl -u ollama.service -n 200
rtk proxy git diff
```

Check effectiveness:

```bash
rtk gain
rtk gain --history
```

## When To Prefer It

- Package installs and lockfile churn.
- Test suites and builds with repetitive logs.
- Git history or status commands inside long-running agent sessions.

## When To Skip It

- Commands that already emit one or two short lines.
- Interactive programs like editors, fuzzy pickers, or full-screen UIs.
- Debugging sessions where every raw log line matters.
