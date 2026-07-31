# RTK - Rust Token Killer (Codex CLI)

**Usage**: Token-optimized CLI proxy for shell commands.

## Rule

When `rtk` is available, route noisy shell commands through it first.
This is especially useful for tests, package installs, builds, linters, and verbose `git` inspection.

Examples:

```bash
rtk git status
rtk git log --oneline -20
rtk cargo test
rtk npm run build
rtk pytest -q
rtk nix build .#nixosConfigurations.ghost.config.system.build.toplevel
```

## Meta Commands

```bash
rtk gain            # Token savings analytics
rtk gain --history  # Recent command savings history
rtk proxy <cmd>     # Run raw command without filtering
```

## When To Skip It

Use the raw command for interactive TUIs, commands that already produce tiny output, or cases where `rtk` changes behavior in a way that blocks the task.

## Verification

```bash
rtk --version
rtk gain
which rtk
```
