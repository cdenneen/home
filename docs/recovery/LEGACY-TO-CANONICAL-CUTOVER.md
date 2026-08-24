# Legacy source-fencing qualification and later authority transitions

**Status: FUTURE ONLY — PHASE B ADMISSION IS BLOCKED. DO NOT RUN.** This runbook defines the bounded `PHASE_B_FENCING_QUALIFICATION`. It does not authorize live access or mutation. It contains no Home activation, canonical writer start, migration, cutover, credential rotation, or evidence deletion.

**Explicit policy correction:** after the missing root-anchored execution/evidence tooling lands and admission is recalculated, a future separately granted Phase B may reversibly fence the exact legacy new-work writers and their reprovisioners before canonical deployment exists. That authority expansion is limited to the reviewed identities and safeguards below; it grants no general drain, activation, migration, or cutover authority.

## Authority-transition ledger

These transitions are distinct. Completion of one supplies evidence to the next admission decision; it never authorizes the next mutation.

| Transition                              | Purpose                                                                                                                                  | Permitted result                                                                                        | Not authorized by that result                                                                                     |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `PHASE_B_FENCING_QUALIFICATION`         | Deliberately fence legacy AXIS new-work writers and resurrection paths, prove no recurrence, and prove generic Hermes reconstructability | `legacy_AXIS_new_work_writer=0`, `canonical_AXIS_writer=0`, independently verified source-fence receipt | Legacy Alpha0 Core/route/report drain, Home activation, canonical start, graduation, migration, cutover, deletion |
| `ACTUAL_LEGACY_DRAIN`                   | Later quiesce non-writer reporters/gateways and settle retained interaction/state custody                                                | Accepted source drain with generic continuity                                                           | Home activation or canonical authority                                                                            |
| `CANONICAL_HOME_COMPOSITION_ACTIVATION` | Later activate an exact reviewed dormant composition                                                                                     | Dormant generation accepted; canonical controllers/writers still zero                                   | Canonical scheduler/controller start                                                                              |
| `CANONICAL_CONTROLLER_GRADUATION`       | Later enable one named canonical capability                                                                                              | One explicitly authorized canonical capability                                                          | Other writers, Alpha0 activation, or cutover                                                                      |
| `CUTOVER`                               | Accept an exact one-authority handoff for one logical role                                                                               | Signed one-authority receipt and rollback boundary                                                      | Evidence deletion, credential rotation, backend or host migration unless separately authorized                    |

Current state:

```text
PHASE_B_EXECUTION_TOOL = NOT_IMPLEMENTED
PHASE_B_RECEIPT_VERIFIER = NOT_IMPLEMENTED
PHASE_B_ADMISSION = BLOCKED
PHASE_B_FENCING_QUALIFICATION = NOT_EXECUTED
ACTUAL_LEGACY_DRAIN = NOT_AUTHORIZED
CANONICAL_HOME_COMPOSITION_ACTIVATION = NOT_AUTHORIZED
CANONICAL_CONTROLLER_GRADUATION = NOT_AUTHORIZED
CUTOVER_READY = NO
```

For each logical role, the safety invariant is always `not (legacy_writer and canonical_writer)`. Phase B establishes a deliberate zero-new-work-writer state for AXIS only; legacy Alpha0 Core/route/report continuity is unchanged until `ACTUAL_LEGACY_DRAIN`.

## Solo-maintainer verification roles

```text
PRODUCT_OWNER = sole human authority and mutation authorizer
HUMAN_SECOND_PARTY_REVIEW = NOT_APPLICABLE
INDEPENDENT_EXACT_HEAD_MODEL_REVIEW = separate evidence gate
MACHINE_VERIFICATION = required
```

The Product Owner names the maintenance window and incident command. An independent exact-head model review verifies the immutable command plan and becomes stale after any change. An off-host collector and machine verifier independently check evidence continuity and acceptance predicates. Model and machine evidence are never described as human approval and never grant mutation authority.

## Phase B scope

Phase B may:

- create fresh owner-only backups and verify disposable restoration;
- perform GET-only custody and external-identity observations;
- lock and validate all six live scheduler registries and expected service identities;
- disable exact legacy new-work provision/recovery paths;
- pause exact legacy new-work/recovery records, including both physical checkout copies;
- wait for already-admitted work and pending effects to reach durable zero;
- establish signed `F0` and run the `24h15m` off-host no-resurrection observation;
- prove generic Hermes reconstruction in a disposable fresh runtime.

Phase B must not:

- run `home-manager switch` or change the Home generation;
- stop generic Hermes or its generic stuck-cron watchdog;
- activate canonical axis-control, Alpha0, a canonical scheduler, or any canonical writer;
- perform legacy interaction/session drain beyond the exact writer/recovery fence;
- migrate a host, database, backend, Hermes DB/session history, or Alpha0 Core;
- mutate GitLab, rotate/revoke credentials, delete forensic evidence, or perform broad Home rollback.

## B0 — authorization and source-fence baseline

All gates pass before the first live mutation. Any mismatch means zero mutation and abort.

### Required gates

1. Product Owner authorization names the exact Home main/runbook tree and permits only Phase B.
2. Canonical producer revisions and required Home current-main CI are verified. No canonical dispatcher, scheduler, mutator, canonical Alpha0 Core, or canonical/new Alpha0 gateway is enabled. The observed legacy Alpha0 gateway may remain until `ACTUAL_LEGACY_DRAIN`.
3. Fresh owner-only backups cover all source Hermes roots, Alpha0 Core, Ghost-local refs/worktrees, controller state, and other forensic evidence required for recovery. Supported/disposable restore checks pass. Backups precede every unit/job/state change, not merely deletion.
4. A fresh complete GET-only custody map proves `AXIS_REMOTE_CUSTODY = 9/9`, no consequential `UNKNOWN`, no newly emerged local-only lineage, and an approved disposition for every retained local artifact.
5. External identity evidence proves bot/app/app-token correspondence and configured delivery ownership for the expected generic principal, a distinct expected Alpha0 principal, and no dedicated AXIS external route, without exposing credentials or destination values. Historical generic origins have an explicit archive/start-fresh disposition. Any identity, delivery, or route ambiguity aborts before mutation.
6. Exactly the six reviewed regular, non-symlink `jobs.json` paths exist with expected ownership, mode, static job identities, cadences, and physical cardinality. Historical snapshots are archive evidence, never registries.
7. Expected gateway, writer, provisioner, watchdog, service, timer, listener, process, and user-manager boot identities match the reviewed inventory. Unknown consequential state aborts.
8. The exact command plan and immutable Nix-store executable paths/closures/digests have fresh independent exact-head model review and machine validation. Root-trusted signer identities/namespaces, owner-only transcript storage, and continuous off-host audit/journal/provider collection are ready.
9. Rollback identities are exact and minimal. Broad job resume and broad Home rollback are prohibited.

### `SOURCE_FENCE_BASELINE_EVIDENCE`

The signed immutable baseline binds only source fencing:

- Home main SHA and runbook blob/tree SHA;
- source host, boot ID, user-manager start identity, wall clock and monotonic clock;
- all six registry metadata, allowlisted semantic digests, and owner-only raw HMACs;
- expected job/service/timer/process/listener and route identities;
- custody/frontier digest, pending/in-flight counts, and local-only classification;
- backup/restore receipts and retention location identities;
- expected fence actions, rollback identities, immutable command paths/closures/digests, signer identities/namespaces, observation cadence, and continuous collector identities/cursors;
- exact generic and Alpha0 external-route/service/session identity results, including the no-dedicated-AXIS-route result.

It does **not** establish canonical deployment.

### Separate later evidence

`CANONICAL_DEPLOYMENT_ATTESTATION` binds a future destination package/config closure, canonical controller revisions, deployment identity, scheduler authority, managed secret references, and activation provenance. It is not a Phase B prerequisite and Phase B cannot create it.

### Required implementation before Phase B can be authorized

The policy and identities below are reviewed, but the root-anchored execution/evidence tool does not exist on current main. Ad hoc shell translation is forbidden.

```text
PHASE_B_EXECUTION_TOOL = NOT_IMPLEMENTED
PHASE_B_RECEIPT_VERIFIER = NOT_IMPLEMENTED
PHASE_B_ADMISSION = BLOCKED
```

A future bounded implementation PR must provide one fail-closed tool and runnable tests that prove all of the following before this runbook can become executable:

1. a trust anchor in a root-owned, non-operator-renamable directory binds allowed signer identities/namespaces, exact immutable executable closures/digests, expected source identities, the runbook tree, and the receipt/evidence schemas;
2. the trust anchor is checked before any signature verifier or other executable is trusted; no command is resolved through operator `PATH` and no baseline may self-declare its verifier;
3. all six registry paths are opened with no-follow stable file descriptors, are distinct regular-file device/inode identities, have exact owner/mode, parse as strict duplicate-free typed JSON, and match a signed complete-document baseline;
4. every registry and expected unit/process identity is preflighted before the first mutation; any mismatch produces zero mutation;
5. exact source units are identified by pre-mask fragment path/digest and state, then reversibly runtime-masked; persistent masks, failed/not-found units, unknown trigger edges, and unowned processes abort;
6. each Hermes pause uses its internal lock, then all six registries are revalidated against the signed baseline with only the cumulative target `enabled: false` changes allowed; partial application is recorded and can never issue a receipt;
7. rollback records exact achieved state, never broad-resumes jobs/recovery/Home, and invalidates the attempt after any restoration;
8. a root-trusted schema/verifier consumes actual B0-B6 artifacts, expected live/source identities, complete event-chain cursors, and signature material; it cannot accept digest-shaped placeholders, caller-supplied time, unverified prior links, or mutable verifier paths;
9. tests cover signature trust rooting, path replacement/TOCTOU, hard-link/cardinality aliases, strict JSON type/duplicate handling, unit masks, partial failure after every mutation, stale evidence, identity mismatch, missing artifacts, and receipt-chain verification;
10. observation tests cover collector disconnect/reconnect, cursor gaps/replay/rollback/rotation, provider-history unavailability, reboot, user-manager/Home identity changes, and wall/monotonic clock discontinuity;
11. rollback tests execute every permitted partial-state recovery path and prove durable pre/post-mutation journal recovery, exact achieved-state accounting, no broad resume, no dual writer, and mandatory baseline/receipt invalidation.

### Six physical registries

The tool must lock, validate, and continuously account for these distinct live files; historical snapshots are excluded:

1. `$HOME/.hermes/cron/jobs.json`
2. `$HOME/.hermes/profiles/axis-control/cron/jobs.json`
3. `$HOME/src/workspace/work/axis-control/.hermes/cron/jobs.json`
4. `$HOME/src/workspace/work/axis-control/.hermes/profiles/axis-control/cron/jobs.json`
5. `$HOME/.local/share/alpha0/hermes/cron/jobs.json`
6. `$HOME/.local/share/alpha0/hermes/profiles/alpha0/cron/jobs.json`

Raw scheduler documents, commands, prompts, destinations, outputs, errors, credentials, and low-entropy route values remain owner-only. Public evidence is allowlisted metadata only.

## B1 — required future reprovisioner fence

Fence resurrection paths before any writer record. The implementation must preflight, stop/disable where applicable, and runtime-mask these exact identities while preserving `hermes-gateway.service` and `hermes-stuck-cron-watchdog.timer`:

- `hermes-supervisor-cron.service`;
- `hermes-watchdog-cron.service`;
- `hermes-watchdog-cutover.service`;
- `axis-development-watchdog-backup.timer` and `.service`;
- `axis-development-watchdog-monitor.service`;
- `hermes-axis-control-scheduler-watchdog.timer` and `.service`.

Acceptance requires every fenced unit inactive and runtime-masked, all underlying pre-mask fragment identities bound to the signed baseline, all expected processes absent, both preserved generic units healthy, and all six registries unchanged. No static or persistent-mask shortcut is accepted.

## B2 — required future writer pauses

Pause, never delete, these exact physical records:

- generic root `bb8d50dc3332` and `a9c0b0e9bcca`;
- checkout root `81776a5f93c5`;
- checkout axis-control profile `81776a5f93c5`.

The shared roadmap ID identifies two physical records, not one authority. After each internal-locking Hermes CLI pause, the tool must atomically record the achieved step and revalidate all six registries. Keep the generic AXIS PO reporter and Alpha0 observer/report schedules unchanged; they are not AXIS new-work writers and legacy Alpha0 authority is not drained. Stopping their gateways belongs to `ACTUAL_LEGACY_DRAIN`.

Record a preliminary admission-stop timestamp only after B1/B2 acceptance. It is not `F0`.

## B3 — converge existing custody without interruption

Across at least two former five-minute intervals:

1. admit no new legacy assignment or claim;
2. let existing workers, reviewers, reconcilers, and CI finish or reach an approved durable boundary, then require every legacy worker/reviewer/reconciler descendant capable of an effect to exit before `F0`; durable custody alone is not descendant quiescence;
3. reconcile all nine lineages plus any new lineage through complete GET-only GitLab reads;
4. require pending and in-flight local effects to reach zero without deleting rows to manufacture zero;
5. classify all board/worktree/ref residue as remotely durable, approved owner-only evidence, or `LEGACY_PROJECTION_ONLY`;
6. require two complete stable canonical reads with the same normalized frontier digest and `NO_OP`.

Any new writer claim, missing page/surface, local-only lineage, unowned process, or consequential `UNKNOWN` aborts. Do not kill active work and do not mutate GitLab.

## B4 — establish signed `F0`

Establish `F0` only when all predicates pass simultaneously:

```text
legacy_AXIS_new_work_writer = 0
legacy_AXIS_reprovisioner = 0
legacy_effect_capable_descendant = 0
pending_local_effects = 0
canonical_AXIS_writer = 0
legacy_ALPHA0_authority = UNCHANGED_NOT_DRAINED
AXIS_REMOTE_CUSTODY = 9/9
generic_gateway = healthy and same start identity
generic_stuck_cron_watchdog = healthy
six_registry_validation = pass
HOME_GENERATION_CHANGED = false
```

Capture signed scheduler, route, service/process, GitLab frontier, custody, backup, boot/user-manager, and generic-continuity digests at `F0`.

## B5 — `24h15m` off-host observation

An off-host collector samples every two minutes from `F0` through `F_END >= F0 + 24h15m`, crossing the next Alpha0 daily boundary and one additional maximum 15-minute recovery cadence. Polls alone cannot prove continuous absence. Before `F0`, root-owned audit/process-accounting rules and persistent user-journal forwarding must continuously capture, chain, and forward off-host: process exec/exit for the source UID/cgroups; writes/replacements of all six registries and scheduler databases; systemd unit/timer/mask/start transitions; Hermes writer/recovery invocations; and generic/Alpha0 provider connection-ownership events using monotonic provider event IDs. The collector starts from signed audit/journal/provider cursors, records every cursor transition in an append-only chain, and reconciles durable scheduler databases and sanitized journals for the whole interval at the end. If any platform cannot supply complete event history, Phase B is blocked rather than inferred from polling.

Acceptance requires:

- no post-`F0` claim by any fenced writer/recovery record in either checkout database or generic root;
- no fenced unit, timer, provisioner, watchdog, writer, or effect-capable legacy worker/reviewer/reconciler descendant reappears;
- the stale checkout-root record never becomes effective;
- no canonical writer, scheduler, dispatcher, mutator, canonical Alpha0 Core, or canonical/new Alpha0 gateway appears; the unchanged legacy Alpha0 gateway is observed until later drain;
- custody remains 9/9 with no new local-only lineage or unattributed GitLab change;
- generic Hermes retains its start identity, route owner, sessions aggregate, and healthy stuck-cron watchdog without unexpected restart;
- the unchanged legacy Alpha0 gateway retains the exact accepted service-start, external route owner, profile, and session aggregate identity; any Alpha0 route/service/session drift invalidates global route proof;
- all six registries remain present and match the accepted disabled/unchanged semantics.

A reboot, user-manager restart, Home generation change, clock discontinuity, registry mutation, audit/journal/provider cursor gap or rotation loss, collector disconnect, `UNKNOWN`/failed sample, writer/reprovisioner/descendant recurrence, unexpected authority, generic restart/session loss, Alpha0 route/service/session drift, or unattributed frontier change invalidates the run. Preserve evidence and require a new baseline and new `F0`; do not reset timestamps and continue. After `F_END`, continuous forwarding and two-minute sampling remain active until later receipt consumption. A future root-trusted receipt must be no older than five minutes and cryptographically bind the uninterrupted chain through consumption. Refresh/link semantics are not yet implemented; no receipt may currently be issued. A gap or identity change requires a fresh Phase B run.

## B6 — disposable generic Hermes reconstruction

Separately prove fresh reconstruction from exact current-main VCS plus managed external secret materialization and fresh runtime state:

1. use two independent mode-`0700` disposable homes;
2. build/render the exact reviewed Home/Hermes input without activating Ghost;
3. materialize the external environment file owner-only from managed authority; validate required variable names and restrictive mode without emitting values, prefixes, or hashes;
4. create the non-secret generic config/profile/plugin declarations in each disposable home;
5. deny gateway start, network/provider/send, scheduler execution, service-manager mutation, GitLab mutation, and access to source Hermes homes;
6. import zero session files, execution/scheduler DBs, route caches, gateway state, process state, locks, WAL/SHM, or historical Kanban;
7. verify generic route ownership/config semantics and generic stuck-cron declarations;
8. require equal sanitized semantic digests across both reconstructions.

Together with B0's exact live bot/app/app-token/delivery proof and accepted archive/start-fresh dispositions, this may close `EXTERNAL_ROUTE_IDENTITY`, current `ROUTE_OWNERSHIP`, and `HERMES_SEMANTIC_RESTORE` only for the reviewed empty required semantic migration set. It proves reconstructability, never `CANONICAL_DEPLOYMENT_ATTESTATION`, route handoff, Home activation, imported historical-session continuity, or destination authority.

## Phase B acceptance and receipt

Phase B could pass only when B0–B6 pass and an independently reviewed root-trusted verifier accepts a signed receipt. That schema/verifier/evidence collector is intentionally **not invented in this documentation PR** and remains a hard implementation prerequisite. It must enforce every requirement listed under "Required implementation before Phase B can be authorized," including exact expected identities and actual artifact presence/consistency rather than digest-shaped placeholders.

Until that separate bounded implementation lands with runnable adversarial tests, the following block is a required **future output contract**, not an issuable receipt:

```text
PHASE_B_FENCING_QUALIFICATION = PROVEN
SOURCE_FENCE_BASELINE_CONTRACT = PROVEN
EXTERNAL_ROUTE_IDENTITY = PROVEN
ROUTE_OWNERSHIP = PROVEN
DUPLICATE_SCHEDULER_TOPOLOGY = PROVEN
GENERIC_ROUTE_RECONSTRUCTION = PROVEN
HERMES_SEMANTIC_RESTORE = PROVEN
LIVE_FENCING_OBSERVATION = PROVEN
AXIS_REMOTE_CUSTODY = 9/9
LEGACY_AXIS_NEW_WORK_WRITER = 0
CANONICAL_AXIS_WRITER = 0
LEGACY_ALPHA0_AUTHORITY = UNCHANGED_NOT_DRAINED
HOME_GENERATION_CHANGED = false
CANONICAL_DEPLOYMENT_ATTESTATION = NOT_ESTABLISHED_BY_PHASE_B
CANONICAL_COMPOSITION_ACTIVATED = NO
CANONICAL_AXIS_CONTROL_ACTIVE = NO
CANONICAL_ALPHA0_ACTIVE = NO
SAFE_DRAIN_READY = YES
CUTOVER_READY = NO
```

Current admission remains:

```text
PHASE_B_EXECUTION_TOOL = NOT_IMPLEMENTED
PHASE_B_RECEIPT_VERIFIER = NOT_IMPLEMENTED
PHASE_B_ADMISSION = BLOCKED
PHASE_B_FENCING_QUALIFICATION = NOT_EXECUTED
LIVE_FENCING_OBSERVATION = NOT_STARTED
SAFE_DRAIN_READY = NO
CUTOVER_READY = NO
```

The future `SAFE_DRAIN_READY = YES` output means only that a later deliberate legacy drain may be considered. It does not authorize drain, activation, graduation, cutover, migration, deletion, or credential action. Return control to the Product Owner.

## Abort and rollback boundaries

### Before `F0`

On any mismatch, stop with zero mutation where possible. After a partial B1/B2 fence, record the exact achieved unit/job set and issue no Phase B receipt; do not claim zero AXIS new-work writers until every B4 predicate passes. Either preserve the partial safe fence for incident review or restore only exact baseline components under explicit Product Owner incident authorization. Any restoration invalidates the attempt and requires a fresh signed baseline. Never broad-resume registries or recovery timers.

### During observation

Any rollback or recurrence invalidates `F0`. Restore only generic gateway/stuck-cron continuity if needed; do not restore AXIS/Alpha0 authority as a shortcut. Preserve all evidence and start over from a fresh baseline after incident resolution.

### After a Phase B receipt

The receipt is bound to source boot/user-manager/Home-generation, six-registry, custody/frontier, generic and Alpha0 route/service/session, and signed-baseline identities. Any change or recurrence invalidates it. A later action requires a fresh admission check and separate Product Owner authorization.

## Later transitions — separately reviewed and authorized

### `ACTUAL_LEGACY_DRAIN`

May later pause non-writer reporters/Alpha0 schedules and stop dedicated AXIS/Alpha0 gateways only after interaction, session, backup, route, and custody gates pass. It must preserve generic Hermes and forensic evidence.

### `CANONICAL_HOME_COMPOSITION_ACTIVATION`

May later require a replacement current-main dormant composition, exact current producer pins, root-owned `CANONICAL_DEPLOYMENT_ATTESTATION`, activation-package closure, managed destination secrets, and a surgical rollback generation. No executable activation command belongs to Phase B.

### `CANONICAL_CONTROLLER_GRADUATION`

May later enable one named canonical controller/scheduler capability only through its own reviewed producer/Home change, managed credentials, mutation-specific tests, signed authority identity, and fresh proof that legacy authority remains zero.

### `CUTOVER`

May be accepted only for a named logical role after source inability to admit/write, destination sole authority, exact route/scheduler/writer ownership, signed receipt, and safe rollback boundary are all proved. Fencing, zero writers, Home activation, or graduation alone is not cutover.
