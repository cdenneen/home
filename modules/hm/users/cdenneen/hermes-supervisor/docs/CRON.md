# Cron Guide

Two Hermes-native jobs share one gateway:

- Worker: model session, guarded preflight, no Slack delivery.
- Reporter: no-agent deterministic script, Slack delivery.

The worker uses Hermes cron overlap prevention plus fenced assignment leases.
The reporter reads only a completed inventory generation and returns `[SILENT]`
when no semantic change or heartbeat is due.

Never add another gateway or scheduler daemon. Job replacement requires a
drain, control job-ID update, output-history retention, and retirement record.

Home Manager's gateway service runs `axis-development-supervisor-cronctl
install` after startup. It creates missing jobs idempotently and records their
IDs in control state. Operators may run:

```bash
axis-development-supervisor-cronctl status --hermes "$(command -v hermes)"
axis-development-supervisor-cronctl install --hermes "$(command -v hermes)"
axis-development-supervisor-cronctl remove --hermes "$(command -v hermes)"
```
