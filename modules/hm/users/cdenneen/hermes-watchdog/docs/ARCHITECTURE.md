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
- GPT-5.4 is invoked only for level-4 deterministic anomalies through the pinned Hermes
  direct auxiliary client with `tools=[]`, never the agent/oneshot tool configuration.
  Prompt, output, timeout, cooldown, and daily-call bounds still apply.
- Routine supervisor Slack cron delivery is disabled. The old projection command remains
  available only for explicit operator emergency use.

## Deterministic Health

Each cycle evaluates liveness, control integrity, delivery effectiveness, and mission
progress. Product progress fingerprints include only gate, debt, capability, and
milestone transitions; assignment starts/completions and activity events cannot reset a
stuck timer. Mission stuck thresholds are the bounded median of prior progress intervals
multiplied by the configured history factor. Explicit disabled/observing/draining modes,
completed missions, external-authority blockers, and entirely waiting/blocked frontiers
are expected waits rather than stuck anomalies, but any executable evidence action makes
the mission actionable rather than waiting.

## Recovery Levels

0. Observe and complete a transient missed-heartbeat catch-up.
1. Retry the canonical Slack projection and record its verified/failed outcome.
2. Run the pinned watchdog cron installer under an ownership lock.
3. Reset and restart only the watchdog backup timer/runtime.
4. Run one bounded no-tool diagnostic and persist a repair escalation restricted to
   `cdenneen/home` and the supervisor/watchdog module paths.
5. Require Product Owner action; no autonomous mutation is permitted.

Every requested, started, completed, failed, waiting-human, and health-restored recovery
transition is appended to the recovery ledger. Incident records transition through
opened, recovering, and resolved events. Slack accepts are persisted before readback, so
a readback crash resumes verification against the accepted timestamp rather than posting
a duplicate. A restart
reconstructs current lifecycle state from `state.json` and continues the append-only
ledgers without adopting or dispatching product work.
