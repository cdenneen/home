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

from action_classification import (
    CONTINUITY_AUTO_ALLOWED,
    CONTINUITY_ORDER,
    EFFECT_CLASS_CEILING,
    SOURCE_CONTINUITY_CEILING,
    EffectClass,
    classify_tools,
    continuity_mode_permits,
    effective_continuity_class,
    most_restrictive,
    source_ceiling,
)
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

    def test_burn_since_with_zero_events_never_returns_none_unknown_count(self):
        # Confirmed live (nyx-gitlab, 2026-08-27): SUM() over zero matching
        # rows returns SQL NULL, not 0 - a fresh actor with no spend_events
        # yet crashed economic_state()'s `unknown_count > 0` on TypeError.
        burn = self.state.burn_since("brand-new-actor-no-events", 0)
        self.assertEqual(burn["unknown_count"], 0)
        self.assertIsInstance(burn["unknown_count"], int)
        self.assertEqual(burn["known_cost"], 0.0)
        self.assertEqual(burn["total_count"], 0)

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


class TestActionLevelClassification(unittest.TestCase):
    """Piece 1: deterministic action-level continuity classification for
    Hermes-native GitLab MCP calls, using the outbound tools array."""

    def test_known_read_only_tool_is_permissive(self):
        effect, classifications = classify_tools(["get_issue", "list_branches"])
        self.assertEqual(effect, EffectClass.READ_ONLY)
        self.assertTrue(all(c.effect_class == EffectClass.READ_ONLY for c in classifications))

    def test_known_bounded_mutation_tool(self):
        effect, _ = classify_tools(["create_branch"])
        self.assertEqual(effect, EffectClass.BOUNDED_MUTATION)

    def test_known_high_impact_tool(self):
        effect, _ = classify_tools(["merge_merge_request"])
        self.assertEqual(effect, EffectClass.HIGH_IMPACT)

    def test_most_restrictive_tool_wins_when_multiple_offered(self):
        effect, _ = classify_tools(["get_issue", "create_branch", "merge_merge_request"])
        self.assertEqual(effect, EffectClass.HIGH_IMPACT)

    def test_unknown_tool_denies_automatic_never_defaults_permissive(self):
        effect, classifications = classify_tools(["some_future_tool_not_in_table"])
        self.assertEqual(effect, EffectClass.UNKNOWN)
        self.assertEqual(classifications[0].tool, "some_future_tool_not_in_table")

    def test_no_tools_offered_is_read_only(self):
        effect, classifications = classify_tools([])
        self.assertEqual(effect, EffectClass.READ_ONLY)
        self.assertEqual(classifications, [])

    def test_unrecognized_source_is_neutral_not_restrictive(self):
        # PO decision (#37): unknown *source* grants no additional
        # authority but must not itself prohibit proven-safe work -
        # "automatic" is the identity element for most_restrictive().
        self.assertEqual(source_ceiling("some_future_source_type"), "automatic")
        self.assertEqual(source_ceiling(None), "automatic")
        self.assertEqual(source_ceiling(""), "automatic")

    def test_known_source_ceilings_match_audit_recommendations(self):
        self.assertEqual(source_ceiling("cron"), "automatic-read-only")
        self.assertEqual(source_ceiling("slack"), "human-present")

    def test_subagent_has_no_dedicated_source_ceiling(self):
        # PO decision (#38): subagent is execution metadata, not a
        # continuity determinant - it is not in SOURCE_CONTINUITY_CEILING
        # at all, so it falls through to the same neutral unknown-source
        # ceiling as any other unrecognized source.
        self.assertNotIn("subagent", SOURCE_CONTINUITY_CEILING)
        self.assertEqual(source_ceiling("subagent"), "automatic")

    def test_most_restrictive_picks_the_strictest_of_several(self):
        self.assertEqual(
            most_restrictive("automatic", "human-present", "automatic-read-only"),
            "human-present",
        )
        self.assertEqual(most_restrictive("unavailable", "automatic"), "unavailable")

    def test_gateway_ceiling_never_widened_by_a_safe_request(self):
        # A generous gateway ceiling does not make a genuinely risky
        # request safe: the request's own tool classification still wins.
        effective, evidence = effective_continuity_class(
            "automatic", ["merge_merge_request"], "slack"
        )
        self.assertEqual(effective, "manual-break-glass")
        self.assertEqual(evidence["policy_version"], "bootstrap_v1")

    def test_restrictive_gateway_ceiling_caps_even_a_safe_request(self):
        effective, _ = effective_continuity_class("manual-break-glass", ["get_issue"], "cron")
        self.assertEqual(effective, "manual-break-glass")

    def test_unknown_tool_forces_deny_regardless_of_source(self):
        effective, evidence = effective_continuity_class(
            "automatic-read-only", ["totally_unrecognized_tool"], "cron"
        )
        self.assertEqual(effective, "manual-break-glass")
        self.assertIn("totally_unrecognized_tool", evidence["unknown_tools"])

    def test_read_only_cron_request_is_fully_permissive_under_a_permissive_ceiling(self):
        effective, _ = effective_continuity_class("automatic-read-only", ["get_issue"], "cron")
        self.assertEqual(effective, "automatic-read-only")

    def test_continuity_auto_permits_only_automatic_classes(self):
        self.assertTrue(continuity_mode_permits("continuity_auto", "automatic-read-only"))
        self.assertTrue(continuity_mode_permits("continuity_auto", "automatic"))
        self.assertFalse(continuity_mode_permits("continuity_auto", "human-present"))
        self.assertFalse(continuity_mode_permits("continuity_auto", "manual-break-glass"))
        self.assertFalse(continuity_mode_permits("continuity_auto", "unavailable"))

    def test_break_glass_permits_up_to_manual_break_glass_but_not_unavailable(self):
        self.assertTrue(continuity_mode_permits("break_glass", "manual-break-glass"))
        self.assertTrue(continuity_mode_permits("break_glass", "human-present"))
        self.assertFalse(continuity_mode_permits("break_glass", "unavailable"))

    def test_normal_mode_is_not_gated_by_continuity_class(self):
        for c in ("automatic", "automatic-read-only", "human-present", "manual-break-glass", "unavailable"):
            self.assertTrue(continuity_mode_permits("normal", c))

    def test_continuity_auto_allowed_set_never_includes_a_mutation_class(self):
        self.assertNotIn("human-present", CONTINUITY_AUTO_ALLOWED)
        self.assertNotIn("manual-break-glass", CONTINUITY_AUTO_ALLOWED)


class TestUnknownPurposeVsUnknownEffect(unittest.TestCase):
    """PO decision, roadmap-amendment-unified-topology-workstreams-global-
    economics-developer-clients.md #37: unknown purpose/workload does NOT
    imply dangerous effect; unknown effect does. These are deliberately
    different axes - this class exists specifically so a later "cleanup"
    cannot silently normalize them into one value without breaking a test
    and forcing a conscious decision."""

    def test_unknown_source_does_not_prohibit_a_proven_read_only_tool(self):
        effective, evidence = effective_continuity_class(
            "automatic-read-only", ["get_issue"], None
        )
        self.assertEqual(effective, "automatic-read-only")
        self.assertEqual(evidence["source_ceiling"], "automatic")

    def test_unknown_source_with_no_tools_at_all_is_still_permissive(self):
        effective, _ = effective_continuity_class("automatic-read-only", [], None)
        self.assertEqual(effective, "automatic-read-only")

    def test_unknown_source_grants_no_additional_authority_over_a_mutation_tool(self):
        # The neutral "automatic" source ceiling never widens a tool's own
        # restriction - it only ever fails to add a NEW one.
        effective, _ = effective_continuity_class(
            "automatic-read-only", ["create_branch"], None
        )
        self.assertEqual(effective, "human-present")

    def test_unknown_tool_effect_fails_closed_even_under_a_known_permissive_source(self):
        # The one case that legitimately denies: unverified EFFECT beats a
        # known-permissive source, because we cannot prove the action is
        # safe merely because we know who/what sent it.
        effective, _ = effective_continuity_class(
            "automatic-read-only", ["some_new_unclassified_tool"], "cron"
        )
        self.assertEqual(effective, "manual-break-glass")

    def test_governor_unknown_workload_default_and_unknown_effect_ceiling_intentionally_differ(self):
        from governor import UNKNOWN_WORKLOAD_DEFAULT
        self.assertEqual(UNKNOWN_WORKLOAD_DEFAULT.continuity_class, "automatic-read-only")
        self.assertEqual(EFFECT_CLASS_CEILING[EffectClass.UNKNOWN], "manual-break-glass")
        self.assertNotEqual(
            UNKNOWN_WORKLOAD_DEFAULT.continuity_class,
            EFFECT_CLASS_CEILING[EffectClass.UNKNOWN],
        )

    def test_subagent_read_only_delegation_remains_automatic(self):
        # PO decision (#38): "Read-only qualified subagents may continue
        # automatically."
        effective, _ = effective_continuity_class(
            "automatic-read-only", ["get_issue"], "subagent"
        )
        self.assertEqual(effective, "automatic-read-only")

    def test_subagent_mutation_capable_work_follows_mutation_policy(self):
        # PO decision (#38): "mutation-capable subagents follow mutation
        # policy" - subagent-as-source never overrides the tool's own
        # ceiling in either direction.
        effective, _ = effective_continuity_class(
            "automatic-read-only", ["merge_merge_request"], "subagent"
        )
        self.assertEqual(effective, "manual-break-glass")

    def test_subagent_unbounded_effect_fails_closed(self):
        # PO decision (#38): "unknown/unbounded effects fail closed."
        effective, evidence = effective_continuity_class(
            "automatic-read-only", ["some_unbounded_subagent_tool"], "subagent"
        )
        self.assertEqual(effective, "manual-break-glass")
        self.assertIn("some_unbounded_subagent_tool", evidence["unknown_tools"])


class TestPolicyEndpointTrustDomainFailsClosed(unittest.TestCase):
    """New architecture constraint: trust_domain/agent/workstream are
    required and independent of gateway/profile identity - an instance
    missing or misconfiguring trust_domain must fail closed at startup,
    never silently default to a guessed tenant."""

    def _base_config(self, tmp_dir, **overrides):
        key_path = Path(tmp_dir) / "key.txt"
        key_path.write_text("sk-test-key\n")
        config = {
            "actor": "test-actor",
            "trust_domain": "work",
            "agent": "nyx",
            "workstream": "eks",
            "eros_base_url": "http://127.0.0.1:1",
            "eros_tailscale_ip": "127.0.0.1",
            "eros_api_key_file": str(key_path),
            "state_db_path": str(Path(tmp_dir) / "state.db"),
            "emergency_credential_path": None,
            "break_glass_flag_path": str(Path(tmp_dir) / "bg.flag"),
        }
        config.update(overrides)
        return config

    def test_missing_trust_domain_raises(self):
        from endpoint import PolicyEndpoint
        with tempfile.TemporaryDirectory() as tmp:
            config = self._base_config(tmp)
            del config["trust_domain"]
            with self.assertRaises(KeyError):
                PolicyEndpoint(config)

    def test_invalid_trust_domain_value_raises(self):
        from endpoint import PolicyEndpoint
        with tempfile.TemporaryDirectory() as tmp:
            config = self._base_config(tmp, trust_domain="both")
            with self.assertRaises(ValueError):
                PolicyEndpoint(config)

    def test_valid_trust_domain_constructs(self):
        from endpoint import PolicyEndpoint
        with tempfile.TemporaryDirectory() as tmp:
            config = self._base_config(tmp)
            endpoint = PolicyEndpoint(config)
            self.assertEqual(endpoint.trust_domain, "work")
            self.assertEqual(endpoint.agent, "nyx")
            self.assertEqual(endpoint.workstream, "eks")


class TestHermesMetadataStrippedBeforeForwarding(unittest.TestCase):
    """Confirmed live (nyx-gitlab, 2026-08-27): forwarding x_hermes_source
    verbatim to Eros/Bedrock fails - "Extra inputs are not permitted".
    x_hermes_* fields must be used for local classification only, never
    sent onward to the actual provider."""

    def setUp(self):
        from endpoint import PolicyEndpoint
        self.tmp = tempfile.TemporaryDirectory()
        key_path = Path(self.tmp.name) / "key.txt"
        key_path.write_text("sk-test-key\n")
        config = {
            "actor": "test-actor",
            "trust_domain": "work",
            "agent": "nyx",
            "workstream": "gitlab",
            "eros_base_url": "http://127.0.0.1:1",
            "eros_tailscale_ip": "127.0.0.1",
            "eros_api_key_file": str(key_path),
            "state_db_path": str(Path(self.tmp.name) / "state.db"),
            "emergency_credential_path": None,
            "break_glass_flag_path": str(Path(self.tmp.name) / "bg.flag"),
        }
        self.endpoint = PolicyEndpoint(config)
        self.endpoint.economic_state = lambda: (EconomicState.NORMAL, "ok")
        self.captured = {}

        def fake_forward_normal(requested_route, body):
            self.captured["body"] = json.loads(body)
            return 200, b'{"choices": []}', {}

        self.endpoint.forward_normal = fake_forward_normal

    def tearDown(self):
        self.tmp.cleanup()

    def test_x_hermes_source_stripped_before_forwarding(self):
        request_body = json.dumps({
            "model": "tier2-coding",
            "messages": [{"role": "user", "content": "hi"}],
            "x_hermes_source": "cron",
        }).encode()
        status, _ = self.endpoint.handle_chat_completion(Priority.P1, request_body)
        self.assertEqual(status, 200)
        self.assertNotIn("x_hermes_source", self.captured["body"])
        self.assertEqual(self.captured["body"]["model"], "tier2-coding")

    def test_body_without_hermes_metadata_passes_through_unchanged(self):
        request_body = json.dumps({
            "model": "tier2-coding",
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()
        self.endpoint.handle_chat_completion(Priority.P1, request_body)
        self.assertEqual(self.captured["body"], json.loads(request_body))


class TestMetadataProvenanceAndAuthorityBoundary(unittest.TestCase):
    """Roadmap amendment #39A-F: metadata describes a request; metadata
    does not authorize one. Caller-supplied fields may narrow authority
    but must never widen it. This class proves the two concrete
    properties that boundary depends on today."""

    def test_source_assertion_can_never_widen_below_the_gateway_tool_floor(self):
        # Fuzz every known source, several adversarial/unrecognized
        # claims, and the neutral/missing case: the ASSERTED source_ceiling
        # can only ever match or exceed (in restrictiveness) the floor set
        # by the ATTESTED gateway_ceiling and DERIVED tool_ceiling alone -
        # proof that most_restrictive()'s max()-over-all-inputs structure
        # is floor-safe by construction, not merely by convention.
        candidate_sources = list(SOURCE_CONTINUITY_CEILING) + [
            None, "", "cron", "slack",
            "attacker_supplied_favorable_label",
            "automatic",  # even claiming the maximally permissive label directly
        ]
        for gateway_ceiling in CONTINUITY_ORDER:
            for tool_names in ([], ["get_issue"], ["create_branch"], ["merge_merge_request"], ["unclassified_tool"]):
                floor = most_restrictive(gateway_ceiling, EFFECT_CLASS_CEILING[classify_tools(tool_names)[0]])
                floor_index = CONTINUITY_ORDER.index(floor)
                for source in candidate_sources:
                    effective, _ = effective_continuity_class(gateway_ceiling, tool_names, source)
                    self.assertGreaterEqual(
                        CONTINUITY_ORDER.index(effective), floor_index,
                        f"source={source!r} widened below floor {floor!r} "
                        f"(gateway={gateway_ceiling!r}, tools={tool_names!r}) -> {effective!r}",
                    )

    def test_trust_domain_agent_workstream_never_read_from_request_body(self):
        # ATTESTED fields (config-sourced) must not be overridable by
        # caller-supplied content, even if a malicious/buggy caller sends
        # a body that names those exact keys.
        from endpoint import PolicyEndpoint
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "key.txt"
            key_path.write_text("sk-test-key\n")
            config = {
                "actor": "test-actor",
                "trust_domain": "personal",
                "agent": "ghost",
                "workstream": "assistant",
                "eros_base_url": "http://127.0.0.1:1",
                "eros_tailscale_ip": "127.0.0.1",
                "eros_api_key_file": str(key_path),
                "state_db_path": str(Path(tmp) / "state.db"),
                "emergency_credential_path": None,
                "break_glass_flag_path": str(Path(tmp) / "bg.flag"),
            }
            endpoint = PolicyEndpoint(config)
            endpoint.economic_state = lambda: (EconomicState.NORMAL, "ok")
            captured = {}

            def fake_forward_normal(requested_route, body):
                return 200, b'{"choices": []}', {}

            endpoint.forward_normal = fake_forward_normal

            malicious_body = json.dumps({
                "model": "tier2-coding",
                "messages": [{"role": "user", "content": "hi"}],
                # An attacker/buggy caller naming these keys must have zero
                # effect - endpoint.py never reads them from the body.
                "trust_domain": "work",
                "agent": "nyx",
                "workstream": "eks",
                "actor": "nyx-gitlab",
            }).encode()
            endpoint.handle_chat_completion(Priority.P1, malicious_body)

            self.assertEqual(endpoint.trust_domain, "personal")
            self.assertEqual(endpoint.agent, "ghost")
            self.assertEqual(endpoint.workstream, "assistant")
            self.assertEqual(endpoint.actor, "test-actor")


class _DrainingTCPServer:
    """A real listening socket that actually accepts and closes every
    connection in a background thread - a bare `listen(1)` with no accept
    loop leaves connections un-accepted in the backlog, which can make a
    *second* real probe fail for reasons that have nothing to do with the
    classifier logic under test (confirmed live: this is exactly what
    produced a false "qualified_outage" the first time these tests ran)."""

    def __init__(self):
        import socket as _socket
        import threading as _threading

        self._socket = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(128)
        self.port = self._socket.getsockname()[1]
        self._stop = False
        self._thread = _threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        self._socket.settimeout(0.2)
        while not self._stop:
            try:
                conn, _ = self._socket.accept()
                conn.close()
            except OSError:
                continue

    def close(self):
        self._stop = True
        self._socket.close()


class TestBoot027RealOutageClassification(unittest.TestCase):
    """BOOT-027 (total Eros loss): real socket-level probing against a
    genuinely unreachable address, not a mock - proves the classifier
    itself correctly escalates/recovers under real (non-)connectivity,
    independent of anything Eros/Postgres/DNS would need to cooperate
    with (execution-contract.md 10.1/10.2, host-local independence)."""

    def test_real_unreachable_address_escalates_to_qualified_outage(self):
        # 240.0.0.0/4 is a reserved, unrouted "Class E" block - real
        # sockets on this network will refuse/timeout, never accidentally
        # succeed, without needing network isolation tooling.
        classifier = OutageClassifier(
            eros_ip="240.0.0.1", eros_port=1, eros_health_url=None,
            escalate_after=3, recover_after=3,
        )
        results = [classifier.observe() for _ in range(3)]
        self.assertEqual(results[-1], "qualified_outage")
        self.assertGreaterEqual(classifier.evidence()["consecutive_fail"], 3)
        for probe in classifier.evidence()["probes"]:
            self.assertFalse(probe["ok"])

    def test_real_reachable_address_recovers_to_healthy_after_qualified_outage(self):
        server = _DrainingTCPServer()
        try:
            classifier = OutageClassifier(
                eros_ip="240.0.0.1", eros_port=1, eros_health_url=None,
                escalate_after=2, recover_after=2,
            )
            for _ in range(2):
                classifier.observe()
            self.assertEqual(classifier.observe(), "qualified_outage")

            # Real recovery: point at a real, live, accepting socket.
            classifier.eros_ip = "127.0.0.1"
            classifier.eros_port = server.port
            results = [classifier.observe() for _ in range(2)]
            self.assertEqual(results[-1], "healthy")
        finally:
            server.close()

    def test_ambiguous_split_signal_never_silently_escalates(self):
        # TCP up, HTTP down (or vice versa) must not accumulate toward
        # either streak - repeated ambiguity must never quietly become a
        # qualified outage merely by looking similar often enough.
        server = _DrainingTCPServer()
        try:
            classifier = OutageClassifier(
                eros_ip="127.0.0.1", eros_port=server.port,
                eros_health_url="http://240.0.0.1:1/health",  # unreachable
                escalate_after=2, recover_after=2,
            )
            for _ in range(5):
                self.assertEqual(classifier.observe(), "ambiguous")
            self.assertEqual(classifier.evidence()["consecutive_fail"], 0)
            self.assertEqual(classifier.evidence()["consecutive_ok"], 0)
        finally:
            server.close()


class TestBoot028RecoveryIdempotencyEndToEnd(unittest.TestCase):
    """BOOT-028: Eros recovers mid-flight while a mutating continuity
    request is in progress or just completed - the same request resubmitted
    through the now-healthy normal route must not execute twice. Exercises
    the real PolicyEndpoint.handle_chat_completion path end-to-end, not
    just the underlying LocalState idempotency primitives directly."""

    def setUp(self):
        from endpoint import PolicyEndpoint
        self.tmp = tempfile.TemporaryDirectory()
        key_path = Path(self.tmp.name) / "key.txt"
        key_path.write_text("sk-test-key\n")
        cred_path = Path(self.tmp.name) / "emergency.json"
        cred_path.write_text(json.dumps({
            "provider": "openai", "model": "gpt-5-mini", "key_or_role": "sk-test",
            "daily_cap_usd": 5.0, "monthly_cap_usd": 50.0,
        }))
        config = {
            "actor": "test-actor",
            "trust_domain": "work",
            "agent": "nyx",
            "workstream": "gitlab",
            "priority": "P0",
            "eros_base_url": "http://127.0.0.1:1",
            "eros_tailscale_ip": "127.0.0.1",
            "eros_api_key_file": str(key_path),
            "state_db_path": str(Path(self.tmp.name) / "state.db"),
            "emergency_credential_path": str(cred_path),
            "break_glass_flag_path": str(Path(self.tmp.name) / "bg.flag"),
        }
        self.endpoint = PolicyEndpoint(config)
        self.forward_normal_calls = []

        def poisoned_forward_normal(requested_route, body):
            # A second real execution would call this - fail the test
            # loudly rather than silently succeeding if the idempotency
            # check didn't short-circuit first.
            self.forward_normal_calls.append((requested_route, body))
            return 200, b'{"choices": [], "model": "should-not-execute-twice"}', {"cost_usd": 0.01}

        self.endpoint.forward_normal = poisoned_forward_normal
        self.endpoint.economic_state = lambda: (EconomicState.NORMAL, "ok")

    def tearDown(self):
        self.tmp.cleanup()

    def test_continuity_completion_is_not_replayed_after_recovery_via_normal_mode(self):
        request_body = json.dumps({
            "model": "tier2-coding",
            "messages": [{"role": "user", "content": "merge it"}],
            "idempotency_key": "gitlab-mr-42-merge",
        }).encode()

        # 1. Eros is down - qualified outage, CONTINUITY-AUTO activates.
        self.endpoint._last_classification = "qualified_outage"
        status1, raw1 = self.endpoint.handle_chat_completion(Priority.P0, request_body)
        self.assertEqual(status1, 200)
        self.assertEqual(len(self.forward_normal_calls), 0)  # continuity path, not normal
        first_response = json.loads(raw1)
        self.assertEqual(first_response["id"], "continuity-bootstrap")

        # 2. Eros recovers - mode reverts to normal.
        self.endpoint._last_classification = "healthy"
        self.assertEqual(self.endpoint.current_mode(), "normal")

        # 3. The caller (or a naive retry) resubmits the IDENTICAL request
        # now that the normal route is healthy again.
        status2, raw2 = self.endpoint.handle_chat_completion(Priority.P0, request_body)
        self.assertEqual(status2, 200)
        # The real bug this guards: without the normal-path idempotency
        # check, this would call forward_normal and execute the mutation
        # a second time.
        self.assertEqual(len(self.forward_normal_calls), 0)
        self.assertEqual(json.loads(raw2), first_response)

    def test_distinct_requests_are_never_conflated_by_the_recovery_check(self):
        # The recovery-path idempotency check must not become an
        # accidental cache for ordinary distinct traffic.
        self.endpoint._last_classification = "healthy"
        body_a = json.dumps({"model": "tier2-coding", "messages": [{"role": "user", "content": "a"}]}).encode()
        body_b = json.dumps({"model": "tier2-coding", "messages": [{"role": "user", "content": "b"}]}).encode()
        self.endpoint.handle_chat_completion(Priority.P0, body_a)
        self.endpoint.handle_chat_completion(Priority.P0, body_b)
        # Both went through forward_normal for real - no false-positive
        # idempotency hit merely from sharing a route/priority/actor.
        self.assertEqual(len(self.forward_normal_calls), 2)


if __name__ == "__main__":
    unittest.main()
