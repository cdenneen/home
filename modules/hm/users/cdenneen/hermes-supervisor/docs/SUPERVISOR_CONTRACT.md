# External Development Supervisor Contract

Version: 1.0.0

## Purpose and boundary

The Hermes Development Supervisor is temporary external bootstrap tooling for
governed repository development. It is not AXIS, an AXIS runtime, scheduler,
cognition system, ontology, memory owner, planning authority, evidence
authority, or product dependency. AXIS must remain operable after complete
decommissioning of this supervisor.

## Authority

GitLab work items, exact approved PlanningRecord revision/digests, ratified
Contracts, protected main, and accepted test/CI evidence are authoritative.
Slack, Hermes prompts, skills, memory, control files, inventory, leases, and run
records are noncanonical operational state.

Routine governed development is delegated: selection, bounded implementation,
tests, CI repair, MR integration, main verification, evidence, and cleanup.
Reserved human authority includes materially new Product Owner decisions,
Contract/ontology overrides, protected-rule bypass, shared-history rewrite,
unrecoverable deletion, unapproved production mutation, budget expansion,
public release, licensing/IP changes, security weakening, and irreversible
migration without recovery.

## Lifecycle

`disabled -> observing -> enabled -> draining -> stopped -> decommissioned`.
Only `enabled` may claim new assignments. `draining` finishes or hands off
active work but claims nothing new. `stopped` and `decommissioned` cause native
preflight suppression before external reads or model calls.

## Repositories and mutation

Mutation is deny-by-default and restricted to `repository_allowlist`.
Supervisor-created branches use `hermes/`; worktrees live below the configured
supervisor worktree root. The supervisor never cleans resources it did not
create and durably own.

## Scheduling and assignments

Hermes cron is the only scheduler. The worker produces local audit output; the
no-agent reporter is the only Slack producer. Every mutation requires a fenced
lease with assignment, resources, owner run, phase, timestamps, expiry, and
token. Integration uses a fresh session and current GitLab state.

## Inventory and queue

The deterministic reconciler is the sole inventory writer. Retrieval failures
fail closed to Unknown. Queue zero requires fresh configured sources, all items
classified, zero Unknown, every Waiting item decomposition-reviewed, no active
assignment/lease, and no Executable item.

## Stop and recovery

Kill switch, lifecycle mode, disk, budget, source freshness, allowlist,
concurrency, and leases are enforced before model invocation. Failed remote
side effects are reconstructed from GitLab before retry. Non-terminal pipelines
are recorded and continued by a later fresh cycle rather than synchronously
polled.

The default worker ceiling is 144 cycles/day, matching a 10-minute cadence.
Cycles with no executable or active assignment return `wakeAgent=false` and do
not consume a model call. The no-agent reporter is outside this budget.

## Decommissioning

Decommission only after draining all assignments/leases/MRs and recording a
final handoff. Disable cron jobs, revoke supervisor credentials, archive required
receipts, remove generated state/worktrees, and leave the general Hermes gateway
healthy for unrelated uses.
