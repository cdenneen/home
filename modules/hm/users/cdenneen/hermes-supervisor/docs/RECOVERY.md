# Recovery Guide

## Gateway restart or host reboot

Home Manager owns the user service and neutral working directory. After restart,
verify Slack connection, cron heartbeat, and jobs. Reconciliation reconstructs
from GitLab; no conversation history is required.

## Stale lease

Run `axis-development-supervisorctl recover`. Before reassignment, inspect the
branch/MR/pipeline and record the canonical phase. Never delete remote evidence
based only on local lease expiry.

## Provider failure

Preflight enforces daily budget. Hermes retries remain bounded. Repeated
provider failure causes a cooldown/blocked run; it must not silently switch to a
paid fallback outside configured policy.

## GitLab outage

Reconciliation fails closed and emits no worker session. Cached inventory must
not authorize mutation. Reporter may emit an alert from the last valid snapshot.

## Slack outage

Development evidence remains canonical. Pending report fingerprint is retained
and retried after delivery health returns.

## Missing worktree after reboot

Recover assignment from GitLab branch/MR/pipeline. Recreate only a
supervisor-owned worktree under the configured root using the recorded branch.
