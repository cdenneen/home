# Operations Guide

## Status

```bash
systemctl --user status hermes-gateway.service
hermes cron status
hermes cron list --all
axis-development-supervisorctl status
axis-development-supervisor-health
```

## Control

Edit `control.json` atomically. `mode=observing` permits reconciliation but no
new assignments; `enabled` permits allowlisted mutation; `draining` claims no
new work; `stopped` and `kill_switch=true` suppress before external reads.

## Normal checks

Verify gateway/Slack, worker/projection cron freshness, inventory generation,
overview semantic freshness, overview delivery status and last successful
update, deployed source revision, overview schema compatibility, disk free
space, active leases, open MRs, and queue invariants. The health command checks
the Block Kit projection record and state; obsolete pending report files and
Hermes delivery acknowledgement fields are not health sources.

## Evidence

GitLab and repositories are canonical. Hermes cron outputs, assignment records,
and run records are operational receipts retained according to the runbook.
