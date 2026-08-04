# Operations Guide

## Status

```bash
systemctl --user status hermes-gateway.service
hermes cron status
hermes cron list --all
axis-development-supervisorctl status
```

## Control

Edit `control.json` atomically. `mode=observing` permits reconciliation but no
new assignments; `enabled` permits allowlisted mutation; `draining` claims no
new work; `stopped` and `kill_switch=true` suppress before external reads.

## Normal checks

Verify gateway/Slack, worker/reporter freshness, inventory schema/generation,
delivery error, disk free space, active leases, open MRs, and queue invariants.

## Evidence

GitLab and repositories are canonical. Hermes cron outputs, assignment records,
and run records are operational receipts retained according to the runbook.
