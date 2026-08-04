# Slack Command and Reporting Guide

The no-agent Slack projection maintains one persistent private-DM overview via
Block Kit `chat.postMessage`/`chat.update`. GitLab is the canonical execution
ledger; colors, symbols, bars, and message timestamps are presentation only.

The overview contains health, mode, reconciliation/progress state, worker and
integrator cards, governed queue depth, blockers/waiting, Product Owner decision
cards, repository convergence, budget/resource state, roadmap bars, active
milestones, and evidence context. Plain text is always supplied as fallback.

Roadmap denominator includes every discovered governed item. Verified progress
is only Integrated plus evidence-backed Completed. Revalidation, discovered,
classified, Waiting, Blocked, Invalid, and Superseded items are never counted as
complete.

Supported operator intents are status, reconcile, pause, resume, drain, stop,
inspect, retry, prioritize, and deprioritize. Material effects resolve through
typed control changes and existing authority; arbitrary message text never
becomes a shell command or persistent policy.

Stop requires explicit confirmation. Prioritization reorders only already
Executable work. Human decisions identify the exact reserved boundary, affected
refs, impact, alternatives, and independent work continuing meanwhile.
