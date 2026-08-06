# Architecture

## Components

- Home Manager owns the pinned Hermes messaging package, one gateway service,
  supervisor skill, component package, schemas, and documentation.
- Deterministic Collector retrieves GitLab/repository facts only.
- Execution Graph Builder combines dependencies, semantic authority records,
  assignments, leases, convergence state, and executable child slices.
- Authority Resolver distinguishes direct, inherited, preparation-only,
  human/governance-required, prohibited, and unresolved authority.
- Semantic Decomposition Engine stores source-grounded candidate-slice records.
- Dispatcher creates one bounded assignment without conflicting active work.
- Executable Frontier Projector persists ranked entries, path conflict domains,
  selected work, deferred reasons, and independent stage capacities.
- Prompt Factory creates assignment-specific context only.
- Hermes Worker Manager launches fresh GPT-5.4 semantic workers and
  GPT-5.3-Codex implementation workers with timeout/process-group cancellation.
- Integrator reconstructs GitLab/MR/pipeline/review state for convergence.
- Workflow State persists implementation handoffs and the reviewer-owned
  integration queue, including main-advance reconciliation classification.
- Classifier converts source facts, including repository convergence facts, into
  canonical graph nodes.
- Mutation Gate is the only repository, GitLab, control and scheduler effect
  authority; read-only state writes use an explicit reconciliation operation.
- Schema Registry validates runtime records on load and before atomic write.
- Lifecycle and Accounting registries own assignment states and model attempts.
- Hermes cron supervisor performs guarded graph rebuild and bounded work.
- Block Kit `SlackProjection` is the sole reporter and renders deterministic
  Slack briefings without a model. Its default dashboard is a fixed 15-section
  executive proof view over roadmap, milestones, WIP, material activity,
  constraints, engineering, deployment, runtime validation, capability gates,
  named runtime surfaces, and human action. Internal execution custody is not
  projected. Atomic state retains the latest Block Kit dashboard as a fallback.
- The Slack decision controller accepts actions only for the exact
  `axis29-mcp-tranche-v2` digest, binds each action to the configured Product
  Owner/workspace/DM/message identity, persists one immutable outcome, and
  rebuilds the execution frontier before updating the same card to scheduling.
- Evidence-derived no-op fingerprints exclude completed technical verification
  from both graph selection and dispatch until repository, semantic, authority,
  acceptance, merge-request, source, or required-test evidence changes.
- Routine analysis and unchanged no-op events remain in the operational event
  log for the dashboard recent-activity line but never enter standalone Slack
  delivery. Incidents and recovery events retain standalone delivery.
- The shared command registry defines Slack command names, aliases, parameters,
  authority, confirmation, and handler keys for the CLI and gateway plugin.
- Reconciler is the sole inventory writer.
- Supervisor control and runtime receipts remain writable under `~/.hermes`.
- Fenced lease controller owns assignment resource exclusion.

## Data flow

Canonical sources -> Collector facts -> Classifier -> execution graph/queue ->
Executable Frontier -> Dispatcher -> canonical lease -> Mutation Gate -> Prompt
Factory -> fresh Hermes worker -> durable handoff/integration queue -> Integrator
-> canonical evidence -> graph rebuild and immediate frontier refill event. SlackProjection reads one
matching completed inventory/graph generation, builds semantics, renders Block
Kit, and records delivery state directly. Live commands independently build
fresh semantics from the same current inventory/graph/control tuple.

Reporting is downstream of scheduling. It copies graph-emitted scheduler focus,
selection, deferral, budget, and constraint state and never invokes scheduler
selection logic.

See `CONVERGENCE.md` for authority decisions, migration, disposition and the
before/after production call graph.

## Explicit exclusions

No OpenCode worker dependency, AXIS runtime package, organism database, AXIS scheduler API, cognition,
ontology, canonical planning, or product identity is imported or exposed.
