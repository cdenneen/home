# Troubleshooting Guide

- Gateway has no Slack adapter: confirm the messaging package wrapper is the
  service `ExecStart`; do not point directly at the Python environment.
- Cron invokes model while stopped: verify preflight emits `wakeAgent=false`
  before reconciliation and model call.
- Inventory stale after GitLab error: treat as invalid; worker must not wake.
- Queue zero with Unknown/retrieval errors: invalid snapshot; inspect source
  statuses and rerun reconciliation.
- Slack overview repeats or fails: inspect `slack-overview-state.json` delivery
  status, last successful update, semantic revision, and source revision. Do not
  manually advance the fingerprint.
- Slack command reports stale state: compare inventory `generation_id` with the
  execution graph `inventory_generation_id`; commands fail instead of mixing
  generations.
- Scheduler focus looks wrong: inspect graph `scheduler_state`. Reporting only
  projects those observed fields and does not select or predict work.
- Terminal cannot find tools: use preflight-provided absolute `tool_paths`.
- Pipeline run exceeds cycle: record Waiting and exit; never long-poll.
- Dirty worktree: preserve provenance and isolate; clean only supervisor-owned
  integrated resources.
