import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_supervisor import control  # noqa: E402 - shared fixture factory


def _assignment(
    *,
    ts,
    assignment_type,
    result_state,
    work_item="ghostspace/axis#7",
    title="Semantically decompose ghostspace/axis#7",
    lifecycle_state=None,
    work_item_disposition=None,
    error=None,
):
    value = {
        "schema": "axis.external-development-supervisor.assignment",
        "schema_version": "4.0.0",
        "assignment_id": f"assignment-{ts}-fixture",
        "assignment_type": assignment_type,
        "result_state": result_state,
        "work_item": work_item,
        "target_ref": work_item,
        "title": title,
        "created_at_epoch": ts,
    }
    if lifecycle_state is not None:
        value["lifecycle_state"] = lifecycle_state
    if work_item_disposition is not None:
        value["work_item_disposition"] = work_item_disposition
    if error is not None:
        value["error"] = error
    return value


# ---------------------------------------------------------------------------
# 1 & 2: escalation threshold boundary
# ---------------------------------------------------------------------------


def test_six_repeated_non_mutating_dispatches_trigger_escalation():
    from axis_supervisor import containment

    assignments = [
        _assignment(ts=1000 + i * 100, assignment_type="read-only-analysis", result_state="analysis-completed")
        for i in range(6)
    ]
    state = containment.compute_state("ghostspace/axis#7", assignments, now=2000)
    assert state.non_mutating_streak == 6
    assert state.escalated is True


def test_five_repeated_non_mutating_dispatches_do_not_trigger_escalation():
    from axis_supervisor import containment

    assignments = [
        _assignment(ts=1000 + i * 100, assignment_type="read-only-analysis", result_state="analysis-completed")
        for i in range(5)
    ]
    state = containment.compute_state("ghostspace/axis#7", assignments, now=2000)
    assert state.non_mutating_streak == 5
    assert state.escalated is False


# ---------------------------------------------------------------------------
# 3 & 4: superficial churn (new assignment IDs, reworded titles) never resets
# ---------------------------------------------------------------------------


def test_new_assignment_for_same_work_item_does_not_reset_counter():
    from axis_supervisor import containment

    assignments = [
        _assignment(ts=1000 + i * 100, assignment_type="read-only-analysis", result_state="analysis-completed")
        for i in range(5)
    ]
    baseline = containment.compute_state("ghostspace/axis#7", assignments, now=2000)
    assert baseline.non_mutating_streak == 5

    # A sixth, distinctly-IDed assignment (different assignment_id per the
    # fixture helper's ts-based naming) continues the streak rather than
    # resetting it - nothing about "it's a new assignment" is a reset signal.
    assignments.append(
        _assignment(ts=1600, assignment_type="no-op-verification", result_state="no-op-verification-completed")
    )
    escalated = containment.compute_state("ghostspace/axis#7", assignments, now=2000)
    assert escalated.non_mutating_streak == 6
    assert escalated.escalated is True


def test_superficial_task_title_changes_do_not_reset_streak():
    from axis_supervisor import containment

    assignments = [
        _assignment(
            ts=1000 + i * 100,
            assignment_type="read-only-analysis",
            result_state="analysis-completed",
            title=f"Semantically decompose ghostspace/axis#7 (rewording {i})",
        )
        for i in range(6)
    ]
    state = containment.compute_state("ghostspace/axis#7", assignments, now=2000)
    assert state.non_mutating_streak == 6
    assert state.escalated is True


# ---------------------------------------------------------------------------
# 5: genuine canonical advancement resets
# ---------------------------------------------------------------------------


def test_genuine_canonical_advancement_resets_the_streak():
    from axis_supervisor import containment

    assignments = [
        _assignment(ts=1000 + i * 100, assignment_type="read-only-analysis", result_state="analysis-completed")
        for i in range(6)
    ]
    assignments.append(
        _assignment(
            ts=1700,
            assignment_type="ci-integration-repair",
            result_state="integrated-post-main-verified",
            lifecycle_state="repository-converged",
        )
    )
    # More non-mutating dispatches after the convergence event start a fresh count.
    assignments.append(
        _assignment(ts=1800, assignment_type="read-only-analysis", result_state="analysis-completed")
    )
    state = containment.compute_state("ghostspace/axis#7", assignments, now=2000)
    assert state.non_mutating_streak == 1
    assert state.escalated is False
    assert state.last_reset_reason == "canonical-advancement"


# ---------------------------------------------------------------------------
# 6: requires-implementation followed by more analysis still counts toward
#    the same no-progress streak (the exact axis#7 defect)
# ---------------------------------------------------------------------------


def test_requires_implementation_disposition_without_dispatch_still_accumulates():
    from axis_supervisor import containment

    assignments = [
        _assignment(
            ts=1000 + i * 100,
            assignment_type="no-op-verification",
            result_state="no-op-verification-completed",
            work_item_disposition="analyzed-only",
        )
        for i in range(4)
    ]
    assignments.append(
        _assignment(
            ts=1400,
            assignment_type="no-op-verification",
            result_state="no-op-verification-completed",
            work_item_disposition="requires-implementation",
        )
    )
    assignments.append(
        _assignment(
            ts=1500,
            assignment_type="no-op-verification",
            result_state="no-op-verification-completed",
            work_item_disposition="requires-implementation",
        )
    )
    state = containment.compute_state("ghostspace/axis#7", assignments, now=2000)
    assert state.non_mutating_streak == 6
    assert state.escalated is True


# ---------------------------------------------------------------------------
# 7: escalation never creates mutation authority
# ---------------------------------------------------------------------------


def test_escalation_never_blocks_mutation_dispatch():
    from axis_supervisor import containment

    assignments = [
        _assignment(ts=1000 + i * 100, assignment_type="read-only-analysis", result_state="analysis-completed")
        for i in range(6)
    ]
    state = containment.compute_state("ghostspace/axis#7", assignments, now=2000)
    assert state.escalated is True
    assert containment.blocks_non_mutating_dispatch(state) is True
    assert containment.blocks_mutation_dispatch(state) is False


# ---------------------------------------------------------------------------
# 8, 9, 10: cooldown after consecutive genuine failures
# ---------------------------------------------------------------------------


def test_four_consecutive_genuine_failures_enter_cooldown():
    from axis_supervisor import containment

    assignments = [
        _assignment(ts=1000 + i * 100, assignment_type="code-implementation", result_state="failed")
        for i in range(4)
    ]
    state = containment.compute_state("ghostspace/axis#96", assignments, now=1400)
    assert state.failure_streak == 4
    assert state.cooling is True
    assert state.cooldown_until_epoch == 1300 + containment.COOLDOWN_SECONDS


def test_read_only_reconciliation_remains_available_during_cooldown():
    from axis_supervisor import containment

    assignments = [
        _assignment(ts=1000 + i * 100, assignment_type="code-implementation", result_state="failed")
        for i in range(4)
    ]
    state = containment.compute_state("ghostspace/axis#96", assignments, now=1400)
    assert state.cooling is True
    assert containment.blocks_non_mutating_dispatch(state) is False
    assert containment.blocks_mutation_dispatch(state) is True


def test_non_mutating_dispatch_actually_succeeds_through_real_dispatcher_during_cooldown(
    tmp_path,
):
    """Same scenario as test_cooldown_prevents_worker_spawn_churn_via_dispatcher,
    but for a non-mutating item - proves end-to-end, through the real
    Dispatcher (not just the pure blocks_non_mutating_dispatch predicate),
    that an active cooldown never blocks read-only/status work. This is the
    regression test for a real bug caught in independent review: an earlier
    version of this module wrote cooldown entries into the shared
    quarantines.json, whose pre-existing, type-unaware read in
    Dispatcher.dispatch() blocks ALL dispatch for a quarantined work item -
    silently also blocking non-mutating dispatch, which cooldown must never
    do. Enforcement now goes solely through the type-scoped checks in
    dispatcher.py."""
    import time as _time

    from axis_supervisor.dispatcher import Dispatcher

    (tmp_path / "control.json").write_text(
        json.dumps(control(max_active_assignments=2)), encoding="utf-8"
    )
    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    recent_base = int(_time.time()) - 600
    for i, a in enumerate(
        _assignment(
            ts=recent_base + i * 60,
            assignment_type="code-implementation",
            result_state="failed",
            lifecycle_state="failed",
        )
        for i in range(4)
    ):
        a.update(
            {
                "project": "ghostspace/axis",
                "responsibility": "axis-runtime/product",
                "repository_ownership": {
                    "schema": "axis.external-development-supervisor.repository-ownership-evidence",
                    "schema_version": "1.0.0",
                    "status": "validated",
                    "context": "fixture",
                    "responsibility": "axis-runtime/product",
                    "repository": "ghostspace/axis",
                    "canonical_repository": "ghostspace/axis",
                    "reason": None,
                },
            }
        )
        (assignments_dir / f"prior-{i}.json").write_text(json.dumps(a), encoding="utf-8")

    dispatcher = Dispatcher(tmp_path)
    item = {
        "ref": "semantic-decomposition:ghostspace/axis#96",
        "target_ref": "ghostspace/axis#96",
        "kind": "semantic-decomposition",
        "assignment_type": "read-only-analysis",
        "project": "ghostspace/axis",
        "title": "reconcile state for ghostspace/axis#96 while cooling",
        "classification": "Executable",
        "authority": {"state": "preparation-only"},
        "source_item": {},
        "source_fingerprint": "reconcile",
    }
    graph = {"inventory_generation_id": "g1", "executable_queue": [item]}
    created = dispatcher.dispatch(graph, "run-reconcile-during-cooldown", item)
    assert created is not None
    assert created["assignment_type"] == "read-only-analysis"


def test_cooldown_prevents_worker_spawn_churn_via_dispatcher(tmp_path):
    import time as _time

    from axis_supervisor.dispatcher import Dispatcher

    (tmp_path / "control.json").write_text(
        json.dumps(control(max_active_assignments=2)), encoding="utf-8"
    )
    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    # dispatch() calls containment.evaluate() with real wall-clock time
    # internally (it has no way to receive a fixed `now` from the caller),
    # so fixture timestamps must be recent, not small offsets from the
    # epoch - otherwise the computed cooldown window is already long
    # expired by the time real `time.time()` is compared against it.
    recent_base = int(_time.time()) - 600
    for i, a in enumerate(
        _assignment(
            ts=recent_base + i * 60,
            assignment_type="code-implementation",
            result_state="failed",
            lifecycle_state="failed",
        )
        for i in range(4)
    ):
        a.update(
            {
                "project": "ghostspace/axis",
                "responsibility": "axis-runtime/product",
                "repository_ownership": {
                    "schema": "axis.external-development-supervisor.repository-ownership-evidence",
                    "schema_version": "1.0.0",
                    "status": "validated",
                    "context": "fixture",
                    "responsibility": "axis-runtime/product",
                    "repository": "ghostspace/axis",
                    "canonical_repository": "ghostspace/axis",
                    "reason": None,
                },
            }
        )
        (assignments_dir / f"prior-{i}.json").write_text(json.dumps(a), encoding="utf-8")

    dispatcher = Dispatcher(tmp_path)
    item = {
        "ref": "implementation:ghostspace/axis#7",
        "target_ref": "ghostspace/axis#7",
        "kind": "implementation",
        "assignment_type": "code-implementation",
        "project": "ghostspace/axis",
        "responsibility": "axis-runtime/product",
        "title": "retry the same failing implementation",
        "classification": "Executable",
        "authority": {"state": "preparation-only"},
        "source_item": {},
        "source_fingerprint": "same",
    }
    graph = {"inventory_generation_id": "g1", "executable_queue": [item]}
    assert dispatcher.dispatch(graph, "run-cooldown", item) is None


def test_escalation_blocks_non_mutating_redispatch_via_dispatcher(tmp_path):
    from axis_supervisor.dispatcher import Dispatcher

    (tmp_path / "control.json").write_text(
        json.dumps(control(max_active_assignments=2)), encoding="utf-8"
    )
    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    for i, a in enumerate(
        _assignment(
            ts=1000 + i * 100,
            assignment_type="read-only-analysis",
            result_state="analysis-completed",
            lifecycle_state="completed",
        )
        for i in range(6)
    ):
        a.update(
            {
                "project": "ghostspace/axis",
                "responsibility": "axis-runtime/product",
                "repository_ownership": {
                    "schema": "axis.external-development-supervisor.repository-ownership-evidence",
                    "schema_version": "1.0.0",
                    "status": "validated",
                    "context": "fixture",
                    "responsibility": "axis-runtime/product",
                    "repository": "ghostspace/axis",
                    "canonical_repository": "ghostspace/axis",
                    "reason": None,
                },
            }
        )
        (assignments_dir / f"prior-{i}.json").write_text(json.dumps(a), encoding="utf-8")

    dispatcher = Dispatcher(tmp_path)
    item = {
        "ref": "semantic-decomposition:ghostspace/axis#7",
        "target_ref": "ghostspace/axis#7",
        "kind": "semantic-decomposition",
        "assignment_type": "read-only-analysis",
        "project": "ghostspace/axis",
        "title": "re-analyze ghostspace/axis#7 again",
        "classification": "Executable",
        "authority": {"state": "preparation-only"},
        "source_item": {},
        "source_fingerprint": "same",
    }
    graph = {"inventory_generation_id": "g1", "executable_queue": [item]}
    assert dispatcher.dispatch(graph, "run-escalated", item) is None


# ---------------------------------------------------------------------------
# 11: without an automatic material-change detector, streaks are NOT reset
#     by anything except the two explicit signals - documented, not silent
# ---------------------------------------------------------------------------


def test_no_automatic_reset_without_an_explicit_detected_signal():
    from axis_supervisor import containment

    # A disposition field changing value, on its own, with no convergence
    # marker and no mutation-type dispatch, must not reset the streak - this
    # module does not attempt to infer "material new evidence" from free-form
    # fields. An explicit override mechanism is a documented future item, not
    # implemented here.
    assignments = [
        _assignment(
            ts=1000 + i * 100,
            assignment_type="read-only-analysis",
            result_state="analysis-completed",
            work_item_disposition=f"analyzed-only-variant-{i}",
        )
        for i in range(6)
    ]
    state = containment.compute_state("ghostspace/axis#7", assignments, now=2000)
    assert state.non_mutating_streak == 6
    assert state.escalated is True


# ---------------------------------------------------------------------------
# 12: stale leases reclaimed only after live-owner exclusion
# ---------------------------------------------------------------------------


def _write_lease(root, name, *, assignment_id, heartbeat_at):
    lease_dir = root / "leases" / name
    lease_dir.mkdir(parents=True)
    (lease_dir / "lease.json").write_text(
        json.dumps(
            {
                "schema": "axis.external-development-supervisor.lease",
                "schema_version": "1.0.0",
                "lease_id": assignment_id,
                "assignment_id": assignment_id,
                "owner_run_id": "run-fixture",
                "fencing_token": "token",
                "resources": ["repo:ghostspace/axis"],
                "read_only": True,
                "acquired_at_epoch": heartbeat_at,
                "heartbeat_at_epoch": heartbeat_at,
                "expires_at_epoch": heartbeat_at + 1200,
            }
        ),
        encoding="utf-8",
    )


def test_stale_lease_with_terminal_assignment_is_archived_not_deleted(tmp_path):
    from axis_supervisor import containment

    old = 1000
    now = old + containment.STALE_LEASE_MIN_AGE_SECONDS + 3600
    _write_lease(tmp_path, "stale-old-1", assignment_id="assignment-old-1", heartbeat_at=old)
    (tmp_path / "assignments").mkdir()
    (tmp_path / "assignments" / "assignment-old-1.json").write_text(
        json.dumps(_assignment(ts=old, assignment_type="code-implementation", result_state="failed", lifecycle_state="failed")),
        encoding="utf-8",
    )

    reclaimed = containment.sweep_stale_leases(tmp_path, now=now)

    assert len(reclaimed) == 1
    assert not (tmp_path / "leases" / "stale-old-1").exists()
    assert (tmp_path / "leases-archive" / "stale-old-1" / "lease.json").exists()


def test_stale_lease_with_non_terminal_assignment_is_left_in_place(tmp_path):
    from axis_supervisor import containment

    old = 1000
    now = old + containment.STALE_LEASE_MIN_AGE_SECONDS + 3600
    _write_lease(tmp_path, "stale-live-1", assignment_id="assignment-live-1", heartbeat_at=old)
    (tmp_path / "assignments").mkdir()
    (tmp_path / "assignments" / "assignment-live-1.json").write_text(
        json.dumps(
            _assignment(
                ts=old,
                assignment_type="code-implementation",
                result_state="pending",
                lifecycle_state="running-implementation",
            )
        ),
        encoding="utf-8",
    )

    reclaimed = containment.sweep_stale_leases(tmp_path, now=now)

    assert reclaimed == []
    assert (tmp_path / "leases" / "stale-live-1").exists()
    assert not (tmp_path / "leases-archive").exists() or not list(
        (tmp_path / "leases-archive").glob("*")
    )


def test_lease_younger_than_minimum_age_is_never_swept(tmp_path):
    from axis_supervisor import containment

    recent = 1000
    now = recent + 3600  # 1 hour old, far under the 7-day minimum
    _write_lease(tmp_path, "stale-recent-1", assignment_id="assignment-recent-1", heartbeat_at=recent)

    reclaimed = containment.sweep_stale_leases(tmp_path, now=now)

    assert reclaimed == []
    assert (tmp_path / "leases" / "stale-recent-1").exists()


# ---------------------------------------------------------------------------
# 13: restart preserves counters (state is always re-derived, never a
#     separate mutable counter, so this is true by construction - prove it)
# ---------------------------------------------------------------------------


def test_state_recomputed_after_simulated_restart_is_identical():
    from axis_supervisor import containment

    assignments = [
        _assignment(ts=1000 + i * 100, assignment_type="read-only-analysis", result_state="analysis-completed")
        for i in range(6)
    ]
    first = containment.compute_state("ghostspace/axis#7", assignments, now=2000)
    # Simulate a restart: nothing but the same durable history is available.
    second = containment.compute_state("ghostspace/axis#7", list(assignments), now=2000)
    assert first == second


# ---------------------------------------------------------------------------
# 14: malformed/missing containment state fails open, never raises, never
#     blocks the legacy dispatcher
# ---------------------------------------------------------------------------


def test_evaluate_fails_open_on_corrupt_assignment_file(tmp_path):
    from axis_supervisor import containment

    assignments_dir = tmp_path / "assignments"
    assignments_dir.mkdir()
    (assignments_dir / "corrupt.json").write_text("{not valid json", encoding="utf-8")

    # Must not raise, must not crash the caller.
    state = containment.evaluate(tmp_path, "ghostspace/axis#7")
    # The corrupt file is simply skipped by load_assignments_for_work_item
    # (it isn't valid JSON so it can't match work_item either) - this is the
    # "no history" case, not an error case, and correctly resolves to no
    # containment action.
    assert state is not None
    assert state.escalated is False
    assert state.cooling is False


def test_evaluate_never_raises_on_missing_root(tmp_path):
    from axis_supervisor import containment

    missing_root = tmp_path / "does-not-exist"
    state = containment.evaluate(missing_root, "ghostspace/axis#7")
    assert containment.blocks_non_mutating_dispatch(state) is False
    assert containment.blocks_mutation_dispatch(state) is False


def test_blocks_helpers_are_false_for_none_state():
    from axis_supervisor import containment

    assert containment.blocks_non_mutating_dispatch(None) is False
    assert containment.blocks_mutation_dispatch(None) is False


# ---------------------------------------------------------------------------
# 15: the real axis#96 successful sequence would not have been prematurely
#     terminated by this production policy at any point along its history
# ---------------------------------------------------------------------------

# Reconstructed from the live 2026-08-28 convergence audit's axis#96 case
# study (11 assignments, one 66-minute window, converged on the 11th/final
# assignment). Reproduced here as literal historical fixture data - not
# re-derived - so this test is a direct regression guard against the
# production policy ever retroactively invalidating this specific,
# already-observed successful outcome.
_AXIS96_HISTORY = [
    (1754395220, "read-only-analysis", "failed", None),
    (1754395574, "read-only-analysis", "failed", None),
    (1754395594, "read-only-analysis", "analysis-completed", None),
    (1754396230, "code-implementation", "failed", None),
    (1754396405, "code-implementation", "failed", None),
    (1754396468, "code-implementation", "failed", None),
    (1754396802, "ci-integration-repair", "failed", None),
    (1754396931, "ci-integration-repair", "failed", None),
    (1754397320, "ci-integration-repair", "failed", None),
    (1754397403, "ci-integration-repair", "integrated-post-main-verified", "repository-converged"),
]


def test_axis96_real_history_never_triggers_escalation_or_hard_block():
    from axis_supervisor import containment

    running = []
    for ts, a_type, result, lifecycle in _AXIS96_HISTORY:
        running.append(
            _assignment(
                ts=ts,
                assignment_type=a_type,
                result_state=result,
                work_item="ghostspace/axis#96",
                lifecycle_state=lifecycle,
            )
        )
        state = containment.compute_state("ghostspace/axis#96", running, now=ts)
        # Escalation (non-mutating-only) never applies - axis#96 has at most
        # 3 consecutive non-mutating dispatches at any prefix, far under the
        # ceiling of 6.
        assert state.escalated is False, f"escalated prematurely at {ts}"
        # Cooldown must never fire either: the real sequence has 3 consecutive
        # code-implementation failures, then switches strategy to
        # ci-integration-repair (this project's own existing notion of a
        # materially different execution plan) for 3 more failures before
        # succeeding on the 4th repair attempt. Failure streak is scoped per
        # strategy specifically so this transition doesn't accumulate into a
        # false cooldown trigger - this is the fix that came directly out of
        # this test initially failing during development.
        assert state.cooling is False, f"cooldown fired prematurely at {ts}"
        # The mutation-attempt ceiling is shadow-only by design (see module
        # docstring) - it must never itself block dispatch, regardless of how
        # many shadow thresholds get crossed.
        assert containment.blocks_mutation_dispatch(state) is False

    final_state = containment.compute_state("ghostspace/axis#96", running, now=_AXIS96_HISTORY[-1][0])
    # The final assignment is a genuine convergence event, so by the time the
    # sequence completes everything has reset - the interesting assertions
    # are the per-step ones above, proving the policy never would have
    # intervened *during* the real sequence, not just that it looks fine
    # after the fact.
    assert final_state.escalated is False
    assert final_state.cooling is False
