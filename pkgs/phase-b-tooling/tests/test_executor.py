from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from common import ATTEMPT, FakeBackend, Fixture
from phase_b import strict_json
from phase_b.executor import BoundCommandBackend, ExecutionError, Executor, Stage
from phase_b.journal import Journal
from phase_b.registry import FIXED_DELTAS, RegistrySet
from phase_b.trust import ExecutableBinding

GRANT_DIGEST = "sha256:" + "a" * 64


class ExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)

    def build(
        self, backend: FakeBackend | None = None
    ) -> tuple[Executor, RegistrySet, Journal, FakeBackend]:
        backend = backend or FakeBackend(self.fixture)
        registry = RegistrySet(
            self.fixture.expectations(), tuple(str(path) for path in self.fixture.paths)
        )
        registry.acquire()
        self.addCleanup(registry.close)
        journal = Journal(
            self.fixture.root / "execution-journal", clock=self.fixture.clock
        )
        executor = Executor(
            ATTEMPT,
            registry,
            journal,
            backend,
            self.fixture.units(),
            self.fixture.preserved(),
            monotonic=self.fixture.clock.monotonic,
            effect_plan_digest=self.fixture.anchor().effect_plan_digest,
            rollback_plan_digest=self.fixture.anchor().rollback_plan_digest,
        )
        return executor, registry, journal, backend

    @staticmethod
    def evidence(fixture: Fixture) -> dict[str, object]:
        return {
            name: fixture.put_artifact({"schema": f"fixture-{name}"}, prefix=name)
            for name in (
                "audit",
                "registry",
                "database",
                "provider-route",
                "custody",
                "identity",
                "time",
            )
        }

    @staticmethod
    def custody(fixture: Fixture, method: str) -> dict[str, object]:
        return {
            "method": method,
            "artifact": fixture.put_artifact(
                {"schema": "fixture-custody", "method": method}, prefix="custody"
            ),
        }

    def converge(self, executor: Executor) -> None:
        executor.record_capture_challenge("a" * 64, strict_json.digest({"baseline": 1}))
        self.fixture.clock.advance(300)
        executor.record_custody_read(
            self.custody(self.fixture, "GET"), self.fixture.clock.monotonic()
        )
        self.fixture.clock.advance(300)
        executor.record_custody_read(
            self.custody(self.fixture, "NO_OP"), self.fixture.clock.monotonic()
        )

    def test_atomic_b1_b2_b3_f0_order_and_exact_targets(self) -> None:
        executor, registry, journal, backend = self.build()
        executor.preflight()
        executor.run_b1()
        executor.run_b2()
        self.assertEqual(executor.stage, Stage.CUSTODY_CONVERGING)
        self.assertEqual(backend.effect_count, 28)
        self.assertEqual(tuple(executor.completed_deltas), FIXED_DELTAS)
        with self.assertRaises(ExecutionError):
            executor.establish_f0_candidate(
                self.evidence(self.fixture),
                "a" * 64,
                "2026-08-20T00:10:00Z",
                lambda _candidate: None,
            )
        self.converge(executor)
        artifact = executor.establish_f0_candidate(
            self.evidence(self.fixture),
            "a" * 64,
            "2026-08-20T00:10:00Z",
            lambda _candidate: None,
        )
        self.assertEqual(executor.stage, Stage.F0_ESTABLISHED)
        self.assertEqual(
            artifact["registry_digests"], list(registry.revalidate(FIXED_DELTAS))
        )
        intents = [
            record["action_id"]
            for record in journal.read_all()
            if record["kind"] == "intent"
        ]
        self.assertEqual(
            intents, [item["action"] for item in self.fixture.effect_plan()]
        )
        self.assertTrue(
            all(
                action.endswith((":stop", ":disable", ":mask", ":pause"))
                for action in intents
            )
        )

    def test_f0_validation_and_preserved_drift_fail_before_checkpoint(self) -> None:
        executor, _registry, journal, backend = self.build()
        executor.preflight()
        executor.run_b1()
        executor.run_b2()
        self.converge(executor)
        evidence = self.evidence(self.fixture)

        def reject(_candidate: dict[str, object]) -> None:
            raise ExecutionError("referenced evidence is invalid")

        with self.assertRaisesRegex(ExecutionError, "referenced evidence"):
            executor.establish_f0_candidate(
                evidence, "a" * 64, "2026-08-20T00:10:00Z", reject
            )
        self.assertEqual(executor.stage, Stage.F0_ELIGIBLE)
        self.assertFalse(
            any(
                record["action_id"] == "f0-established"
                for record in journal.read_all()
            )
        )

        preserved = self.fixture.preserved()[0]

        def drift_during_validation(_candidate: dict[str, object]) -> None:
            backend.preserved[preserved.name] = replace(
                preserved, start_identity="drifted-during-validation"
            )

        with self.assertRaisesRegex(ExecutionError, "continuity drifted"):
            executor.establish_f0_candidate(
                evidence,
                "a" * 64,
                "2026-08-20T00:10:00Z",
                drift_during_validation,
            )
        self.assertFalse(
            any(
                record["action_id"] == "f0-established"
                for record in journal.read_all()
            )
        )

        backend.preserved[preserved.name] = replace(
            preserved, start_identity="drifted-start-identity"
        )
        with self.assertRaisesRegex(ExecutionError, "continuity drifted"):
            executor.establish_f0_candidate(
                evidence,
                "a" * 64,
                "2026-08-20T00:10:00Z",
                lambda _candidate: None,
            )
        self.assertEqual(executor.stage, Stage.F0_ELIGIBLE)
        self.assertFalse(
            any(
                record["action_id"] == "f0-established"
                for record in journal.read_all()
            )
        )

    def test_restart_after_capture_challenge_requires_exact_incident_rollback(self) -> None:
        executor, registry, journal, backend = self.build()
        executor.preflight()
        executor.run_b1()
        executor.run_b2()
        executor.record_capture_challenge(
            "a" * 64, strict_json.digest({"baseline": 1})
        )
        recovery = Executor(
            ATTEMPT,
            registry,
            journal,
            backend,
            self.fixture.units(),
            self.fixture.preserved(),
            monotonic=self.fixture.clock.monotonic,
        )
        with self.assertRaisesRegex(ExecutionError, "incident grant"):
            recovery.recover()

    def test_anonymous_prelink_outcome_crash_recovers_from_intent_and_state(
        self,
    ) -> None:
        executor, registry, journal, backend = self.build()
        executor.preflight()
        unit = self.fixture.units()[0]
        action = f"b1:{unit.name}:stop"
        journal.append("intent", action, {"operation": "stop"})
        backend.unit_operation(unit.name, "stop")

        def fault(stage: str) -> None:
            if stage == "before-link":
                raise RuntimeError("recovery-before-link")

        journal._fault_enabled = True
        journal.fault = fault
        restarted = Executor(
            ATTEMPT,
            registry,
            journal,
            backend,
            self.fixture.units(),
            self.fixture.preserved(),
            monotonic=self.fixture.clock.monotonic,
        )
        with self.assertRaisesRegex(RuntimeError, "recovery-before-link"):
            restarted.recover()
        self.assertEqual(journal.orphan_temps(), ())
        self.assertEqual(len(journal.read_all()), 3)

        journal._fault_enabled = False
        journal.fault = lambda _stage: None
        with self.assertRaisesRegex(ExecutionError, "another recovery pass"):
            restarted.recover()
        self.assertTrue(
            any(
                record["kind"] == "recovery" and record["action_id"] == action
                for record in journal.read_all()
            )
        )

    def test_persistent_mask_drift_is_not_accepted_as_runtime_fence(self) -> None:
        executor, _registry, journal, backend = self.build()
        original_operation = backend.unit_operation

        def persistently_mask(name: str, operation: str) -> None:
            original_operation(name, operation)
            if operation == "mask":
                backend.states[name] = replace(
                    backend.states[name], unit_file_state="masked"
                )

        backend.unit_operation = persistently_mask  # type: ignore[method-assign]
        executor.preflight()
        with self.assertRaisesRegex(ExecutionError, "B1 failed"):
            executor.run_b1()
        self.assertNotEqual(executor.stage, Stage.F0_ELIGIBLE)
        mask_outcomes = [
            record
            for record in journal.read_all()
            if record["kind"] == "outcome"
            and record["action_id"].endswith(":mask")
        ]
        self.assertEqual(mask_outcomes, [])

    def test_all_preflight_surfaces_checked_before_first_effect(self) -> None:
        for defect in ("unit", "process", "preserved", "registry"):
            with self.subTest(defect=defect):
                fixture = Fixture()
                try:
                    backend = FakeBackend(fixture)
                    if defect == "unit":
                        name = next(iter(backend.states))
                        backend.states[name] = replace(
                            backend.states[name], active_state="failed"
                        )
                    elif defect == "process":
                        backend.processes = ("unowned-worker",)
                    elif defect == "preserved":
                        name = next(iter(backend.preserved))
                        backend.preserved[name] = replace(
                            backend.preserved[name], healthy=False
                        )
                    registry = RegistrySet(
                        fixture.expectations(),
                        tuple(str(path) for path in fixture.paths),
                    )
                    registry.acquire()
                    try:
                        if defect == "registry":
                            fixture.paths[5].write_text('{"jobs":[]}', encoding="utf-8")
                        executor = Executor(
                            ATTEMPT,
                            registry,
                            Journal(fixture.root / "journal", clock=fixture.clock),
                            backend,
                            fixture.units(),
                            fixture.preserved(),
                        )
                        with self.assertRaises((ExecutionError, RuntimeError)):
                            executor.preflight()
                        self.assertEqual(backend.effect_count, 0)
                    finally:
                        registry.close()
                finally:
                    fixture.close()

    def test_fault_before_and_after_every_atomic_effect_is_invalidated(self) -> None:
        for after in (False, True):
            for fail_effect in range(1, 29):
                with self.subTest(after=after, fail_effect=fail_effect):
                    fixture = Fixture()
                    try:
                        backend = FakeBackend(
                            fixture, fail_effect=fail_effect, fail_after_effect=after
                        )
                        registry = RegistrySet(
                            fixture.expectations(),
                            tuple(str(path) for path in fixture.paths),
                        )
                        registry.acquire()
                        try:
                            journal = Journal(
                                fixture.root / "journal", clock=fixture.clock
                            )
                            executor = Executor(
                                ATTEMPT,
                                registry,
                                journal,
                                backend,
                                fixture.units(),
                                fixture.preserved(),
                                monotonic=fixture.clock.monotonic,
                            )
                            executor.preflight()
                            with self.assertRaises(ExecutionError):
                                if fail_effect <= 24:
                                    executor.run_b1()
                                else:
                                    executor.run_b1()
                                    executor.run_b2()
                            self.assertEqual(len(journal.pending_intents()), 1)
                            with self.assertRaises(ExecutionError):
                                executor.recover()
                            self.assertEqual(journal.pending_intents(), {})
                            self.assertEqual(executor.stage, Stage.INVALID)
                            self.assertTrue(
                                any(
                                    record["kind"] == "invalidation"
                                    for record in journal.read_all()
                                )
                            )
                        finally:
                            registry.close()
                    finally:
                        fixture.close()

    def test_exact_reverse_rollback_is_atomic_and_grant_bounded(self) -> None:
        executor, registry, journal, backend = self.build()
        executor.preflight()
        executor.run_b1()
        executor.run_b2()
        second = FIXED_DELTAS[1]
        second_outcome = next(
            record
            for record in journal.read_all()
            if record["kind"] == "outcome"
            and record["action_id"]
            == f"b2:{second.registry_index}:{second.job_id}:pause"
        )
        preimage_jobs = {
            job["id"]: job
            for job in second_outcome["payload"]["preimage"]["document"]["jobs"]
        }
        self.assertFalse(preimage_jobs[FIXED_DELTAS[0].job_id]["enabled"])
        self.assertTrue(preimage_jobs[second.job_id]["enabled"])
        with self.assertRaises(ExecutionError):
            executor.rollback_before_f0(("all",), GRANT_DIGEST)
        authorized = tuple(reversed(executor.completed_actions))
        executor.persist_rollback_authorization(
            authorized, GRANT_DIGEST, journal.head()
        )
        executor.rollback_before_f0(authorized, GRANT_DIGEST)
        self.assertEqual(executor.stage, Stage.ROLLED_BACK)
        self.assertEqual(executor.completed_actions, [])
        self.assertEqual(journal.read_all()[-1]["action_id"], "rollback-complete")
        registry.revalidate()
        self.assertFalse(any(backend.job_is_paused(delta) for delta in FIXED_DELTAS))
        rollback_intents = [
            record["action_id"]
            for record in journal.read_all()
            if record["kind"] == "intent"
            and record["action_id"].startswith("rollback:")
        ]
        self.assertEqual(
            rollback_intents, [item["action"] for item in self.fixture.rollback_plan()]
        )

    def test_restart_completes_every_pending_authorized_rollback_effect(self) -> None:
        total_effects = len(self.fixture.effect_plan())
        for after in (False, True):
            for rollback_ordinal in range(1, total_effects + 1):
                with self.subTest(after=after, rollback_ordinal=rollback_ordinal):
                    fixture = Fixture()
                    try:
                        backend = FakeBackend(fixture)
                        registry = RegistrySet(
                            fixture.expectations(),
                            tuple(str(path) for path in fixture.paths),
                        )
                        registry.acquire()
                        journal = Journal(fixture.root / "journal", clock=fixture.clock)
                        executor = Executor(
                            ATTEMPT,
                            registry,
                            journal,
                            backend,
                            fixture.units(),
                            fixture.preserved(),
                            monotonic=fixture.clock.monotonic,
                        )
                        executor.preflight()
                        executor.run_b1()
                        executor.run_b2()
                        authorized = tuple(reversed(executor.completed_actions))
                        executor.persist_rollback_authorization(
                            authorized, GRANT_DIGEST, journal.head()
                        )
                        backend.fail_effect = backend.effect_count + rollback_ordinal
                        backend.fail_after_effect = after
                        with self.assertRaises(ExecutionError):
                            executor.rollback_before_f0(
                                tuple(reversed(executor.completed_actions)),
                                GRANT_DIGEST,
                            )
                        self.assertEqual(len(journal.pending_intents()), 1)
                        registry.close()

                        recovered_registry = RegistrySet(
                            registry.expectations,
                            tuple(str(path) for path in fixture.paths),
                        )
                        try:
                            restarted = Executor(
                                ATTEMPT,
                                recovered_registry,
                                journal,
                                backend,
                                fixture.units(),
                                fixture.preserved(),
                                monotonic=fixture.clock.monotonic,
                            )
                            restarted.recover(
                                allow_incident_rollback=True,
                                verified_rollback_grant_digest=GRANT_DIGEST,
                            )
                            remaining = tuple(reversed(restarted.completed_actions))
                            if remaining:
                                restarted.rollback_before_f0(
                                    remaining, GRANT_DIGEST
                                )
                            self.assertEqual(restarted.completed_actions, [])
                            recovered_registry.revalidate()
                            self.assertFalse(
                                any(
                                    backend.job_is_paused(delta)
                                    for delta in FIXED_DELTAS
                                )
                            )
                            self.assertTrue(
                                all(
                                    record["payload"].get(
                                        "authorization_grant_digest"
                                    )
                                    == GRANT_DIGEST
                                    for record in journal.read_all()
                                    if record["action_id"].startswith("rollback:")
                                    and record["kind"]
                                    in {"intent", "outcome", "recovery"}
                                )
                            )
                        finally:
                            recovered_registry.close()
                    finally:
                        fixture.close()

    def test_restart_reconciles_only_authorized_restored_unit_drift(self) -> None:
        for drift_kind in (
            "authorized-unit",
            "reconcile-before-effect",
            "reconcile-after-effect",
            "unauthorized-unit",
            "preserved",
        ):
            with self.subTest(drift_kind=drift_kind):
                fixture = Fixture()
                try:
                    backend = FakeBackend(fixture)
                    registry = RegistrySet(
                        fixture.expectations(),
                        tuple(str(path) for path in fixture.paths),
                    )
                    registry.acquire()
                    journal = Journal(fixture.root / "journal", clock=fixture.clock)
                    executor = Executor(
                        ATTEMPT,
                        registry,
                        journal,
                        backend,
                        fixture.units(),
                        fixture.preserved(),
                        monotonic=fixture.clock.monotonic,
                    )
                    executor.preflight()
                    executor.run_b1()
                    executor.run_b2()
                    full_authorized = tuple(reversed(executor.completed_actions))
                    executor.persist_rollback_authorization(
                        full_authorized, GRANT_DIGEST, journal.head()
                    )
                    # Four B2 restores plus unmask/enable/start restore the last
                    # baseline-active unit. Crash before the next unit effect.
                    backend.fail_effect = backend.effect_count + 8
                    backend.fail_after_effect = False
                    with self.assertRaises(ExecutionError):
                        executor.rollback_before_f0(full_authorized, GRANT_DIGEST)
                    restored_name = fixture.units()[-1].name
                    state = backend.states[restored_name]
                    backend.states[restored_name] = replace(
                        state,
                        active_state="inactive",
                        **(
                            {"source_fragment_digest": "sha256:" + "f" * 64}
                            if drift_kind == "unauthorized-unit"
                            else {}
                        ),
                    )
                    if drift_kind == "preserved":
                        preserved_name = fixture.preserved()[0].name
                        backend.preserved[preserved_name] = replace(
                            backend.preserved[preserved_name], healthy=False
                        )
                    registry.close()

                    recovered_registry = RegistrySet(
                        registry.expectations,
                        tuple(str(path) for path in fixture.paths),
                    )
                    try:
                        restarted = Executor(
                            ATTEMPT,
                            recovered_registry,
                            journal,
                            backend,
                            fixture.units(),
                            fixture.preserved(),
                            monotonic=fixture.clock.monotonic,
                        )
                        restarted.recover(
                            allow_incident_rollback=True,
                            verified_rollback_grant_digest=GRANT_DIGEST,
                        )
                        remaining = tuple(reversed(restarted.completed_actions))
                        if drift_kind in {
                            "authorized-unit",
                            "reconcile-before-effect",
                            "reconcile-after-effect",
                        }:
                            if drift_kind.startswith("reconcile-"):
                                backend.fail_effect = (
                                    backend.effect_count + len(remaining) + 1
                                )
                                backend.fail_after_effect = drift_kind.endswith(
                                    "after-effect"
                                )
                                with self.assertRaises(ExecutionError):
                                    restarted.rollback_before_f0(
                                        remaining, GRANT_DIGEST
                                    )
                                recovered_registry.close()
                                backend.fail_effect = None
                                final_registry = RegistrySet(
                                    registry.expectations,
                                    tuple(str(path) for path in fixture.paths),
                                )
                                try:
                                    restarted = Executor(
                                        ATTEMPT,
                                        final_registry,
                                        journal,
                                        backend,
                                        fixture.units(),
                                        fixture.preserved(),
                                        monotonic=fixture.clock.monotonic,
                                    )
                                    restarted.recover(
                                        allow_incident_rollback=True,
                                        verified_rollback_grant_digest=GRANT_DIGEST,
                                    )
                                finally:
                                    final_registry.close()
                            else:
                                restarted.rollback_before_f0(
                                    remaining, GRANT_DIGEST
                                )
                            self.assertEqual(restarted.stage, Stage.ROLLED_BACK)
                            self.assertTrue(
                                all(
                                    Executor._same_preflight(
                                        backend.inspect_unit(expected.name), expected
                                    )
                                    for expected in fixture.units()
                                )
                            )
                            reconciliations = [
                                record
                                for record in journal.read_all()
                                if record["kind"] in {"outcome", "recovery"}
                                and record["action_id"].startswith(
                                    "rollback-reconcile:"
                                )
                            ]
                            self.assertEqual(len(reconciliations), 1)
                            self.assertEqual(
                                reconciliations[0]["payload"]["restores"],
                                f"b1:{restored_name}:stop",
                            )
                        else:
                            with self.assertRaises(ExecutionError):
                                restarted.rollback_before_f0(
                                    remaining, GRANT_DIGEST
                                )
                            self.assertNotEqual(restarted.stage, Stage.ROLLED_BACK)
                            self.assertFalse(
                                any(
                                    record["action_id"] == "rollback-complete"
                                    for record in journal.read_all()
                                )
                            )
                    finally:
                        recovered_registry.close()
                finally:
                    fixture.close()

    def test_restart_recovers_atomic_registry_replacement_and_exact_state_vector(
        self,
    ) -> None:
        executor, registry, journal, backend = self.build()
        executor.preflight()
        executor.run_b1()
        delta = FIXED_DELTAS[0]
        action = f"b2:{delta.registry_index}:{delta.job_id}:pause"
        journal.append(
            "intent",
            action,
            {
                "operation": "hermes-internal-pause",
                "preimage_digests": list(registry.last_digests),
                "preimage": registry.evidence()[delta.registry_index],
            },
        )
        backend.pause_job(delta)
        registry.close()

        recovered_registry = RegistrySet(
            registry.expectations,
            tuple(str(path) for path in self.fixture.paths),
        )
        self.addCleanup(recovered_registry.close)
        restarted = Executor(
            ATTEMPT,
            recovered_registry,
            journal,
            backend,
            self.fixture.units(),
            self.fixture.preserved(),
            monotonic=self.fixture.clock.monotonic,
        )
        with self.assertRaises(ExecutionError):
            restarted.recover()
        self.assertEqual(restarted.stage, Stage.INVALID)
        recovery = next(
            item for item in journal.read_all() if item["kind"] == "recovery"
        )
        self.assertEqual(recovery["payload"]["status"], "achieved-before-crash")
        self.assertIn("preimage", recovery["payload"])
        self.assertIn("postimage", recovery["payload"])

        # First recovery durably resolves the unknown outcome; the next invocation
        # accounts that exact achieved prefix and may consume an incident grant.
        restarted.recover(allow_incident_rollback=True)
        self.assertEqual(restarted.completed_deltas, [delta])
        self.assertIn(action, restarted.completed_actions)
        authorized = tuple(reversed(restarted.completed_actions))
        restarted.persist_rollback_authorization(
            authorized, GRANT_DIGEST, journal.head()
        )
        restarted.rollback_before_f0(authorized, GRANT_DIGEST)
        self.assertFalse(backend.job_is_paused(delta))
        recovered_registry.revalidate()

    def test_bound_hermes_adapter_receives_only_pinned_home_and_lock_fds(self) -> None:
        _executor, registry, _journal, inspector = self.build()
        closure = Path("/nix/store/" + "a" * 32 + "-fixture")
        systemctl = ExecutableBinding(
            "systemctl", closure / "bin/systemctl", closure, "sha256:" + "1" * 64
        )
        hermes = ExecutableBinding(
            "hermes", closure / "bin/hermes", closure, "sha256:" + "2" * 64
        )
        adapter = ExecutableBinding(
            "hermes-mutation-adapter",
            closure / "bin/adapter",
            closure,
            "sha256:" + "3" * 64,
        )
        dropper = ExecutableBinding(
            "privilege-dropper", closure / "bin/setpriv", closure, "sha256:" + "4" * 64
        )
        backend = BoundCommandBackend(
            systemctl,
            hermes,
            adapter,
            dropper,
            inspector,
            registry,
            source_uid=os.getuid(),
            source_gid=os.getgid(),
            source_user="fixture-user",
            source_home=str(self.fixture.root / "home"),
            user_manager_machine=".host",
        )
        with patch(
            "phase_b.executor.subprocess.run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            backend.pause_job(FIXED_DELTAS[0])
        _args, kwargs = run.call_args
        command = run.call_args.args[0]
        self.assertEqual(
            command[:7],
            [
                str(dropper.path),
                "--reuid",
                str(os.getuid()),
                "--regid",
                str(os.getgid()),
                "--clear-groups",
                "--",
            ],
        )
        self.assertEqual(command[7], str(adapter.path))
        self.assertEqual(
            kwargs["env"]["HERMES_HOME"], f"/proc/self/fd/{kwargs['pass_fds'][0]}"
        )
        self.assertEqual(
            kwargs["env"]["PHASE_B_HERMES_LOCK_FD"], str(kwargs["pass_fds"][-1])
        )
        self.assertEqual(kwargs["env"]["PATH"], "")

    def test_bound_restore_uses_exact_preimage_protocol_via_source_dropper(
        self,
    ) -> None:
        _executor, registry, _journal, inspector = self.build()
        delta = FIXED_DELTAS[0]
        preimage = registry.evidence()[delta.registry_index]
        inspector.pause_job(delta)
        registry.revalidate((delta,), changed_delta=delta)
        postimage = registry.evidence()[delta.registry_index]
        closure = Path("/nix/store/" + "a" * 32 + "-fixture")

        def binding(name: str, digit: str) -> ExecutableBinding:
            return ExecutableBinding(
                name, closure / "bin" / name, closure, "sha256:" + digit * 64
            )

        backend = BoundCommandBackend(
            binding("systemctl", "1"),
            binding("hermes", "2"),
            binding("hermes-mutation-adapter", "3"),
            binding("privilege-dropper", "4"),
            inspector,
            registry,
            source_uid=os.getuid(),
            source_gid=os.getgid(),
            source_user="fixture-user",
            source_home=str(self.fixture.root / "home"),
            user_manager_machine=".host",
        )
        with patch(
            "phase_b.executor.subprocess.run",
            return_value=SimpleNamespace(returncode=0),
        ) as run:
            backend.restore_job_preimage(delta, preimage, postimage)
        command = run.call_args.args[0]
        self.assertIn("restore-preimage", command)
        payload = strict_json.loads_canonical(run.call_args.kwargs["input"])
        self.assertEqual(payload["job_id"], delta.job_id)
        self.assertEqual(
            payload["expected_postimage_digest"],
            registry.last_digests[delta.registry_index],
        )
        self.assertEqual(
            strict_json.canonical(payload["exact_preimage"]),
            strict_json.canonical(registry.expectations[delta.registry_index].document),
        )
        restored = next(
            job
            for job in payload["exact_preimage"]["jobs"]
            if job["id"] == delta.job_id
        )
        self.assertIs(restored["enabled"], True)

    def test_b3_requires_post_b2_and_inter_read_five_minutes(self) -> None:
        executor, _registry, _journal, _backend = self.build()
        executor.preflight()
        executor.run_b1()
        executor.run_b2()
        with self.assertRaises(ExecutionError):
            executor.record_custody_read(
                self.custody(self.fixture, "GET"), self.fixture.clock.monotonic()
            )
        executor.record_capture_challenge("a" * 64, strict_json.digest({"baseline": 1}))
        self.fixture.clock.advance(300)
        wrong = self.custody(self.fixture, "NO_OP")
        with self.assertRaises(ExecutionError):
            executor.record_custody_read(wrong, self.fixture.clock.monotonic())
        executor.record_custody_read(
            self.custody(self.fixture, "GET"), self.fixture.clock.monotonic()
        )
        self.fixture.clock.advance(299)
        with self.assertRaises(ExecutionError):
            executor.record_custody_read(
                self.custody(self.fixture, "NO_OP"), self.fixture.clock.monotonic()
            )

    def test_f0_requires_every_typed_evidence_reference(self) -> None:
        executor, _registry, _journal, _backend = self.build()
        executor.preflight()
        executor.run_b1()
        executor.run_b2()
        self.converge(executor)
        for field in self.evidence(self.fixture):
            with self.subTest(field=field):
                changed = self.evidence(self.fixture)
                changed[field] = None
                with self.assertRaises(ExecutionError):
                    executor.establish_f0_candidate(
                        changed,
                        "a" * 64,
                        "2026-08-20T00:10:00Z",
                        lambda _candidate: None,
                    )


if __name__ == "__main__":
    unittest.main()
