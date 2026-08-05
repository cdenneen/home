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

The configured Product Owner can send deterministic private-DM commands without
invoking an LLM. Always use the `!axis` prefix. `!axis help` renders the current
command, alias, description, and parameter contract from
`axis_supervisor.command_registry`; it is the authoritative command list.
Slack rewrites the known bang command to the
typed `/axis` gateway command before busy-agent input handling. A trusted Hermes
plugin then checks the Slack platform, exact private-DM channel, and configured
Product Owner user ID before executing `axis-development-supervisor-command`
with argv only. The result is returned directly; no model, skills selection,
terminal agent, or coding worker is involved. Plain text such as `roadmap`
remains a normal Hermes conversation and must not be used for supervisor control.

Material effects resolve through typed control changes and existing authority;
arbitrary message text never becomes a shell command or persistent policy.
The registry marks every command as Product Owner DM authority and distinguishes
read-only commands from effectful commands whose command text is the explicit
confirmation.

Command results are generated from the current inventory, execution graph, and
control in one invocation. They include semantic revision, generation time,
source inventory revision, and staleness, and never read
`slack-overview-record.json` to compose live state.
