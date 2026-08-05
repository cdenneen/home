# Cron Guide

Two Hermes-native jobs share one gateway:

- Worker: model session, guarded preflight, no Slack delivery.
- Slack projection: no-agent deterministic Block Kit update.

The worker uses Hermes cron overlap prevention plus fenced assignment leases.
The projection accepts only matching inventory and graph generations. It writes
fresh overview semantics each run and skips the Slack API call when the rendered
fingerprint is unchanged. No text reporter or pending delivery queue exists.

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
