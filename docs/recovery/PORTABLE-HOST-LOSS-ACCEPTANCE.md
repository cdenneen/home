# Portable host-loss acceptance

This report answers the control-plane portability question with evidence available on 2026-08-20. It defines acceptance for recovery onto an authorized host; it does not select that host or authorize a cutover.

## Main portability question

> After legacy evidence is harvested, can axis-control and Alpha0 be treated as portable management-plane products whose source, deployment, durable state, secrets, and external identities can be reconstituted on an authorized host independent of AXIS Core/runtime host?

**PARTIAL.**

- **Source:** substantially portable. Canonical producer repositories and pinned packages replace the rootless legacy workspace.
- **Deployment:** designed to be host-configurable under Nix/Home Manager, but current runtime activation and root-owned signed deployment identity are unproved.
- **Durable state:** axis-control primarily reconstructs from GitLab; Alpha0's existing SQLite is structurally portable. Qualified pending-event export and Hermes semantic restore remain incomplete.
- **Secrets:** metadata requirements and existing Home/Alpha0 SOPS authorities are known, but axis-control's managed GitLab mapping and several integration producers are incomplete. Exposed legacy provider credentials require reissue.
- **External identities:** Slack Socket Mode is not inherently hostname-bound, but exact route ownership, credential custody, OAuth grants, and callback exceptions require acceptance.
- **Host independence:** canonical axis-control can be separate from `AXIS_CORE`. Complete Alpha0 supervision cannot currently cross that boundary because it reads controller/Hermes files and SQLite locally.

The answer must remain **PARTIAL** until all six explicit blockers are closed: cross-host supervision, managed secret mapping, Hermes semantic restore, signed deployment, custody convergence, and exact placement evidence.

## Evidence scale

- `YES`: the current canonical contract/evidence supports the statement, though activation gates may still apply.
- `PARTIAL`: a supported core exists, but required operational evidence or one part of the behavior is missing.
- `NO`: the current contract does not provide the behavior.

## Fourteen final questions

### 1. Which axis-control behaviors currently require Ghost specifically?

**PARTIAL — canonical: none by hostname; current legacy deployment: several by path/state.**

Canonical GitLab observation, controller state, signed trust, and package execution can be provisioned on another conforming Linux/Nix host. The report-only watchdog requires co-location with its Hermes profile because it reads local jobs JSON, execution SQLite, gateway state/heartbeat, PID, and `/proc`; it does not require Ghost by name. Captured legacy behavior does require Ghost's user checkout, `.venv`, Home checkout, user PATH, multiple Hermes homes, local refs/worktrees, and legacy units. Those are `LEGACY_COUPLING_TO_REMOVE`, not product requirements. Evidence: `HOST-COUPLING-AUDIT.md` and the current dormant assertions in `flake.nix`.

### 2. Which Alpha0 behaviors currently require Ghost specifically?

**PARTIAL — canonical: none by hostname; current data, routes, and supervision are host-local.**

Alpha0 paths and listeners are configurable, and its SQLite backup is structurally compatible with canonical code. Current durable database/Hermes state, dedicated Slack route, legacy checkout-bound wrappers, manual GitLab relay, and local axis-control supervision artifacts reside on or are rendered for Ghost. The cross-host supervision gap is a product-contract gap; the remaining names/paths are deployment or legacy coupling. Evidence: `ALPHA0-REAL-DATA-RECOVERY.md`, `HERMES-GATEWAY-OWNERSHIP.md`, and `HOST-COUPLING-AUDIT.md`.

### 3. Which dependencies can already be expressed entirely as deployment configuration?

**YES, for the following bounded set.**

- owner/XDG data homes and Hermes home/profile;
- GitLab DNS hostname within canonical HTTPS/implicit-443 policy;
- GitLab token reference and trusted PO identity list (consumer side; managed producer mapping remains incomplete);
- Alpha0 SQLite/config paths and loopback listener selections;
- Home Manager packages, user-systemd units, clean environment allowlists, and disabled service gates;
- Slack/API/provider secret references and profile routing declarations;
- separately bounded forced-command node and forwarding-relay SSH identities, known hosts, and repository/cache paths;
- backend endpoints, TLS/namespace/secret references as axis-lab inventory inputs, without implying admission.

Fixed `/etc/axis-control` trust files, Nix closure identity, local procfs for the watchdog, Alpha0 SQLite semantics, and storage authority rules are product/platform contracts rather than arbitrary deployment configuration.

### 4. Which legacy filesystem couplings must be removed?

**YES — the removal set is identified.**

Remove runtime dependence on the rootless axis-control workspace, personal AXIS/Git checkouts, checkout-local `.hermes`, mutable `.venv`, user-specific profile binaries/PATH, mutable Home checkout, hard-coded Alpha0 checkout/config wrappers, overlapping Hermes roots, raw `.env`, and temporary worktrees. Preserve unique commits/evidence owner-only until custody acceptance; do not delete first or import these paths into canonical deployment. The disabled legacy Home module remains a regression hazard and must never be re-enabled as the portability solution.

### 5. Which Hermes artifacts actually need state migration versus recreation?

**PARTIAL — the disposition is complete, but semantic restore is unproved.**

Migrate only accepted generic interaction continuity and Alpha0 profile sessions, scheduler de-duplication/ticker state, profile durable state, and non-reconstructible events through supported semantic backup/restore. Recreate package/config templates, wrappers, prompts, producer skills, units, and scheduler declarations from reviewed VCS. Reconstruct route maps and provider/status/Kanban projections from canonical declarations/data. Archive dormant AXIS/rootless histories; discard liveness/cache/scratch after acceptance; reissue every secret map/environment credential. The artifact-by-artifact decision is in `HERMES-STATE-MIGRATION.md`.

### 6. Which secrets must move if axis-control changes hosts?

**PARTIAL — requirements are known; one managed mapping is incomplete.**

Provision the managed least-privilege GitLab token, trusted PO identity configuration, signed deployment trust/allowed-signer material, and any separately approved future interaction/provider identity. No secret value or runtime environment file moves. The canonical observer does not need AXIS Core/backend or Slack/provider credentials unless those adapters are separately graduated. Axis-control's `GITLAB_TOKEN` consumer exists, but its complete reviewed Home SOPS destination mapping does not.

### 7. Which secrets must move if Alpha0 changes hosts?

**PARTIAL — the set is known; provider authorization and exact enabled integrations remain gated.**

Provision the audit-key reference; distinct Core and gateway secret maps; dedicated Slack bot/app/member identity; API server key; selected model-provider credential; separate service-scoped `gitlab.com` and `git.ap.org` observer credentials where enabled; independently authorized forced-command node and forwarding-relay SSH identities where required; and OAuth/provider identities for each enabled integration. The operator `glab_cli_config` cannot substitute for either observer service credential, and neither SSH principal authorizes the other operation. Use Home/Alpha0 SOPS/sops-nix for managed files. The SQLite database is durable data, not a secret authority, but transfer must be encrypted and owner-only.

### 8. Which credentials require rotation rather than migration?

**YES — rotation/reauthorization rules are explicit.**

Unconditionally rotate/reissue the provider credential evidenced in mode-`0644` legacy environment files, Alpha0 API server key, axis-control GitLab token from unverified legacy custody, per-host LiteLLM key, each destination-specific forced-command node or forwarding-relay SSH identity, cookie signing secret, and narrowly scoped Cloudflare/GitHub credentials where host/custody changes. Reauthorize AWS SSO, Google OAuth, Microsoft Graph OAuth, and AP GitLab operator sessions rather than copying caches. Alpha0's two GitLab observer service credentials are separately reusable only after custody and read-only scope proof; otherwise rotate/reissue each independently. Other keys are reusable only after explicit custody/scope/registration proof. See `SECRET-REQUIREMENTS-MANIFEST.md`.

### 9. Can Slack bot identity survive a host move without functional change?

**PARTIAL.**

The logical app/bot identity can survive because the observed integration uses outbound Slack Socket Mode and no host-bound callback listener. Functional continuity still requires policy-permitted credential reuse or reissue, exactly one source/destination route owner, dedicated Alpha0 profile/session semantic restore, and verification that no separately configured callback/DNS allowlist exists. The source must disconnect before the destination connects.

### 10. Can canonical axis-control run on a different host while AXIS Core remains on Ghost?

**YES, for canonical controller/observer functions.**

It uses GitLab over HTTPS and its own configurable local state; it does not call AXIS Core, read the AXIS Core SQLite database, or require an AXIS checkout. The destination still needs a conforming Unix/Nix runtime, local locking, managed GitLab/PO configuration, and root-provisioned signed trust. Its Hermes dogfood watchdog must be co-located with the observed Hermes profile or redesigned; that profile need not share the AXIS Core host.

### 11. Can canonical Alpha0 supervise axis-control over a host boundary?

**NO, not completely under the current contract.**

Alpha0 can fetch bounded GitLab truth remotely and can execute separately bounded SSH work packages. Full AXIS supervision also reads axis-control roadmap/handoff/scheduler-health JSON and local Hermes jobs/execution SQLite. No authenticated bounded remote status/provenance operation exports that state. GitLab-only health cannot prove controller scheduler health, exact controller deployment, custody, or meaningful progress. Do not substitute shared writable homes, NFS, or remote live SQLite.

### 12. Can Alpha0 move using its existing SQLite architecture without simultaneously changing its storage design?

**YES.**

The online backup was structurally compatible with canonical schema v5: integrity and foreign keys passed, expected tables/columns/relationships matched, and disposable initialization made no migration/count change. A future move needs a final backup after write quiescence, encrypted transfer, destination mode `0700` directory/database `0600`, signed audit-key open, and one-writer proof. SQLite remains Alpha0's current migration backend. PostgreSQL or Supabase is only a possible future Alpha0 backend candidate subject to a separate Alpha0 storage evaluation, migration, parity, restore, rollback, and authority-transfer gate. It is explicitly out of scope for host migration and does not imply sharing any AXIS Supabase project, database, schema, credential, writer, or backup boundary.

### 13. What exact migration acceptance proves there is only one authority after cutover?

**PARTIAL — the proof contract is defined but has not been executed.**

For `AXIS_CONTROL`: exact source scheduler/recovery identities are absent or disabled; no source controller/reconciler process or new epoch appears across two former scheduler intervals and one watchdog interval; exactly one destination scheduler and one route owner exist; source/destination custody tuples and stable GitLab digests match; no duplicate branch/MR/event appears.

For `ALPHA0`: source Core/gateway/schedules cannot restart; source database metadata remains unchanged after final backup; exactly one destination SQLite writer and one dedicated route owner exist; signed audit opens at the accepted head; destination progression produces no duplicate delivery; generic/AXIS gateways do not absorb traffic.

A root-owned signed receipt must bind logical roles, exact producer/deployment closures, source stop evidence, canonical digest/custody, final backup/audit metadata, destination service/scheduler/route identity, secret references, observation window, reviewers, and rollback boundary. Independent verification completes the proof.

### 14. What placement decisions remain properly owned by axis-lab rather than this recovery effort?

**YES — ownership boundaries are explicit; choices remain undecided.**

Axis-lab owns active host/DR selection; local versus managed provider; region/tier/account; endpoint/secret injection; persistent disk and backup sizing; firewall/TLS/Tailscale/cloudflared/DNS routing; database/schema/collection/namespace; outage-domain and resource-pressure validation; DR activation; provider admission; budget/quota/free-tier suitability; and interruption/degradation/rollback evidence. Recovery preserves governance: PostgreSQL needs explicit authority transfer, vector/graph are projections, cache is ephemeral. This report does not choose any placement.

## Expanded host-loss acceptance

### Required recovery inputs

| Product | Required inputs independent of a lost host | Inputs explicitly not required/forbidden |
|---|---|---|
| `AXIS_CONTROL` | Reviewed canonical VCS; pinned deployment definitions/closure; managed secret references; GitLab canonical truth; root-owned signed deployment trust; qualified versioned export of any non-reconstructible pending event | Ghost; rootless workspace; local AXIS checkout/Core filesystem; board/task projection; caches; worktrees; PID/lock/heartbeat; raw `.env`; hand-edited scheduler state |
| `ALPHA0` | Reviewed canonical VCS; pinned deployment definitions/closure; managed audit-key/Core/gateway/integration references; final validated SQLite online backup; qualified Hermes durable-state semantic backup; external app/OAuth identity records | Ghost; legacy wrappers/checkouts; database-backend redesign; whole-home copy; OAuth/AWS caches; ephemeral Hermes state; raw environment; unclassified sessions/logs |

### Acceptance matrix

| Acceptance area | Required proof | Current result |
|---|---|---|
| Canonical source | Exact producer commits in reviewed canonical history or independently approved immutable pins; required CI and exact-head review | `PARTIAL`: producer sources exist; current Home candidate/runtime binding is not fully accepted |
| Deployment identity | Root-owned signed manifest/trust binds source, package/interpreter closure, configuration references, entrypoints, services and scheduler identities | `NO`: deployed identity remains unproved |
| Host-independent install | Fresh authorized host installs with no legacy workspace, source-host path, or AXIS Core filesystem dependency | `PARTIAL`: source/Home assertions support it; fresh-host acceptance not signed |
| Axis-control reconstruction | Two complete stable GET-only GitLab reads; pending events use versioned idempotent import; no action on incomplete custody/review/current-main | `PARTIAL`: stable reconstruction was observed; current complete custody and event export remain open |
| Alpha0 SQLite restore | Final post-quiescence online backup; integrity/FK/schema/migrations/aggregates; signed audit open; no initialization mutation; one writer | `PARTIAL`: structural disposable test passed; final quiesced backup/signed open/one-writer proof remain |
| Hermes continuity | Complete artifact classification plus supported semantic backup/disposable restore for selected sessions/profile/scheduler state | `NO`: classification exists, semantic restore acceptance does not |
| Secret provisioning | Every consumer maps to managed Home/Alpha0 SOPS reference; destination modes pass; rotations/reauthorizations complete; no Git/store secret | `PARTIAL`: broad authority exists; axis-control and discovered integration mappings remain incomplete |
| External identity | Exactly one Slack route; profile/session isolation; OAuth/callback/DNS actions complete | `PARTIAL`: topology is understood; exact identity/exclusivity remains unproved |
| Cross-host supervision | Authenticated bounded schema-versioned controller status/provenance with freshness, host/deployment identity, and fail-closed unknowns | `NO`: current Alpha0 reader is filesystem-local |
| Custody | Every lineage has complete remote/local disposition; no active worker/reviewer/CI interrupted; stable canonical digest | `PARTIAL`: three captured lineages depended partly on local custody; fresh convergence required |
| Placement | Destination logical-role binding, network/persistence/capacity/availability evidence, axis-lab admission where applicable | `NO`: no final placement and exact evidence are accepted |
| One-authority cutover | Source no-admit/no-write observation, destination sole scheduler/writer/route, signed receipt, independent verification, safe rollback boundary | `NO`: design exists; execution is unauthorized/incomplete |

### Required host-loss rehearsal

A portable acceptance rehearsal must use an isolated fresh authorized host or equivalent clean environment and must:

1. install exact signed producer/deployment closures without access to Ghost or a legacy workspace;
2. provision only managed references into mode-`0700` runtime directories and files mode `0600` or stricter;
3. reconstruct axis-control through two stable GET-only reads while `AXIS_CORE` remains elsewhere;
4. restore Alpha0 from a disposable copy of the qualified SQLite backup without schema/backend change or external mutation;
5. perform signed audit open with managed key and prove wrong/missing key fails closed;
6. restore selected Hermes durable artifacts semantically and prove namespace/routing/scheduler de-duplication;
7. prove Slack/external route ownership without overlapping the source;
8. demonstrate source no-admit/no-write and destination sole authority over the required observation window;
9. produce and independently verify the metadata-only signed receipt;
10. exercise rollback at the declared boundary without creating dual writers or a forked audit chain.

## Conservative final disposition

`PORTABLE_MANAGEMENT_PLANE = PARTIAL` and `CUTOVER_READY = NO`.

Canonical architecture no longer justifies treating Ghost as product authority. It does not yet justify treating host loss as fully accepted. Cross-host supervision, managed secret mapping, Hermes semantic restore, signed deployment, custody, and exact placement evidence must close through normal reviewed producer/Home/axis-lab work and an authorized rehearsal. No final placement is decided by this report.
