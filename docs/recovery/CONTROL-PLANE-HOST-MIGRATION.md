# Control-plane host migration

**FUTURE ONLY — DO NOT RUN.** This is an acceptance design, not authorization. It performs no service, scheduler, credential, provider, database, GitLab, or deployment mutation.

## Scope and logical identities

- `AXIS_CONTROL/source`: the currently authorized axis-control runtime, if any.
- `AXIS_CONTROL/destination`: an authorized replacement host prepared while disabled.
- `ALPHA0/source`: the sole current Alpha0 Core/database writer and dedicated interaction owner.
- `ALPHA0/destination`: an authorized replacement host prepared while disabled.
- `AXIS_CORE`: remains independent and may stay on its existing runtime host.

The destination hostname is deliberately unspecified. The source and destination become authorities only through signed acceptance, never because of their names or installation state.

This plan preserves:

- GitLab as AXIS execution truth;
- `AXIS_CONTROL` as the sole AXIS SDLC authority;
- `ALPHA0` as read-only AXIS supervisor and interaction authority without AXIS dispatch, repair, review, merge, or custody rights;
- Alpha0 SQLite as the existing durable architecture;
- one active writer, scheduler, and gateway route owner at a time.

This plan excludes final host placement, AXIS backend placement, database-backend conversion, legacy workspace transfer, live execution, provider mutation, credential values, scheduler payloads, and any implicit activation. Host migration and database migration are separate transitions. **Alpha0 remains SQLite throughout.**

## Boundary with Phase B source fencing

Host migration may eventually consume a fresh independently verified `PHASE_B_FENCING_QUALIFICATION` receipt; current Phase B admission is `BLOCKED` because the root-anchored execution/evidence tool and receipt verifier are not implemented. No receipt is currently issuable. This migration plan does not define, execute, or retroactively validate Phase B. The receipt proves legacy AXIS new-work writer/reprovisioner absence, custody preservation, unchanged legacy Alpha0 authority, generic continuity, and generic reconstructability for an exact boot/user-manager/Home-generation baseline. It does not prove destination deployment.

Any future signed receipt must be no older than five minutes and cryptographically link an uninterrupted off-host audit/journal/provider event chain from `F0` through consumption. Refresh/link semantics require their own implemented, root-trusted schema and verifier; documentation alone cannot satisfy this gate. Reboot, user-manager restart, Home generation change, evidence/cursor gap, registry/custody/route/service/session drift, or source recurrence requires a fresh source-fence baseline and full Phase B qualification before migration continues.

## Global authorization gate

Before either product sequence begins, all of the following must exist:

1. named source/destination logical-role bindings, maintenance window, and Product Owner incident command; independent exact-head model review and machine verification are separate evidence gates, not second-human approval;
2. exact reviewed producer and deployment commits, exact closures, current required CI, and fresh independent exact-head model review after the last change;
3. root-owned signed deployment manifests/trust binding source, package, interpreter, configuration references, entrypoints, and scheduler identity;
4. owner-only transcript/evidence storage with public reports restricted to sanitized metadata;
5. fresh inventory of source services, timers, all scheduler registries, route owners, active custody, durable state, secret references, and destination network dependencies;
6. complete managed secret mappings, without values, and provider/OAuth rotation or reauthorization plans;
7. destination runtime directories mode `0700`, secret/database/state files mode `0600` or stricter, and proof that no secret/database/session enters Git or the Nix store;
8. fresh supported backup, disposable restore, rollback generation/package, and capacity evidence;
9. no `UNKNOWN` custody, route, deployment, secret, or durable-state fact;
10. separate written authorization for every future mutating phase.

Installation success never authorizes stopping source authority.

---

## `AXIS_CONTROL` migration sequence

### Phase AC-1 — consume and revalidate the Phase B receipt

**Preconditions**

- Global gate passes.
- An implemented root-trusted receipt verifier proves the exact source boot, user-manager, Home-generation, command closure, six-registry, generic/Alpha0 route-service-session, and custody identities, and is no older than five minutes.
- The receipt's `24h15m` observation and generic reconstruction proof passed.

**Future-only action**

Perform read-only no-recurrence checks and verify uninterrupted audit/journal/provider cursors through the current sample. Do not repeat or broaden the source fence inside host migration. Confirm every fenced AXIS writer/reprovisioner remains masked/inactive, every effect-capable descendant remains absent, generic continuity and unchanged Alpha0 authority remain identity-stable, and no canonical AXIS writer appeared after the receipt.

**Acceptance**

- Phase B receipt is root-trusted, schema-valid, at most five minutes old by its signed `observed_through_at`, identity-matched, and linked to a gap-free continuous evidence chain and any prior receipt.
- No recurrence, registry drift, custody regression, Home/user-manager change, or unexplained frontier change exists.

**Abort**

Abort on stale/mismatched receipt, recurrence, unexpected identity, route loss, or custody regression. Obtain a new Product Owner Phase B grant rather than repairing the fence inside migration.

**Rollback**

No migration mutation has occurred. Preserve the qualified AXIS zero-new-work-writer state and Phase B evidence; legacy Alpha0 authority remains unchanged. Restore only exact generic continuity under incident authorization.

### Phase AC-2 — refresh custody and canonical truth

**Preconditions**

- AC-1 receipt revalidation passes.
- No legacy or canonical AXIS new-work writer is active; legacy Alpha0 authority remains unchanged/not drained.

**Future-only action**

Re-observe every captured and newly discovered lineage after the Phase B receipt. Classify each exactly one of:

- `REMOTE_COMPLETE`;
- `REMOTE_IN_FLIGHT`;
- `LOCAL_UNPUSHED` with approved preservation/adoption;
- `LEGACY_PROJECTION_ONLY`;
- `UNKNOWN`, which blocks progress.

Bind project/work item, PlanningRecord revision/digest, assignment, branch, local/remote head, MR IID/head, exact-head pipeline, review state, current-main evidence, worker identity, and pending event identity. Let in-flight operations finish naturally. A unique local commit must be pushed through normal reviewed custody, adopted through an explicit record, or preserved owner-only before controller authority stops.

**Acceptance**

- No lineage remains `UNKNOWN`.
- No local-only commit is silently discarded.
- No active controller/reconciler operation remains.
- Two complete GET-only canonical observations have the same digest and the second is `NO_OP`.

**Abort**

Abort on incomplete GitLab surfaces, local/remote disagreement without disposition, owner loss, unbounded process, or changed exact head.

**Rollback**

Keep source fence and evidence intact; correct custody through normal review. If continuity requires resuming source admission, do so only through separately authorized exact-component rollback; invalidate the receipt and repeat Phase B before migration resumes.

### Phase AC-3 — classify controller state

**Preconditions**

- AC-2 custody acceptance passes.
- State inventory is complete and owner-only backups exist.

**Future-only action**

- Recreate source, schema, configuration, profiles, prompts, skills, and scheduler definitions from reviewed VCS/deployment.
- Reconstruct project/issue/MR/branch/pipeline/frontier/remote custody from GitLab.
- Transfer only canonical pending event-journal records that cannot safely be reconstructed, and only through a versioned integrity-checked export/import contract.
- Archive legacy board/task/checkpoint/rootless-workspace state as evidence.
- Discard caches, temporary worktrees, process state, locks, and heartbeats only after retention acceptance.

**Acceptance**

- `AXIS_CONTROL/destination` bootstraps without a legacy workspace or local `AXIS_CORE` filesystem.
- Imported events cannot duplicate effects already visible in GitLab.
- Unknown, corrupt, orphaned, or acknowledged events fail closed.

**Abort**

Abort if any proposed imported record lacks stable identity, schema/version, integrity, pending status, or idempotency proof.

**Rollback**

Do not import. Preserve the source journal owner-only and keep destination action paths disabled; GitLab reconstruction remains read-only.

### Phase AC-4 — prepare destination disabled

**Preconditions**

- AC-3 classification passes.
- Managed `GITLAB_TOKEN`, PO identity, signed trust, and required profile references are complete.

**Future-only action**

Install the exact reviewed source/package closure. Materialize owner-only controller/Hermes directories and configuration from reviewed definitions. Provision secrets from existing Home SOPS/sops-nix authority. Keep gateway, scheduler, dispatch, mutation, repair, review, and merge paths disabled. Verify direct GitLab DNS/HTTPS, root-owned trust, executable/interpreter identity, and scheduler declaration. Run two GET-only bootstraps.

**Acceptance**

- Source/package/configuration match signed exact revisions.
- Direct GitLab bootstrap is complete, stable, GET-only, and non-actionable where review/custody/current-main evidence is incomplete.
- No local AXIS checkout, `AXIS_CORE` filesystem, custom-port relay, or source-host path is required.

**Abort**

Abort on deployment mismatch, missing secret mapping, insecure mode, network failure, unstable observation, or any write attempt.

**Rollback**

Remove/disable the unaccepted destination runtime without changing source or GitLab. Retain only sanitized failure evidence.

### Phase AC-5 — compare source and destination custody

**Preconditions**

- AC-4 stable observations pass.
- Phase B source fence remains valid; any surviving legacy continuity retains authority until separately drained.

**Future-only action**

Compare the complete custody tuple: project, work item, PlanningRecord revision/digest, assignment, branch, remote head, MR IID/head, exact-head pipeline, review state, current-main verification, and pending event identity. Remote-authoritative facts must be exactly equal. Every source-only projection or destination reconstruction difference needs an explicit non-authoritative disposition.

**Acceptance**

- Destination frontier/custody matches final source/GitLab truth.
- No transition is actionable solely because transport succeeded.
- A different local projection cannot authorize work.

**Abort**

Abort on any missing tuple member, digest instability, projection treated as authority, or unexplained difference.

**Rollback**

Keep destination disabled and the Phase B source fence intact; repeat canonical observation after resolving evidence, not by editing destination state.

### Phase AC-6 — `ACTUAL_LEGACY_DRAIN`

**Preconditions**

- AC-5 exact comparison passes.
- The Product Owner grants this drain separately from Phase B, activation, graduation, and cutover.
- Source rollback package is accepted; destination remains disabled.

**Future-only action**

Stop source controller gateway/scheduler authority after interactions and custody reach durable boundaries. Prove source jobs disabled, recovery timers unable to recreate them, controller/reconciler processes absent, no new source epoch, and no route/session owner still requiring source continuity.

**Acceptance**

- General source no-admit/no-write and route quiescence pass over the reviewed interval.
- No destination controller, gateway, scheduler, or writer is active.
- Generic Hermes continuity required outside AXIS remains healthy.

**Abort / rollback**

Abort on active ownership, recurrence, custody change, or generic-continuity loss. Before any destination activation, restore only the exact source component authorized by the rollback package; this invalidates downstream receipts and requires a fresh Phase B qualification.

### Phase AC-7 — `CANONICAL_HOME_COMPOSITION_ACTIVATION`

**Preconditions**

- AC-6 drain acceptance passes.
- Root-owned `CANONICAL_DEPLOYMENT_ATTESTATION`, exact activation closure, managed secrets, destination identity, and surgical rollback generation are accepted under a new Product Owner grant.

**Future-only action**

Activate the reviewed dormant Home composition. Start destination observer only and repeat stable GET-only reconstruction. Keep interaction, scheduling, reconciliation mutation, and canonical writer authority disabled.

**Acceptance**

- Destination observer matches final source/GitLab truth without writes.
- Source remains drained; no route or scheduler overlap exists.
- Activation provenance and rollback identity match the deployment attestation.

**Abort / rollback**

Abort on any write, overlap, source recurrence, digest change, or deployment mismatch. Roll back only the reviewed destination activation while preserving the source fence and forensic evidence.

### Phase AC-8 — `CANONICAL_CONTROLLER_GRADUATION`

**Preconditions**

- AC-7 observer acceptance passes.
- A separate Product Owner grant names the exact route, scheduler, writer, and rollback identities.

**Future-only action**

Enable destination interaction and scheduling in the reviewed order. Prove exactly one enabled `AXIS_CONTROL` scheduler identity and one gateway owner per `AXIS_CONTROL` route, with no duplicate branch/MR/custody/event identity.

**Acceptance**

- Canonical destination is the sole AXIS writer, scheduler, and route owner.
- Stable GitLab observation spans the graduation boundary.
- Source remains unable to admit work or resurrect authority.

**Abort / rollback**

Abort before destination admission on overlap, recurrence, digest change, or missing evidence. After destination admission, never simply restart source: quiesce destination and execute a reviewed reverse handoff with the same custody protocol.

### Phase AC-9 — `CUTOVER`

**Preconditions**

- AC-8 graduation and its observation window pass.
- The Product Owner separately accepts the complete migration receipt.

**Future-only action**

Record the signed migration receipt described below. Do not treat activation time as cutover time.

**Acceptance**

The receipt binds Phase B, actual drain, canonical deployment, activation, sole route/scheduler/writer authority, final canonical digest, graduation observation, rollback identity, and explicit Product Owner cutover acceptance.

---

## `ALPHA0` migration sequence

### Phase A0-1 — prepare destination disabled

**Preconditions**

- Global gate passes.
- Exact Alpha0 producer/Home revisions and canonical SQLite schema are accepted.
- Secret manifest has complete audit-key, Core, gateway, provider, Slack, API, and integration references.

**Future-only action**

Install exact reviewed Alpha0 package/module. Recreate Hermes configuration, routing shim, preflights, wrappers, prompts, skills, and disabled scheduler definitions from VCS/package. Provision separate Core and gateway secret maps through managed authority. Keep Core, gateway, schedules, providers, and any mutation disabled.

**Acceptance**

- No legacy checkout, source-host path, or mutable wrapper is referenced.
- Core/gateway secret domains are distinct.
- Owner/profile isolation, profile-aware session keys, clarification routing, loopback listeners, and no generic fallback pass.
- No destination process writes.

**Abort**

Abort on checkout coupling, failed preflight, secret-domain overlap, public listener, route overlap, or any write.

**Rollback**

Remove/disable the destination deployment; source remains unchanged and authoritative.

### Phase A0-2 — provisional SQLite rehearsal

**Preconditions**

- A0-1 passes.
- An approved owner-only backup tool and encrypted transport are available.

**Future-only action**

Use SQLite's online backup API while source remains available. Transfer the provisional backup and sanitized metadata through an authenticated encrypted channel to disposable destination storage. Validate integrity, foreign keys, migration history/checksums, schema shape, aggregate counts, structural audit chain, canonical read-only rendering, and network-denial behavior. Do not use this backup as cutover authority.

**Acceptance**

- Source is not file-copied, initialized, migrated, checkpointed, or modified.
- Existing SQLite schema is retained unchanged.
- Disposable validation passes without external mutation.
- Signed HMAC verification remains blocked unless the managed audit key is authorized and provisioned.

**Abort**

Abort on source metadata change caused by the procedure, integrity/schema drift, network attempt, migration, count change, or wrong-key acceptance.

**Rollback**

Delete disposable plaintext according to owner policy; retain only encrypted recovery evidence. Source remains live and unchanged.

### Phase A0-3 — qualify Hermes artifacts and integrations

**Preconditions**

- A0-2 passes.
- Every Hermes artifact has one disposition in `HERMES-STATE-MIGRATION.md`.

**Future-only action**

Recreate disabled Hermes declarations from VCS and dry-render one exclusive owner per route. Reconstruct projections from Alpha0 Core/provider evidence; archive source sessions/executions/ticker state under approved retention without importing them; discard ephemeral state only after retention review; reissue secrets. Only an owner-selected generic unprofiled session subset may receive a separate supported semantic backup/restore rehearsal after explicit continuity qualification. Classify each external integration as rotate/reissue, OAuth reauthorize, callback update, DNS update, or no host-bound change.

**Acceptance**

- No unprofiled legacy state overrides the fresh dedicated Alpha0 profile.
- Route/profile namespaces are exclusive without importing Alpha0 Hermes sessions or scheduler history.
- Scheduler definitions remain disabled with fresh runtime identities.
- One-owner Slack route proof is feasible; mode-`0644` legacy credential references are rotation-required.

**Abort**

Abort on route/profile collision, duplicate delivery, missing integration owner, callback uncertainty, route overlap, or any attempted import of unqualified Hermes state.

**Rollback**

Discard the disposable reconstruction; preserve source archives. Do not fall back to a bulk Hermes-home copy.

### Phase A0-4 — `ACTUAL_LEGACY_DRAIN`, final source quiescence, and backup

**Preconditions**

- A0-1 through A0-3 pass and the Phase B receipt remains valid.
- A separate Product Owner drain grant names exact Core/gateway/schedule identities, final backup destination, and rollback boundary; activation, graduation, and cutover remain unauthorized.

**Future-only action**

Pause sources of new Alpha0 scheduled work. Drain active interactions and bounded supervision. Stop source Core/gateway authority. Prove no database writer or scheduled invocation remains. Then create the final SQLite online backup and owner-only metadata binding source identity, completion time, migration versions, integrity/foreign-key result, audit-head metadata, and deployment revision. Transfer encrypted/authenticated; never use Git or Nix store.

**Acceptance**

- Final backup occurs after the last authorized source write.
- Source database metadata remains unchanged throughout the post-backup observation window.
- Source schedules/gateway cannot restart automatically.
- This final backup, not the provisional backup, is restore authority.

**Abort**

Abort on active interaction, writer, recurring schedule, changed source metadata, failed backup/integrity, or insecure transfer target.

**Rollback**

Before destination writes, stop destination and resume only the exact reviewed source deployment/route after verifying source database continuity. Repeat the final backup for a later attempt.

### Phase A0-5 — restore and validate destination

**Preconditions**

- A0-4 final backup and source no-write evidence pass.
- Destination data directory is newly created mode `0700`; database target is mode `0600`.

**Future-only action**

Restore the final SQLite backup. Validate integrity, foreign keys, migration history, schema shape, aggregates, and structural audit continuity. Provision the audit key via managed reference and perform signed Core-open/audit verification. Run canonical doctor/status/daily brief/operating brief with external mutation denied. Reconstruct disabled Hermes route/job declarations with fresh runtime state; restore only a separately qualified owner-selected generic session subset, if one exists. Keep jobs and gateway disabled.

**Acceptance**

- Destination audit-head metadata and aggregates match the final backup.
- Initialization performs no migration or count change.
- Missing/wrong audit key fails closed; authorized key permits signed read-only open.
- SQLite architecture remains unchanged.
- No provider, GitLab, Slack, or scheduler mutation occurs.

**Abort**

Abort on any integrity/audit/schema/count mismatch, migration, network mutation attempt, insecure mode, or Hermes semantic difference.

**Rollback**

Before any destination write, discard the destination restore and resume the exact source under A0-4 rollback. The source backup remains authoritative.

### Phase A0-6 — `CANONICAL_HOME_COMPOSITION_ACTIVATION`

**Preconditions**

- A0-5 passes.
- Root-owned `CANONICAL_DEPLOYMENT_ATTESTATION`, exact activation closure, destination identity, managed credentials, and rollback generation are accepted under a new Product Owner grant.

**Future-only action**

Activate the reviewed dormant composition. Start destination Core read-only/paused and verify signed audit open. Keep destination gateway, schedules, and writer authority disabled.

**Acceptance**

- Destination read-only state matches the accepted final backup and audit head.
- Source remains drained and no route, scheduler, or writer overlap exists.
- Activation provenance and rollback identity match the deployment attestation.

**Abort / rollback**

Abort on source recurrence, write, unsigned audit open, deployment mismatch, or route/scheduler appearance. Roll back only the reviewed destination activation while preserving source evidence and the final backup.

### Phase A0-7 — `CANONICAL_CONTROLLER_GRADUATION`

**Preconditions**

- A0-6 passes.
- A separate Product Owner grant names each integration action, route, schedule, writer, and rollback identity.

**Future-only action**

Start the destination gateway only after exclusive route proof. Restore schedules one at a time after exact wrapper/state qualification. Enable destination Core writes only at the reviewed boundary. Observe source across at least two former scheduler intervals and one gateway/watchdog interval.

**Acceptance: exact one-authority proof**

- Exactly one Alpha0 Core writer and one dedicated Alpha0 gateway exist.
- Source database remains unchanged after final backup and source cannot auto-restart.
- Destination continues from the accepted audit head without duplicate scheduled delivery.
- Generic and axis-control gateways do not absorb Alpha0 traffic.

**Abort / rollback**

Abort before destination write on recurrence, overlap, unsigned audit open, duplicate-delivery risk, missing integration evidence, or source database change. Before a destination write, restore source only through the reviewed rollback. After a destination write, never resume the old source database: quiesce destination, create/validate a new consistent backup, reverse-transfer encrypted, restore, and repeat one-writer proof.

### Phase A0-8 — `CUTOVER`

**Preconditions**

- A0-7 graduation and observation pass.
- The Product Owner separately accepts the complete migration receipt.

**Future-only action**

Record cutover acceptance without changing authority. Activation or first destination write is not itself cutover.

**Acceptance**

The signed receipt binds Phase B, actual drain/final backup, canonical deployment and activation, one-writer/one-route graduation evidence, observation result, rollback identity, and explicit Product Owner cutover acceptance.

## Signed one-authority migration receipt

Migration is incomplete until the Product Owner accepts a root-owned signed receipt after separate independent exact-head model review and machine verification. This is not a second-human approval. The metadata-only receipt contains:

- source and destination logical-role bindings;
- exact producer/deployment revisions, package/interpreter closures, and trust identity;
- Phase B fence, actual drain/stop/quiescence, activation, graduation, and cutover-acceptance timestamps plus observation results;
- final axis-control observation digest and custody summary;
- final Alpha0 backup completion, schema/migration, aggregate, and audit-head metadata without database hash or payload;
- destination service, scheduler, gateway, and route identities;
- secret-reference identities without values;
- Product Owner incident-command identity plus independent exact-head model-review and machine-verification evidence identities;
- last safe rollback boundary and whether destination admitted any write;
- explicit proof that source cannot admit/write work and destination is sole authority.

No secret, database/session payload, raw scheduler output, environment, log, or message may appear in the receipt.

## Expanded host-loss inputs

A fresh authorized host must be recoverable from:

- `AXIS_CONTROL`: reviewed VCS, pinned deployment definitions, managed secret references, GitLab truth, signed deployment trust, and any qualified non-reconstructible pending-event export;
- `ALPHA0`: reviewed VCS, pinned deployment definitions, managed secret references/audit key, final validated SQLite backup, reconstructed exclusive Hermes route/job declarations, any separately qualified owner-selected generic session export, and external integration identity records.

Ghost, legacy workspaces, local AXIS checkouts, caches, non-durable sessions, hand-edited scheduler state, PID/lock/heartbeat state, and runtime environment files are not valid recovery inputs.

## Current blockers

This design remains non-executable because managed cross-host supervision deployment, complete managed secret mapping, exclusive Hermes route reconstruction, signed deployment identity, live authority quiescence and exact destination/placement evidence are incomplete. The supervision contract is proven on merged current mains and through disposable network isolation; code custody is 9/9 remotely recoverable. Neither fact authorizes drain or cutover. `CUTOVER_READY` remains `NO`.
