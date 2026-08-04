# Supervisor Reporting Contract

Version: 1.0.0

The deterministic no-agent reporter is the only Slack report producer. Worker
output remains local audit evidence.

Reports contain a human briefing followed by technical evidence. Human order:
Summary, Since Last Update, Completed, Current Focus, Why This Work, categorized
Blockers, Need Product Owner, Next, Roadmap Progress, Waiting Breakdown, idle
proof when applicable, roadmap-state confidence, and Engineering Decisions.

Technical detail contains report/run ID, inventory generation/timestamp/hash,
repository SHAs, authority references, MRs/pipelines/issues, assignments,
leases, worktrees, and source-linked evidence.

Deliver only on semantic inventory change, urgent failure/human decision, or
the configured heartbeat. Otherwise emit exactly `[SILENT]`. Advance delivered
state only after the prior Hermes delivery completed successfully. Failed
delivery retries the same semantic fingerprint.

Need Product Owner is YES only for a specific reserved human-authority decision.
Governance or technical blockers alone do not imply human action. Queue zero
must quote the computed idle-proof checks.
