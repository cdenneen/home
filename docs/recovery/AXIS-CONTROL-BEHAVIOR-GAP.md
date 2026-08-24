# axis-control legacy behavior gap

Comparison point: canonical axis-control PR #3 head `4c25bc19040295fc6579dde9c6831ef143d298d5` versus credential-free Ghost evidence captured 2026-08-20. GitLab remains execution truth; the rootless Ghost workspace is unreviewed prior art.

The canonical recovery implements Stage 0–3 and a disabled Stage 4 observer. Dispatch, mutation, review delegation, merge, scheduler activation and autonomy remain frozen. No legacy controller implementation is safe to reactivate or import wholesale.

## Classification definitions

- `CANONICAL_EQUIVALENT`: useful invariant already represented canonically.
- `LEGACY_GOOD_MISSING`: desirable concept absent from the current canonical stage; re-specify behind canonical evidence and regression tests.
- `LEGACY_BAD_REJECT`: unsafe legacy behavior that must not be ported.
- `LEGACY_ONLY_TRANSITIONAL`: useful only to understand/drain the old runtime, not a future authority.
- `UNKNOWN`: evidence is insufficient; preserve uncertainty.

## Semantic matrix

| Responsibility | Classification | Decision |
|---|---|---|
| GitLab as AXIS execution truth | `CANONICAL_EQUIVALENT` | Preserve. Canonical makes the read boundary explicit and GET-only. |
| Missing/nonterminal exact-head CI remains waiting | `CANONICAL_EQUIVALENT` | Preserve canonical `CI_WAIT`; never infer success from absence. |
| Atomic state replacement, unchanged-write suppression and exclusive reconciliation | `CANONICAL_EQUIVALENT` | Canonical secure epoch/checkpoint/journal are stronger. |
| Malformed state is preserved and surfaced rather than silently used | `CANONICAL_EQUIVALENT` | Canonical fail-closed storage tests cover this. |
| Persist launch before process; individual durable event ACK | `CANONICAL_EQUIVALENT` | Canonical event journal supersedes legacy observation-after-launch. |
| Typed dependency/gate semantics, full-closure and cross-repository frontier | `LEGACY_GOOD_MISSING` | Define one versioned/digested GitLab authority surface; arbitrary notes cannot authorize gates. |
| Acceptance-ledger decomposition, conflict-domain capacity and cross-milestone unblockers | `LEGACY_GOOD_MISSING` | Rebuild only before dispatch graduation and release only after canonical `COMPLETE`. |
| Bounded review convergence/stall handling | `LEGACY_GOOD_MISSING` | Apply only to schema-valid, exact-head, independently reviewed evidence. |
| Binding-driven historical merged-MR recovery | `LEGACY_GOOD_MISSING` | Query from durable assignment/MR bindings and build exact merge/current-main receipts. |
| Qualified CI infrastructure versus product failure disposition | `LEGACY_GOOD_MISSING` | Require complete exact-pipeline/job evidence and bounded retries; trace substrings are advisory only. |
| Feature-branch push destination and duplicate-MR fences | `LEGACY_GOOD_MISSING` | Port at a future authorized mutation boundary, bound to canonical custody. |
| PO-facing packet/reminder/clear UX | `LEGACY_GOOD_MISSING` | Rebuild over canonical PO identity with an append-only outbox and delivery ACK. |
| Read-only operator/Kanban projection | `LEGACY_GOOD_MISSING` | YAGNI until requested; it may visualize canonical evidence but never establish custody. |
| Fixed first page of issues/MRs and latest 20 notes accepted as complete | `LEGACY_BAD_REJECT` | Canonical bounded pagination/fail-closed completeness supersedes it. |
| Free-text or untrusted note dependency/PO authority | `LEGACY_BAD_REJECT` | Mutable/truncated prose cannot authorize execution or release. |
| Local Markdown/SHA as qualified review | `LEGACY_BAD_REJECT` | Require exact project/MR/head/diff, independent reviewer, findings and content-addressed evidence. |
| Branch prefix/token overlap/local task state as custody | `LEGACY_BAD_REJECT` | Canonical full topology/assignment/adoption tuple is required. |
| Successful pipeline on merge SHA as current-main completion | `LEGACY_BAD_REJECT` | Require merge receipt, current default-head ancestry, named tests and signed deployment evidence. |
| Dependency/acceptance release immediately after merge | `LEGACY_BAD_REJECT` | Release only after canonical current-main completion. |
| Stale first-pipeline fallback when no pipeline matches current MR head | `LEGACY_BAD_REJECT` | Missing matching evidence stays unknown/waiting. |
| Pending-event truncation to 31 identities plus overflow marker | `LEGACY_BAD_REJECT` | Lossy overflow is forbidden; canonical journal retains every identity. |
| Event key that coalesces different outcomes on same lineage/head | `LEGACY_BAD_REJECT` | Identity must bind type and consequential evidence. |
| Arbitrary/stale ACK strings and ACK without transition proof | `LEGACY_BAD_REJECT` | ACK exact event ID only after durable committed transition/read-back. |
| Effects before intent/state, failure write after lock, no read-after-write | `LEGACY_BAD_REJECT` | Future writes require intent-before-effect, stable idempotency, read-back, persist, individual ACK. |
| Watchdog health from PID/timestamps and automatic restart | `LEGACY_BAD_REJECT` | Canonical digest/process/job identity checks and non-mutating watchdog supersede it. |
| Notifier state advanced before delivery | `LEGACY_BAD_REJECT` | A crash must not suppress undelivered consequential alerts. |
| Enabled five-minute model-waking legacy scheduler | `LEGACY_ONLY_TRANSITIONAL` | It is a drain target, not canonical Stage 4 behavior. |
| Hermes Kanban claim/promote/complete/archive control | `LEGACY_ONLY_TRANSITIONAL` | Preserve until active custody is drained; do not make it canonical authority. |
| Legacy MR metadata/milestone/description reconciliation | `LEGACY_ONLY_TRANSITIONAL` | Needed only to reconcile old in-flight work; canonical read-only recovery must not duplicate it. |
| Legacy roadmap/index/review/handoff files | `LEGACY_ONLY_TRANSITIONAL` | Derived evidence for custody/history, then archive/retire. |
| Lowest open milestone as active frontier | `UNKNOWN` | There were 19 active milestones; replay against an explicit authority record before choosing a rule. |
| Live legacy source revision and review provenance | `UNKNOWN` | Root has no Git metadata. Current hashes/tests do not establish reviewed provenance. |
| Historical lost/coalesced completion events | `UNKNOWN` | Source demonstrates the window; actual loss cannot be reconstructed from current snapshot alone. |
| PO notifier delivery owner | `UNKNOWN` | No configured unit/job reference was found; manual/model invocation was not excluded. |

## Defects that block graduation

1. Canonical bootstrap transport collection can report an observation complete while review qualification, custody and current-main verification are explicitly `UNKNOWN`. This is safe only while action is frozen. Before any transition can act, completeness must be scoped to the authority surfaces required by that transition and missing surfaces named explicitly.
2. Canonical collection does not yet reconstruct binding-driven historical merged MRs, exact merge receipts, ancestry, required current-main tests and deployment evidence.
3. Typed frontier/decomposition, review convergence and infrastructure disposition need canonical schemas. Legacy notes, Markdown, mutable local records and substring heuristics are not acceptable substitutes.
4. Any future mutation requires an effect outbox using the existing durable journal primitive at every kill boundary.

## Focused regression work items

These are bounded future producer issues; none enables scheduling or writes by itself.

1. **Authority-surface completeness:** complete MR transport plus missing custody/review remains non-actionable and explicitly unknown; missing current-main evidence cannot release dependencies.
2. **Historical MR/current-main reconstruction:** tracked MR disappearing from open list, later default-branch commits, reused branch/head, absent local state and missing historical detail all fail closed.
3. **Typed executable frontier:** untrusted `SATISFIED` text cannot release a gate; reopen re-blocks; related/future-consumer edges do not block; release waits for canonical `COMPLETE`.
4. **Qualified review convergence:** same SHA in another project/MR, reviewer=implementer, changed diff at same head and undispositioned findings cannot qualify; repeated blocking review stops without merge.
5. **Qualified infrastructure disposition:** exact pipeline/job binding, complete evidence and retry budget; new head invalidates prior classification.
6. **Future effect protocol:** kill-boundary tests for metadata write, MR creation, merge, acceptance release and optional Hermes effect; concurrent failure paths cannot overwrite a newer epoch.
7. **Optional operator projection:** read-only from canonical evidence, never a custody or completion authority.

## Validation basis

Canonical focused suite passed 44 tests. Legacy focused suites passed 176 roadmap/Kanban/CI/manager/precheck tests plus completion, lineage, watchdog, PO and 39 safe workspace-hardening tests. Isolated probes reproduced nine discarded overflow identities, stale-green CI advancement, sparse custody acceptance, weak ACK/checkpoint acceptance, status coalescing, untrusted PO extraction and false-green watchdog health. Passing legacy tests do not qualify unsafe behavior; several intentionally bless it.

## Disposition

Port invariants, not files. Open separate axis-control issues/PRs with focused tests and independent exact-head review. Keep Stage 4 disabled and non-mutating until all action-specific authority surfaces and effect boundaries are proven. No Alpha0 or Home implementation may acquire AXIS SDLC authority while these gaps are addressed.
