# AXIS Development Watchdog

The watchdog is an independent deterministic control loop. It has its own runtime root,
cron ownership ID, heartbeat, current state, and append-only state, observation, incident,
recovery, and projection ledgers. It does not import supervisor code, acquire supervisor
leases, invoke the supervisor dispatcher, or write product repositories.

## Boundaries

- Supervisor evidence under `axis-development-supervisor` is read-only to the watchdog.
- The sole exception is `slack-overview-state.json`, whose existing dashboard and incident
  timestamp/fingerprint maps remain the canonical Slack projection identity.
- The supervisor worker cron remains independently owned and scheduled every ten minutes.
- The watchdog cron is independently owned and scheduled every five minutes with
  `--no-agent`. GPT-5.4 is invoked separately, without tools, only after deterministic
  anomaly detection and within prompt, timeout, cooldown, and daily-call bounds.
- Routine supervisor Slack cron delivery is disabled. The old projection command remains
  available only for explicit operator emergency use.

## Deterministic Health

Each cycle evaluates liveness, control integrity, delivery effectiveness, and mission
progress. Mission stuck thresholds are the bounded median of prior progress intervals
multiplied by the configured history factor. Explicit disabled/observing/draining modes,
completed missions, external-authority blockers, and entirely waiting/blocked frontiers
are expected waits rather than stuck anomalies.

## Recovery Levels

0. Observe and catch up a transient missed heartbeat.
1. Retry watchdog-owned Slack projection.
2. Repair the watchdog-owned cron registration.
3. Restart only the watchdog runtime.
4. Escalate a supervisor repair restricted to `cdenneen/home`.
5. Require Product Owner action; no autonomous mutation is permitted.

Incident records transition through opened, recovering, and resolved events. A restart
reconstructs current lifecycle state from `state.json` and continues the append-only
ledgers without adopting or dispatching product work.
