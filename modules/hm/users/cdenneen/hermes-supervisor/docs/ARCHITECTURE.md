# Architecture

## Components

- Home Manager owns the pinned Hermes messaging package, one gateway service,
  supervisor skill, scripts, schemas, and documentation.
- Hermes cron worker performs guarded reconciliation and bounded work.
- Hermes cron reporter renders deterministic Slack briefings without a model.
- Reconciler is the sole inventory writer.
- Supervisor control and runtime receipts remain writable under `~/.hermes`.
- Fenced lease controller owns assignment resource exclusion.

## Data flow

Canonical GitLab/repository sources -> reconciler -> immutable inventory
generation -> guarded preflight -> fresh Hermes worker -> GitLab/repository
evidence -> next reconciliation. Reporter reads only completed inventory
generations and acknowledges delivery on the following run.

## Explicit exclusions

No AXIS runtime package, organism database, AXIS scheduler API, cognition,
ontology, canonical planning, or product identity is imported or exposed.
