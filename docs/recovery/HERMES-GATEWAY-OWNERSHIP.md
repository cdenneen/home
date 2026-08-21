# Hermes gateway and wrapper ownership

Point-in-time Ghost map from 2026-08-20 with a read-only 2026-08-21 refresh. This report names ownership and routing shape without credentials, channel values, messages, sessions, prompts, logs or scheduler output. The exhaustive route/trigger table and current verdict are in `EXCLUSIVE-ROUTE-AND-HERMES-PORTABILITY.md`: `ROUTE_OWNERSHIP = PARTIAL`.

## Live ownership

| Domain | Live unit / command | State root, profile and cwd | Ingress/listener | Scheduler ownership | Future owner |
|---|---|---|---|---|---|
| Generic communication | `hermes-gateway.service`; unprofiled `hermes gateway run` | `~/.hermes`; default profile; cwd `~/.hermes` | generic Slack Socket Mode; no local TCP listener observed | root registry contains legacy AXIS supervisor every 10 minutes and watchdog every 5 minutes | Generic gateway exclusively owns non-AXIS/non-Alpha0 communication and generic stuck-cron recovery. It must not own AXIS SDLC. |
| Legacy AXIS scheduler host | `hermes-axis-control-gateway.service`; `hermes --profile axis-control gateway run` | checkout-local Hermes root/profile; cwd `/home/cdenneen/src/workspace/work/axis-control` | no external platform, served profile or listener | checkout profile owns the effective roadmap job; separate legacy scheduler watchdog timer. AXIS interaction and PO reporting use the generic gateway/profile. | Canonical axis-control exclusively owns AXIS execution/SDLC. Current scheduler host/job are drain targets, not canonical deployment or interaction authority. |
| Legacy Alpha0 interaction | `hermes-alpha0-gateway.service`; clean-env `hermes gateway run --external-supervisor` | dedicated owner home `/home/cdenneen/.local/share/alpha0/hermes`, routed `alpha0` profile; cwd at owner home | dedicated Slack ingress plus loopback `127.0.0.1:8643` | daily no-agent status at 08:00 and hourly no-agent AXIS SITREP | Canonical Alpha0 owns its dedicated interaction, status and bounded supervision projection only. It may supervise axis-control but may not perform AXIS SDLC actions. |

All three gateway processes were enabled and active at capture, but only generic Hermes and Alpha0 owned external identities. They share a Hermes package closure but are not interchangeable: the dedicated AXIS process is a scheduler host, not an external front door. Systemd does not express dependencies or route exclusivity among them; continuity and collision avoidance depend on configuration, state and destination ownership.

Scheduler custody has **four expected authority registries**, not just the three gateway owner homes. In addition to generic `~/.hermes/cron`, root profile `~/.hermes/profiles/axis-control/cron`, and dedicated Alpha0 `~/.local/share/alpha0/hermes/cron`, the rootless checkout has its own `/home/cdenneen/src/workspace/work/axis-control/.hermes/profiles/axis-control/cron`. That checkout profile held an enabled five-minute `no_agent=false` roadmap precheck that could wake mutating model work. It is a critical drain target and is not represented by the sanitized five-job inventory captured from the other three registries.

A 2026-08-21 read-only qualification also found additional live root/profile `jobs.json` paths. The checkout root and checkout profile are distinct files containing the same enabled agent-waking AXIS job identity, creating hidden duplicate persisted authority. Alpha0's routed-profile live path must also be accounted for even when its declarations reconstruct from the owner-root inventory. Every live jobs file must therefore be planned; the four expected registries are not a complete live filesystem inventory. Two older `state-snapshots/*/cron/jobs.json` files were also observed and are non-live `ARCHIVE_EVIDENCE`, not scheduler authority.

## Target non-overlap contract

1. One Slack chat/channel has exactly one gateway owner. No fallback duplicate route may exist across generic, axis-control and Alpha0 homes.
2. Generic gateway retains generic sessions/state and the generic stuck-cron watchdog. Legacy generic-root AXIS supervisor/watchdog jobs are not generic behavior and must be drained explicitly.
3. axis-control alone owns AXIS issue, roadmap, branch, worktree, merge-request, repair, review, merge and current-main behavior. Hermes/Kanban is at most a profile-scoped adapter or view.
4. Alpha0 retains a separate owner home, root/default owner profile, routed `alpha0` profile, dedicated app/channel route, distinct external secret references, loopback API, clean environment, external-supervisor behavior and profile-aware session keys.
5. Alpha0's AXIS SITREP is observation/supervision only. It cannot create, repair, review or merge AXIS work.
6. Scheduler restoration is explicit and reviewed. A generated inventory does not prove import of the live registry or execution history.

## Disappearance impact

- If the generic gateway disappears, generic communication and its current root scheduler stop. Dedicated AXIS/Alpha0 processes remain, but generic-only routes become unreachable.
- If the legacy AXIS scheduler gateway disappears before drain, its effective roadmap scheduler stops, while AXIS interaction and PO alerts remain on generic Hermes. Generic-root AXIS jobs and the independent recovery timer can continue, so stopping the process alone is not a work-admission fence.
- If the Alpha0 gateway disappears, dedicated Alpha0 Slack/web ingress, daily status, hourly SITREP and its scheduler ticker stop. Generic/AXIS gateways must not silently absorb that traffic because dedicated session, credential and external-supervisor semantics would be lost.

These effects were inferred read-only. No failure or stop test was performed.

## Canonical Alpha0 routing requirements

Canonical Alpha0 startup must retain its three fail-closed preflights: root/profile ownership and secret-command shape, profile-aware session keys, and clarification bypass. The routing shim only fills a missing profile from source context; it does not override explicit choices. Startup failure must prevent restart loops rather than route through another gateway.

State directories must be `0700`, state/secret files `0600`, and the service must retain owner-only umask/hardening. Core and gateway secret maps remain separate and external to source/Nix store.

## Wrapper disposition

| Live wrapper | Classification | Migration decision |
|---|---|---|
| profile `alpha0-status.py` and root `run_alpha0_status_job.py` | `LEGACY_ONLY_TRANSITIONAL` | Useful timeout/output/mutation-false validation is represented canonically. Remove checkout fallback and duplicate copies; invoke the pinned installed wrapper with explicit config/audit-key-file references. |
| `alpha0-daily-brief.py` | `CANONICAL_EQUIVALENT` | Preserve thin deterministic action-brief adapter from the pinned package. |
| `alpha0-axis-operations-sitrep.py` | `UNKNOWN` pending managed-state review | Preserve hourly/no-agent/bounded intent. Do not replace until canonical `--apply`, config, state and audit-key semantics are qualified on a disposable restore. |
| axis profile PO blocker wrapper | `LEGACY_GOOD_MISSING` UX, legacy implementation rejected | Rebuild only if required, over canonical PO evidence and durable delivery outbox/ACK. |

## PR #681 target

The dormant Home candidate keeps the generic gateway enabled, forces the embedded supervisor and legacy axis gateway false, and imports both producers with Alpha0 Core/gateway false and no canonical axis scheduler/gateway. It installs only a report-only unscheduled axis observer definition. This target removes overlapping declaration authority, but it is not deployed and cannot itself prove runtime routing or state migration.

## Acceptance gates before gateway change

- Re-observe unit/job/routing metadata and map every channel/chat to one owner without recording values in the report.
- Archive all three source state roots under approved owner-only retention without importing them as authority. Reconstruct disabled route/job declarations from reviewed VCS with fresh runtime state; prove a disposable semantic restore only if an owner later qualifies a generic unprofiled session subset.
- Drain all new AXIS work sources and active custody before stopping the dedicated AXIS scheduler host. Preserve the generic gateway because it owns generic communication and the current AXIS interaction/reporting route.
- Qualify canonical Alpha0 preflights and both scheduler wrappers from the exact pinned package.
- Rebase Home composition, pass exact-head required CI, obtain fresh approval and signed deployment evidence.
- After future activation, prove generic continuity, absence of duplicate routes/legacy AXIS authority, and dedicated Alpha0 isolation before deleting any old state.

`HERMES_ROUTING_UNDERSTOOD = PARTIAL`: every observed route/session origin has a logical owner; provider attestation established distinct generic and Alpha0 identities and no dedicated AXIS external route. Qualification remains partial because app-token correspondence/delivery readback, historical generic-session disposition, duplicate enabled scheduler records, managed generic reconstruction and the no-restart observation remain incomplete. Cutover remains forbidden. See `PORTABLE-CONTROL-PLANE-BOUNDARIES.md`.
