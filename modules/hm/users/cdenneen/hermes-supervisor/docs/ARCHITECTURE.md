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
- Repository Convergence Engine captures provenance before disposition.
- Hermes cron supervisor performs guarded graph rebuild and bounded work.
- Hermes cron reporter renders deterministic Slack briefings without a model.
- Reconciler is the sole inventory writer.
- Supervisor control and runtime receipts remain writable under `~/.hermes`.
- Fenced lease controller owns assignment resource exclusion.

## Data flow

Canonical sources -> Collector -> inventory -> semantic records -> execution
graph -> Dispatcher -> lease -> Prompt Factory -> fresh Hermes worker ->
Integrator -> canonical evidence -> graph rebuild. Reporter reads completed
inventory/graph generations and acknowledges delivery on the following run.

## Explicit exclusions

No OpenCode worker dependency, AXIS runtime package, organism database, AXIS scheduler API, cognition,
ontology, canonical planning, or product identity is imported or exposed.
