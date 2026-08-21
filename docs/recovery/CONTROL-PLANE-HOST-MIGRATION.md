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

## Global authorization gate

Before either product sequence begins, all of the following must exist:

1. named source/destination logical-role bindings, maintenance window, incident commander, and independent verifier;
2. exact reviewed producer and deployment commits, exact closures, current required CI, and fresh exact-head approval after the last change;
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

### Phase AC-1 — freeze sources of new work

**Preconditions**

- Global gate passes.
- Exact scheduler/recovery identities match the signed sanitized inventory.
- All active workers, reviewers, CI runs, and local custody are mapped.

**Future-only action**

Pause only exact sources of new controller work. Disable exact watchdog/recovery paths that could recreate an epoch or job. Leave existing workers, reviewers, CI, and interaction needed for their custody uninterrupted.

**Acceptance**

- No new assignment appears for at least two former scheduler intervals.
- Existing workers/reviewers/CI continue to their natural durable boundary.
- Every expected job/timer is absent or disabled by exact identity.

**Abort**

Abort on new work after freeze, unexpected identity, automatic recreation, route loss needed by an active owner, or any interrupted worker/reviewer/CI.

**Rollback**

With incident-command approval, resume only the exact minimum source component needed for continuity. Never broadly resume all jobs. Rebuild the custody map from scratch.

### Phase AC-2 — converge custody and canonical truth

**Preconditions**

- AC-1 acceptance passes.
- No source of new work is active.

**Future-only action**

Re-observe every captured and newly discovered lineage. Classify each exactly one of:

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

Keep source freeze and evidence intact; correct custody through normal review. If continuity requires resuming source admission, do so only through a separately authorized exact-component rollback and repeat AC-1.

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
- Source is still frozen and retains authority.

**Future-only action**

Compare the complete custody tuple: project, work item, PlanningRecord revision/digest, assignment, branch, remote head, MR IID/head, exact-head pipeline, review state, current-main verification, and pending event identity. Remote-authoritative facts must be exactly equal. Every source-only projection or destination reconstruction difference needs an explicit non-authoritative disposition.

**Acceptance**

- Destination frontier/custody matches final source/GitLab truth.
- No transition is actionable solely because transport succeeded.
- A different local projection cannot authorize work.

**Abort**

Abort on any missing tuple member, digest instability, projection treated as authority, or unexplained difference.

**Rollback**

Keep destination disabled and source frozen; repeat canonical observation after resolving evidence, not by editing destination state.

### Phase AC-6 — one-authority handoff

**Preconditions**

- AC-5 exact comparison passes.
- Source rollback package and destination activation are independently approved.

**Future-only action**

1. Stop source controller gateway/scheduler authority.
2. Prove source jobs absent/disabled, recovery timers unable to recreate them, controller/reconciler processes absent, and no new source epoch.
3. Start destination observer only and repeat stable GET-only reconstruction.
4. Enable destination interaction/scheduling only in a separately reviewed activation.
5. Record the signed migration receipt described below.

**Acceptance: exact one-authority proof**

- Exactly one enabled `AXIS_CONTROL` scheduler identity exists.
- Exactly one gateway owns each `AXIS_CONTROL` route.
- Source cannot admit new work and remains inactive over at least two former scheduler intervals plus one watchdog interval.
- Destination does not duplicate branch/MR/custody/event identity.
- Stable GitLab observation digest spans the handoff boundary.
- Signed receipt binds source stop evidence, destination deployment, route/scheduler identity, final canonical digest, and activation time.

**Abort**

Abort before destination admission on route overlap, source process/job recurrence, digest change, deployment mismatch, or missing receipt fields.

**Rollback**

Before destination admits work, stop destination and restore the exact source authority with destination disabled. After destination admits work, never simply restart source: quiesce destination and execute a reviewed reverse handoff with the same custody protocol.

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

### Phase A0-4 — final source write quiescence and backup

**Preconditions**

- A0-1 through A0-3 pass.
- Maintenance authorization names exact Core/gateway/schedule identities and final backup destination.

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

### Phase A0-6 — integration route and one-writer handoff

**Preconditions**

- A0-5 passes.
- Each integration action and destination credential is complete.
- Source route and auto-recovery stop controls are independently verified.

**Future-only action**

Start destination Core read-only/paused and verify signed audit open. Start destination gateway only after exclusive Slack route proof. Restore schedules one at a time after exact wrapper/state qualification. Observe source across at least two former scheduler intervals and one gateway/watchdog interval. Record source service/timer/job inactivity and unchanged source database metadata before allowing destination progression.

**Acceptance: exact one-authority proof**

- Exactly one Alpha0 Core writer exists.
- Exactly one Alpha0 gateway owns the dedicated route.
- Source database remains unchanged after final backup and source cannot auto-restart.
- Destination continues from the accepted audit head without duplicate scheduled delivery.
- Generic and axis-control gateways do not absorb Alpha0 traffic.
- Signed receipt binds the one-writer, one-route, source-no-write, and observation-window evidence.

**Abort**

Abort before destination write on source recurrence, route overlap, unsigned audit open, duplicate delivery risk, missing integration evidence, or database metadata change.

**Rollback**

Before any destination write, stop destination and restore source route/Core as reviewed. After a destination write, never resume the old source database: quiesce destination, create/validate a new consistent backup, reverse-transfer encrypted, restore, and repeat one-writer proof. Route rollback is separate and may return interaction to a safe paused state without discarding accepted destination Core data.

## Signed one-authority migration receipt

Migration is incomplete until an independent verifier accepts a root-owned signed receipt containing metadata only:

- source and destination logical-role bindings;
- exact producer/deployment revisions, package/interpreter closures, and trust identity;
- source freeze/stop/quiescence timestamps and observation-window result;
- final axis-control observation digest and custody summary;
- final Alpha0 backup completion, schema/migration, aggregate, and audit-head metadata without database hash or payload;
- destination service, scheduler, gateway, and route identities;
- secret-reference identities without values;
- incident commander and independent reviewer identities;
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
