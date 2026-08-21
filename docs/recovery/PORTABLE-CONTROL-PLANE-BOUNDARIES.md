# Portable control-plane boundary qualification

Observed read-only on 2026-08-21. This report closes the code-custody question, qualifies route ownership without changing it, records the minimum cross-host supervision prototype, and reduces the Hermes migration set. It contains no credential values, route identifiers, message/session payloads, raw scheduler output, or database content.

No worker, service, gateway, scheduler, database, credential, Home generation, GitLab object, or live Ghost state was changed. Product commits were made only in bounded canonical producer branches; no merge or activation occurred.

## AXIS custody re-observation

GitLab remains execution truth. Current remote `ghostspace/axis` main was `c8f39c9706915e3c3a6c4b63a9c38a094da1e62a`; `ghostspace/axis-governance` main was `49d1c96d747eb0b7880903ae2daa081377eff857`. Zero required/recorded GitLab approvals is reported as `GL 0/0`, never as affirmative review.

The original nine work-item lineages contain eleven separately tracked slices because axis#57 and axis#85 each have an already-merged A slice plus an open B slice. The earlier map conflated those pairs; the rows below correct their branch/MR identities.

| Work item / slice | Branch and exact durable head | MR / implementation | Review state | CI state | Expected next transition | Remaining Ghost-local state |
|---|---|---|---|---|---|---|
| axis#27 / AX27-A | `feat/axis27-axisvault-adapter-contract-20260818t1309z` at `263832e8bec1686ed90d1926627b64d1d98900f6` | !192 open draft; repair pushed | GL 0/0; prior local review is stale for this head | failed `2777554756` | repair CI, exact-head review, undraft, merge | board assignment and controller/review history only; no unique code |
| axis#45 / AX45-A | `feat/axis45-remote-node-enrollment-invitation-20260818t1416z` at `290496e0ee3d9eab7be7968ba2873f1656fd3bb9` | !198 open, conflicted; repair pushed | GL 0/0; no qualifying current-head review | failed `2777038947` | resolve conflict, repair CI, exact-head review, merge | controller/review history only; no unique code |
| axis#57 / AXIS57-A discovery | deleted source branch; implementation `3b990ec4cb987432bd38a9e0e8cda9bc1ea55110`, merge `36bce509ebc2108c633134e1ddabba85b3104091`, both on main | !191 merged; slice complete | local-only exact-head 4/5, nonblocking, slice-complete | MR failed `2769343754`; merge pipeline canceled | no A-slice code transition | review/current-main evidence and completion projection only |
| axis#57 / AXIS57-B security repair (`t_2485b494`) | `feat/axis57-external-endpoint-security-20260818t1338z` at `09d5157b99c4932306884fcd5726ae4bec3abe6b` | !193 open draft/conflicted; repair pushed | GL 0/0; current-head review attempts failed before substantive review | failed `2777181363` | restore review capability, exact-head review, resolve conflict/CI, undraft | block reason, run history and failed review attempts; no unique code |
| axis#60 / AXIS60-A | `feat/axis60-voice-privacy-guardrails-20260818t1416z` at `359b1fa4196cda989a490d2fb9a27e0083ec0126` | !196 open draft; repair pushed | GL 0/0; prior review invalidated by new head | failed `2777440554` | repair CI, exact-head review, undraft, merge | controller/review history only; no unique code |
| axis#81 transformation | implementation `b8898df77e67e403855c3432b73dcdbd29759011`, merge `c2786c975ac206a2a0acf9185c10a632755ec60c`, both on main | !184 merged; issue closure-blocked | local-only exact-head 5/5, merge, slice-complete | MR success `2767737793`; merge pipeline canceled | reconcile current-main/closure evidence | closure assignment and stale projection only |
| axis#85 / AXIS85-A inbox bound | deleted source branch; implementation `cce88792f9f913ce81cb039be6ac53ab79d074b1`, merge `623c8d7904d9e2444ebcf8ffafca3f7d105847c5`, both on main | !190 merged; slice complete | local-only exact-head 5/5, qualifying, slice-complete | MR success `2769271420`; merge pipeline failed | no A-slice code transition | review/completion record only |
| axis#85 / AXIS85-B recovery/resume | `feat/axis85-recovery-resume-proof-20260818t1457z` at `09dfb660f47e4bd74067b424d4f0873bbb1b0e53` | !200 open, mergeable; slice complete but CI-blocked | local-only exact-head 5/5, merge, slice-complete | failed `2770266245`; local classification says shared packaging drift | land shared repair, rebase/rerun CI, merge | review and drift classification only; no unique code |
| axis#97 / AX97-A | deleted source branch; implementation `72aad6a71f9bdbe9fa6bc0a8f80247244d9452c5`, merge `49205618d9c8b466368ceccffcbf6f683a1f7f77`, both on main | !189 merged; issue closure-blocked | local-only exact-head 4/5, nonblocking, slice-complete | MR success `2769234255`; merge pipeline failed `2776958874` | resolve shared main drift, reconcile/close | closure/drift evidence and stale-card cleanup only |
| axis#103 / AX103-A | `feat/axis103-deliberation-foundation-20260818t123427z` at `22384b4693a3be208f093b9c5b5d92257b45c4c1` | !188 open draft; remote head supersedes stale local head | GL 0/0; current-head local attempt failed before substantive review | failed `2776965313` | restore review capability, exact-head review, repair CI, undraft | failed review attempt/controller mapping only; no unique code |
| axis-governance#239 / candidate certification | implementation lives in axis branch `feat/axisgov239-candidate-certification-pipeline-20260818t173035z` at `bfdb864e09c4a70ae688c8547a44ac03070474e8` | axis !201 open, mergeable; implementation complete | local-only exact-head 5/5, merge-ready | failed `2770474847` | repair shared validation/CI, merge !201, reconcile governance issue | qualifying review and controller mapping only |

All open implementation heads exist on live GitLab branches/MR refs. Every merged implementation and merge commit above is contained by current `ghostspace/axis` main. No unpushed implementation commit remains among the nine mapped lineages. Dirty legacy worktrees contain either unrelated tracked deletions or a duplicated evidence document and are not legitimate checkpoint sources. No product-code checkpoint was performed.

The remaining Ghost-local records are operational provenance: board assignments/final transitions, local exact-head review records, failed review attempts, drift classifications, controller checkpoints and stale-card cleanup. Losing them would reduce forensic precision, but it would not lose an implementation head. Open heads without current review must be reviewed again; local evidence must never be promoted into GitLab as fabricated approval.

`ACTIVE_AXIS_CUSTODY = FULLY_REMOTE_RECOVERABLE`: 9/9 work-item lineages have recoverable code custody from GitLab and canonical repositories. This does not establish merge readiness, current-main correctness, route exclusivity, or safe drain.

## Sanitized route ownership

Every observed route/session origin maps to one semantic owner:

| Origin | Physical state domain | Logical owner | Qualification |
|---|---|---|---|
| Generic configured route and ten indexed sessions | generic Hermes root | `GENERIC_HERMES` | mapped |
| AXIS-profile route, eleven indexed sessions and PO-alert origin | generic root with axis-control profile | `AXIS_CONTROL` | mapped, but overlaps a separate AXIS gateway domain |
| Dedicated Alpha0 route and 26 indexed sessions | Alpha0 owner root/profile | `ALPHA0` | mapped and namespace-distinct; external app/channel identity still requires value-free owner attestation |
| Two historical generic session routes | generic Hermes root | `GENERIC_HERMES` | mapped; continuity/archive disposition pending |
| Dedicated checkout-local AXIS gateway ingress | checkout-local Hermes root/profile | `AXIS_CONTROL` | semantic owner known; provider/app route equivalence remains unattested |

No duplicate was found among the opaque configured route identifiers. Credential-free inspection cannot prove whether the generic AXIS-profile route and checkout-local AXIS gateway use the same or different external provider/app/channel identity. That requires a value-free owner attestation; secret values must not enter evidence.

### Scheduler registries

The four expected authority registries remain:

| Expected registry domain | Logical owner | Sanitized authority |
|---|---|---|
| generic root registry | `AXIS_CONTROL` for both observed legacy AXIS jobs | two enabled legacy AXIS jobs physically under the generic root |
| generic-root axis-control profile registry | `AXIS_CONTROL` | one enabled no-agent PO-alert job |
| checkout-local axis-control profile registry | `AXIS_CONTROL` | one enabled agent-waking roadmap job |
| Alpha0 owner-root registry | `ALPHA0` | two enabled no-agent projection/status jobs |

Read-only metadata also exposed additional live root/profile `jobs.json` paths. Most importantly, the checkout root and checkout axis-control profile are distinct files containing the same enabled agent-waking AXIS job identity. That is hidden duplicate persisted scheduler authority. Alpha0 also has a routed-profile live path that must be accounted for even when canonical declarations are reconstructed from its owner-root inventory. Two older `state-snapshots/*/cron/jobs.json` files are non-live `ARCHIVE_EVIDENCE`; they must not be counted or imported as scheduler authority.

Current Home and PR #681 declarations are not runtime proof: disabled/absent generated declarations do not remove persisted jobs or stop already-running gateways. No registry or gateway was changed.

`ROUTE_OWNERSHIP = PARTIAL` because semantic ownership is mapped, but checkout scheduler duplication, generic/AXIS route equivalence, dedicated Alpha0 external identity, historical generic-session disposition and live declaration/runtime convergence remain unproved.

## Minimum cross-host supervisory contract

Two bounded stacked producer PRs prototype the transport-neutral boundary:

- axis-control PR #10, head `63cefcb9639b7b8a0fa5a1822e06a158699a1ea5`, exposes `axis-control.supervision.v1` through the existing JSON status CLI;
- Alpha0 PR #4, head `3c0a5bae6242f0956782fd0ca36b9f5a747e07ea`, strictly consumes a supplied response and supplied GitLab observation.

The response is capped at 64 KiB and contains only controller revision, verified deployment identity, watchdog-derived runtime health, correctness/completeness health, deterministic frontier digest, bounded active-lineage summary, committed-transition evidence or `UNKNOWN`, bounded drift/PO summaries and observation time. It excludes paths, PIDs, argv, commands, worktrees, Hermes rows and credentials.

The Alpha0 proof blocks controller path construction, artifact reads, SQLite, process/subprocess and local filesystem access. With only a supplied producer response and bounded GitLab observation it reproduces revision/deployment/runtime/correctness/frontier/lineage/transition/drift/PO assessment, detects exact-head disagreement, and retains `mutation_performed=false`. Missing/headless GitLab evidence, missing project coverage, malformed/contradictory producer evidence and omitted summaries fail closed. No transport framework or live service was added.

`CROSS_HOST_ALPHA0_SUPERVISION = PARTIAL`: the pure information boundary is proven by tests, but both PRs are unmerged, the normal Alpha0 status caller still uses the legacy local path, an authenticated transport/injection mechanism is intentionally unselected, and no signed live deployment emitted this response.

## Reduced Hermes migration set

Canonical reconstruction eliminates most runtime-state migration:

| Domain | Required semantic migration | Reconstruct/archive/discard |
|---|---|---|
| `GENERIC_HERMES` | none currently qualified; an owner-selected subset of generic unprofiled Slack sessions is conditional only if continuity cannot safely restart | reconstruct route map from an owner-approved declaration; recreate jobs/config; archive execution history; discard liveness/locks/cache |
| `AXIS_CONTROL` | none | reconstruct execution/custody from GitLab and canonical producer; archive sessions, execution DB, ticker and reconciled legacy reviews/state when retention requires, but never import them as live authority; recreate any future scheduler disabled with a fresh identity; discard only worktrees, PID/heartbeat/locks/cache after custody and retention acceptance |
| `ALPHA0` Hermes | none currently qualified | reconstruct routes/jobs/projections; archive current sessions/executions; discard liveness/locks/cache; no pending Hermes obligation was qualified |

Alpha0 Core SQLite remains required application durable state and uses its separately managed audit-key authority, but it is not Hermes state. It must move only through the already-defined supported final backup/restore procedure in a separately authorized host migration.

`HERMES_DURABLE_STATE_REQUIRED = NONE_CURRENTLY_QUALIFIED`; conditional candidate: owner-selected generic unprofiled Slack session subset after explicit continuity need and disposable semantic-restore proof.

## Completion state

```text
ACTIVE_AXIS_CUSTODY:
  FULLY_REMOTE_RECOVERABLE: 9/9
  PARTIAL: 0/9
  LOCAL_ONLY_RISK: 0/9

ROUTE_OWNERSHIP:
  PARTIAL

CROSS_HOST_ALPHA0_SUPERVISION:
  PARTIAL

HERMES_DURABLE_STATE_REQUIRED:
  none currently qualified; conditional owner-selected generic session subset only

SAFE_DRAIN_READY:
  NO

CUTOVER_READY:
  NO
```

`SAFE_DRAIN_READY` remains `NO`: healthy work was not interrupted; live controller/gateway/scheduler authority remains; hidden duplicate scheduler custody and route equivalence are unresolved; current exact-head review/CI is missing for several open lineages; and pending legacy controller events still require bounded reconciliation. Remote code custody is necessary but not sufficient for safe drain.
