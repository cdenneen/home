# Supervisor 1.2 Architectural Convergence

## Safety Freeze

- Mutation remained disabled for the entire migration.
- Freeze source revision: `3bfc2b7c0486b669b7e900a519a17d5b9e3ccea7`.
- Runtime snapshot: `/tmp/opencode/axis-supervisor-freeze-20260805T014516Z`.
- Snapshot SHA-256: `f6cb47c099a07b5e0bd6ba750713b6b123b1665dedaec4efcf420028ab748211`.
- Rollback generation before convergence: Home Manager generation 30.
- Before metrics: 55 source files, 8,857 counted lines, 34 tests.

## Before

```text
GitLab + repositories
        |
        v
collector.py ------------------------+
  classification + queue + graph     |
        |                             |
        v                             v
inventory.json                  execution-graph.json
  embedded queue/graph          graph.py queue/graph
        |                             |
        +-------------+---------------+
                      v
                   cycle.py

report.py -> pending delivery state -> retired text delivery
SlackProjection -> persistent Block Kit message

assignment lease snapshot <----> live lease file
legacy assignment proof verifier + semantic verifier
advisory baseline policy + independent mutation checks
```

## After

```text
GitLab + repositories
        |
        v
collector.py
  source facts only
        |
        v
inventory.json --schema validated--+
                                      |
                                      v
classifier.py -> graph.py -> execution-graph.json
                    |          classification, dependencies,
                    |          queue, scheduler state, queue-zero
                    v
                 cycle.py -> dispatcher.py -> workers.py
                    |              |
                    +------ MutationGate ------+
                           canonical lease      |
                                                v
                               repository/GitLab effects

SlackProjection -> roadmap semantic record -> persistent Block Kit message
commands.py ----- fresh inventory+graph --------^

completion receipt / historical adapter -> one verification_result validator
AccountingLedger -> one append-only model-attempt history
Lifecycle registry -> one assignment-state vocabulary
Schema registry -> validated reads and atomic validated writes
```

## Authoritative Components

| Concern                  | Authority                                                      | Decision                                                                                                                                                                                                      |
| ------------------------ | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Mutation                 | `axis_supervisor.mutation.MutationGate`                        | Default deny. Baseline/proof bootstrap policy is retired. Global control, exact authority, repository allowlist, governance state, canonical lease, operation class, source and budget are executable checks. |
| Verification             | `axis_supervisor.verification`                                 | `verification_result` is the only verification model. Current assignments emit `completion_receipt`; historical assignments are compatibility-adapted into the same model.                                    |
| Reporter                 | `axis_supervisor.slack_projection.SlackProjection`             | Sole report producer and Slack delivery owner.                                                                                                                                                                |
| Runtime schemas          | `axis_supervisor.schema_registry` plus `schemas/*.schema.json` | Records are validated on load and before atomic write. Incompatible versions and corrupt/partial records fail closed.                                                                                         |
| Source facts             | `axis_supervisor.collector`                                    | Collects normalized facts and dependency edges only.                                                                                                                                                          |
| Classification and queue | `axis_supervisor.classifier` and `axis_supervisor.graph`       | Graph owns classification, ranking, executable queue, observed scheduler state and queue-zero proof.                                                                                                          |
| Lease                    | `leases/<lease-id>/lease.json`                                 | Sole mutable lease authority. Assignments contain only lease ID and URI.                                                                                                                                      |
| Lifecycle                | `axis_supervisor.lifecycle`                                    | One canonical `lifecycle_state`; legacy state/phase values are read-only migration inputs.                                                                                                                    |
| Model accounting         | `axis_supervisor.accounting.AccountingLedger`                  | One append-only ledger with model, provider, role, run, assignment, attempt, result and optional usage.                                                                                                       |
| Slack commands           | `axis_supervisor.command_registry`                             | CLI parsing, plugin parsing and generated help share one command/authority/confirmation registry.                                                                                                             |

## Policy Decision

The old baseline was a noncanonical bootstrap snapshot and was not a valid
mutation prerequisite after semantic authority, canonical leases and the
operation gate existed. It is removed rather than preserved as advisory text.
No safety guarantee is lost: every external effect is denied unless the
executable `MutationGate` authorizes the exact operation and lease.

The `resume` command resumes observation/execution mode but does not enable
repository mutation. Mutation requires a separate source change or explicitly
authorized control operation after the enablement gate is satisfied.

## Runtime Migration

| Record                | Migration                                                                                                                                                                    |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Control               | Version 3 removes proof/baseline and unread fields, renames reporter freshness, and forces observing mode with mutation false.                                               |
| Inventory             | Regenerated from live source facts. Old embedded classification, queue and graph fields are not migrated.                                                                    |
| Execution graph       | Regenerated from the new inventory and actual accounting budget.                                                                                                             |
| Assignment            | Legacy state/phase and schema-less proof records are adapted to canonical lifecycle/schema in memory; new writes use `lifecycle_state`, lease ID/URI and completion receipt. |
| Lease                 | Valid live leases use the canonical schema. Invalid, corrupt or expired directories are moved to `stale-*` recovery custody.                                                 |
| Semantic record       | Existing records remain schema validated. A bounded compatibility fingerprint preserves pre-convergence source-evidence keys; new records use source-fact fingerprints.      |
| Slack semantic record | Regenerated as schema version 1.2 from matching inventory and graph generations.                                                                                             |
| Slack delivery state  | Legacy state is adapted once and then written as `slack-state` 1.0.                                                                                                          |
| Model attempts        | New calls use the ledger. Historical per-assignment counters remain historical evidence only.                                                                                |

## Deleted Or Retained

| Component                                                         | Disposition               | Reason                                                                          |
| ----------------------------------------------------------------- | ------------------------- | ------------------------------------------------------------------------------- |
| `scripts/report.py`                                               | Deleted                   | Retired reporter; no production caller.                                         |
| `schemas/report.schema.json`                                      | Deleted                   | Described only retired reporter records.                                        |
| report pending/delivery state                                     | Deleted during activation | Obsolete protocol.                                                              |
| `RepositoryConvergenceEngine`                                     | Deleted                   | No caller; source collection/classifier retain required convergence behavior.   |
| baseline defaults/schema/runtime file                             | Deleted                   | Advisory-only bootstrap policy retired.                                         |
| `CandidateSlice`, `SEMANTIC_CLASSES`, stale package `__version__` | Deleted                   | No callers; canonical definitions live in schemas/classifier/release `VERSION`. |
| unread control fields                                             | Deleted                   | Active control must not advertise ineffective configuration.                    |
| health CLI                                                        | Retained                  | Operator-only production health entrypoint.                                     |
| cycle/cron/control wrappers                                       | Retained                  | Operator and systemd entrypoints.                                               |
| Slack fallback skill                                              | Retained                  | Guides unsupported plain-text requests to deterministic `!axis` commands.       |
| `VERSION`                                                         | Retained                  | Deployed release marker and operator evidence.                                  |

## Production Call Graph

```text
Home Manager
  +-> hermes-gateway.service
  |     +-> axis-supervisor-commands plugin -> commands.py
  +-> hermes-supervisor-cron.service -> cronctl.py
        +-> worker cron -> preflight.py
        |     +-> supervisorctl.py recover
        |     +-> reconcile.py -> collector.py
        |     +-> cycle.py rebuild -> classifier.py -> graph.py
        |     +-> cycle.py run-next -> dispatcher.py -> workers.py -> integrator.py
        +-> reporter cron -> slack_projection.py -> SlackProjection -> reporting.py
```

## Proof Obligations

- Mutation denial: global disable, untrusted source, missing authority, wrong
  repository, wrong/expired/read-only lease, wrong fencing token, non-executable
  governance and exhausted model budget all deny.
- Verification: current completion receipts and historical proof adapters feed
  the same nine-check validator; missing pipeline, cleanup or fresh-cycle evidence
  cannot verify.
- Reporter: only the no-agent Block Kit cron writes the overview record/state.
- Queue: inventory contains facts only; graph contains the only executable queue
  and scheduler selection.
- Lease: assignment snapshots cannot authorize anything; every ownership and
  expiry check reloads the canonical lease record.
- Schema: control, inventory, graph/queue, assignment, lease, verification,
  semantic record, Slack overview/state and run records are executable schemas.

## Mutation Enablement

Recommendation after convergence: **deny broad mutation enablement**. The gate
and negative tests are necessary but not sufficient to authorize roadmap-wide
work. The implementation model is now a no-tool patch planner; path validation
occurs before tests, tests run networkless in bubblewrap, and deterministic gated
helpers own commit/publish/merge. A separately authorized bounded mutation proof
must exercise those controls against a disposable target before enablement.
Until that proof is approved, control remains observing with
`allow_repository_mutation=false`.

## After Metrics

- Source files: 65 (before: 55).
- Counted lines: 12,771 (before: 8,857).
- Tests: 61 (before: 34).
- Removed production modules: retired `report.py`, dead `convergence.py`.
- Removed schemas/policy files: report schema, baseline schema/defaults.
- Added focused authorities: lifecycle, mutation, accounting, schema registry,
  classifier and command registry.
- Added executable schemas: verification, run, model-attempt and Slack state.

The file/line increase is intentional rather than a cleanup score: executable
schemas, negative/fault tests, migration adapters and this decision record make
previously advisory or implicit behavior explicit. Production ownership paths
were reduced even though validation code and evidence increased.
