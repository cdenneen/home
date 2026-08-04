# Troubleshooting Guide

- Gateway has no Slack adapter: confirm the messaging package wrapper is the
  service `ExecStart`; do not point directly at the Python environment.
- Cron invokes model while stopped: verify preflight emits `wakeAgent=false`
  before reconciliation and model call.
- Inventory stale after GitLab error: treat as invalid; worker must not wake.
- Queue zero with Unknown/retrieval errors: invalid snapshot; inspect source
  statuses and rerun reconciliation.
- Reporter repeats or loses messages: inspect pending/delivered state and cron
  `last_delivery_error`; do not manually advance the fingerprint.
- Terminal cannot find tools: use preflight-provided absolute `tool_paths`.
- Pipeline run exceeds cycle: record Waiting and exit; never long-poll.
- Dirty worktree: preserve provenance and isolate; clean only supervisor-owned
  integrated resources.
