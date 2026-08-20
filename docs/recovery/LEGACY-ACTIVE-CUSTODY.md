# Legacy active AXIS custody

Observed read-only on Ghost at 2026-08-20 12:39 CDT. The map correlates the legacy Hermes board database, live process metadata, Git worktrees/refs and GitLab SSH refs. A later independent GET-only GitLab snapshot is noted separately. Runtime databases/logs, credentials and message payloads were not copied or reported.

GitLab is execution truth. The legacy board is assignment/controller evidence, and Ghost worktrees/refs are local custody evidence; neither overrides GitLab.

## Nine mapped lineages

| Work / task | Type | Legacy status | Branch, MR and captured head | Custody location | Recoverability at custody capture |
|---|---|---|---|---|---|
| `axis#27/AX27-A`, `t_85b44d79` | repair/reviewer | `ACTIVE_HEALTHY` | `feat/axis27-axisvault-adapter-contract-20260818t1309z`, !192, `ca1f6d1851829e368fea18aa1adaa81c0c66dd2d` | board worktree | `FULLY_REMOTE_RECOVERABLE`: exact branch/head present remotely. |
| `axis#45/AX45-A`, `t_2795e466` | repair | `ACTIVE_HEALTHY` | `feat/axis45-remote-node-enrollment-invitation-20260818t1416z`, !198, `668e3782877c61a719bf1b8457e9ec7c122b9666` | `/tmp` worktree | `FULLY_REMOTE_RECOVERABLE`: exact branch/head present remotely. |
| `axis#57/AXIS57-A`, `t_2485b494` | repair | `ACTIVE_HEALTHY` | `feat/axis57-endpoint-discovery-routing-20260818t1225z`, !193, local `3b990ec4cb987432bd38a9e0e8cda9bc1ea55110` | board worktree | `PARTIALLY_REMOTE_RECOVERABLE`: named branch absent from SSH refs; local commit and MR reference remain. |
| `axis#60/AXIS60-A`, `t_fc2adf8d` | repair | `ACTIVE_HEALTHY` | `feat/axis60-voice-privacy-guardrails-20260818t1416z`, !196, `02659f7b19f211249c5d416a5f0ad5f7f1a3c568` | board worktree | `FULLY_REMOTE_RECOVERABLE`: exact branch/head present remotely. |
| `axis#81` closure, `t_fbe8af3b` | reconciler | `COMPLETED_UNRECONCILED` | !184, review head `50e8c341810787c1a0bedb8a688924ea54d58d1d` | shared controller cwd | `FULLY_REMOTE_RECOVERABLE`: merged lineage exists; legacy projection cleanup remains. |
| `axis#85/AXIS85-A/B`, `t_09dbcd1b` / `t_3c7aa3c7` | implementation/recovery | `ACTIVE_HEALTHY` | `feat/axis85-bounded-operational-delivery-20260818t0740z`, !200, local `cce88792f9f913ce81cb039be6ac53ab79d074b1` | `/tmp` worktree plus newer local recovery branch | `PARTIALLY_REMOTE_RECOVERABLE`: named branch absent from SSH refs at capture. |
| `axis#97/AX97-A`, `t_97bff2e6` | implementation | `COMPLETED_UNRECONCILED` | `feat/axis97-blueprint-mutation-authz-20260818t1225z`, !189, local `72aad6a71f9bdbe9fa6bc0a8f80247244d9452c5` | `/tmp` worktree | `PARTIALLY_REMOTE_RECOVERABLE`: named branch absent from SSH refs; local evidence still mattered at capture. |
| `axis#103/AX103-A`, `t_40d6873e` | implementation | `COMPLETED_UNRECONCILED` | `feat/axis103-deliberation-foundation-20260818t123427z`, !188, remote `22384b4693a3be208f093b9c5b5d92257b45c4c1`; older local `f693947d...` | `/tmp` worktree | `FULLY_REMOTE_RECOVERABLE`: newer remote branch is authoritative; do not select local by age. |
| `axis-governance#239/AXGOV239-B`, `t_693e1035` | implementation | `ACTIVE_HEALTHY` | `feat/axisgov239-candidate-certification-pipeline-20260818t173035z`, MR not established in legacy metadata, `bfdb864e09c4a70ae688c8547a44ac03070474e8` | `/tmp` worktree | `FULLY_REMOTE_RECOVERABLE`: exact branch/head present remotely. |

Six lineages were fully and three partially remote recoverable at the custody capture. None was silently classified `FAILED`, `STALE` or `OWNER_GONE`; older blocked cards without fresh heartbeat remain backlog evidence.

## Later GitLab truth window

At 2026-08-20T17:50:32Z, the stable canonical/direct GET snapshot contained 12 open MRs across the three projects. It corroborated the remote heads for !192, !188, !196, !198 and the governance branch represented by axis !201. It also showed:

- !193 open at remote head `42274c5345ae14e9caa7371bbf9046a6840a53c5`, not the Ghost-local lineage head;
- !200 open at remote head `09dfb660f47e4bd74067b424d4f0873bbb1b0e53`, not the Ghost-local lineage head;
- !189 had merged and `ghostspace/axis` main was its merge commit, explaining its absence from the open-MR set.

These are not reasons to discard Ghost-local refs. They demonstrate that legacy task state and GitLab advanced independently and must be reconciled by exact assignment/head/merge evidence before retention decisions.

## Cutover blockers

1. A live `reconcile-roadmap-execution` process and active board runs existed at capture. Starting a second controller or switching authority would create dual writers.
2. Several live runs used the shared rootless controller cwd, so branch/head ownership could not be uniquely inferred from cwd.
3. !193 and !200 had local/remote head disagreement; axis#57/#85/#97 named refs were absent in the SSH capture. Preserve local refs/worktrees until every unique commit is classified as merged, pushed, superseded or intentionally abandoned.
4. Stale duplicate/controller-defect cards `t_e4d2c769` (axis#81) and `t_f790d252` (axis#97) coexisted with successor work; legacy projection was not converged.
5. The worktree registry had prunable entries and many detached verification trees. Registry membership is not custody proof.
6. Review qualification, exact current pipelines and current-main verification remained unknown in canonical collection. Remote branch presence alone is not completion.

## Required re-observation before drain

For every lineage, record a fresh tuple without payloads: project/work item, assignment/task ID, branch, local head, remote head, MR IID/state/head, exact-head pipeline, qualified review state, current-main ancestry, worker PID/heartbeat and local-only commit count. Abort if any active/healthy worker would be interrupted or any unique commit lacks an approved durable destination.

Classify each item exactly one of:

- `REMOTE_COMPLETE`: canonical completion/current-main evidence is complete;
- `REMOTE_IN_FLIGHT`: branch/MR exact head is durable and an owner continues;
- `LOCAL_UNPUSHED`: unique local commits require preservation/adoption;
- `LEGACY_PROJECTION_ONLY`: stale/duplicate derived card with no unique work;
- `UNKNOWN`: evidence mismatch or incomplete surface; drain cannot pass.

## Preservation and adoption rules

- Never bulk-push or merge a legacy worktree.
- Preserve local refs read-only until a canonical custody record binds project, work item, PlanningRecord, assignment, branch, head, MR and adoption evidence.
- Remote GitLab wins for shared facts, but does not prove that a newer local commit is disposable.
- Do not interrupt workers/reviewers/CI. First disable only sources of *new* work in the authorized drain window; allow existing custody to reach a recorded boundary.
- Archive board/task metadata as evidence after reconciliation. Do not import it as canonical controller state.

`ACTIVE_AXIS_CUSTODY = PARTIAL`: all nine relevant lineages were mapped, but three relied partly on Ghost-local custody and later GitLab reads exposed head/state drift. Safe cutover was not established.
