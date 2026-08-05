# Supervisor Reporting Contract

Version: 1.3.0

`SlackProjection` is the sole Slack transport owner. The deterministic no-agent
cron invokes it directly. It updates one persistent Product Owner DM status
message in place and drains the bounded lifecycle notification outbox. Worker
output remains local audit evidence.

Assignment and worker transitions are append-only records in
`operational-events.jsonl`. Product Owner-visible transitions are also persisted
in `slack-outbox.json` before any Slack request. The outbox preserves failures,
uses bounded retry backoff, and emits a concise recovery summary after an outage.

Reports contain a human briefing followed by technical evidence. Human order:
Summary, Since Last Update, Completed, Current Focus, Why This Work, categorized
Blockers, Need Product Owner, Next, Roadmap Progress, Waiting Breakdown, idle
proof when applicable, roadmap-state confidence, and Engineering Decisions.

Technical detail contains report/run ID, inventory generation/timestamp/hash,
repository SHAs, authority references, MRs/pipelines/issues, assignments,
leases, worktrees, and source-linked evidence.

Roadmap composition is a mutually exclusive partition of governed work items.
Every row shows count, total inventory denominator, and percentage; row counts
must sum exactly to discovered inventory. `Verified complete` means verified
under the Supervisor 1.1 audit standard: current main, acceptance evidence,
required tests, successful pipeline, governance linkage, closure, integration,
cleanup, and fresh-cycle recognition were all rechecked. Acceptance criteria,
MRs, milestones, implementation slices, and evidence records are verification
inputs or aggregates, not additional roadmap-denominator entities.

Audit and readiness coverage is reported separately and is not roadmap progress.
It includes inventory classification, closed-item revalidation, Waiting-item
decomposition, dependency evaluation, queue eligibility, source linkage, and
milestone readiness. `slack-overview-record.json` is the exact semantic input to
Block Kit rendering. It identifies its semantic revision, generation timestamp,
inventory revision, graph revision, deployed source revision, schema version,
and source staleness.

Milestones show explicit total, verified, closed-pending-recheck, running,
executable, waiting, and blocked counts. Green means eligible progress, blue
running, yellow expected waiting/review, red a concrete blocker, gray future or
unmapped, and check-mark verified complete. Zero executable counts include a
reason. The global governed queue is partitioned by milestone, unmilestoned,
repository convergence, governance reconciliation, revalidation, and
CI/integration sources, with an explanation when active milestones contain no
lifecycle-executable items.

The roadmap presentation has three independent views:

1. Complete Roadmap projects every governed milestone from M4 through the
   current endpoint in numeric order, including closed sub-milestones. Roadmap
   membership is recovered from GitLab milestone titles, `roadmap::AX-M*`
   labels, or source-linked `owning_milestone` evidence.
2. Active Execution renders observed `scheduler_state` from the execution graph.
   Current focus, selected work, deferred work, budget, and constraint are copied
   from graph fields only. Reporting never calls scheduler selection logic or
   predicts the next batch. The earliest unresolved roadmap frontier remains a
   lifecycle projection and does not claim scheduler selection.
3. Strategic Programs projects non-exclusive cross-cutting streams such as
   efficiency architecture, cognition projection, runtime decomposition,
   provider runtime, plugin lifecycle, repository convergence, and
   revalidation.

Queue terminology is fixed: governed roadmap inventory is the complete work-item
denominator; supervisor work remaining is every non-verified item; the ready
work queue contains bounded work the supervisor can execute now;
implementation-executable, revalidation-ready, governance-reconciliation-ready,
and repository-convergence-ready are distinct. Waiting, blocked, and other
not-ready work is reported as the reconciled remainder.

Revalidation uses four exclusive tiers. Tier A reconstructs canonical evidence
without mutation and may batch independent repositories when the scheduler does.
Tier B performs bounded technical inspection or reruns. Tier C creates governed
corrective implementation. Tier D isolates reserved human authority. Every item
retains its own assignment, evidence fingerprint, verification record, and
failure disposition; batching never weakens the nine-check standard.

The projection refreshes the semantic record every run. Operational worker,
retry, commit, test, MR, merge, lease, grant, and disposition transitions are
meaningful even when roadmap classification totals do not change. The status
projection reads live assignments plus the event ledger rather than relying
only on the last inventory snapshot.

Delivery stages are explicit: `notification_created`, `notification_queued`,
`notification_send_attempted`, `Slack_API_accepted`,
`Slack_message_created`, `Slack_message_updated`,
`Slack_message_verified`, `delivery_failed`, and `delivery_unknown`.
Success requires a Slack response with the expected DM channel and timestamp,
followed by message readback from that channel. Formatter success, local state
writes, gateway connectivity, or an intermediate HTTP success are not delivery.
Failures remain queued, affect observability and overall health, and never
advance the successful fingerprint.

Assignment lifecycle completion is operational only. Reporting must show the
assignment type, assignment result, and work-item disposition separately.
`analysis-completed` never means implementation completed. Canonical roadmap
completion remains exclusively derived from an implementation completion
receipt or a bounded no-op verification against an exact main revision.

Global repository mutation remains default-deny. An eligible implementation may
run only with a canonical per-assignment mutation grant binding exact authority,
source revision, branch/worktree, paths, tests, operation classes, Git/GitLab
effects, model/retry/prompt/cost budgets, expiry, required evidence, and
integration conditions. Grant consumption does not itself prove canonical work
item completion.

Live commands generate a new semantic record from one matching current
inventory/graph/control tuple. They never compose with the persisted overview.
Generation mismatch fails explicitly as stale. Every command response includes
semantic revision, generation timestamp, source inventory revision, and
staleness.

Need Product Owner is YES only for a specific reserved human-authority decision.
Governance or technical blockers alone do not imply human action. Queue zero
must quote the computed idle-proof checks.
