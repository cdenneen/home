# Live Ghost reconciliation baseline

Observed 2026-08-20. This is a credential-free, point-in-time recovery record. It does not authorize activation or decommission.

## Authority boundary

| Concern | Authority | Status in this report |
|---|---|---|
| axis-control source and controller contracts | `ghostspace-com/axis-control` | Canonical source. Main/base is `ba7e03ecce879be7047263b827d4a4dba8fd8527`; approved [PR #3](https://github.com/ghostspace-com/axis-control/pull/3) head is `4c25bc19040295fc6579dde9c6831ef143d298d5`; exact-head review anchor is [4978762677](https://github.com/ghostspace-com/axis-control/pull/3#pullrequestreview-4978762677). |
| Alpha0 source and supervision contracts | `ghostspace-com/alpha0` | Canonical source. Main/base is `c6dc926e8e3622ca5f9e9ac6f3dbc78cf43c9254`; approved [PR #2](https://github.com/ghostspace-com/alpha0/pull/2) head is `c000ed805b9231e39b8240469ca398a19e006aed`; exact-head review anchor is [4978762733](https://github.com/ghostspace-com/alpha0/pull/2#pullrequestreview-4978762733). |
| Ghost deployment composition | `cdenneen/home` | Deployment authority. Main at capture is `d43652b39e8f6b729288859c03916fc7bfd29f80`. |
| AXIS issues, branches, merge requests, pipelines and default branches | GitLab | Canonical execution truth. Runtime boards, Markdown and controller caches are derived evidence only. |
| Ghost rootless axis-control workspace and legacy Hermes/Alpha0 runtime | none | Prior art and operational evidence, never source or deployment authority. |

[Home PR #681](https://github.com/cdenneen/home/pull/681) is the dormant integration candidate. Its captured head is `0c2e875d7bf170eb34d06a309346e991412e125a`, based on `7612277b171fd397da1808b63c8a19aab4ac185f`, and it pins the two producer PR heads above. It is `DIRTY` against current Home main and its last approval was for stale head `de20d3b10cc7f4a21f384ad11d9acea9376aedf7`. It is not activation approval.

**Deployment is `UNKNOWN`.** No independently signed, root-owned evidence binds Ghost's installed package/configuration to these commits. **Cutover is forbidden.** No Home switch, service/job change, worker interruption, GitLab write, merge, credential rotation or runtime deletion was performed by this reconciliation.

## Captured evidence

The quarantine at `/home/cdenneen/src/workspace/gss-ops/evidence/ghost-legacy-20260820` contains 80 credential-free copied candidates: 71 axis-control and 9 Alpha0/deployment files. Verification passed with exact source/copy hash parity, mode `0700` directories, mode `0600` files, no scanner matches, no forbidden paths and no staged files. Three separately authorized private Alpha0 recovery files under `alpha0-private/` were excluded from the source manifest and are not in Git.

The live Alpha0 database was backed up with SQLite's online backup API and tested only through disposable offline copies. Structural compatibility with canonical Alpha0 head passed: 27 tables, 39 foreign keys, no column or foreign-key drift, integrity `ok`, zero foreign-key violations and no migration required. Audit HMAC verification remains intentionally unperformed because no production key was read.

## Live topology at capture

Ghost had three active Hermes gateways:

1. generic `hermes-gateway.service`, owner home `~/.hermes`;
2. legacy `hermes-axis-control-gateway.service`, axis-control profile and rootless workspace cwd;
3. legacy `hermes-alpha0-gateway.service`, dedicated Alpha0 home, external supervisor and loopback API.

The sanitized quarantine metadata captured five enabled Hermes jobs across three registries: two generic-root legacy AXIS jobs, one root-profile PO alert job, and two dedicated Alpha0 report/SITREP jobs. A separate live trigger-path review identified a sixth enabled job in a fourth registry under the rootless checkout: five-minute `no_agent=false` job `81776a5f93c5`, which could wake mutating roadmap work. Legacy AXIS watchdog timers were also enabled. PR #681's target instead leaves the generic gateway intact while keeping all producer Core/gateway/scheduler gates dormant and retaining only an unscheduled report-only axis observer definition.

## Canonical GitLab reconstruction

Two isolated GET-only canonical reads were complete and replay-safe. Both yielded observation digest `sha256:1c595cde7ccb2498c4c5a4e27152b98d32ffed5d1bc86f290ad06af7af94f8df`; the second preflight was `NO_OP`. No dispatch, mutation or merge path ran.

Independent GETs matched canonical collection for all three project identities, default-branch heads, issue/MR/branch counts, open-MR IID/head pairs and current-main pipeline facts. At capture GitLab contained 102 open issues, 27 branches and 12 open MRs: 11 in `ghostspace/axis`, none in `ghostspace/axis-governance`, and one in `ghostspace/axis-lab`. Review qualification and custody for every open MR, and current-main verification for every project, remain `UNKNOWN`; therefore the 15 shadow transitions are observation debt, not authority to act.

## Reconciliation findings

- Canonical lifecycle, exact-head review, PO evidence, custody, merge receipt/current-main checks, secure storage, durable event journal and non-mutating watchdog supersede weaker legacy behavior.
- Useful missing concepts are typed dependency/frontier semantics, bounded review convergence, binding-driven historical MR recovery, qualified CI-infrastructure classification, future feature-push fences and PO-facing delivery UX. They require new canonical evidence contracts and tests; legacy implementations must not be copied wholesale.
- Reject legacy free-text/note authority, local Markdown review qualification, token/branch-based custody, merge-SHA-only completion, immediate dependency release, stale pipeline fallback, bounded lossy pending queues, pre-delivery notifier state advancement and mutable auto-restarting watchdog logic.
- Nine live/recent lineages were mapped. Six were fully remote recoverable and three only partially remote recoverable at capture. A live reconciler, active workers and unconverged duplicate cards made a cutover unsafe.
- Alpha0 real data is structurally compatible, but signed Core-open, exclusive Hermes route/job reconstruction with fresh runtime state, scheduler graduation and live gateway/provider behavior remain unverified. No Alpha0 Hermes runtime state is currently a mandatory migration input.
- Dedicated Alpha0 routing/session isolation and generic gateway continuity are required. One channel/chat must have exactly one gateway owner; overlapping fallback routes are forbidden.

## Readiness matrix

| Classification | Result | Evidence / blocker |
|---|---|---|
| `CANONICAL_AXIS_CONTROL_SOURCE` | `MERGED_NOT_DEPLOYED` | PRs #3, #4 and #10 merged through reviewed flow; current main `830b6432a758a633afbf2f3127ceb3dfeba340d7` passed post-merge CI and remains unactivated. |
| `CANONICAL_ALPHA0_SOURCE` | `MERGED_NOT_DEPLOYED` | PRs #2 and #4 merged through reviewed flow; current main `94e90beb00c46bca74f927437e1c8805eb64d099` passed post-merge CI and remains unactivated. |
| `LEGACY_EVIDENCE_CAPTURE` | `PASS_WITH_PRIVATE_EXCLUSIONS` | 80 source-like entries verified; private DB evidence remains out of Git by design. |
| `AXIS_CONTROL_BEHAVIOR_PARITY` | `PARTIAL` | Strong canonical safety contracts; bounded useful frontier/reconstruction/CI/UX gaps remain. |
| `ALPHA0_REAL_DATA_RECOVERY` | `PASS_WITH_LIMITS` | Structural/offline compatibility passed; HMAC-signed Core-open and exclusive Hermes route/job reconstruction remain. |
| `HERMES_ROUTING_UNDERSTOOD` | `PARTIAL` | Six live registries are mapped; provider attestation established distinct generic and Alpha0 identities and no dedicated AXIS external route. App-token correspondence/delivery readback, historical generic-session disposition, duplicate enabled scheduler records and managed reconstruction remain incomplete. |
| `HOST_COUPLING_CLASSIFIED` | `PASS_WITH_LIMITS` | Canonical, deployment, legacy, ephemeral and unknown couplings are classified; the bounded cross-host supervision contract is proven on merged mains and through isolated network transport, but managed live transport/deployment remains unaccepted. |
| `SECRET_PORTABILITY` | `PARTIAL` | Metadata-only requirements and preferred Home/Alpha0 SOPS authorities are mapped; axis-control and several integration mappings/rotations remain incomplete. |
| `HERMES_STATE_PORTABILITY` | `PARTIAL` | Required Hermes migration set is empty; isolated Alpha0/axis-control profile/job reconstruction is restart-equivalent, but the generic route declaration and any conditional selected-session input remain unqualified. |
| `CONTROL_PLANE_HOST_MIGRATION` | `PLANNED_NOT_AUTHORIZED` | The source-fencing plan deterministically covers known jobs, six registries, reprovision/recovery paths, generic continuity, aborts and rollback while retaining Alpha0 SQLite; it has not been executed. |
| `DEPLOYMENT_PLACEMENT` | `PARTIAL_UNDECIDED` | Logical roles and constraints are mapped; exact destinations and axis-lab-owned backend admission decisions remain open. |
| `PORTABLE_MANAGEMENT_PLANE` | `PARTIAL` | Source and core state paths are portable and the cross-host supervision contract is proven, but managed transport integration, secrets, signed deployment, routing and exact placement evidence remain incomplete. |
| `ACTIVE_AXIS_CUSTODY` | `FULLY_REMOTE_RECOVERABLE` | All 9 mapped work-item lineages have remote implementation custody; local review/controller evidence remains forensic and must not be fabricated in GitLab. |
| `SAFE_DRAIN_READY` | `NO` | Live new-work sources/reconciler/workers, the enabled stale checkout-root record, pending event reconciliation, managed generic reconstruction and an authorized `24h15m` no-restart observation still require a future phased drain. |
| `HOME_COMPOSITION_READY` | `NO` | PR #681 remains blocked and lacks exact-head approval/passing required CI; no Home generation was activated. |
| `SECURITY_REMEDIATION_READY` | `PLANNED_NOT_AUTHORIZED` | Paths/modes and future owner-only targets are known; values were not inspected and rotation was not performed. |
| `CUTOVER_READY` | `NO` | Deployment identity, quiescence, routing exclusivity, live authority convergence, Home review/CI, exclusive Hermes reconstruction, cross-host supervision integration and rollback gates are incomplete. Code custody is 9/9 remotely recoverable but is not cutover authority. |

## Source reports

This baseline consolidates the credential-free forensic reports named in the reconciliation task. The detailed durable records in this directory remain the controlling description; `/tmp` reports and the local quarantine are evidence inputs, not deployment inputs.
