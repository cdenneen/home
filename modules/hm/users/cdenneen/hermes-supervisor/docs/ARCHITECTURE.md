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
- Prompt Factory creates assignment-specific context only.
- Hermes Worker Manager launches fresh GPT-5.4 semantic workers and
  GPT-5.3-Codex implementation workers with timeout/process-group cancellation.
- Integrator reconstructs GitLab/MR/pipeline/review state for convergence.
- Classifier converts source facts, including repository convergence facts, into
  canonical graph nodes.
- Mutation Gate is the only repository, GitLab, control and scheduler effect
  authority; read-only state writes use an explicit reconciliation operation.
- Schema Registry validates runtime records on load and before atomic write.
- Lifecycle and Accounting registries own assignment states and model attempts.
- Hermes cron supervisor performs guarded graph rebuild and bounded work.
- Block Kit `SlackProjection` is the sole reporter and renders deterministic
  Slack briefings without a model.
- The shared command registry defines Slack command names, aliases, parameters,
  authority, confirmation, and handler keys for the CLI and gateway plugin.
- Reconciler is the sole inventory writer.
- Supervisor control and runtime receipts remain writable under `~/.hermes`.
- Fenced lease controller owns assignment resource exclusion.

## Data flow

Canonical sources -> Collector facts -> Classifier -> execution graph/queue ->
Dispatcher -> canonical lease -> Mutation Gate -> Prompt Factory -> fresh Hermes worker ->
Integrator -> canonical evidence -> graph rebuild. SlackProjection reads one
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
