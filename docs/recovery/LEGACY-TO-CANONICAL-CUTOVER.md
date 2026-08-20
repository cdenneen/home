# Legacy-to-canonical drain and cutover plan

**Status: FUTURE ONLY — DO NOT RUN.** This document prepares a controlled drain; it is not authorization. Deployment is `UNKNOWN` and `CUTOVER_READY = NO`.

The target of Home PR #681 is deliberately dormant: generic Hermes remains, legacy AXIS/Alpha0 gateways and embedded supervisor are absent, Alpha0 Core/gateway are disabled, canonical axis scheduling is disabled, and only a report-only unscheduled axis observer definition remains. A later activation/graduation is a separate change.

## Non-negotiable invariants

- GitLab remains AXIS execution truth. axis-control alone owns AXIS SDLC; Alpha0 can supervise but cannot dispatch/repair/review/merge AXIS work.
- Drain means stop only sources of *new* legacy work first. Do not interrupt active workers, reviewers, CI or unresolved local custody.
- Keep the generic gateway, generic sessions/state and generic stuck-cron watchdog healthy.
- Harvest and verify owner-only backups before any unit/job/state change. Do not merge legacy DB/session/Kanban state into canonical state.
- One channel/chat has one gateway owner. No generic/AXIS/Alpha0 fallback overlap.
- Abort on unknown custody, unexpected job/unit identity, changed exact head, failed required CI/review, missing rollback proof or any command whose output differs from the signed preflight.

## Phase 0 — authorization and immutable preflight

### Required gates

1. axis-control and Alpha0 producer heads are in reviewed canonical history or separately approved as exact immutable pins.
2. Home PR #681 has been rebased onto current main, pins those exact heads, passes strict required CI, and has a fresh exact-head approval after the last push.
3. An owner-approved maintenance window, named incident commander, independent verifier and signed command transcript location exist.
4. Fresh owner-only backups exist for the three Hermes roots, Alpha0 Core and all Ghost-local Git refs/worktrees with possible unique commits; disposable restore checks pass.
5. Fresh GET-only GitLab and custody maps have no `UNKNOWN` item. Every lineage is `REMOTE_COMPLETE`, `REMOTE_IN_FLIGHT`, `LOCAL_UNPUSHED` with an approved preservation/adoption plan, or `LEGACY_PROJECTION_ONLY`.
6. No canonical dispatcher/mutator/scheduler is enabled. Runtime and CI credentials are distinct; no credential value enters commands, Nix, store paths or transcript.
7. Root-owned signed deployment evidence binds approved Home/producer heads, package/config closures, entrypoints, scheduler identity and reviewer identity.

### Sanitized scheduler inspection

Never place raw `hermes cron list`, scheduler output, prompts, commands, delivery targets or error text in the signed transcript. Before the window, independently review this allowlisted helper and record its digest with the command plan:

```bash
# FUTURE ONLY — emits identity/cadence flags, never command, target, output or error text.
sanitize_hermes_registry() {
  python3 - "$1" "$2" <<'PY'
import json, pathlib, sys
label, path = sys.argv[1], pathlib.Path(sys.argv[2])
data = json.loads(path.read_text())
for job in sorted(data.get("jobs", []), key=lambda item: str(item.get("id"))):
    schedule = job.get("schedule") or {}
    allowed_schedule = {key: schedule.get(key) for key in ("kind", "minutes", "expr", "display") if key in schedule}
    print(json.dumps({"registry": label, "id": job.get("id"), "name": job.get("name"),
        "enabled": job.get("enabled"), "no_agent": job.get("no_agent"),
        "schedule": allowed_schedule, "command_present": bool(job.get("command") or job.get("script")),
        "delivery_configured": bool(job.get("delivery"))}, sort_keys=True))
PY
}

assert_job_disabled_or_absent() {
  python3 - "$1" "$2" <<'PY'
import json, pathlib, sys
path, expected = pathlib.Path(sys.argv[1]), sys.argv[2]
jobs = [job for job in json.loads(path.read_text()).get("jobs", []) if str(job.get("id")) == expected]
if len(jobs) > 1 or (jobs and jobs[0].get("enabled") is not False):
    raise SystemExit(f"job {expected}: expected absent or exactly one disabled record")
PY
}
```

### Future verification commands

```console
# FUTURE ONLY; run locally on Ghost in the approved reviewed Home checkout.
export APPROVED_HOME_HEAD='<exact signed Home head>'
test "$(git rev-parse HEAD)" = "$APPROVED_HOME_HEAD"
git status --porcelain=v1
git submodule status  # expected empty unless the approved tree says otherwise
home-manager build --flake .#cdenneen@ghost
systemctl --user is-active hermes-gateway.service
systemctl --user is-active hermes-stuck-cron-watchdog.timer
systemctl --user list-timers --all
sanitize_hermes_registry generic "$HOME/.hermes/cron/jobs.json"
sanitize_hermes_registry root-axis-profile "$HOME/.hermes/profiles/axis-control/cron/jobs.json"
sanitize_hermes_registry checkout-axis-profile "$HOME/src/workspace/work/axis-control/.hermes/profiles/axis-control/cron/jobs.json"
sanitize_hermes_registry alpha0 "$HOME/.local/share/alpha0/hermes/cron/jobs.json"
pgrep -a -u "$USER" -f 'reconcile-roadmap-execution|hermes.*axis-control|axis-development|alpha0'
```

`git status` must be empty except separately inventoried untracked files that are outside the reviewed worktree; the isolated activation checkout itself must be clean. Compare only sanitized scheduler output with the signed inventory. Keep any unavoidable raw diagnostic output in separately access-controlled owner-only storage, never in the transcript or report.

**Abort:** any gate fails, any registry/job/unit differs from the signed inventory, generic gateway/watchdog is unhealthy, or a new active lineage appears.

## Phase 1 — freeze sources of new legacy AXIS work

Preconditions: Phase 0 passed; fresh custody map signed; all active owners notified; pausing jobs is explicitly authorized. Do not stop gateways or existing workers.

```console
# FUTURE ONLY — pause exact known new-work/recovery jobs; do not delete them.
HERMES_HOME="$HOME/.hermes" hermes cron pause a9c0b0e9bcca
HERMES_HOME="$HOME/.hermes" hermes cron pause bb8d50dc3332
HERMES_HOME="$HOME/src/workspace/work/axis-control/.hermes" \
  hermes --profile axis-control cron pause 81776a5f93c5

# Prevent legacy watchdog/provisioning paths from recreating epochs/jobs.
systemctl --user disable --now axis-development-watchdog-backup.timer
systemctl --user disable --now hermes-axis-control-scheduler-watchdog.timer
systemctl --user stop axis-development-watchdog-backup.service
systemctl --user stop hermes-axis-control-scheduler-watchdog.service
systemctl --user disable hermes-supervisor-cron.service
systemctl --user disable hermes-watchdog-cron.service
systemctl --user disable hermes-watchdog-cutover.service

# Prove paused/disabled and inactive state; do not run a scheduler tick.
assert_job_disabled_or_absent "$HOME/.hermes/cron/jobs.json" a9c0b0e9bcca
assert_job_disabled_or_absent "$HOME/.hermes/cron/jobs.json" bb8d50dc3332
assert_job_disabled_or_absent "$HOME/src/workspace/work/axis-control/.hermes/profiles/axis-control/cron/jobs.json" 81776a5f93c5
! systemctl --user is-active --quiet axis-development-watchdog-backup.service
! systemctl --user is-active --quiet hermes-axis-control-scheduler-watchdog.service
! systemctl --user is-enabled --quiet axis-development-watchdog-backup.timer
! systemctl --user is-enabled --quiet hermes-axis-control-scheduler-watchdog.timer
```

The profile PO alert job `adb213a9d005` is not a work dispatcher and may remain during custody convergence if its delivery owner is explicitly confirmed. It is paused later before removing the AXIS gateway.

**Abort/rollback:** if pausing creates a new run, a watchdog recreates a job, a job identity/cadence differs, or an active worker loses required interaction, stop the procedure. With explicit incident-command approval only, resume the exact paused job needed for continuity using `hermes cron resume <id>` in the same `HERMES_HOME`; do not broadly resume all jobs. Re-observe custody from scratch.

## Phase 2 — converge existing custody without interruption

Preconditions: no new legacy assignment appears for at least two former scheduler intervals; existing workers/reviewers/CI remain running.

1. Reconcile all nine captured lineages plus any newly discovered lineage against current GitLab.
2. Allow remote-in-flight owners to reach a durable boundary. Do not start replacement work.
3. Preserve every local-only commit/ref in owner-only evidence or adopt through a separate canonical custody record and normal MR; never bulk-push.
4. Resolve stale duplicate cards as `LEGACY_PROJECTION_ONLY` only after proving no unique commit/session result.
5. Wait for the current `reconcile-roadmap-execution` process to exit naturally and prove no replacement starts.

```console
# FUTURE ONLY — observation commands; no kill and no GitLab write.
pgrep -a -u "$USER" -f 'reconcile-roadmap-execution|hermes.*axis-control'
git -C "$HOME/src/workspace/personal/work/axis" worktree list --porcelain
git -C "$HOME/src/workspace/personal/work/axis" show-ref
# Run the approved GET-only canonical reconstruction twice; require stable digest + NO_OP.
```

**Acceptance:** zero active legacy assignment/reviewer/repair runs; no running reconciler; all local commits classified with an approved durable disposition; two complete stable canonical reads; review/custody/current-main unknowns do not authorize action.

**Abort:** process remains active beyond its bounded run, local/remote heads disagree without disposition, any owner is gone, or GitLab surfaces are incomplete.

## Phase 3 — quiesce legacy AXIS interaction authority

Preconditions: Phase 2 acceptance passed; PO alert delivery is no longer required; generic gateway ownership is independently verified.

```console
# FUTURE ONLY.
hermes --profile axis-control cron pause adb213a9d005
systemctl --user disable --now hermes-axis-control-gateway.service
systemctl --user stop axis-development-watchdog-backup.service
systemctl --user stop hermes-axis-control-scheduler-watchdog.service

! systemctl --user is-active --quiet hermes-axis-control-gateway.service
! pgrep -u "$USER" -f 'hermes.*axis-control|reconcile-roadmap-execution'
systemctl --user is-active hermes-gateway.service
systemctl --user is-active hermes-stuck-cron-watchdog.timer
```

Do not remove rootless workspace, board DB, sessions, refs, jobs or evidence. Absence of processes is not permission to delete state.

**Abort/rollback:** if generic communication or a non-AXIS route fails, re-enable/start only the dedicated AXIS gateway with incident approval, keep work-dispatch jobs paused, and re-establish the previous exclusive route map. Do not resume scheduler/watchdogs automatically.

## Phase 4 — quiesce legacy Alpha0 scheduler and gateway

Preconditions: signed Core backup and disposable structural test passed; dedicated Hermes semantic backup/restore passed; status/SITREP outputs and exact channel ownership were qualified; no active Alpha0 interaction requires the gateway.

```console
# FUTURE ONLY.
HERMES_HOME="$HOME/.local/share/alpha0/hermes" hermes cron pause 6d29e5d5338a
HERMES_HOME="$HOME/.local/share/alpha0/hermes" hermes cron pause 5990e9abf356
systemctl --user disable --now hermes-alpha0-gateway.service

assert_job_disabled_or_absent "$HOME/.local/share/alpha0/hermes/cron/jobs.json" 6d29e5d5338a
assert_job_disabled_or_absent "$HOME/.local/share/alpha0/hermes/cron/jobs.json" 5990e9abf356
! systemctl --user is-active --quiet hermes-alpha0-gateway.service
systemctl --user is-active hermes-gateway.service
```

**Abort/rollback:** if a dedicated Alpha0 route/session is still active or semantic restore is incomplete, do not stop the gateway. If already stopped and continuity is required, restore only the exact reviewed gateway; keep both schedules paused until their wrappers/state are separately accepted.

## Phase 5 — activate the reviewed dormant Home composition

Preconditions: Phases 0–4 passed; activation-package closure equals signed evidence; rollback generation is recorded; a second operator confirms the exact Home head and command.

```console
# FUTURE ONLY — this is the only Home activation command in the procedure.
test "$(git rev-parse HEAD)" = "$APPROVED_HOME_HEAD"
home-manager generations
home-manager switch --flake .#cdenneen@ghost
```

The activation's fail-closed decommission logic may remove only exact known legacy AXIS cron records whose IDs, names, commands and cadences match its reviewed controls. Drift or ambiguity must fail activation. It must preserve generic jobs and must not install or start a canonical scheduler, axis gateway, Alpha0 Core or Alpha0 gateway.

**Abort:** any preflight/decommission ambiguity, unexpected unit/job addition/removal, failed generic gateway/watchdog, Nix closure mismatch, or activation error. Do not “fix forward” by editing live JSON/YAML or weakening checks.

## Phase 6 — post-activation acceptance

```console
# FUTURE ONLY — fail-closed metadata/status checks; do not print environments or payloads.
systemctl --user is-active hermes-gateway.service
systemctl --user is-active hermes-stuck-cron-watchdog.timer
! systemctl --user is-active --quiet hermes-axis-control-gateway.service
! systemctl --user is-active --quiet hermes-alpha0-gateway.service
! systemctl --user is-enabled --quiet axis-development-watchdog-backup.timer
! systemctl --user is-enabled --quiet hermes-axis-control-scheduler-watchdog.timer
assert_job_disabled_or_absent "$HOME/.hermes/cron/jobs.json" a9c0b0e9bcca
assert_job_disabled_or_absent "$HOME/.hermes/cron/jobs.json" bb8d50dc3332
assert_job_disabled_or_absent "$HOME/.hermes/profiles/axis-control/cron/jobs.json" adb213a9d005
assert_job_disabled_or_absent "$HOME/src/workspace/work/axis-control/.hermes/profiles/axis-control/cron/jobs.json" 81776a5f93c5
assert_job_disabled_or_absent "$HOME/.local/share/alpha0/hermes/cron/jobs.json" 6d29e5d5338a
assert_job_disabled_or_absent "$HOME/.local/share/alpha0/hermes/cron/jobs.json" 5990e9abf356
sanitize_hermes_registry generic "$HOME/.hermes/cron/jobs.json"
sanitize_hermes_registry root-axis-profile "$HOME/.hermes/profiles/axis-control/cron/jobs.json"
sanitize_hermes_registry checkout-axis-profile "$HOME/src/workspace/work/axis-control/.hermes/profiles/axis-control/cron/jobs.json"
sanitize_hermes_registry alpha0 "$HOME/.local/share/alpha0/hermes/cron/jobs.json"
! pgrep -u "$USER" -f "$HOME/src/workspace/work/axis-control|reconcile-roadmap-execution"
home-manager generations
```

Acceptance requires all of the following:

- generic gateway and generic stuck-cron watchdog healthy, with generic session continuity and no duplicate route owner;
- legacy AXIS/Alpha0 gateways, AXIS scheduler/watchdog timers, provisioning/cutover units and rootless-workspace processes absent;
- exact legacy AXIS jobs absent or paused as specified; no new work assigned; no canonical scheduler/model wake/mutation path installed;
- Alpha0 Core and gateway disabled; live Core data and dedicated Hermes evidence unchanged except approved scheduler pause metadata;
- two complete stable GET-only axis-control observations with no mutation; any review/custody/current-main `UNKNOWN` remains non-actionable;
- all nine historical lineages and any successors still remotely/local-evidence recoverable under their approved dispositions;
- signed deployment record binds the realized Home/producer closures and configuration to the approved exact heads;
- credential files remain external and owner-only; no secret appears in store/log/report.

Only after an observation window covering at least two former AXIS scheduler intervals and one generic watchdog interval may the dormant composition be accepted. State deletion and credential rotation are separate authorized procedures.

## Rollback

### Before Phase 5

There is no Home/runtime cutover to roll back. Keep sources paused, preserve evidence, correct the failed gate through normal review, and resume only the minimum exact legacy component explicitly authorized by the incident commander.

### After Phase 5

Rollback is allowed only if no canonical dispatcher/mutator has admitted work (the dormant target should make this true) and the recorded previous generation is trusted.

```console
# FUTURE ONLY; independent operator confirms no canonical work was admitted.
home-manager switch --rollback
systemctl --user is-active hermes-gateway.service
sanitize_hermes_registry generic "$HOME/.hermes/cron/jobs.json"
sanitize_hermes_registry root-axis-profile "$HOME/.hermes/profiles/axis-control/cron/jobs.json"
sanitize_hermes_registry checkout-axis-profile "$HOME/src/workspace/work/axis-control/.hermes/profiles/axis-control/cron/jobs.json"
sanitize_hermes_registry alpha0 "$HOME/.local/share/alpha0/hermes/cron/jobs.json"
```

Home rollback does not safely reconstruct cron records deleted by fail-closed decommission and must not automatically reactivate legacy mutation. Restore a removed job only from its signed sanitized definition, with exact ownership/cadence review and explicit authorization. Keep dispatch jobs paused until a fresh custody map passes. If rollback would create dual authority or lose generic continuity, leave the dormant generation in place and escalate rather than improvising.

## Later graduation (not this cutover)

Activating canonical axis scheduling/dispatch, Alpha0 Core, Alpha0 gateway or either Alpha0 schedule requires separate producer/Home PRs, exact-head tests and review, signed deployment evidence, runtime secrets, isolated real-data/interaction qualification and explicit authorization. This drain cannot be used as implicit autonomy approval.
