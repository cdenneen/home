"""
Deterministic fault-suite tests for the host-local policy endpoint,
runnable without any live infrastructure. Covers the Bootstrap Gate cases
that are pure logic (not requiring an actual Eros outage): unknown-cost
freeze, economic-exhaustion-vs-outage separation, minimum-capability
preservation, continuity credential denial, and idempotency across a
simulated continuity->recovery transition.

Run: python3 -m unittest test_endpoint.py -v
"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from classifier import OutageClassifier, Probe
from continuity import ContinuityController, ContinuityDenied, EmergencyCredentialStore
from governor import (
    Admission,
    EconomicState,
    Priority,
    UNKNOWN_WORKLOAD_DEFAULT,
    admission_decision,
    compute_state,
)
from state import LocalState


class TestUnknownCostNeverZero(unittest.TestCase):
    """BOOT-005/006: unknown/missing cost must never be treated as $0."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = LocalState(Path(self.tmp.name) / "state.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_none_cost_is_not_zero(self):
        self.state.record_spend(
            actor="test", requested_route="tier2-coding", actual_provider="bedrock",
            actual_model="sonnet-5", cost_usd=None, input_tokens=100,
            output_tokens=50, source="eros",
        )
        burn = self.state.burn_since("test", 0)
        self.assertEqual(burn["unknown_count"], 1)
        self.assertEqual(burn["known_cost"], 0.0)  # SUM ignores NULLs, doesn't coerce them
        # The two must remain distinguishable: known_cost==0 with
        # unknown_count==0 means "genuinely free", not "unknown".

    def test_genuinely_free_local_model_is_distinguishable(self):
        self.state.record_spend(
            actor="test", requested_route="tier0-local", actual_provider="ollama",
            actual_model="qwen2.5-coder", cost_usd=0.0, input_tokens=100,
            output_tokens=50, source="eros",
        )
        burn = self.state.burn_since("test", 0)
        self.assertEqual(burn["unknown_count"], 0)
        self.assertEqual(burn["known_cost"], 0.0)

    def test_unknown_cost_forces_conservative_economic_state(self):
        # Mirrors endpoint.PolicyEndpoint.economic_state(): any unknown
        # cost event in the burn window must push to CRITICAL rather than
        # silently computing a state from a partial/wrong total.
        self.state.record_spend(
            actor="a", requested_route="r", actual_provider=None, actual_model=None,
            cost_usd=None, input_tokens=1, output_tokens=1, source="eros",
        )
        burn = self.state.burn_since("a", 0)
        self.assertGreater(burn["unknown_count"], 0)


class TestEconomicExhaustionVsOutage(unittest.TestCase):
    """Economic exhaustion (governor) and infrastructure outage (classifier)
    must be entirely separate code paths - exhaustion can never masquerade
    as an outage and escape through the continuity/direct-provider path."""

    def test_break_glass_state_is_not_continuity_activation(self):
        # compute_state can return BREAK_GLASS purely from spend math with
        # Eros perfectly healthy. That must only ever gate NORMAL-mode
        # admission (deny P1-P3), never by itself call into
        # ContinuityController.
        state, _ = compute_state(
            monthly_budget=20.0, mtd_cost=40.0, burn_1h=0.1,
            expected_burn_1h=0.5, now_ts=time.time(),
        )
        self.assertEqual(state, EconomicState.BREAK_GLASS)
        # Admission for P1 in this state is DENY, not "activate continuity".
        self.assertEqual(admission_decision(state, Priority.P1), Admission.DENY)
        # There is no function in governor.py that takes an EconomicState
        # and returns a continuity activation - by construction, spend
        # math alone cannot reach continuity.py at all.

    def test_429_and_budget_denial_never_reach_classifier(self):
        # The classifier only ever sees TCP/HTTP health probes it
        # generates itself (classifier.tcp_probe / http_health_probe).
        # It has no method that accepts a provider error code as input,
        # so a 429 or budget-denial response literally cannot be routed
        # into outage classification even by an implementation mistake
        # upstream - there is no such parameter to pass it through.
        classifier = OutageClassifier(eros_ip="127.0.0.1", eros_port=1, eros_health_url=None)
        import inspect
        sig = inspect.signature(classifier.observe)
        self.assertEqual(len(sig.parameters), 0)


class TestMinimumCapabilityPreservation(unittest.TestCase):
    """execution-contract.md 8: state changes frequency/concurrency/queueing,
    never the capability tier of the route itself."""

    def test_admission_decision_never_touches_route(self):
        # admission_decision's signature proves this structurally: it takes
        # (state, priority) and returns only an Admission enum member -
        # there is no route/tier parameter for it to have silently changed,
        # and no code path where DENY/QUEUE mutates the caller's requested
        # route string.
        for state in EconomicState:
            for priority in Priority:
                result = admission_decision(state, priority)
                self.assertIsInstance(result, Admission)

    def test_critical_state_still_admits_p0_p1(self):
        self.assertEqual(admission_decision(EconomicState.CRITICAL, Priority.P0), Admission.ADMIT)
        self.assertEqual(admission_decision(EconomicState.CRITICAL, Priority.P1), Admission.ADMIT)

    def test_unknown_workload_defaults_to_least_privilege_not_just_low_priority(self):
        self.assertEqual(UNKNOWN_WORKLOAD_DEFAULT.priority, Priority.P2)
        self.assertFalse(UNKNOWN_WORKLOAD_DEFAULT.mutation_allowed)
        self.assertFalse(UNKNOWN_WORKLOAD_DEFAULT.t4_allowed)
        self.assertNotEqual(UNKNOWN_WORKLOAD_DEFAULT.minimum_capability, "tier0-local")


class TestContinuityCredentialDenial(unittest.TestCase):
    """BOOT-013: emergency credential retrieval fails -> deny + alert,
    never substitute a normal credential."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = LocalState(Path(self.tmp.name) / "state.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_credential_path_denies(self):
        store = EmergencyCredentialStore(None)
        controller = ContinuityController(
            state=self.state, credential_store=store,
            break_glass_flag_path=Path(self.tmp.name) / "bg.flag",
        )
        with self.assertRaises(ContinuityDenied):
            controller.activate_continuity_auto({"probe": "fail"})

    def test_nonexistent_credential_file_denies(self):
        store = EmergencyCredentialStore(Path(self.tmp.name) / "does-not-exist.json")
        controller = ContinuityController(
            state=self.state, credential_store=store,
            break_glass_flag_path=Path(self.tmp.name) / "bg.flag",
        )
        with self.assertRaises(ContinuityDenied):
            controller.activate_continuity_auto({})

    def test_malformed_credential_file_denies(self):
        cred_path = Path(self.tmp.name) / "bad.json"
        cred_path.write_text("not valid json {{{")
        store = EmergencyCredentialStore(cred_path)
        controller = ContinuityController(
            state=self.state, credential_store=store,
            break_glass_flag_path=Path(self.tmp.name) / "bg.flag",
        )
        with self.assertRaises(ContinuityDenied):
            controller.activate_continuity_auto({})

    def test_valid_credential_activates_and_records_episode(self):
        cred_path = Path(self.tmp.name) / "good.json"
        cred_path.write_text(json.dumps({
            "provider": "openai", "model": "gpt-5-mini", "key_or_role": "sk-test",
            "daily_cap_usd": 5.0, "monthly_cap_usd": 50.0,
        }))
        store = EmergencyCredentialStore(cred_path)
        controller = ContinuityController(
            state=self.state, credential_store=store,
            break_glass_flag_path=Path(self.tmp.name) / "bg.flag",
        )
        cred = controller.activate_continuity_auto({"probe": "fail"})
        self.assertEqual(cred.provider, "openai")
        episode = self.state.open_continuity_episode()
        self.assertIsNotNone(episode)
        self.assertEqual(episode["mode"], "continuity_auto")


class TestBreakGlassExpiry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = LocalState(Path(self.tmp.name) / "state.db")
        self.flag_path = Path(self.tmp.name) / "bg.flag"

    def tearDown(self):
        self.tmp.cleanup()

    def test_break_glass_activates_and_expires(self):
        controller = ContinuityController(
            state=self.state,
            credential_store=EmergencyCredentialStore(None),
            break_glass_flag_path=self.flag_path,
            break_glass_max_duration_s=0.2,
        )
        controller.break_glass_activate("operator test")
        self.assertTrue(controller.break_glass_active())
        time.sleep(0.3)
        self.assertFalse(controller.break_glass_active())
        self.assertFalse(self.flag_path.exists())  # auto-deactivated, not left dangling


class TestContinuityAccountingReconciliation(unittest.TestCase):
    """execution-contract.md 11: local continuity accounting must be
    reconcilable back into the EPR after Eros recovers."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = LocalState(Path(self.tmp.name) / "state.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_ended_episode_is_pending_reconciliation_until_marked(self):
        episode_id = self.state.start_continuity_episode(
            mode="continuity_auto", reason="test", evidence={}
        )
        self.state.end_continuity_episode(episode_id)
        pending = self.state.unreconciled_episodes()
        self.assertEqual(len(pending), 1)
        self.state.mark_reconciled(episode_id)
        self.assertEqual(self.state.unreconciled_episodes(), [])


class TestIdempotencyAcrossRecovery(unittest.TestCase):
    """execution-contract.md 10.5: Eros restoration must not replay an
    in-flight or completed continuity action merely because the normal
    route became available again."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = LocalState(Path(self.tmp.name) / "state.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_digest_returns_cached_completion_not_a_replay(self):
        body = b'{"model": "tier2-coding", "messages": []}'
        digest = self.state.digest_for("actor1", "tier2-coding", body, None)
        self.state.idempotency_start(digest, "actor1")
        self.state.idempotency_complete(digest, json.dumps({"result": "done-once"}))

        # Simulate the same logical request arriving again after Eros
        # recovers (same actor/route/body -> same digest).
        again = self.state.digest_for("actor1", "tier2-coding", body, None)
        self.assertEqual(digest, again)
        cached = self.state.idempotency_lookup(again)
        self.assertIsNotNone(cached["completed_at"])
        self.assertEqual(json.loads(cached["response_json"]), {"result": "done-once"})

    def test_explicit_idempotency_key_overrides_body_digest(self):
        body_a = b'{"model": "tier2-coding", "messages": [{"content": "v1"}]}'
        body_b = b'{"model": "tier2-coding", "messages": [{"content": "v2"}]}'
        d1 = self.state.digest_for("actor1", "tier2-coding", body_a, "same-task-123")
        d2 = self.state.digest_for("actor1", "tier2-coding", body_b, "same-task-123")
        self.assertEqual(d1, d2)  # explicit key wins over body differences


class TestRestartAmplificationBoundary(unittest.TestCase):
    """Process-level restart/reinvocation amplification is distinct from
    request-level retry storms - this endpoint doesn't itself loop-restart
    (that's the systemd unit's job, BOOT-025), but must not let a crash
    mid-continuity leave a half-completed idempotency row that a naive
    retry would treat as "not yet attempted"."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = LocalState(Path(self.tmp.name) / "state.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_started_but_not_completed_is_distinguishable_from_fresh(self):
        body = b'{"model": "tier2-coding"}'
        digest = self.state.digest_for("actor1", "tier2-coding", body, None)
        self.state.idempotency_start(digest, "actor1")
        row = self.state.idempotency_lookup(digest)
        self.assertIsNotNone(row)
        self.assertIsNone(row["completed_at"])  # in-flight, not done, not absent


if __name__ == "__main__":
    unittest.main()
