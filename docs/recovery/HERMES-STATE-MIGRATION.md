# Hermes state migration

**Status: FUTURE ONLY — DO NOT RUN.** This credential-free design classifies state; it does not authorize service, scheduler, route, credential, or data changes.

## Dispositions

- `RECREATE_FROM_VCS`: install immutable source, templates, wrappers, prompts, skills, units, and schedule definitions from an exact reviewed producer/deployment revision.
- `RECONSTRUCT_FROM_CANONICAL`: render generated configuration or re-read projections from canonical authorities.
- `MIGRATE_DURABLE`: transfer unique continuity state only through a supported, quiesced, integrity-checked semantic backup/restore.
- `ARCHIVE_EVIDENCE`: retain owner-only history without importing it into the live destination state plane.
- `DISCARD_EPHEMERAL`: recreate or delete machine/process artifacts after accepted checkpoint and retention review.
- `SECRET_REISSUE`: provision credentials from managed authority; never copy a runtime environment file.

Each row below has one disposition. Conditional decisions are split into separate rows rather than assigning an artifact two outcomes.

## Runtime domains

The capture identified four scheduler registries across three gateway domains:

1. generic owner home `~/.hermes`, including its root scheduler;
2. root profile `~/.hermes/profiles/axis-control`;
3. rootless checkout home and profile under the legacy axis-control workspace;
4. dedicated Alpha0 owner home with routed `alpha0` profile.

These are independent namespaces. They must never be merged. The intended dormant Home composition keeps only generic communication active, disables legacy AXIS and Alpha0 gateways/schedules, and installs an unscheduled report-only canonical AXIS observer definition.

## Complete artifact disposition matrix

| Domain | Artifact | Current authority/value | Disposition | Migration action | Acceptance evidence |
|---|---|---|---|---|---|
| Generic | Gateway/profile configuration template | Home/producer declaration | `RECREATE_FROM_VCS` | Install exact reviewed Home generation and Hermes package. | Signed deployment binds template, package, and entrypoint. |
| Generic | Rendered route map and non-secret profile ownership | Generated deployment state | `RECONSTRUCT_FROM_CANONICAL` | Render one exclusive owner per route; do not byte-copy mutable YAML. | Sanitized route inventory shows no duplicate owner. |
| Generic | Generic sessions and session index required for current interaction continuity | Unique application state | `MIGRATE_DURABLE` | Quiesce gateway; use supported semantic export/restore; preserve namespace and ownership. | Disposable restore passes session lookup/routing without payload disclosure. |
| Generic | Generic Kanban/mission state still authoritative for non-AXIS interaction | Unique application state, if explicitly accepted | `MIGRATE_DURABLE` | Export/import with supported schema; if no importer exists, migrate a checkpointed whole generic DB, never selected rows. | Schema/integrity and application-level semantic checks pass. |
| Generic | Derived Kanban/status views | Reconstructable projection | `RECONSTRUCT_FROM_CANONICAL` | Re-read designated durable owners after activation. | Regenerated view is consistent and does not create authority. |
| Generic | Execution database required for generic continuity | Local durable state | `MIGRATE_DURABLE` | Stop writers, checkpoint SQLite, copy through authenticated encrypted channel, exclude WAL/SHM after checkpoint. | Integrity/schema/version/free-space checks and supported open pass. |
| Generic | Non-AXIS scheduler definitions | Reviewed deployment declaration | `RECREATE_FROM_VCS` | Reinstall declared names/cadences/commands only; runtime job IDs are not authority. | Sanitized inventory equals signed declaration and jobs remain disabled until accepted. |
| Generic | Scheduler execution/ticker history required for delivery de-duplication | Local durable scheduler state | `MIGRATE_DURABLE` | Use supported semantic scheduler backup/restore, not JSON/DB merging. | No duplicate delivery in a bounded disabled rehearsal. |
| Generic | Gateway state JSON needed by supported restore | Local compatibility state | `MIGRATE_DURABLE` | Include only if the target Hermes version documents compatibility. | Exact target version accepts state in disposable restore. |
| Generic | Gateway heartbeat, PID, lock, socket, cron heartbeat, WAL/SHM after checkpoint | Machine/process state | `DISCARD_EPHEMERAL` | Let destination recreate after start. | No source machine identity appears in destination liveness. |
| Generic | Logs/journal without approved retention need | Operational output | `DISCARD_EPHEMERAL` | Start a new destination journal lineage. | No logs copied into runtime state. |
| Generic | Logs/journal with an approved retention need | Historical evidence | `ARCHIVE_EVIDENCE` | Seal separately in encrypted owner-only archive; never replay. | Retention/access policy and archive custody recorded. |
| Root AXIS profile | Profile configuration and route declaration | Future canonical adapter declaration only | `RECONSTRUCT_FROM_CANONICAL` | Render only if a separately approved AXIS interaction adapter is introduced; dormant target renders no active route. | Route has exactly one owner and cannot mutate AXIS. |
| Root AXIS profile | PO alert/scheduler definition | Legacy UX prior art | `ARCHIVE_EVIDENCE` | Retain sanitized name/cadence/identity evidence; do not restore legacy job. | Canonical PO outbox/ACK design is separately reviewed before any replacement. |
| Root AXIS profile | Sessions and session index | Disabled authority-plane continuity | `ARCHIVE_EVIDENCE` | Seal profile namespace owner-only; do not merge into generic or canonical controller state. | Archive custody and retention recorded. |
| Root AXIS profile | Execution database and gateway state | Legacy execution evidence | `ARCHIVE_EVIDENCE` | Preserve quiesced evidence if retention requires; never import as current health. | Destination bootstrap succeeds without it. |
| Root AXIS profile | Heartbeats, PID, locks, cache, ticker scratch | Process state | `DISCARD_EPHEMERAL` | Discard after signed quiescence/retention acceptance. | No scheduler/gateway can restart from discarded state. |
| Checkout AXIS root/profile | Source, prompts, producer-owned skills, wrappers | Unreviewed/rootless copies of producer material | `ARCHIVE_EVIDENCE` | Keep current captured evidence only; deploy replacements from canonical VCS in separate rows. | No checkout path exists in deployed service/config. |
| Checkout AXIS root/profile | Canonical prompts, skills, wrapper replacement | Reviewed axis-control producer | `RECREATE_FROM_VCS` | Install from pinned package/closure. | Signed source/package revision and immutable path verified. |
| Checkout AXIS root/profile | Scheduler registry including legacy model-waking job | Legacy runtime authority | `ARCHIVE_EVIDENCE` | Preserve sanitized identity/cadence/disabled proof; never import registry. | Exact source job is absent/disabled and cannot be recreated. |
| Checkout AXIS root/profile | Board/task/checkpoint state, supervisor receipts, event journals | Legacy derived/history state | `ARCHIVE_EVIDENCE` | Seal owner-only; reconstruct current custody from GitLab. | No legacy record authorizes destination work. |
| Checkout AXIS root/profile | Sessions, Kanban, state databases | Disabled rootless authority plane | `ARCHIVE_EVIDENCE` | Preserve separately only as required evidence; never merge namespaces. | Destination controller bootstrap does not read the archive. |
| Checkout AXIS root/profile | Worktrees, virtualenv, cache, PID, lock, heartbeat, temp snapshots | Rebuildable/machine state | `DISCARD_EPHEMERAL` | Delete only after custody and retention approval. | Every unique commit has approved durable disposition first. |
| Alpha0 owner/default profile | Hermes package, configuration templates, routing shim, wrappers, prompts, producer-owned skills, units | Canonical Alpha0/Home producers | `RECREATE_FROM_VCS` | Install exact reviewed package and deployment definitions; keep disabled. | No Ghost/checkout path; profile and clarification preflights pass. |
| Alpha0 owner/default profile | Rendered route map and provider observations | Generated configuration/projections | `RECONSTRUCT_FROM_CANONICAL` | Render dedicated app/profile ownership and re-read providers after authorization. | One owner per route; observations do not mutate providers. |
| Alpha0 routed profile | Sessions required for interaction continuity | Unique dedicated-profile state | `MIGRATE_DURABLE` | Use supported profile-aware semantic export/restore. | Session keys retain profile identity; no generic fallback/collision. |
| Alpha0 owner/routed profile | Kanban/profile durable state that cannot be reconstructed | Unique accepted interaction state | `MIGRATE_DURABLE` | Transfer only after owner classifies it as active and target schema accepts it. | Application-level semantics pass on disposable restore. |
| Alpha0 owner/routed profile | Derived Kanban/status/brief projections | Reconstructable from Alpha0 SQLite/providers | `RECONSTRUCT_FROM_CANONICAL` | Re-render from accepted Core data and bounded provider observations. | Counts/semantics match without writes. |
| Alpha0 owner/routed profile | Scheduler definitions for daily status and AXIS SITREP | Canonical producer definitions after wrapper review | `RECREATE_FROM_VCS` | Recreate disabled; do not copy runtime IDs or legacy wrappers. | Exact wrapper/config/audit-key semantics pass before individual enablement. |
| Alpha0 owner/routed profile | Scheduler execution/ticker state needed to avoid duplicate delivery | Local durable scheduler state | `MIGRATE_DURABLE` | Use a supported semantic restore. | Disabled rehearsal proves cadence/last-delivery semantics and no duplicate. |
| Alpha0 owner/routed profile | Historical scheduler registry, retired wrapper/config copies | Legacy evidence | `ARCHIVE_EVIDENCE` | Seal sanitized metadata; do not activate. | Canonical registry is generated independently. |
| Alpha0 owner/routed profile | Gateway PID/state, heartbeats, locks, cache, temporary files | Process state | `DISCARD_EPHEMERAL` | Recreate at destination start. | New liveness identity is destination-local. |
| Alpha0 owner/routed profile | Logs without approved retention need | Operational output | `DISCARD_EPHEMERAL` | Do not transfer. | Destination runtime contains no source log payload. |
| All domains | User-authored skill proven unique and required | Unique durable user artifact | `MIGRATE_DURABLE` | Compare metadata/content digest against pinned producer in an owner-only procedure; transfer only approved originals. | Independent review proves absence from VCS and safe target ownership. |
| All domains | Installed/package-owned skills | Producer artifact | `RECREATE_FROM_VCS` | Reinstall from package. | No mutable local copy takes precedence. |
| All domains | Environment files, rendered secret maps, embedded credentials | Credential material | `SECRET_REISSUE` | Provision with SOPS/sops-nix/provider reauthorization at `0600` or stricter; never copy `/run` or legacy `.env`. | Managed reference and restrictive mode pass; old credentials revoked after cutover. |
| All domains | Mode-`0644` legacy provider credential files | Exposed legacy credential container | `SECRET_REISSUE` | Issue a replacement and revoke old credential; permission repair alone is insufficient. | Rotation receipt contains identity metadata only. |
| All domains | Root/profile state snapshots and private backup metadata | Recovery evidence, not live authority | `ARCHIVE_EVIDENCE` | Encrypt, deduplicate owner-side, record source/version/time/retention; keep outside Git. | Disposable restore is proved separately from live activation. |

## Semantic backup/restore contract

A filesystem copy is not a semantic restore. Before any `MIGRATE_DURABLE` row moves, a supported procedure must:

1. identify exact source and target Hermes versions and profile namespaces;
2. stop or quiesce all writers without interrupting unresolved custody;
3. checkpoint SQLite through supported application/SQLite behavior;
4. export only the selected namespace and artifact class;
5. use authenticated encrypted transport into a mode-`0700` destination directory with files mode `0600` or stricter;
6. validate schema, integrity, profile-aware session keys, scheduler de-duplication, route ownership, and application-level reads on a disposable restore;
7. leave gateway and scheduler definitions disabled;
8. record rollback custody without any payload in the public receipt.

Until this test passes, `HERMES_STATE_PORTABILITY = PARTIAL`.

## Integration identity and host-move actions

| Identity facet | Host-move answer | Required action |
|---|---|---|
| Slack bot/app identity | Can survive a host move in principle | Socket Mode has no host-bound callback in the inspected topology. Retain logical app identity only if policy and credential custody permit; otherwise reissue credentials. |
| Slack route/channel ownership | Does not move automatically | Prove source gateway stopped and destination is the sole owner before destination connection. Never let generic/AXIS/Alpha0 fallback overlap. |
| Callback/ingress | No local Slack callback observed | No hostname/DNS update is required for Socket Mode. Any separately discovered callback remains blocked pending provider update evidence. |
| Alpha0 loopback API | Host-local by design | Recreate the configured loopback port without public exposure and check collision. |
| Hostname/path | Not identity authority | Render destination paths from deployment options; do not preserve `ghost` or `/home/cdenneen` unless an external allowlist explicitly requires it. |
| Session identity | Profile-bound durable state | Preserve namespaces and profile-aware key semantics; do not merge homes. |
| Routing shim | Compatibility code, not mutable state | Recreate exact reviewed shim from VCS/package; retire only after native behavior passes regression checks. |
| Scheduler job ID | Runtime evidence, not declaration authority | Recreate reviewed name/cadence/command disabled; retain old ID only in sanitized archive/receipt. |
| Provider/OAuth credential | External authority | Reissue or reauthorize according to `SECRET-REQUIREMENTS-MANIFEST.md`. |

### Slack host-move answer

**YES, conditionally:** the logical Slack bot/app identity can survive a host move without functional change because the observed integration uses outbound Socket Mode rather than a host-bound callback. This does not make the move currently ready. Credential custody, exact channel/chat ownership, dedicated Alpha0 profile/session semantics, semantic state restore, and source-no-connection proof must pass. Credentials referenced by the mode-`0644` legacy environment files cannot survive as-is; they are rotation-required.

## Abort and rollback

Abort before transfer or start on unknown artifact ownership, unsupported schema/version, route overlap, failed profile/session preflight, incomplete secret provisioning, live writer, incomplete SQLite checkpoint, or any payload appearing in public evidence.

Before a destination gateway accepts interaction, rollback may stop the disabled destination and resume only the exact reviewed source route. After destination interaction creates durable state, do not simply restart the source. Quiesce the destination, create a new semantic backup, validate reverse restore, transfer route ownership separately, and prove one writer/one gateway again.

## Residual risks

- No supported end-to-end Hermes semantic backup/restore has been accepted.
- Exact live channel/chat exclusivity and dedicated Alpha0 identity remain unverified.
- Session, queued-work, Kanban, and scheduler semantic compatibility were not inspected because payload reads are prohibited.
- The evidence bundle excluded private Alpha0 databases and is not a complete runtime backup.
- Fresh quiesced metadata and application-level restore tests are required in the authorized maintenance window.
