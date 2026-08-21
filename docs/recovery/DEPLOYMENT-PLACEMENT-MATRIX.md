# Deployment placement matrix

This report translates current evidence into logical placement requirements. It does **not** select a destination, activate disaster recovery, admit a backend, or transfer storage authority.

The labels `AXIS_CORE`, `AXIS_CONTROL`, `ALPHA0`, `AXIS_POSTGRES`, `AXIS_VECTOR`, `AXIS_GRAPH`, and `AXIS_CACHE` are recovery placement labels independent of hostnames. The five AXIS data-plane labels are not established producer configuration variable names and must not be treated as new architecture or authority declarations.

## Evidence-backed matrix

| logical role | current host | possible destination | canonical source | durable state | secret dependencies | network dependencies | availability requirement | migration strategy |
|---|---|---|---|---|---|---|---|---|
| `AXIS_CORE` | Axis-lab inventory describes Ghost as primary; exact live placement/activation was not re-attested by this report | An axis-lab-admitted persistent Linux host; inventory also describes GCP `maintainer-savage` as DR standby, not active | `ghostspace/axis`; authority constraints in `ghostspace/axis-governance`; concrete placement in `ghostspace/axis-lab` | Local SQLite boot/continuity substrate, application data root, and any separately qualified durable session/service state | Managed application/provider references selected by axis-lab; no machine-tied node secret may be copied | Provider endpoints over explicitly declared TLS/routing; optional node/Tailscale/cloudflared attachment; no implicit shared-host assumption | Must boot from local SQLite and report degraded state when optional graph/vector/cache providers are absent; requires persistent disk | Recreate package/config from exact VCS; restore qualified portable SQLite/application backup; reauthorize provider attachments; do not combine host move with PostgreSQL authority transfer |
| `AXIS_CONTROL` | Legacy controller/gateway was on Ghost at capture; current Home source is a dormant canonical observer candidate, not activation proof | Any authorized conforming Linux/Nix host with direct GitLab HTTPS and root-owned signed deployment trust; final host undecided | `ghostspace-com/axis-control`; deployment composition in `cdenneen/home` | Reconstructable GitLab-derived state plus owner-only journal/telemetry; only qualified non-reconstructible pending events migrate; local Hermes observer state is separate | Managed least-privilege GitLab token, PO identity configuration, signed trust/allowed-signer material; optional future interaction secrets | Direct DNS HTTPS to configured GitLab on implicit 443; local Unix filesystem/locks; local procfs only for co-located Hermes watchdog | One controller authority; GET-only bootstrap must work without `AXIS_CORE`; report-only observer remains disabled/unscheduled until graduation | Freeze new work, converge custody, recreate signed package/config, reconstruct from GitLab, import only qualified pending events, prove source stopped and one destination scheduler/route owner |
| `ALPHA0` | Legacy dedicated Core data/Hermes gateway was on Ghost at capture; canonical Home services are dormant | Any authorized conforming Linux/Nix host with persistent owner-only storage and approved integration routing; final host undecided | `ghostspace-com/alpha0`; deployment composition in `cdenneen/home` | Alpha0's current durable backend is its SQLite database, plus audit chain, approved profile sessions, scheduler de-duplication/profile state, and non-reconstructible events | Audit-key reference; separate Core/gateway maps; Slack bot/app/member; API key; selected provider; separate GitLab observer, node, relay, and OAuth identities as enabled | Loopback Core/gateway APIs; GitLab/provider APIs; outbound Slack Socket Mode; bounded SSH node transport where configured | Exactly one SQLite writer and one dedicated gateway route owner; signed audit must fail closed; current full AXIS supervision requires local axis-control/Hermes artifacts | Host migration keeps SQLite: provisional then final online backup after write quiescence; semantic Hermes restore; reissue/reauthorize integrations; one-writer/one-route receipt |
| `AXIS_POSTGRES` | Current authority and placement remain governed by existing AXIS continuity and axis-lab/governance decisions; the Ghost projection references remote Supabase PostgreSQL but does not admit it or certify authority transfer | Supabase or another managed/local PostgreSQL is only a possible future destination after explicit axis-lab/governance admission and authority-transfer evidence meeting TLS, schema, pool, backup, budget, and outage-domain requirements | Storage authority in `ghostspace/axis-governance`; provider/profile admission in `ghostspace/axis-lab` | Shared durable relational state only after explicit governed transfer; backups/PITR and schema migration records | Managed database user/credential reference and operator token; exact admitted producer mapping remains pending | TLS endpoint, selected pooler/port, database/user, routing/firewall, monitoring and backup access | Unavailability after authority transfer must be visible; competing SQLite canonical writes are forbidden; exactly one canonical writer per organism/scope | Do not migrate as part of control-plane host move; perform a separately governed SQLite→PostgreSQL transfer with fencing, reconciliation, rollback, and one-authority proof |
| `AXIS_VECTOR` | Axis-lab Ghost projection uses loopback Qdrant; DR inventory describes Qdrant Cloud Free; live/certified status not established | Local Qdrant beside `AXIS_CORE` or axis-lab-admitted managed Qdrant; final choice undecided | Projection semantics in `ghostspace/axis`/`axis-governance`; placement/admission in `axis-lab` | Rebuildable vector index only; never index-only organism truth | Endpoint/collection and managed credential if remote | Local loopback `6333/6334` in Ghost projection or selected managed TLS endpoint | Loss must degrade semantic/nearest-neighbor capability without preventing SQLite boot or changing durable authority | Recreate service/config, provision new attachment, rebuild from qualified canonical sources, validate freshness/rebuild and degraded-mode behavior |
| `AXIS_GRAPH` | Axis-lab Ghost projection uses loopback Neo4j; DR inventory describes Aura Free with authentication broken/unverified | Local Neo4j beside `AXIS_CORE` or axis-lab-admitted Aura/managed graph; final choice undecided | Projection semantics in `ghostspace/axis`/`axis-governance`; placement/admission in `axis-lab` | Rebuildable graph projection; graph-only writes cannot become truth | Managed graph credential and namespace/database reference | Local loopback `7687/7474` in Ghost projection or selected managed TLS endpoint | Loss removes traversal projection only; AXIS must boot from SQLite and must not promote stale alternate truth | Recreate/provision, rotate/reissue unverified managed credential, rebuild relationships from canonical meaning, qualify degradation and recovery |
| `AXIS_CACHE` | Axis-lab Ghost projection uses loopback Redis; DR inventory proposes unprovisioned Upstash Free | Local Redis or an axis-lab-admitted independent managed cache such as Upstash; final choice undecided | Cache/coordination semantics in `ghostspace/axis`/`axis-governance`; placement/admission in `axis-lab` | No durable truth; ephemeral cache, leases, queues/streams must reconcile from designated owners | Managed cache credential only if selected service requires it | Local loopback `6379` or selected managed TLS endpoint; DR should avoid dependence on Ghost outage domain | Flush/loss must be recoverable and visible without losing durable organism truth; stale lease/queue state cannot authorize writes | Recreate empty, reissue attachment secret, reconcile/rebuild from durable owners, test service-loss and stale-coordination behavior |

## AXIS authority and degraded mode

The matrix preserves the governing boot sequence:

1. start `AXIS_CORE`;
2. load its local SQLite continuity substrate;
3. discover capabilities;
4. connect available providers;
5. validate health;
6. operate visibly degraded when optional providers are absent.

`AXIS_VECTOR` and `AXIS_GRAPH` are projections. `AXIS_CACHE` is ephemeral coordination/cache. Their loss cannot transfer authority or prevent SQLite-based degraded boot. `AXIS_POSTGRES` is different: it becomes canonical only through an explicit governed transfer. After that transfer, PostgreSQL loss must produce visible unavailable-authority behavior; it must not silently permit a stale SQLite copy to resume canonical writes.

Moving `AXIS_CONTROL` does not move `AXIS_CORE` or any data role. Moving `ALPHA0` retains Alpha0's own SQLite and does not select an AXIS storage backend.

Alpha0's current durable backend is SQLite. PostgreSQL or Supabase may be evaluated only as a separate future Alpha0 storage-backend candidate, subject to Alpha0-specific migration, behavioral-parity, restore, rollback, and authority-transfer gates. That evaluation is not part of Alpha0 host migration and does not imply that Alpha0 and AXIS should share a Supabase project, database, schema, credential, writer, or backup boundary.

## Evidence-supported possibilities, not decisions

Current architecture permits consideration of:

- local SQLite-only `AXIS_CORE`;
- optional local or managed graph/vector/cache providers;
- split controller and provider hosts over reviewed secure networking;
- an independent managed-SaaS DR ring;
- remote nodes attached to one organism;
- `AXIS_CONTROL` on a different host from `AXIS_CORE`;
- `ALPHA0` on a different host after a bounded remote supervision contract exists or with supervision explicitly partial.

These are possibilities, not approved placements. The inspected axis-lab environment README explicitly states that inventory selects profiles and supplies controlled input; it does not define architecture, authority, certification, or production admission.

## Decisions that remain owned by axis-lab

Axis-lab must decide and prove, subject to governance:

- the active host and DR activation target for each logical role;
- local versus managed provider, region, tier, account/project, and outage domain;
- endpoint and managed secret injection;
- persistent disk sizing, backup/PITR, retention, restore, and resource pressure;
- firewall, TLS, Tailscale, cloudflared, proxy, DNS, and health routing;
- provider schema/database/collection/namespace and rebuild procedure;
- interruption, degraded-mode, rollback, and failover evidence;
- production backend admission for each domain record class;
- budgets, free-tier suitability, monitoring, quotas, suspension risk, and availability targets.

This recovery effort may state constraints and missing evidence. It must not choose those values or reinterpret storage authority.

## Placement evidence still missing

- Exact live placement, package/configuration closure, and activation state are not independently signed.
- Permanent PostgreSQL transfer, projection degradation/rebuild, Redis service loss, and distributed embodiment are not certified by current acceptance evidence.
- The managed-SaaS DR inventory is standby/incomplete: Aura authentication is unverified, Upstash is not provisioned, and DR activation/provider home is unresolved.
- Axis-control and Alpha0 have no accepted destination host, signed deployment manifest, or completed managed secret mapping.
- `ALPHA0` lacks a bounded authenticated cross-host `AXIS_CONTROL` status/provenance transport.

Therefore `DEPLOYMENT_PLACEMENT = PARTIAL_UNDECIDED`. No final placement is made here.
