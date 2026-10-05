# Host-coupling audit

Observed from credential-free repository and sanitized evidence available on 2026-08-20. This report classifies host coupling; it does not select a destination or authorize a deployment.

## Logical roles

Product and deployment reasoning uses logical roles, never a hostname as authority:

- `AXIS_CORE`: AXIS application process and its local SQLite boot/continuity substrate.
- `AXIS_CONTROL`: the sole AXIS SDLC controller and its report-only Hermes observer profile.
- `ALPHA0`: Alpha0 Core, dedicated interaction gateway, and read-only AXIS supervision.
- `AXIS_POSTGRES`: optional shared durable PostgreSQL custody after a governed authority transfer.
- `AXIS_VECTOR`: rebuildable vector projection.
- `AXIS_GRAPH`: rebuildable graph projection.
- `AXIS_CACHE`: ephemeral cache/coordination service.

A current hostname is deployment evidence only. It never changes the authority assigned to a logical role.

## Classification

| Classification | Meaning |
|---|---|
| `REQUIRED_PRODUCT_CONTRACT` | Semantics the product currently requires regardless of host. |
| `DEPLOYMENT_CONFIGURATION` | A required but selectable path, endpoint, identity, package, or launcher input. |
| `LEGACY_COUPLING_TO_REMOVE` | A captured host, user, checkout, or layout dependency that must not be ported. |
| `EPHEMERAL` | Recreated process, cache, lock, heartbeat, test, or scratch state. |
| `UNKNOWN` | Evidence or ownership is not complete enough to migrate or discard. |
| `NOT_PRESENT` | An explicitly checked coupling is absent from the inspected canonical source. |

## Canonical axis-control coupling

| Dependency | Classification | Evidence and consequence |
|---|---|---|
| GitLab HTTPS API, DNS hostname, and implicit TCP 443 | `REQUIRED_PRODUCT_CONTRACT` | `src/axis_control/gitlab.py` in the audited producer accepts DNS HTTPS without URL syntax, redirects, IP literals, or explicit ports. Canonical truth is remote; a localhost relay with a custom port is not compatible. |
| GitLab projects `ghostspace/{axis,axis-governance,axis-lab}` | `REQUIRED_PRODUCT_CONTRACT` | The audited `bootstrap.py` defines the current topology. This is product topology, not a checkout dependency. |
| `GITLAB_TOKEN`, `GITLAB_HOST`, and `AXIS_CONTROL_PO_USER_IDS` | `DEPLOYMENT_CONFIGURATION` | The process consumes environment metadata/credentials. It does not require a Ghost file or persist the token. The PO allowlist fails closed when absent. |
| `AXIS_CONTROL_HOME`, XDG data home, and user home fallback | `DEPLOYMENT_CONFIGURATION` | The audited `storage.py` selects an owner-writable state root. JSON, journal, and telemetry are controller-local but reconstructable except for qualified pending events. |
| Unix advisory locks and atomic rename semantics | `REQUIRED_PRODUCT_CONTRACT` | Current journal/preflight code uses `fcntl` and a local Unix filesystem. Distributed/shared filesystem semantics are not qualified. |
| `/etc/axis-control/deployment-manifest.json` and `/etc/axis-control/deployment-trust.json` | `REQUIRED_PRODUCT_CONTRACT` | These fixed root-provisioned paths are the production trust boundary, portable across conforming Linux hosts but not satisfied by copying user state. |
| Nix store closure and `ssh-keygen` verification tool | `REQUIRED_PRODUCT_CONTRACT` / `DEPLOYMENT_CONFIGURATION` | Attested production requires immutable executable/interpreter identity; the verifier must be in the closure or deployment PATH. |
| `HERMES_HOME` and `AXIS_CONTROL_HERMES_PROFILE` | `DEPLOYMENT_CONFIGURATION` | The audited CLI and Home module default to a user Hermes home and profile `axis-control`; neither requires a Ghost hostname. |
| Hermes jobs registry, execution SQLite, gateway state, heartbeat, and signed wrapper | `REQUIRED_PRODUCT_CONTRACT` for the report-only watchdog | The audited watchdog reads profile-local files and a specific execution schema. These are dogfood health evidence, not AXIS execution authority. |
| Linux PID signaling and `/proc/<pid>` identity | `REQUIRED_PRODUCT_CONTRACT` for the current watchdog | Local PID/start-time/cmdline checks mean the watchdog must be co-located with its Hermes profile. A remote mount does not satisfy process identity. |
| Home Manager and user systemd | `DEPLOYMENT_CONFIGURATION` | They are the supplied Linux integration. Manual equivalent observation is possible, but no non-systemd production launcher is currently qualified. |
| AXIS Core filesystem, database, listener, socket, or checkout | `NOT_PRESENT` | The canonical controller does not call `AXIS_CORE`, read its SQLite database, or require its checkout. |
| Controller-owned listener or localhost application dependency | `NOT_PRESENT` | Canonical controller source exposes no application listener. |
| `.env` ingestion | `NOT_PRESENT` | Canonical code reads an externally prepared process environment and rejects `.env` source artifacts. |
| Test homes, fixtures, `/tmp`, fake SQLite, and rehearsal directories | `EPHEMERAL` | These exercise isolation and interpolation; they are not production defaults. |

The current Home composition supports this direction: `hosts/nixos/ghost-home.nix` disables the legacy dedicated AXIS gateway and recovered supervisor and enables only `services.axis-control-observer`. `flake.nix` asserts no checkout path in rendered services, no observer/watchdog timer, a Nix-store wrapper, and dormant Alpha0 services. `systemd.user.startServices = false` means source composition is not runtime activation proof.

## Canonical Alpha0 coupling

| Dependency | Classification | Evidence and consequence |
|---|---|---|
| Alpha0 Core SQLite database | `REQUIRED_PRODUCT_CONTRACT` for current durable state | The path is XDG/configurable. The structurally tested Ghost backup had canonical schema v5 and requires no backend migration. Host migration and database-backend migration are separate transitions. |
| Audit signing key reference | `REQUIRED_PRODUCT_CONTRACT` | Signed audit open must fail closed with a missing/wrong key. The key is externally provisioned; it is not database content. |
| Alpha0 dedicated Hermes owner home/profile | `DEPLOYMENT_CONFIGURATION` for interaction continuity | Profile isolation and routing preflights are required, but declarations reconstruct from reviewed VCS with fresh runtime state. Source sessions and scheduler history are archive-only under current qualification; never bulk-copy the home. |
| Core `127.0.0.1:8040`, Hermes `127.0.0.1:8642/8643`, optional service loopbacks | `DEPLOYMENT_CONFIGURATION` with intentional local isolation | Loopback protects the boundary but requires co-located clients or an explicitly reviewed proxy. It does not require Ghost by name. |
| GitLab API and optional `127.0.0.1:19443` TLS relay | `DEPLOYMENT_CONFIGURATION` | Direct trusted routing is portable. The current manual Ghost-to-Nyx relay in `hosts/nixos/ghost-home.nix` is host-specific and gives API reachability only. |
| Local axis-control roadmap/handoff/scheduler JSON and Hermes execution SQLite | `LEGACY_COUPLING_TO_REMOVE` from cross-host supervision | The normal Alpha0 status caller still joins local files with GitLab. Merged axis-control main `830b6432a758a633afbf2f3127ceb3dfeba340d7` and Alpha0 main `94e90beb00c46bca74f927437e1c8805eb64d099` prove the bounded transport-neutral replacement contract, but the normal caller is unintegrated and no signed live deployment has exercised it. |
| Remote forced-command SSH work packages | `REQUIRED_PRODUCT_CONTRACT` for remote work execution, not supervision | The audited node transport binds request/result digests with strict host keys and bounds. `hosts/nixos/nyx-alpha0-node.nix` supplies a hardened node, but no axis-control status operation. |
| Slack app/bot identity, API key, provider credentials, OAuth grants | `DEPLOYMENT_CONFIGURATION` | External identities are re-provisioned through managed authorities. Socket Mode has no host callback, but route exclusivity and credential custody still gate a move. |
| Home Manager, Nix, user systemd, `/run/secrets`, clean environment | `DEPLOYMENT_CONFIGURATION` | The canonical module supplies this Linux deployment contract. No macOS/non-systemd launcher is qualified. |
| Provider observations, daily/status projections, derived Kanban views | `DEPLOYMENT_CONFIGURATION` | Re-read or rebuild from canonical sources; do not promote them to durable authority. |
| Host-local OAuth caches and AWS SSO cache | `LEGACY_COUPLING_TO_REMOVE` as transferable state | Reauthorize on the destination. Cached sessions are not migration inputs. |

### Cross-host supervision gap

The `ALPHA0` cross-host supervision **contract is `PROVEN`**. Merged mains and a disposable private-network proof show that `axis-control.supervision.v1` replaces controller filesystem, process, Hermes database and worktree reads and fails closed for drift, missing/contradictory evidence and unavailability. Cross-host **deployment is `PARTIAL`** because the normal status caller still uses the local path, managed authenticated transport/injection is unselected, and no signed live deployment has exercised the response. Copying or remotely mounting live SQLite is forbidden.

## Legacy and captured coupling to remove

| Coupling | Classification | Required disposition |
|---|---|---|
| `/home/cdenneen/src/workspace/work/axis-control` as cwd, script root, virtualenv, state root, Hermes home, and worktree base | `LEGACY_COUPLING_TO_REMOVE` | Deploy pinned packages/configuration. Archive unique evidence; never make the checkout a runtime dependency. |
| Other personal AXIS/Git checkouts and repository-name assumptions | `LEGACY_COUPLING_TO_REMOVE` | Use GitLab and bounded, custody-bound ephemeral clones only after mutation graduation. |
| `/etc/profiles/per-user/cdenneen/bin/*`, user-specific PATH, mutable `.venv` | `LEGACY_COUPLING_TO_REMOVE` | Use the reviewed package closure and explicitly injected PATH. |
| Mutable Home checkout used by deployment behavior | `LEGACY_COUPLING_TO_REMOVE` | Deployment must come from an exact reviewed Home revision and signed closure. |
| Hard-coded Alpha0 status/SITREP checkout and config paths | `LEGACY_COUPLING_TO_REMOVE` | Invoke canonical installed wrappers with explicit configuration and audit-key references. |
| Legacy `127.0.0.1:8780` health call | `LEGACY_COUPLING_TO_REMOVE` unless separately approved | Define an authenticated adapter contract before retaining it. |
| Root, profile, checkout-local, and dedicated Alpha0 Hermes homes treated as interchangeable | `LEGACY_COUPLING_TO_REMOVE` | Preserve namespaces. Reconstruct route ownership; never merge homes. |
| Mode-`0644` credential-bearing legacy `.env` files | `LEGACY_COUPLING_TO_REMOVE` | Never migrate. The provider credential referenced there is `ROTATE_AND_REISSUE`; use managed external files at `0600` or stricter. |
| AWS/Bedrock provider switches embedded in the AXIS gateway unit | `LEGACY_COUPLING_TO_REMOVE` from `AXIS_CONTROL` | Keep non-secret switches in deployment config only if a future approved adapter needs them; reauthorize credentials separately. |
| Legacy gateways, model-waking schedules, supervisors, watchdogs, provisioners, and recovery timers | `LEGACY_COUPLING_TO_REMOVE` | Drain by exact identity. Canonical report-only observer remains unscheduled until separate graduation. |
| Synthetic `axis-supervisor@localhost` Git identity | `LEGACY_COUPLING_TO_REMOVE` | A future mutator needs an independently reviewed deployment identity. |
| Board/task/checkpoint JSON, review corpus, event/log transcripts, rootless state | `UNKNOWN` or evidence-only | Preserve owner-only where unique, but do not import it as controller authority. |
| PID files, locks, heartbeats, caches, WAL/SHM after checkpoint, temporary worktrees | `EPHEMERAL` | Recreate or discard after quiesced acceptance. |

The disabled `modules/hm/users/cdenneen/hermes-axis-control-gateway.nix` still contains checkout, username, profile-binary, AWS, and mutable environment coupling. It is inert under `hosts/nixos/ghost-home.nix`, but re-enabling it would restore the defect.

## Portability conclusions

- `AXIS_CONTROL` canonical SDLC observation can run away from `AXIS_CORE`: **YES**, when direct GitLab HTTPS, managed credentials, owner-only local state, Nix closure, and root-owned signed trust are provisioned.
- The current report-only Hermes watchdog can observe a remote Hermes profile: **NO**. It is intentionally host-local through SQLite, PID, and procfs.
- `ALPHA0` can move without changing SQLite architecture: **YES**, subject to final quiesced backup, audit-key verification, exclusive Hermes route/job reconstruction and one-owner integration proof.
- `ALPHA0` can supervise remote `AXIS_CONTROL` through the existing bounded contract: **YES, contract proven**. Production deployment remains **PARTIAL** pending managed authenticated transport/injection, normal-caller integration and signed live deployment.
- Ghost-specific canonical product behavior: **none identified by hostname**. Remaining Ghost dependence is deployment state, legacy paths, current routing, credentials, local durable data, and unimplemented cross-host supervision.

This classification is complete for the inspected canonical references, current Home composition, sanitized evidence inventory, and six supplied portability audits. Runtime activation and uninspected secret/database/session payloads remain outside scope.
