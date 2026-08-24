# Exclusive route ownership and minimal Hermes semantic portability

**Evidence date:** 2026-08-21
**Scope:** read-only qualification and disposable isolated tests. No gateway, service, scheduler, worker, credential, GitLab state, Home generation, or production route was changed.

## Source integration

Normal producer integration completed before this qualification.

| Product | Merged pull requests | Merged current `main` | Current-main CI |
|---|---|---|---|
| axis-control | #3, #4, #10 | `830b6432a758a633afbf2f3127ceb3dfeba340d7` | run `32506348009`: success |
| Alpha0 | #2, #4 | `94e90beb00c46bca74f927437e1c8805eb64d099` | run `32506305059`: success |

The reviewed feature heads are ancestors of these merge commits. PR-head success is not used as the final source-integration claim.

## Solo-maintainer review governance

```text
HUMAN_SECOND_PARTY_REVIEW = NOT_APPLICABLE
repository has one authorized human maintainer
```

Home default-branch governance requires pull requests, strict current-base NixOS and Darwin checks, resolved review conversations, linear history, deletion/non-fast-forward protection, and no admin bypass actor. The required human approval count is zero and most-recent-push approval is disabled because the sole authorized human cannot provide a valid second-party approval. Independent exact-head model review remains a separate evidence/policy gate; it is never represented as human approval and becomes stale after any head change.

## Supervision boundary

```text
CROSS_HOST_SUPERVISION_CONTRACT = PROVEN
CROSS_HOST_SUPERVISION_DEPLOYMENT = PARTIAL
```

The merged producer and consumer contract is complete enough and is not reopened by this report. A disposable rootless two-container test exercised the existing contract over a private TCP bridge with zero mounts on either container. The consumer had no shared filesystem, SSH path, producer SQLite/Hermes access, producer process inspection, or producer-host subprocess access. Local controller access primitives were patched to fail.

| Case | Result |
|---|---|
| missing authentication | HTTP 401 |
| matching producer and GitLab evidence | `PROGRESSING`; no mutation |
| changed GitLab head | `DRIFTED`; no mutation |
| incomplete producer envelope | `UNKNOWN` |
| contradictory producer evidence | `UNKNOWN` |
| producer unavailable | explicit `UNAVAILABLE` |

The isolated proof used axis-control `830b6432a758a633afbf2f3127ceb3dfeba340d7`, Alpha0 `94e90beb00c46bca74f927437e1c8805eb64d099`, an ephemeral bearer credential, and response digest `sha256:ccf62d1539b202c3b99416064b24f079bf4b7227587aad9316e150c46886bf8f`. Matching and changed-head request latency was 0.462 ms and 0.554 ms respectively on the local private bridge. These are samples, not an SLO.

Deployment remains `PARTIAL`: the HTTP wrapper and credential were disposable test fixtures, not reviewed production transport; no TLS/server identity, managed consumer credential, signed deployed source binding, or authorized physical two-host deployment was established. This does not weaken or redesign `axis-control.supervision.v1`.

## Exact-one-owner route qualification

Logical owners are restricted to `GENERIC_HERMES`, `AXIS_CONTROL`, and `ALPHA0`. Route aliases below are evidence-local; provider identifiers, channel/chat values, session identifiers, prompts, messages, commands, delivery targets, and credential material are excluded.

| Route identity | Ingress / trigger | Current gateway / profile | Scheduler registry | Durable state dependency | Intended canonical owner | Future disposition | Cutover action required? |
|---|---|---|---|---|---|---|---|
| `GENERIC-CURRENT` | generic messaging ingress | generic gateway / default | none inherent | 10 indexed generic sessions; none selected for migration | `GENERIC_HERMES` | reconstruct owner-approved route; start sessions fresh unless a subset is separately qualified | yes: prove sole source/destination connection and continuity |
| `GENERIC-HISTORICAL-A` | historical generic session origin, absent from current route config | generic gateway / default history | none | one indexed session | `GENERIC_HERMES` | owner selects archive or conditional session export | yes: explicit owner disposition |
| `GENERIC-HISTORICAL-B` | historical generic session origin, absent from current route config | generic gateway / default history | none | one indexed session | `GENERIC_HERMES` | owner selects archive or conditional session export | yes: explicit owner disposition |
| `AXIS-MULTIPLEX` | AXIS interaction carried by generic root | generic gateway / `axis-control` routed profile | root-profile PO-alert registry | 12 indexed AXIS sessions; archive-only | `AXIS_CONTROL` | archive legacy route/sessions; reconstruct only an approved non-mutating adapter | yes: confirm generic gateway ownership and dedicated scheduler has no external route |
| `AXIS-DEDICATED` | no external ingress; dedicated legacy scheduler host | legacy axis-control gateway / explicit `axis-control` profile | checkout profile registry | no session index observed in checkout root/profile | `AXIS_CONTROL` | drain and archive; never restore as source authority | yes: scheduler/work custody drain and source-no-process proof |
| `ALPHA0-DEDICATED` | dedicated Alpha0 messaging ingress | legacy Alpha0 gateway / owner root routed to `alpha0` | Alpha0 owner-root registry | 27 indexed Alpha0-profile sessions; archive-only | `ALPHA0` | recreate exact route/profile shim and start sessions fresh | yes: exact producer preflights, external identity attestation, source-no-connection proof |
| `ALPHA0-LOOPBACK` | host-local Alpha0 API | legacy Alpha0 gateway / default owner | none | none | `ALPHA0` | recreate loopback-only from reviewed deployment | yes: collision and listener preflight |
| `GENERIC-AXIS-WORKER` | enabled interval, agent-waking AXIS work | generic gateway / default | generic root | runtime ID/history archive-only | `AXIS_CONTROL` | do not restore | yes: future exact pause/removal and absence proof |
| `GENERIC-AXIS-WATCHDOG` | enabled interval, AXIS recovery | generic gateway / default | generic root | runtime ID/history archive-only | `AXIS_CONTROL` | do not restore; preserve the unrelated generic stuck-cron watchdog | yes: future exact pause/removal after recovery sources are fenced |
| `AXIS-PO-ALERT` | enabled interval, no-agent AXIS report | generic gateway / `axis-control` | generic root-profile | session/execution history archive-only | `AXIS_CONTROL` | archive; rebuild only over canonical durable outbox/ACK if separately required | yes: drain explicitly |
| `AXIS-ROADMAP` | enabled interval, agent-waking AXIS work | dedicated axis-control gateway / `axis-control` | **checkout root and checkout profile** | duplicate persisted records; runtime history archive-only | `AXIS_CONTROL` | archive; do not import or restore | yes: independently reconcile both physical registries and prove no fallback recreation |
| `ALPHA0-DAILY` | enabled daily no-agent brief | Alpha0 gateway / default owner | Alpha0 owner root | execution/ticker history archive-only | `ALPHA0` | recreate disabled from merged producer | yes: wrapper rehearsal and no-duplicate-delivery proof |
| `ALPHA0-SITREP` | enabled interval, bounded no-agent observation | Alpha0 gateway / default owner | Alpha0 owner root | execution/ticker history archive-only | `ALPHA0` | recreate disabled after wrapper qualification | yes: prove observation-only semantics before individual enablement |
| `GENERIC-STUCK-CRON` | timer may recover generic gateway from bounded stale-run evidence | generic root | generic root execution state | fresh destination runtime only | `GENERIC_HERMES` | preserve/recreate from canonical Home | yes: prove it observes only retained generic authority |
| `AXIS-SCHEDULER-WATCHDOG` | timer may restart dedicated AXIS gateway | dedicated AXIS checkout profile | checkout profile | legacy health/history only | `AXIS_CONTROL` | drain/archive | yes: fence before AXIS job pauses and prove no restart path |
| `AXIS-WATCHDOG-BACKUP` | legacy AXIS recovery timer | generic/Home legacy watchdog plane | generic-root AXIS state | legacy derived state only | `AXIS_CONTROL` | drain/archive | yes: fence before cron pauses |
| `AXIS-WATCHDOG-MONITOR` | configured manual monitor, currently inactive | generic/Home legacy watchdog plane | generic-root AXIS state | legacy derived state only | `AXIS_CONTROL` | drain/archive | yes: prove it cannot recreate legacy execution |
| `AXIS-SUPERVISOR-PROVISIONER` | enabled oneshot provisions/resumes AXIS worker jobs | generic gateway dependency | generic root | persisted jobs outlive declarations | `AXIS_CONTROL` | remove legacy provisioner | yes: fence before job pauses |
| `AXIS-WATCHDOG-PROVISIONER` | enabled oneshot provisions/resumes AXIS watchdog job | generic gateway dependency | generic root | persisted jobs outlive declarations | `AXIS_CONTROL` | remove legacy provisioner | yes: fence before job pauses |
| `AXIS-WATCHDOG-CUTOVER` | enabled failed oneshot may reinstall AXIS authority | legacy watchdog plane | indirect generic root | legacy control state only | `AXIS_CONTROL` | drain/archive; failed state is not a fence | yes: disable and prove no fallback action |
| `ALPHA0-NYX-RELAY` | configured non-Hermes forwarding relay, inactive | separate Alpha0 service | none | none in Hermes | `ALPHA0` | keep outside Hermes migration | no for Hermes; separate admission before relay activation |

### Scheduler registry closure

Six live physical `jobs.json` paths exist:

1. generic root: two enabled AXIS-owned records;
2. generic `axis-control` profile: one enabled AXIS-owned report record;
3. checkout root: one enabled AXIS roadmap record;
4. checkout `axis-control` profile: the same logical AXIS roadmap record, independently persisted;
5. Alpha0 owner root: two enabled Alpha0 records;
6. Alpha0 routed profile: empty, but still a live path requiring a negative assertion.

That is seven enabled physical records representing six logical jobs. Historical `state-snapshots` are `ARCHIVE_ONLY`, not scheduler registries. Declarative removal alone is insufficient because provisioners, recovery timers, and persisted records can survive or recreate authority.

The duplicate AXIS roadmap records share ID, name, cadence, creation time, workdir, prompt, and static definition, but are distinct files with divergent runtime histories. The checkout-root copy stopped advancing on 2026-08-17. The profile copy continued advancing under the only active checkout gateway, whose service, watchdog, and tests all select profile `axis-control`. The root command is only a compatibility wrapper for the profile implementation.

```text
DUPLICATE_SCHEDULER_TOPOLOGY = PROVEN
CHECKOUT_ROOT_RECORD = STALE_DERIVED_RECORD
CURRENT_EFFECTIVE_RECORD = CHECKOUT_PROFILE
FUTURE_CANONICAL_RECORD = NEITHER_LEGACY_RECORD
```

The enabled root record remains a mutation-capable reactivation hazard if an unprofiled gateway appears. A future authorized Phase B implementation may only fence provisioners and pause the root record through an explicit root `HERMES_HOME`; removal belongs to a separately reviewed post-drain retention/deletion transition. It must prove the root registry remains frozen while the profile advances and prevent reproduction. Canonical continuation reconstructs a fresh disabled schedule from reviewed VCS rather than adopting either legacy registry.

### Live external identity attestation

An owner-local, read-only provider attestation on 2026-08-21 established two pairwise-distinct external identities without emitting credentials, destination values, messages, prompts, or session payloads:

- the generic gateway owns the connected generic identity and carries the routed AXIS profile;
- the Alpha0 gateway owns a separate connected Alpha0 identity;
- the dedicated AXIS gateway has no external platform, served profile, or listener of its own. It is a scheduler host, not a third external identity. AXIS interaction and reporting use the generic gateway identity.

Each configured external route therefore resolves to one active gateway owner. The dedicated AXIS process must not be represented as a separate chat identity. Provider bot identity and connected state were attested, but app-token-to-app identity was not independently exercised and no delivery readback was performed.

```text
EXTERNAL_ROUTE_IDENTITY = PARTIAL
ROUTE_OWNERSHIP = PARTIAL
```

Every known route maps semantically to one allowed logical owner, and no active external identity collision was observed. Exact qualification remains partial because:

- app-token-to-app correspondence and destination delivery were not independently attested;
- generic historical session origins still require owner archive/start-fresh decisions;
- the AXIS roadmap record remains enabled in both checkout registries, although only the profile copy is currently ticked;
- live state is point-in-time and does not prove a no-restart/no-recreation interval.

The two legacy AXIS environment files remain mode `0644`; future credential rotation and managed-secret replacement are required, but were not authorized in this phase.

## Minimal Hermes semantic migration set

Each prior candidate was reduced as follows.

| Artifact class | Disposition |
|---|---|
| packages, templates, wrappers, prompts, producer skills, units, job declarations | `RECREATE_FROM_VCS` |
| route maps and non-secret ownership | `RECONSTRUCT_FROM_CANONICAL` after owner-approved declarations |
| Kanban, mission, status, brief and provider projections | `RECONSTRUCT_FROM_CANONICAL` |
| scheduler ticker/de-dup state | `SAFELY_START_FRESH`; no non-reconstructible delivery boundary is qualified |
| pending generic delivery/events | `SAFELY_START_FRESH`; no accepted unacknowledged effect is qualified |
| generic unprofiled sessions | `SAFELY_START_FRESH` now; only an owner-selected subset may later become conditional `MIGRATE_DURABLE` |
| AXIS sessions, execution databases, ticker, board/checkpoint/event history | `ARCHIVE_ONLY` after canonical reconciliation |
| Alpha0 Hermes sessions, execution databases and ticker history | `ARCHIVE_ONLY`; destination starts fresh |
| gateway state, route cache, heartbeat, PID, locks, sockets, WAL/SHM and temporary files | `SAFELY_START_FRESH` |
| Alpha0 Core SQLite and audit chain | durable application state outside Hermes; retain the separate final-backup contract |

```text
REQUIRED_SEMANTIC_MIGRATION_SET.HERMES = []
```

No execution database or Hermes home is a migration unit. A future owner-selected generic session subset must preserve exact selected conversation/message lineage by session ID while routing, handoff, activity, obligations, jobs and process state remain absent/reset. Transparent next-message Slack continuation is not currently established because the supported importer resets routing.

## Isolated semantic reconstruction proof

A disposable mode-`0700` destination was created twice from exact merged producer files. No production gateway, network call, Slack send, GitLab call, worker, scheduler, service manager, or durable database was used. The candidate migration set was empty.

| Assertion | Result |
|---|---|
| Alpha0 owner/routed profile structural verification | pass |
| Alpha0 scheduler inventory | pass; two unique jobs, both disabled |
| axis-control scheduler declaration | pass; one disabled no-agent observer |
| duplicate destination scheduler authority | none |
| imported durable Hermes items | 0 |
| execution/session databases required | no |
| process/liveness state required | no |
| second clean reconstruction semantic equality | pass |
| semantic digest | `sha256:ae398097b68bfb039e34aedfd9bf3eab506b1c2eff42f6ca1d82a9316b9f68f4` |
| generic non-secret route identity | `UNKNOWN_NOT_IN_VCS` |

```text
HERMES_SEMANTIC_RESTORE = PARTIAL
GENERIC_ROUTE_RECONSTRUCTION = PARTIAL
```

The empty required set and canonical Alpha0/axis-control profile/job reconstruction are reproducible. Home PR #692 reviewed head `f313cd60d850505b081490a3ef8ee74cf590a910` added a missing-file-only, mode-`0600` generic config bootstrap, an out-of-store `EnvironmentFile` contract, legacy Slack plugin alias cleanup, and executable activation checks. It merged through the linear protected-PR flow as current-main commit `3b3baa62e652277bf538188e0f8d565a8b52fc27`; the reviewed and merged trees are identical. The reviewed head passed both targeted Nix checks, a Ghost Home activation-package build, disposable fresh/existing/dangling-config checks, and independent review with no findings. Current-main NixOS and Darwin CI run `32596200628` succeeded. The merged declaration does not manage the external environment file, migrate credentials, activate Ghost, or restore historical sessions. Until it is paired with managed secret authority in a disposable reconstruction, generic route reconstruction remains partial. Existing runtime route rows must not be imported to hide that gap.

### Evidence basis for Phase B fencing qualification

The empty destination migration set does not make current source state inconsequential. At the read-only observation, the effective profile scheduler had a current claim and a live reconciliation descendant; completion-trigger state had 10 pending effects across six lineages; and the legacy board had six running tasks and six running task runs. The generic worker, both checkout records, provisioners, the two-minute scheduler watchdog, and the 15-minute backup recovery path remained enabled. Those source records must be reconciled or explicitly preserved before a fence; they are archive evidence, never destination authority.

```text
REQUIRED_SEMANTIC_MIGRATION_SET.HERMES = []        PROVEN
NO_SOURCE_STATE_NEEDED_FOR_SAFE_DRAIN_TODAY = true REFUTED
PHASE_B_FENCING_QUALIFICATION = NOT_EXECUTED
LIVE_FENCING_OBSERVATION = NOT_STARTED
```

## Reviewed Phase B source-fencing plan

Current admission is `PHASE_B_ADMISSION = BLOCKED`: the policy/identity plan is reviewed, but no root-anchored six-registry execution tool, continuous evidence collector, or receipt verifier exists on current main. No Product Owner grant may issue until a separate bounded implementation PR closes those gaps and admission is recalculated.

The safety invariant is **at most one writer per logical role**: `not (legacy_writer and canonical_writer)`. Phase B deliberately targets `legacy_AXIS_new_work_writer=0, canonical_AXIS_writer=0`; legacy Alpha0 authority remains unchanged until later drain. It is not legacy interaction drain, Home activation, controller graduation, migration, or cutover. After the missing tool/verifier implementation lands and admission is recalculated, the future executable boundary would be `LEGACY-TO-CANONICAL-CUTOVER.md`; it is not executable now.

### Classification

- **Creates new work:** generic-root AXIS worker; both physical copies of the checkout AXIS roadmap job; dedicated AXIS gateway only while its scheduler record is effective; supervisor provisioner.
- **Observes/reports only:** AXIS PO alert; Alpha0 daily brief; Alpha0 AXIS SITREP; dormant canonical observer. These are not paused merely to qualify new-work fencing.
- **Watchdog/recovery:** generic-root AXIS watchdog; AXIS scheduler watchdog; AXIS development backup/monitor; watchdog provisioner/cutover.
- **Preserve for generic continuity:** generic gateway and generic stuck-cron watchdog.

### Deterministic Phase B sequence — future only

1. Bind a Product Owner grant to `SOURCE_FENCE_BASELINE_EVIDENCE`: exact Home/runbook SHA, fresh owner-only backup/restore receipts, complete 9/9 custody with no consequential `UNKNOWN`, all six physical registry identities/digests, service/timer/process/route identity, expected reversible actions, rollback identities, and collector/transcript identity. This is distinct from future `CANONICAL_DEPLOYMENT_ATTESTATION`.
2. Verify the root-trusted signature over the exact source baseline, validate and externally lock all six registries before mutation, then runtime-mask supervisor/watchdog provisioners, cutover/recovery services, scheduler watchdog, and development backup/monitor. Preserve generic stuck-cron recovery.
3. Pause the exact generic-root AXIS watchdog and worker records, then independently pause both physical checkout roadmap records. Shared logical ID is not proof both files changed. Pause, never delete.
4. Record a preliminary admission-stop timestamp. Across at least two former five-minute intervals, admit no new claim; let existing work finish or reach durable custody; require pending/in-flight effects to reach zero and two stable complete GET-only reads with `NO_OP`.
5. Establish signed `F0` only after every legacy AXIS new-work writer/reprovisioner is fenced, every effect-capable legacy worker/reviewer/reconciler descendant is absent, pending/local-only effects are zero, custody remains 9/9, all six registries validate, generic Hermes is healthy, the Home generation is unchanged, no canonical AXIS writer exists, and legacy Alpha0 authority is explicitly unchanged/not drained.
6. Run the `24h15m` off-host no-resurrection observation from `F0`, sampling every two minutes and reconciling durable databases/journals at the end. Reboot, user-manager restart, Home generation change, clock discontinuity, evidence gap, `UNKNOWN`, registry mutation, descendant/authority recurrence, generic restart/session loss, Alpha0 route/service/session drift, or unattributed frontier change invalidates the run.
7. Separately prove generic reconstruction twice in mode-`0700` disposable homes from current-main VCS plus owner-only external secret materialization and fresh runtime state, with network/start/send denied and zero imported session/execution/scheduler DB state. Equal sanitized semantic digests prove reconstructability only, not deployment.
8. Return control to the Product Owner. Reporter/gateway drain, Home activation, canonical graduation, and cutover each require a later separate grant.

Before `F0`, rollback records the exact partial fence and never claims zero AXIS new-work writers; exact baseline restoration requires incident authorization and a fresh Phase B attempt. After `F0`, rollback preserves the AXIS zero-new-work-writer state and restores only an exact component required for generic/active-work continuity. Never broad-resume jobs, restore recovery timers, or run broad Home rollback.

```text
SOURCE_FENCING_PLAN = PROVEN
SOURCE_FENCE_BASELINE_CONTRACT = PROVEN
CANONICAL_DEPLOYMENT_GATE = SEPARATE
HOME_ACTIVATION = OUTSIDE_PHASE_B
```

The plan covers all known jobs, registries, provisioners, watchdogs, backups, custody, identity, preservation, aborts, rollback, observation, and generic reconstruction boundaries. It has not been executed.

## Final status

```text
AXIS_REMOTE_CUSTODY: 9/9

CROSS_HOST_SUPERVISION_CONTRACT:
PROVEN

CROSS_HOST_SUPERVISION_DEPLOYMENT:
PARTIAL

EXTERNAL_ROUTE_IDENTITY:
PARTIAL

ROUTE_OWNERSHIP:
PARTIAL

DUPLICATE_SCHEDULER_TOPOLOGY:
PROVEN

GENERIC_ROUTE_RECONSTRUCTION:
PARTIAL

REQUIRED_SEMANTIC_MIGRATION_SET:
[]

HERMES_SEMANTIC_RESTORE:
PARTIAL

SOURCE_FENCING_PLAN:
PROVEN

SOURCE_FENCE_BASELINE_CONTRACT:
PROVEN

CANONICAL_DEPLOYMENT_ATTESTATION:
NOT_ESTABLISHED

PHASE_B_EXECUTION_TOOL:
NOT_IMPLEMENTED

PHASE_B_RECEIPT_VERIFIER:
NOT_IMPLEMENTED

PHASE_B_ADMISSION:
BLOCKED

PHASE_B_FENCING_QUALIFICATION:
NOT_EXECUTED

LIVE_FENCING_OBSERVATION:
NOT_STARTED

CANONICAL_COMPOSITION_ACTIVATED:
NO

CANONICAL_CONTROLLER_GRADUATION:
NOT_AUTHORIZED

SAFE_DRAIN_READY:
NO

CUTOVER_READY:
NO
```

`PHASE_B_ADMISSION` remains `BLOCKED` and `SAFE_DRAIN_READY` remains `NO`: external identity attestation retains bounded provider/delivery gaps; managed generic reconstruction proof is incomplete; both checkout records remain enabled despite the proven stale-derived classification; current source custody includes active work and pending effects; and no authorized `24h15m` fence/no-resurrection observation has passed. Phase B success could make safe drain **eligible for a later Product Owner decision**, but could not activate, graduate, migrate, or cut over anything. Final host or data-service placement remains outside this report.
