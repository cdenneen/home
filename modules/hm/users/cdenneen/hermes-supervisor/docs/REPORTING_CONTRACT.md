# Supervisor Reporting Contract

Version: 1.1.1

The deterministic no-agent Block Kit projection is the only Slack report
producer. It updates one persistent Product Owner DM message in place. Worker
output remains local audit evidence.

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
milestone readiness. The technical record
`slack-overview-record.json` is the exact semantic input to Block Kit rendering.

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
2. Active Execution orders milestone work by current supervisor queue rank,
   while separately naming the earliest unresolved execution frontier and the
   current supervisor focus. Parallel work does not imply earlier prerequisites
   are complete.
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
without mutation and may batch at most two independent repositories per cycle.
Tier B performs bounded technical inspection or reruns. Tier C creates governed
corrective implementation. Tier D isolates reserved human authority. Every item
retains its own assignment, evidence fingerprint, verification record, and
failure disposition; batching never weakens the nine-check standard.

Deliver only on semantic inventory change, urgent failure/human decision, or
the configured heartbeat. Otherwise emit exactly `[SILENT]`. Advance delivered
state only after the prior Hermes delivery completed successfully. Failed
delivery retries the same semantic fingerprint.

Need Product Owner is YES only for a specific reserved human-authority decision.
Governance or technical blockers alone do not imply human action. Queue zero
must quote the computed idle-proof checks.
