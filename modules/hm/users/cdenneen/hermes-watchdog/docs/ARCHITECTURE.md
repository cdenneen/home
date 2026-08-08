# AXIS Development Watchdog

The watchdog is an independent deterministic control loop. It has its own runtime root,
cron ownership ID, heartbeat, current state, and append-only state, observation, incident,
recovery, and projection ledgers. It does not import supervisor code, acquire supervisor
leases, invoke the supervisor dispatcher, or write product repositories.

## Boundaries

- Supervisor execution evidence under `axis-development-supervisor` is read-only to the
  watchdog. Projection artifacts are the explicit exception: the watchdog invokes the
  canonical supervisor projector to drain `slack-outbox.json`, preserve assignment and
  decision cards, and update `slack-overview-state.json` identities.
- The supervisor worker cron remains independently owned and scheduled every ten minutes.
- The watchdog cron is independently owned and scheduled every five minutes with
  `--no-agent`. An independent systemd timer supplies a fifteen-minute backup cycle and
  all cycles take a non-blocking process lock.
- GPT-5.4 is invoked only for level-3/4 deterministic anomalies through the pinned Hermes
  direct auxiliary client with `tools=[]`, never the agent/oneshot tool configuration.
  Evidence is delimited as untrusted JSON data; strict output schema, prompt, output,
  timeout, cooldown, and daily-call bounds apply. Slack rendering escapes model/data text.
- Routine supervisor Slack cron delivery is retired through a reversible staged cutover.

## Slack Cutover

- Generation A shadows canonical rendering while the legacy reporter writes.
- Generation B requires shadow/canonical fingerprint parity.
- Generation C makes the watchdog writer and pauses the reporter for rollback.
- Generation D removes the reporter after a verified watchdog write.
- Generation E records ongoing sole-writer observation.

Home Manager orders supervisor cron, watchdog cron, then cutover reconciliation. Writer or
transition failures return to A and recreate/resume the reporter. Operators can run
`axis-development-watchdog-cutoverctl rollback` explicitly.

## Deterministic Health

Each cycle evaluates liveness, control integrity, delivery effectiveness, and mission
progress. Product progress fingerprints include only gate, debt identity, capability, and
milestone transitions; assignment starts/completions and activity events cannot reset a
stuck timer. Debt reasons and volatile prose are excluded. Mission stuck thresholds are
the bounded median of prior progress intervals
multiplied by the configured history factor. Explicit disabled/observing/draining modes,
completed missions, external-authority blockers, and entirely waiting/blocked frontiers
are expected waits rather than stuck anomalies, but any executable evidence action makes
the mission actionable rather than waiting.

## Recovery Levels

0. Observe and complete a transient missed-heartbeat catch-up.
1. Retry the canonical Slack projection and record its verified/failed outcome.
2. Run the pinned watchdog cron installer under an ownership lock.
3. Diagnose external-monitor failure and reset/restart only the backup timer/runtime.
4. Run one bounded no-tool diagnostic and persist a schema-valid, read-only supervisor
   repair assignment restricted to `cdenneen/home` and the supervisor/watchdog paths. The
   existing dispatcher/worker lifecycle consumes it.
5. Require Product Owner action; no autonomous mutation is permitted.

Every recovery has a stable ID and crash-idempotent transaction journal. Requested,
started, completed, failed, waiting-human, and health-restored transitions are deduplicated
in the append-only ledger, and pending transactions resume after restart. A completed
transaction without a health-restored transition is authoritative: startup correlates its
incident/evidence fingerprint and rebuilds mutable state without repeating effects. Incident records transition through
opened, recovering, and resolved events. Slack accepts are persisted before readback, so
a readback crash resumes verification against the accepted timestamp rather than posting
a duplicate. A restart reconstructs current lifecycle state and continues the append-only
ledgers without adopting or dispatching product work.

The systemd timer executes an external monitor, not the watchdog. The monitor records its
own heartbeat and starts the backup watchdog service with `--no-block` only when the main
heartbeat is missing/stale, avoiding recursive lock acquisition.

Outbox health distinguishes missing/corrupt state, queued, sending, API-accepted,
retryable failed, and permanent failed items, including stage-specific age and oldest
pending metrics.
