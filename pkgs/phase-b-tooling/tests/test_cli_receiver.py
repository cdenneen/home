from __future__ import annotations

import hashlib
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from common import ATTEMPT, START, FakeBackend, FakeF0EvidenceSource, Fixture, signed
from phase_b import strict_json
from phase_b.collect_cli import _collect_with
from phase_b.collector import STREAMS, AcceleratedClock
from phase_b.execute_cli import (
    BoundF0EvidenceSource,
    CaptureRequest,
    _execute_with,
    _validate_operation_grant_interval,
)
from phase_b.executor import ExecutionError, Executor
from phase_b.journal import Journal
from phase_b.receiver import BoundReceiverClient, DurableReceiverState
from phase_b.registry import FIXED_DELTAS, RegistrySet
from phase_b.trust import HMACSHA256Verifier, TrustError, _parse_anchor
from phase_b.verifier import VerificationError
from phase_b.verify_cli import _verify_with


def _fresh_cas_times(receiver: DurableReceiverState) -> tuple[str, str]:
    now = receiver.trusted_now()
    return (
        now.isoformat().replace("+00:00", "Z"),
        (now + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
    )


class ReceiverTests(unittest.TestCase):
    def test_durable_cas_is_one_time_and_bool_is_not_counter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secure_root = Path(temporary)
            root = secure_root / "receiver"
            receiver = DurableReceiverState(
                root, owner_uid=os.getuid(), secure_root=secure_root
            )
            head = receiver.current_head(ATTEMPT)
            arguments = (
                ATTEMPT,
                0,
                None,
                "consumer-nonce-001",
                "consumer-001",
                "PHASE_B_FENCING_QUALIFICATION",
                "sha256:" + "1" * 64,
                head,
                "sha256:" + "2" * 64,
                *_fresh_cas_times(receiver),
            )
            self.assertTrue(receiver.compare_and_set(*arguments))
            snapshot, live_head = receiver.consumption_snapshot(ATTEMPT)
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot["counter"], 1)
            self.assertEqual(snapshot["receiver_head"], head)
            self.assertEqual(live_head, head)
            self.assertFalse(receiver.compare_and_set(*arguments))
            self.assertFalse(
                receiver.compare_and_set(
                    ATTEMPT,
                    True,
                    None,
                    "consumer-nonce-002",
                    "consumer-001",
                    "PHASE_B_FENCING_QUALIFICATION",
                    "sha256:" + "1" * 64,
                    head,
                    "sha256:" + "2" * 64,
                    *_fresh_cas_times(receiver),
                )
            )

    def test_cas_publishes_complete_state_across_short_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secure_root = Path(temporary)
            receiver = DurableReceiverState(
                secure_root / "receiver",
                owner_uid=os.getuid(),
                secure_root=secure_root,
            )
            head = receiver.current_head(ATTEMPT)
            real_write = os.write

            def short_write(descriptor: int, data: Any) -> int:
                return real_write(descriptor, data[:7])

            with patch("phase_b.receiver.os.write", side_effect=short_write):
                self.assertTrue(
                    receiver.compare_and_set(
                        ATTEMPT,
                        0,
                        None,
                        "consumer-short-write",
                        "consumer-001",
                        "PHASE_B_FENCING_QUALIFICATION",
                        "sha256:" + "1" * 64,
                        head,
                        "sha256:" + "2" * 64,
                        *_fresh_cas_times(receiver),
                    )
                )
            snapshot, _live_head = receiver.consumption_snapshot(ATTEMPT)
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            self.assertEqual(snapshot["counter"], 1)
            self.assertEqual(snapshot["nonces"], ["consumer-short-write"])

    def test_cas_rechecks_freshness_and_grant_expiry_under_receiver_lock(self) -> None:
        for delay, grant_seconds in ((301, 900), (31, 30)):
            with (
                self.subTest(delay=delay, grant_seconds=grant_seconds),
                tempfile.TemporaryDirectory() as temporary,
            ):
                secure_root = Path(temporary)
                clock = AcceleratedClock(START)
                receiver = DurableReceiverState(
                    secure_root / "receiver",
                    owner_uid=os.getuid(),
                    secure_root=secure_root,
                    wall=clock.wall,
                    monotonic=clock.monotonic,
                )
                head = receiver.current_head(ATTEMPT)
                clock.advance(delay)
                self.assertFalse(
                    receiver.compare_and_set(
                        ATTEMPT,
                        0,
                        None,
                        "consumer-delayed",
                        "consumer-001",
                        "PHASE_B_FENCING_QUALIFICATION",
                        "sha256:" + "1" * 64,
                        head,
                        "sha256:" + "2" * 64,
                        START.isoformat().replace("+00:00", "Z"),
                        (START + timedelta(seconds=grant_seconds))
                        .isoformat()
                        .replace("+00:00", "Z"),
                    )
                )
                snapshot, _head = receiver.consumption_snapshot(ATTEMPT)
                self.assertIsNone(snapshot)

    def test_bound_receiver_counter_two_request_binds_atomic_times(self) -> None:
        client = object.__new__(BoundReceiverClient)
        requests: list[tuple[str, dict[str, Any]]] = []

        def request(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
            requests.append((operation, payload))
            return {"accepted": True}

        client._request = request  # type: ignore[method-assign]
        continued = START.isoformat().replace("+00:00", "Z")
        expires = (START + timedelta(minutes=15)).isoformat().replace(
            "+00:00", "Z"
        )
        self.assertTrue(
            client.compare_and_set(
                ATTEMPT,
                1,
                "sha256:" + "0" * 64,
                "consumer-nonce-002",
                "consumer-002",
                "PHASE_B_FENCING_QUALIFICATION",
                "sha256:" + "1" * 64,
                "sha256:" + "2" * 64,
                "sha256:" + "3" * 64,
                continued,
                expires,
            )
        )
        self.assertEqual(requests[0][0], "compare-and-set")
        self.assertEqual(requests[0][1]["expected_counter"], 1)
        self.assertEqual(requests[0][1]["continued_at"], continued)
        self.assertEqual(requests[0][1]["grant_expires_at"], expires)

    def test_append_serializes_with_cas_and_stale_head_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secure_root = Path(temporary)
            receiver = DurableReceiverState(
                secure_root / "receiver",
                owner_uid=os.getuid(),
                secure_root=secure_root,
            )
            stale_head = receiver.current_head(ATTEMPT)
            entered = threading.Event()
            release = threading.Event()
            original = receiver.chain.append_checkpoint_fast

            def delayed(*args, **kwargs):
                entered.set()
                self.assertTrue(release.wait(5))
                return original(*args, **kwargs)

            receiver.chain.append_checkpoint_fast = delayed  # type: ignore[method-assign]
            envelope = strict_json.canonical(
                signed(
                    {"schema": "phase-b.source-event.v1"},
                    "phase-b-source-event.audit",
                )
            )
            append = threading.Thread(
                target=lambda: receiver.append_source(0, envelope), daemon=True
            )
            append.start()
            self.assertTrue(entered.wait(5))
            result: list[bool] = []
            cas = threading.Thread(
                target=lambda: result.append(
                    receiver.compare_and_set(
                        ATTEMPT,
                        0,
                        None,
                        "consumer-nonce-race",
                        "consumer-001",
                        "PHASE_B_FENCING_QUALIFICATION",
                        "sha256:" + "1" * 64,
                        stale_head,
                        "sha256:" + "2" * 64,
                        *_fresh_cas_times(receiver),
                    )
                ),
                daemon=True,
            )
            cas.start()
            release.set()
            append.join(5)
            cas.join(5)
            self.assertEqual(result, [False])

    def test_separate_receiver_instance_reloads_durable_head_before_cas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secure_root = Path(temporary)
            root = secure_root / "receiver"
            writer = DurableReceiverState(
                root, owner_uid=os.getuid(), secure_root=secure_root
            )
            stale = DurableReceiverState(
                root, owner_uid=os.getuid(), secure_root=secure_root
            )
            stale_head = stale.current_head(ATTEMPT)
            envelope = strict_json.canonical(
                signed(
                    {"schema": "phase-b.source-event.v1"},
                    "phase-b-source-event.audit",
                )
            )
            writer.append_source(0, envelope)
            self.assertFalse(
                stale.compare_and_set(
                    ATTEMPT,
                    0,
                    None,
                    "consumer-nonce-cross-instance",
                    "consumer-001",
                    "PHASE_B_FENCING_QUALIFICATION",
                    "sha256:" + "1" * 64,
                    stale_head,
                    "sha256:" + "2" * 64,
                    *_fresh_cas_times(stale),
                )
            )

    def test_receiver_rejects_late_synthetic_checkpoint_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secure_root = Path(temporary)
            clock = AcceleratedClock(START + timedelta(seconds=126))
            receiver = DurableReceiverState(
                secure_root / "receiver",
                owner_uid=os.getuid(),
                secure_root=secure_root,
                wall=clock.wall,
                monotonic=clock.monotonic,
            )
            envelope = signed(
                {
                    "schema": "phase-b.source-event.v1",
                    "event_class": "continuity-checkpoints",
                    "observed_at": START.isoformat().replace("+00:00", "Z"),
                    "metadata": {
                        "checkpoints": [
                            {"received_at": START.isoformat().replace("+00:00", "Z")}
                        ]
                    },
                },
                "phase-b-source-event.audit",
            )
            with self.assertRaises(RuntimeError):
                receiver.append_source(0, strict_json.canonical(envelope))

    def test_partial_receiver_attempt_is_terminally_unconsumable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secure_root = Path(temporary)
            root = secure_root / "receiver"
            receiver = DurableReceiverState(
                root, owner_uid=os.getuid(), secure_root=secure_root
            )
            envelope = strict_json.canonical(
                signed(
                    {"schema": "phase-b.source-event.v1"},
                    "phase-b-source-event.audit",
                )
            )
            receiver.append_source(0, envelope)
            receiver.invalidate_attempt(ATTEMPT, "partial-continuation-append")
            self.assertTrue(receiver.attempt_invalid(ATTEMPT))
            self.assertTrue(
                any(
                    record["action_id"].startswith("terminal-invalidation-")
                    for record in receiver.export_records()
                )
            )
            self.assertFalse(
                receiver.compare_and_set(
                    ATTEMPT,
                    0,
                    None,
                    "consumer-nonce-invalid",
                    "consumer-001",
                    "PHASE_B_FENCING_QUALIFICATION",
                    "sha256:" + "1" * 64,
                    receiver.current_head(ATTEMPT),
                    "sha256:" + "2" * 64,
                    *_fresh_cas_times(receiver),
                )
            )
            reopened = DurableReceiverState(
                root, owner_uid=os.getuid(), secure_root=secure_root
            )
            self.assertTrue(reopened.attempt_invalid(ATTEMPT))

    def test_receiver_rejects_gap_replay_and_noncanonical_source_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secure_root = Path(temporary)
            receiver = DurableReceiverState(
                secure_root / "receiver",
                owner_uid=os.getuid(),
                secure_root=secure_root,
            )
            envelope = signed(
                {"schema": "phase-b.source-event.v1"}, "phase-b-source-event.audit"
            )
            canonical = strict_json.canonical(envelope)
            receiver.append_source(0, canonical)
            with self.assertRaises(RuntimeError):
                receiver.append_source(0, canonical)
            with self.assertRaises(strict_json.StrictJSONError):
                receiver.append_source(1, canonical + b"\n")


class CollectorProductionBoundaryTests(unittest.TestCase):
    def test_fixed_offhost_collector_completes_accelerated_fixture(self) -> None:
        from test_collector import CollectorTests, snapshot

        source = CollectorTests(
            methodName="test_complete_accelerated_authenticated_observation"
        )
        source.setUp()
        try:
            source.open_all()
            source.run_duration()
            source.close_all()
            root = source.fixture.root / "collector-runtime"
            root.mkdir(mode=0o700)
            root.chmod(0o700)
            inbox, artifacts, state = root / "inbox", root / "artifacts", root / "state"
            for path in (
                inbox,
                inbox / "events",
                artifacts,
                artifacts / "evidence",
                state,
            ):
                path.mkdir(parents=True, mode=0o700, exist_ok=True)
                path.chmod(0o700)
            plan = []
            file_index = 0
            for record in source.journal.read_all()[1:]:
                if record["action_id"].startswith("source:"):
                    value = record["payload"]["envelope"]
                    at = value["payload"]["observed_monotonic"]
                    kind = "source-event"
                elif record["action_id"].startswith("sample:"):
                    value = record["payload"]["snapshot"]
                    at = record["payload"]["elapsed_monotonic"]
                    kind = "sample"
                else:
                    continue
                name = f"event-{file_index:06d}.json"
                file_index += 1
                self._put(inbox / "events" / name, value)
                plan.append({"at_monotonic": at, "kind": kind, "file": name})
            start = {
                "schema": "phase-b.observation-start.v1",
                "attempt_id": ATTEMPT,
                "starting_cursors": [
                    item.as_dict() for item in source.collector.starting_cursors
                ],
                "identities": source.collector.identities,
                "plan": plan,
            }
            self._put(
                inbox / "observation-start.json", signed(start, "phase-b-observation")
            )
            f0 = {
                "schema": "phase-b.f0.v3",
                "capture_id": "a" * 64,
                "attempt_id": ATTEMPT,
                "f0_at": START.isoformat().replace("+00:00", "Z"),
                "evidence": {},
                "custody_reads": [],
                "registry_digests": source.collector.identities["registry_digests"],
                "journal_head": "sha256:" + "1" * 64,
            }
            self._put(inbox / "f0.json", signed(f0, "phase-b-f0"))
            for artifact_id, data in source.fixture.artifacts.items():
                path = artifacts / "evidence" / f"{artifact_id}.artifact"
                path.write_bytes(data)
                path.chmod(0o400)
            runtime_clock = AcceleratedClock(START)
            _collect_with(
                source.anchor,
                inbox_root=inbox,
                artifact_root=artifacts,
                state_root=state,
                owner_uid=os.getuid(),
                secure_root=source.fixture.root,
                signature_verifier=HMACSHA256Verifier(),
                clock=runtime_clock,
                verify_binding=lambda _binding: None,
                signer=lambda _binding, payload: signed(payload, "phase-b-observation"),
            )
            output = strict_json.loads_canonical(
                (artifacts / "observation.json").read_bytes()
            )
            self.assertEqual(output["payload"]["derived"]["history_complete"], True)
            self.assertEqual(output["payload"]["f0_at"], f0["f0_at"])
            self.assertEqual(output["payload"]["f0_digest"], strict_json.digest(f0))
            self.assertEqual(
                output["payload"]["f0_at"],
                output["payload"]["observation_started_at"],
            )
            continued_at = runtime_clock.wall().isoformat().replace("+00:00", "Z")
            source_events = []
            for cursor in output["payload"]["ending_cursors"]:
                stream = cursor["stream"]
                source_cursor = f"{stream}-continuation-0001"
                raw = {
                    "schema": "phase-b.raw-batch.v1",
                    "stream": stream,
                    "source_cursor": source_cursor,
                    "events": [source._state_event(stream)],
                }
                raw_bytes = strict_json.canonical(raw)
                artifact_id = f"continuation-{stream}"
                raw_path = artifacts / "evidence" / f"{artifact_id}.artifact"
                raw_path.write_bytes(raw_bytes)
                raw_path.chmod(0o400)
                source_events.append(
                    signed(
                        {
                            "schema": "phase-b.source-event.v1",
                            "attempt_id": ATTEMPT,
                            "stream": stream,
                            "generation": cursor["generation"],
                            "offset": cursor["offset"] + 1,
                            "previous_token": cursor["token"],
                            "event_class": "continuity-checkpoints",
                            "observed_at": continued_at,
                            "observed_monotonic": runtime_clock.monotonic(),
                            "metadata": {
                                "checkpoints": [
                                    {
                                        "start_monotonic": runtime_clock.monotonic(),
                                        "end_monotonic": runtime_clock.monotonic() + 1,
                                        "received_at": continued_at,
                                        "source_cursor": source_cursor,
                                        "batch": {
                                            "id": artifact_id,
                                            "digest": "sha256:"
                                            + hashlib.sha256(raw_bytes).hexdigest(),
                                            "media_type": "application/json",
                                            "owner_only": True,
                                        },
                                        "event_count": 1,
                                        "lost": 0,
                                        "backlog": 0,
                                        "replay": 0,
                                    }
                                ]
                            },
                        },
                        f"phase-b-source-event.{stream}",
                    )
                )

            def refresh_segment(
                counter: int,
                index: int,
                final: bool,
                observed_at: str,
                events: list[dict[str, object]],
                consumer: str,
            ) -> dict[str, object]:
                return {
                    "schema": "phase-b.receiver-refresh-segment.v1",
                    "attempt_id": ATTEMPT,
                    "refresh_id": f"refresh-{counter:03d}",
                    "refresh_counter": counter,
                    "segment_index": index,
                    "final": final,
                    "observed_at": observed_at,
                    "source_events": events,
                    "sample": snapshot(source.fixture) if final else None,
                    "consumer_identity": consumer,
                    "consumer_nonce": f"{consumer}-nonce",
                    "requested_transition": "PHASE_B_FENCING_QUALIFICATION",
                    "authorization_grant_digest": "sha256:" + "1" * 64,
                    "invalidating_event_count": 0,
                }

            continuation_event = refresh_segment(
                1, 1, True, continued_at, source_events, "consumer-001"
            )
            self._put(inbox / "continuation-event.json", continuation_event)
            _collect_with(
                source.anchor,
                inbox_root=inbox,
                artifact_root=artifacts,
                state_root=state,
                owner_uid=os.getuid(),
                secure_root=source.fixture.root,
                signature_verifier=HMACSHA256Verifier(),
                clock=runtime_clock,
                verify_binding=lambda _binding: None,
                signer=lambda _binding, payload: signed(payload, "phase-b-observation"),
            )
            manifest = strict_json.loads_canonical(
                (artifacts / "bundle-manifest-00000001.json").read_bytes()
            )
            self.assertEqual(
                manifest["payload"]["schema"],
                "phase-b.collector-import-manifest.v4",
            )
            continuation = strict_json.loads_canonical(
                (artifacts / "continuation-00000001.json").read_bytes()
            )
            self.assertEqual(
                len(continuation["payload"]["source_records"]), len(STREAMS)
            )
            self.assertEqual(
                continuation["payload"]["source_records"][0]["payload"]["envelope"],
                source_events[0],
            )

            prior_head = continuation["payload"]["current_head"]
            runtime_clock.advance(1)
            refreshed_at = runtime_clock.wall().isoformat().replace("+00:00", "Z")
            second_sources = []
            for prior in source_events:
                payload = deepcopy(prior["payload"])
                checkpoint = deepcopy(payload["metadata"]["checkpoints"][0])
                checkpoint["start_monotonic"] = checkpoint["end_monotonic"]
                checkpoint["end_monotonic"] += 1
                checkpoint["received_at"] = refreshed_at
                payload.update(
                    {
                        "offset": payload["offset"] + 1,
                        "previous_token": strict_json.digest(prior),
                        "observed_at": refreshed_at,
                        "observed_monotonic": checkpoint["end_monotonic"],
                        "metadata": {"checkpoints": [checkpoint]},
                    }
                )
                second_sources.append(
                    signed(payload, f"phase-b-source-event.{payload['stream']}")
                )
            second_event = refresh_segment(
                2, 1, True, refreshed_at, second_sources, "consumer-002"
            )
            self._put(inbox / "continuation-event.json", second_event)
            receiver_state = DurableReceiverState(
                state / ATTEMPT / "receiver",
                owner_uid=os.getuid(),
                secure_root=source.fixture.root,
                wall=runtime_clock.wall,
                monotonic=runtime_clock.monotonic,
            )
            head_before_unconsumed_refresh = receiver_state.current_head(ATTEMPT)
            with self.assertRaisesRegex(TrustError, "not durably consumed"):
                _collect_with(
                    source.anchor,
                    inbox_root=inbox,
                    artifact_root=artifacts,
                    state_root=state,
                    owner_uid=os.getuid(),
                    secure_root=source.fixture.root,
                    signature_verifier=HMACSHA256Verifier(),
                    clock=runtime_clock,
                    verify_binding=lambda _binding: None,
                    signer=lambda _binding, payload: signed(
                        payload, "phase-b-observation"
                    ),
                )
            self.assertEqual(
                receiver_state.current_head(ATTEMPT), head_before_unconsumed_refresh
            )
            first_receipt_digest = "sha256:" + "a" * 64
            self.assertTrue(
                receiver_state.compare_and_set(
                    ATTEMPT,
                    0,
                    None,
                    "consumer-001-nonce",
                    "consumer-001",
                    "PHASE_B_FENCING_QUALIFICATION",
                    "sha256:" + "1" * 64,
                    head_before_unconsumed_refresh,
                    first_receipt_digest,
                    *_fresh_cas_times(receiver_state),
                )
            )
            _collect_with(
                source.anchor,
                inbox_root=inbox,
                artifact_root=artifacts,
                state_root=state,
                owner_uid=os.getuid(),
                secure_root=source.fixture.root,
                signature_verifier=HMACSHA256Verifier(),
                clock=runtime_clock,
                verify_binding=lambda _binding: None,
                signer=lambda _binding, payload: signed(payload, "phase-b-observation"),
            )
            second = strict_json.loads_canonical(
                (artifacts / "continuation-00000002.json").read_bytes()
            )
            second_manifest = strict_json.loads_canonical(
                (artifacts / "bundle-manifest-00000002.json").read_bytes()
            )
            self.assertEqual(second["payload"]["observation_head"], prior_head)
            self.assertEqual(second_manifest["payload"]["consumption_counter"], 2)
            second_receipt_digest = "sha256:" + "b" * 64
            self.assertTrue(
                receiver_state.compare_and_set(
                    ATTEMPT,
                    1,
                    first_receipt_digest,
                    "consumer-002-nonce",
                    "consumer-002",
                    "PHASE_B_FENCING_QUALIFICATION",
                    "sha256:" + "1" * 64,
                    second["payload"]["current_head"],
                    second_receipt_digest,
                    *_fresh_cas_times(receiver_state),
                )
            )

            # A four-minute refresh arrives through two separate current
            # invocations. The first segment cannot publish a continuation.
            round_sources = second_sources
            runtime_clock.advance(120)
            first_round_at = runtime_clock.wall().isoformat().replace("+00:00", "Z")
            first_round_sources = []
            for prior in round_sources:
                payload = deepcopy(prior["payload"])
                checkpoint = deepcopy(payload["metadata"]["checkpoints"][-1])
                checkpoint["start_monotonic"] = checkpoint["end_monotonic"]
                checkpoint["end_monotonic"] += 120
                checkpoint["received_at"] = first_round_at
                payload.update(
                    {
                        "offset": payload["offset"] + 1,
                        "previous_token": strict_json.digest(prior),
                        "observed_at": first_round_at,
                        "observed_monotonic": checkpoint["end_monotonic"],
                        "metadata": {"checkpoints": [checkpoint]},
                    }
                )
                first_round_sources.append(
                    signed(payload, f"phase-b-source-event.{payload['stream']}")
                )
            first_round = refresh_segment(
                3, 1, False, first_round_at, first_round_sources, "consumer-003"
            )
            self._put(inbox / "continuation-event.json", first_round)
            _collect_with(
                source.anchor,
                inbox_root=inbox,
                artifact_root=artifacts,
                state_root=state,
                owner_uid=os.getuid(),
                secure_root=source.fixture.root,
                signature_verifier=HMACSHA256Verifier(),
                clock=runtime_clock,
                verify_binding=lambda _binding: None,
                signer=lambda _binding, payload: signed(payload, "phase-b-observation"),
            )
            self.assertFalse((artifacts / "continuation-00000003.json").exists())

            runtime_clock.advance(120)
            second_round_at = runtime_clock.wall().isoformat().replace("+00:00", "Z")
            second_round_sources = []
            for prior in first_round_sources:
                payload = deepcopy(prior["payload"])
                checkpoint = deepcopy(payload["metadata"]["checkpoints"][-1])
                checkpoint["start_monotonic"] = checkpoint["end_monotonic"]
                checkpoint["end_monotonic"] += 120
                checkpoint["received_at"] = second_round_at
                payload.update(
                    {
                        "offset": payload["offset"] + 1,
                        "previous_token": strict_json.digest(prior),
                        "observed_at": second_round_at,
                        "observed_monotonic": checkpoint["end_monotonic"],
                        "metadata": {"checkpoints": [checkpoint]},
                    }
                )
                second_round_sources.append(
                    signed(payload, f"phase-b-source-event.{payload['stream']}")
                )
            second_round = refresh_segment(
                3, 2, True, second_round_at, second_round_sources, "consumer-003"
            )
            self._put(inbox / "continuation-event.json", second_round)
            _collect_with(
                source.anchor,
                inbox_root=inbox,
                artifact_root=artifacts,
                state_root=state,
                owner_uid=os.getuid(),
                secure_root=source.fixture.root,
                signature_verifier=HMACSHA256Verifier(),
                clock=runtime_clock,
                verify_binding=lambda _binding: None,
                signer=lambda _binding, payload: signed(payload, "phase-b-observation"),
            )
            third = strict_json.loads_canonical(
                (artifacts / "continuation-00000003.json").read_bytes()
            )
            self.assertEqual(len(third["payload"]["source_records"]), 2 * len(STREAMS))
            self.assertTrue(
                receiver_state.compare_and_set(
                    ATTEMPT,
                    2,
                    second_receipt_digest,
                    "consumer-003-nonce",
                    "consumer-003",
                    "PHASE_B_FENCING_QUALIFICATION",
                    "sha256:" + "1" * 64,
                    third["payload"]["current_head"],
                    "sha256:" + "c" * 64,
                    *_fresh_cas_times(receiver_state),
                )
            )

            # Preloading a future segment is rejected; the collector never waits
            # or advances an injected clock to make it current.
            buffered_at = runtime_clock.wall() + timedelta(seconds=120)
            buffered_text = buffered_at.isoformat().replace("+00:00", "Z")
            buffered_sources = []
            for prior in second_round_sources:
                payload = deepcopy(prior["payload"])
                checkpoint = deepcopy(payload["metadata"]["checkpoints"][-1])
                checkpoint["start_monotonic"] = checkpoint["end_monotonic"]
                checkpoint["end_monotonic"] += 120
                checkpoint["received_at"] = buffered_text
                payload.update(
                    {
                        "offset": payload["offset"] + 1,
                        "previous_token": strict_json.digest(prior),
                        "observed_at": buffered_text,
                        "observed_monotonic": checkpoint["end_monotonic"],
                        "metadata": {"checkpoints": [checkpoint]},
                    }
                )
                buffered_sources.append(
                    signed(payload, f"phase-b-source-event.{payload['stream']}")
                )
            self._put(
                inbox / "continuation-event.json",
                refresh_segment(
                    4, 1, True, buffered_text, buffered_sources, "consumer-004"
                ),
            )
            before = runtime_clock.wall()
            with self.assertRaisesRegex(TrustError, "attempt invalidated"):
                _collect_with(
                    source.anchor,
                    inbox_root=inbox,
                    artifact_root=artifacts,
                    state_root=state,
                    owner_uid=os.getuid(),
                    secure_root=source.fixture.root,
                    signature_verifier=HMACSHA256Verifier(),
                    clock=runtime_clock,
                    verify_binding=lambda _binding: None,
                    signer=lambda _binding, payload: signed(
                        payload, "phase-b-observation"
                    ),
                )
            self.assertEqual(runtime_clock.wall(), before)
            runtime_clock.advance(120)
            with self.assertRaisesRegex(TrustError, "terminally invalidated"):
                _collect_with(
                    source.anchor,
                    inbox_root=inbox,
                    artifact_root=artifacts,
                    state_root=state,
                    owner_uid=os.getuid(),
                    secure_root=source.fixture.root,
                    signature_verifier=HMACSHA256Verifier(),
                    clock=runtime_clock,
                    verify_binding=lambda _binding: None,
                    signer=lambda _binding, payload: signed(
                        payload, "phase-b-observation"
                    ),
                )
        finally:
            source.doCleanups()

    def test_refresh_fault_recovery_uses_durable_intent_not_mutable_inbox(self) -> None:
        from test_collector import identities, snapshot

        stages = ["after-refresh-segment-artifact", "after-refresh-intent"]
        stages.extend(
            stage
            for ordinal in range(len(STREAMS))
            for stage in (
                f"before-receiver-append-{ordinal}",
                f"after-receiver-append-{ordinal}",
            )
        )
        stages.extend(
            (
                "before-receiver-extension",
                "after-receiver-extension",
                "before-continuation-publication",
                "after-continuation-publication",
                "before-manifest-publication",
                "after-manifest-publication",
            )
        )
        terminal_stages = {
            *(
                f"after-receiver-append-{ordinal}"
                for ordinal in range(len(STREAMS) - 1)
            ),
            *(
                f"before-receiver-append-{ordinal}"
                for ordinal in range(1, len(STREAMS))
            ),
        }

        raw_events = {
            "audit": {
                "type": "process-snapshot",
                "data": {
                    "writers": 0,
                    "reprovisioners": 0,
                    "effect_capable_descendants": 0,
                },
            },
            "user-journal": {
                "type": "journal-cursor",
                "data": {"cursor": "journal-head"},
            },
            "systemd": {
                "type": "systemd-cursor",
                "data": {"cursor": "systemd-head"},
            },
            "registry": {
                "type": "registry-snapshot",
                "data": {"registry_digests": None},
            },
            "database": {
                "type": "database-snapshot",
                "data": {"pending": 0, "inflight": 0, "local_only": 0},
            },
            "provider-route": {
                "type": "route-snapshot",
                "data": {
                    "generic_route_identity": "generic-route-001",
                    "alpha0_route_identity": "alpha0-route-001",
                    "dedicated_axis_route": "ABSENT",
                },
            },
            "custody": {
                "type": "custody-snapshot",
                "data": {
                    "remote": 9,
                    "total": 9,
                    "pending": 0,
                    "inflight": 0,
                    "local_only": 0,
                    "frontier_digest": "sha256:" + "e" * 64,
                },
            },
            "identity": {"type": "identity-snapshot", "data": None},
            "time": {
                "type": "time-anchor",
                "data": {
                    "wall_at": (START + timedelta(seconds=1))
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "monotonic": 1,
                },
            },
        }

        for stage_number, stage in enumerate(stages):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temporary:
                fixture = Fixture()
                try:
                    root = Path(temporary)
                    inbox = root / "inbox"
                    artifacts = root / "artifacts"
                    state = root / "state"
                    for path in (inbox, artifacts, artifacts / "evidence", state):
                        path.mkdir(parents=True, mode=0o700, exist_ok=True)
                        path.chmod(0o700)
                    clock = AcceleratedClock(START)
                    receiver = DurableReceiverState(
                        state / ATTEMPT / "receiver",
                        owner_uid=os.getuid(),
                        secure_root=root,
                        wall=clock.wall,
                        monotonic=clock.monotonic,
                    )
                    identity = identities(fixture)
                    raw_events["registry"]["data"]["registry_digests"] = identity[
                        "registry_digests"
                    ]
                    raw_events["identity"]["data"] = identity
                    ending = []
                    for sequence, stream in enumerate(STREAMS):
                        envelope = signed(
                            {
                                "schema": "phase-b.source-event.v1",
                                "attempt_id": ATTEMPT,
                                "stream": stream,
                                "generation": 0,
                                "offset": 1,
                                "previous_token": f"start-{stream}",
                                "event_class": "heartbeat",
                                "observed_at": START.isoformat().replace("+00:00", "Z"),
                                "observed_monotonic": 0,
                                "metadata": {
                                    "source_state_digest": "sha256:" + "a" * 64
                                },
                            },
                            f"phase-b-source-event.{stream}",
                        )
                        receiver.append_source(
                            sequence, strict_json.canonical(envelope)
                        )
                        ending.append(
                            {
                                "stream": stream,
                                "generation": 0,
                                "offset": 1,
                                "token": strict_json.digest(envelope),
                            }
                        )
                    base_records = receiver.export_records()
                    observation = signed(
                        {
                            "attempt_id": ATTEMPT,
                            "observed_through_at": START.isoformat().replace(
                                "+00:00", "Z"
                            ),
                            "ending_cursors": ending,
                            "coverage": {
                                stream: {"end_monotonic": 0} for stream in STREAMS
                            },
                            "identities": identity,
                            "chain_head": "sha256:" + "b" * 64,
                            "receiver_custody": {
                                "head": base_records[-1]["record_hash"],
                                "sequence": len(base_records),
                            },
                        },
                        "phase-b-observation",
                    )
                    self._put(artifacts / "observation.json", observation)
                    source_events = []
                    for cursor in ending:
                        stream = cursor["stream"]
                        source_cursor = f"{stream}-refresh"
                        raw = {
                            "schema": "phase-b.raw-batch.v1",
                            "stream": stream,
                            "source_cursor": source_cursor,
                            "events": [raw_events[stream]],
                        }
                        raw_bytes = strict_json.canonical(raw)
                        artifact_id = f"raw-{stream}"
                        raw_path = artifacts / "evidence" / f"{artifact_id}.artifact"
                        raw_path.write_bytes(raw_bytes)
                        raw_path.chmod(0o400)
                        observed = (
                            (START + timedelta(seconds=1))
                            .isoformat()
                            .replace("+00:00", "Z")
                        )
                        source_events.append(
                            signed(
                                {
                                    "schema": "phase-b.source-event.v1",
                                    "attempt_id": ATTEMPT,
                                    "stream": stream,
                                    "generation": cursor["generation"],
                                    "offset": cursor["offset"] + 1,
                                    "previous_token": cursor["token"],
                                    "event_class": "continuity-checkpoints",
                                    "observed_at": observed,
                                    "observed_monotonic": 1,
                                    "metadata": {
                                        "checkpoints": [
                                            {
                                                "start_monotonic": 0,
                                                "end_monotonic": 1,
                                                "received_at": observed,
                                                "source_cursor": source_cursor,
                                                "batch": {
                                                    "id": artifact_id,
                                                    "digest": "sha256:"
                                                    + hashlib.sha256(
                                                        raw_bytes
                                                    ).hexdigest(),
                                                    "media_type": "application/json",
                                                    "owner_only": True,
                                                },
                                                "event_count": 1,
                                                "lost": 0,
                                                "backlog": 0,
                                                "replay": 0,
                                            }
                                        ]
                                    },
                                },
                                f"phase-b-source-event.{stream}",
                            )
                        )
                    event = {
                        "schema": "phase-b.receiver-refresh-segment.v1",
                        "attempt_id": ATTEMPT,
                        "refresh_id": "crash-refresh-001",
                        "refresh_counter": 1,
                        "segment_index": 1,
                        "final": True,
                        "observed_at": (START + timedelta(seconds=1))
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "source_events": source_events,
                        "sample": snapshot(fixture),
                        "consumer_identity": "consumer-crash",
                        "consumer_nonce": "nonce-crash",
                        "requested_transition": "PHASE_B_FENCING_QUALIFICATION",
                        "authorization_grant_digest": "sha256:" + "c" * 64,
                        "invalidating_event_count": 0,
                    }
                    self._put(inbox / "continuation-event.json", event)
                    clock.advance(1)

                    def fail_at(actual: str, expected: str = stage) -> None:
                        if actual == expected:
                            raise RuntimeError("injected refresh crash")

                    with self.assertRaisesRegex(RuntimeError, "injected refresh crash"):
                        _collect_with(
                            fixture.anchor(),
                            inbox_root=inbox,
                            artifact_root=artifacts,
                            state_root=state,
                            owner_uid=os.getuid(),
                            secure_root=root,
                            signature_verifier=HMACSHA256Verifier(),
                            clock=clock,
                            verify_binding=lambda _binding: None,
                            signer=lambda _binding, payload: signed(
                                payload, "phase-b-observation"
                            ),
                            fault=fail_at,
                        )
                    segment_artifact = (
                        artifacts
                        / "evidence"
                        / "refresh-segment-00000001-00000001.artifact"
                    )
                    if segment_artifact.exists():
                        self.assertEqual(
                            stat.S_IMODE(segment_artifact.stat().st_mode), 0o400
                        )
                    if stage_number % 2:
                        (inbox / "continuation-event.json").unlink()
                    else:
                        self._put(inbox / "continuation-event.json", {"changed": True})
                    if stage == "after-refresh-intent":
                        clock.advance(receiver.MAX_RECEPTION_SKEW_SECONDS + 1)
                        with self.assertRaisesRegex(
                            TrustError, "stale; attempt invalidated"
                        ):
                            _collect_with(
                                fixture.anchor(),
                                inbox_root=inbox,
                                artifact_root=artifacts,
                                state_root=state,
                                owner_uid=os.getuid(),
                                secure_root=root,
                                signature_verifier=HMACSHA256Verifier(),
                                clock=clock,
                                verify_binding=lambda _binding: None,
                                signer=lambda _binding, payload: signed(
                                    payload, "phase-b-observation"
                                ),
                            )
                        self.assertTrue(receiver.attempt_invalid(ATTEMPT))
                    elif stage in terminal_stages:
                        with self.assertRaisesRegex(TrustError, "partial"):
                            _collect_with(
                                fixture.anchor(),
                                inbox_root=inbox,
                                artifact_root=artifacts,
                                state_root=state,
                                owner_uid=os.getuid(),
                                secure_root=root,
                                signature_verifier=HMACSHA256Verifier(),
                                clock=clock,
                                verify_binding=lambda _binding: None,
                                signer=lambda _binding, payload: signed(
                                    payload, "phase-b-observation"
                                ),
                            )
                        self.assertTrue(receiver.attempt_invalid(ATTEMPT))
                    else:
                        _collect_with(
                            fixture.anchor(),
                            inbox_root=inbox,
                            artifact_root=artifacts,
                            state_root=state,
                            owner_uid=os.getuid(),
                            secure_root=root,
                            signature_verifier=HMACSHA256Verifier(),
                            clock=clock,
                            verify_binding=lambda _binding: None,
                            signer=lambda _binding, payload: signed(
                                payload, "phase-b-observation"
                            ),
                        )
                        self.assertTrue(
                            (artifacts / "continuation-00000001.json").exists()
                        )
                        self.assertTrue(
                            (artifacts / "bundle-manifest-00000001.json").exists()
                        )
                finally:
                    fixture.close()

    @staticmethod
    def _put(path: Path, value: object) -> None:
        path.write_bytes(strict_json.canonical(value))
        path.chmod(0o600)


class VerifierProductionBoundaryTests(unittest.TestCase):
    def test_fixed_verifier_consumes_once_and_emits_only_public_allowlist(self) -> None:
        from test_verifier import MemoryConsumption, VerifierTests

        VerifierTests.setUpClass()
        try:
            case = VerifierTests
            root = case.fixture.root / "verifier-runtime"
            root.mkdir(mode=0o700)
            artifacts, journals = root / "artifacts", root / "journals"
            for path in (artifacts, artifacts / "evidence", journals):
                path.mkdir(mode=0o700)
                path.chmod(0o700)
            fixed = {
                "baseline.json": case.bundle.baseline,
                "f0.json": case.bundle.f0,
                "observation.json": case.bundle.observation,
                "reconstruction-1.json": case.bundle.reconstructions[0],
                "reconstruction-2.json": case.bundle.reconstructions[1],
                "receipt.json": case.bundle.receipt,
                "previous-receipts.json": [],
                "continuation-00000001.json": case.bundle.continuation_proof,
            }
            for name, value in fixed.items():
                self._put(artifacts / name, value)
            evidence_manifest = []
            for artifact_id, data in case.fixture.artifacts.items():
                name = f"{artifact_id}.artifact"
                path = artifacts / "evidence" / name
                path.write_bytes(data)
                path.chmod(0o400)
                evidence_manifest.append(
                    {
                        "name": name,
                        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                    }
                )
            manifest = signed(
                {
                    "schema": "phase-b.collector-import-manifest.v4",
                    "consumption_counter": 1,
                    "attempt_id": ATTEMPT,
                    "observation_digest": strict_json.digest(case.bundle.observation),
                    "observation_journal_head": case.observation_journal.head(),
                    "receiver_head": case.observation["receiver_custody"]["head"],
                    "receiver_sequence": case.observation["receiver_custody"][
                        "sequence"
                    ],
                    "continuation_digest": strict_json.digest(
                        case.bundle.continuation_proof
                    ),
                    "current_receiver_head": case.receiver_head,
                    "current_receiver_sequence": case.continuation_payload[
                        "extensions"
                    ][-1]["sequence"]
                    + 1,
                    "terminal_cursors": case.continuation_payload["terminal_cursors"],
                    "terminal_continuity": case.continuation_payload[
                        "terminal_continuity"
                    ],
                    "terminal_source_walls": case.continuation_payload[
                        "terminal_source_walls"
                    ],
                    "evidence": sorted(
                        evidence_manifest, key=lambda item: item["name"]
                    ),
                },
                "phase-b-observation",
            )
            self._put(artifacts / "bundle-manifest-00000001.json", manifest)
            shutil.copytree(case.execution_journal.directory, journals / ATTEMPT)
            (journals / "observation").mkdir(mode=0o700)
            shutil.copytree(
                case.observation_journal.directory, journals / "observation" / ATTEMPT
            )
            authority = MemoryConsumption(case.now, case.receiver_head)
            verified_bindings: list[str] = []
            output = root / "public.json"
            fd = os.open(output, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            try:
                _verify_with(
                    case.anchor,
                    authority=authority,
                    artifact_root=artifacts,
                    journal_root=journals,
                    output_fd=fd,
                    owner_uid=os.getuid(),
                    secure_root=case.fixture.root,
                    signature_verifier=HMACSHA256Verifier(),
                    verify_binding=lambda binding: verified_bindings.append(binding.name),
                )
            finally:
                os.close(fd)
            self.assertIn("signature-verifier", verified_bindings)
            self.assertIn("verifier", verified_bindings)
            self.assertIn("receiver-client", verified_bindings)
            public = strict_json.loads_canonical(output.read_bytes().rstrip(b"\n"))
            self.assertEqual(
                set(public),
                {
                    "axis_remote_custody",
                    "canonical_alpha0_active",
                    "canonical_axis_control_active",
                    "canonical_axis_writer",
                    "canonical_composition_activated",
                    "canonical_deployment_attestation",
                    "cutover_ready",
                    "duplicate_scheduler_topology",
                    "external_route_identity",
                    "generic_route_reconstruction",
                    "hermes_semantic_restore",
                    "home_generation_changed",
                    "legacy_alpha0_authority",
                    "legacy_axis_new_work_writer",
                    "live_fencing_observation",
                    "phase_b_fencing_qualification",
                    "route_ownership",
                    "safe_drain_ready",
                    "source_fence_baseline_contract",
                },
            )
            self.assertEqual(public["phase_b_fencing_qualification"], "PROVEN")
            self.assertEqual(public["source_fence_baseline_contract"], "PROVEN")
            self.assertEqual(public["external_route_identity"], "PROVEN")
            self.assertEqual(public["route_ownership"], "PROVEN")
            self.assertEqual(public["duplicate_scheduler_topology"], "PROVEN")
            self.assertEqual(public["generic_route_reconstruction"], "PROVEN")
            self.assertEqual(public["hermes_semantic_restore"], "PROVEN")
            self.assertEqual(public["live_fencing_observation"], "PROVEN")
            self.assertEqual(public["axis_remote_custody"], "9/9")
            self.assertEqual(public["legacy_alpha0_authority"], "UNCHANGED_NOT_DRAINED")
            self.assertFalse(public["home_generation_changed"])
            self.assertEqual(public["canonical_composition_activated"], "NO")
            self.assertEqual(public["canonical_axis_control_active"], "NO")
            self.assertEqual(public["canonical_alpha0_active"], "NO")
            self.assertEqual(public["safe_drain_ready"], "YES")
            self.assertEqual(public["cutover_ready"], "NO")
            self.assertNotIn("attempt", public)
            self.assertNotIn("digest", public)
            self.assertNotIn("fixture-secret-canary", output.read_text())
        finally:
            VerifierTests.tearDownClass()

    @staticmethod
    def _put(path: Path, value: object) -> None:
        path.write_bytes(strict_json.canonical(value))
        path.chmod(0o600)


class ProductionBoundaryTests(unittest.TestCase):
    def test_operation_grant_times_are_canonical_current_and_ordered(self) -> None:
        valid = {
            "issued_at": START.isoformat().replace("+00:00", "Z"),
            "expires_at": (START + timedelta(minutes=1))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        self.assertEqual(
            _validate_operation_grant_interval(
                valid, START, existing_journal=False
            )[0],
            START,
        )
        future = {**valid, "issued_at": valid["expires_at"]}
        with self.assertRaisesRegex(TrustError, "validity interval"):
            _validate_operation_grant_interval(
                future, START, existing_journal=False
            )
        reversed_interval = {**valid, "expires_at": valid["issued_at"]}
        with self.assertRaisesRegex(TrustError, "validity interval"):
            _validate_operation_grant_interval(
                reversed_interval, START, existing_journal=False
            )
        noncanonical = {**valid, "issued_at": valid["issued_at"].replace("Z", "+00:00")}
        with self.assertRaisesRegex(TrustError, "canonical UTC"):
            _validate_operation_grant_interval(
                noncanonical, START, existing_journal=False
            )
        expired = {
            "issued_at": (START - timedelta(minutes=2))
            .isoformat()
            .replace("+00:00", "Z"),
            "expires_at": (START - timedelta(minutes=1))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        with self.assertRaisesRegex(TrustError, "expired"):
            _validate_operation_grant_interval(
                expired, START, existing_journal=False
            )
        _validate_operation_grant_interval(expired, START, existing_journal=True)

    def setUp(self) -> None:
        self.fixture = Fixture()
        self.anchor = self.fixture.anchor()

    def tearDown(self) -> None:
        self.fixture.close()

    @staticmethod
    def _mkdir(path: Path) -> None:
        path.mkdir(parents=True, mode=0o700)
        path.chmod(0o700)

    @staticmethod
    def _put(path: Path, value: object) -> None:
        path.write_bytes(strict_json.canonical(value))
        path.chmod(0o600)

    def test_capture_clients_use_only_fixed_request_mode_and_canonical_stdin(
        self,
    ) -> None:
        calls: list[tuple[list[str], dict[str, Any]]] = []

        def fake_run(arguments: list[str], payload: dict[str, Any]) -> dict[str, Any]:
            calls.append((arguments, payload))
            if arguments[-1] == "capture-f0":
                return {"audit-envelope": True}
            if payload.get("operation") == "capture-f0":
                return {
                    source: {"signed": source}
                    for source in (
                        "registry",
                        "database",
                        "provider-route",
                        "identity",
                        "time",
                    )
                }
            return {"custody-envelope": True}

        request = CaptureRequest(
            ATTEMPT,
            "sha256:" + "1" * 64,
            "c" * 64,
            "B3_GET",
            "sha256:" + "2" * 64,
        )
        source = BoundF0EvidenceSource(self.anchor)
        with patch.object(BoundF0EvidenceSource, "_run", side_effect=fake_run):
            source.capture_custody(request, "GET")
            source.capture_final(request)
        custody_arguments, custody_payload = calls[0]
        sensor_arguments, sensor_payload = calls[2]
        self.assertEqual(custody_arguments[1:], ["request"])
        self.assertEqual(sensor_arguments[1:], ["request"])
        self.assertEqual(custody_payload["operation"], "capture-custody")
        self.assertEqual(sensor_payload["operation"], "capture-f0")
        for payload in (custody_payload, sensor_payload):
            self.assertFalse({"path", "socket", "endpoint", "namespace"} & set(payload))

    def test_capture_client_rejects_noncanonical_and_oversized_stdout(self) -> None:
        with self.assertRaises(strict_json.StrictJSONError):
            BoundF0EvidenceSource._run(
                [sys.executable, "-c", "import sys;sys.stdout.write('{\\\"z\\\":1, \\\"a\\\":2}')"],
                {"request": True},
            )
        with self.assertRaisesRegex(TrustError, "exceeded its bound"):
            BoundF0EvidenceSource._run(
                [
                    sys.executable,
                    "-c",
                    "import sys;sys.stdout.write('x' * (4 * 1024 * 1024 + 1))",
                ],
                {"request": True},
            )

    def test_fixed_executor_cli_runtime_completes_fixture_without_path_inputs(
        self,
    ) -> None:
        root = self.fixture.root / "fixed-runtime"
        inputs, artifacts, journals, receipts = (
            root / "inputs",
            root / "artifacts",
            root / "journals",
            root / "receipts",
        )
        for path in (inputs, artifacts, journals, receipts, artifacts / "evidence"):
            self._mkdir(path)
        baseline = self.fixture.baseline(self.anchor)
        self._put(inputs / "baseline.json", signed(baseline, "phase-b-baseline"))
        grant = {
            "schema": "phase-b.operation-grant.v1",
            "action": "EXECUTE_PHASE_B_FENCING_QUALIFICATION",
            "attempt_id": ATTEMPT,
            "baseline_digest": strict_json.digest(baseline),
            "issued_at": START.isoformat().replace("+00:00", "Z"),
            "expires_at": (START + timedelta(minutes=15))
            .isoformat()
            .replace("+00:00", "Z"),
            "grant_nonce": "operation-grant-001",
        }
        self._put(
            inputs / "operation-grant.json", signed(grant, "phase-b-operation-grant")
        )
        custody_pages = [
            {
                "surface": "lineages",
                "page": 1,
                "last": True,
                "records": [
                    {
                        "id": f"lineage-{index}",
                        "custody": "REMOTE",
                        "consequential": True,
                    }
                    for index in range(9)
                ],
            },
            {
                "surface": "custody",
                "page": 1,
                "last": True,
                "records": [
                    {
                        "remote": 9,
                        "total": 9,
                        "pending": 0,
                        "inflight": 0,
                        "local_only": 0,
                    }
                ],
            },
            {"surface": "pending-effects", "page": 1, "last": True, "records": []},
            {
                "surface": "residue",
                "page": 1,
                "last": True,
                "records": [
                    {
                        "identity": "checkout-derived",
                        "classification": "STALE_DERIVED",
                    }
                ],
            },
        ]
        custody_refs = [
            self.fixture.put_artifact(
                {
                    "schema": "phase-b.custody-read.v1",
                    "method": method,
                    "pages": custody_pages,
                    "deletions": [],
                },
                prefix="custody",
            )
            for method in ("GET", "NO_OP")
        ]
        self._put(
            inputs / "custody-read-1.json",
            {"method": "GET", "artifact": custody_refs[0]},
        )
        self._put(
            inputs / "custody-read-2.json",
            {"method": "NO_OP", "artifact": custody_refs[1]},
        )
        post_documents = deepcopy(self.fixture.documents)
        for delta in FIXED_DELTAS:
            for job in post_documents[delta.registry_index]["jobs"]:
                if job["id"] == delta.job_id:
                    job.update(
                        {
                            "enabled": False,
                            "state": "paused",
                            "paused_at": "2026-08-20T00:00:00+00:00",
                            "paused_reason": None,
                        }
                    )
            post_documents[delta.registry_index]["updated_at"] = (
                "2026-08-20T00:00:00+00:00"
            )
        evidence = {
            "process": self.fixture.put_artifact(
                {
                    "schema": "phase-b.process-evidence.v1",
                    "processes": [
                        {"identity": "generic-gateway-process", "count": 1},
                        {"identity": "axis-writer-process", "count": 0},
                    ],
                    "effect_capable_descendants": [],
                },
                prefix="process",
            ),
            "listener": self.fixture.put_artifact(
                {
                    "schema": "phase-b.listener-evidence.v1",
                    "listeners": [
                        {"identity": "generic-gateway-listener", "count": 1},
                        {"identity": "dedicated-axis-listener", "count": 0},
                    ],
                },
                prefix="listener",
            ),
            "route": self.fixture.put_artifact(
                {
                    "schema": "phase-b.route-evidence.v1",
                    "authority_identities": self.fixture.authority(),
                    "alpha0_authority": "UNCHANGED_NOT_DRAINED",
                },
                prefix="route",
            ),
            "identity": self.fixture.put_artifact(
                {
                    "schema": "phase-b.identity-evidence.v1",
                    "source_identity": {
                        "host_identity": "host-001",
                        "machine_id": "machine-001",
                        "boot_id": "boot-001",
                        "user_manager_id": "manager-001",
                        "home_generation": "home-001",
                        "booted_closure": "sha256:" + "c" * 64,
                    },
                    "preserved_start_identities": {
                        item.name: item.start_identity
                        for item in self.fixture.preserved()
                    },
                    "stuck_watchdog_healthy": True,
                },
                prefix="identity",
            ),
            "registry": self.fixture.put_artifact(
                {
                    "schema": "phase-b.registry-evidence.v1",
                    "registries": [
                        {
                            "index": index,
                            "path": str(path),
                            "digest": strict_json.digest(post_documents[index]),
                        }
                        for index, path in enumerate(self.fixture.paths)
                    ],
                },
                prefix="registry",
            ),
        }
        self._put(inputs / "f0-evidence.json", evidence)
        for artifact_id, data in self.fixture.artifacts.items():
            path = artifacts / "evidence" / f"{artifact_id}.artifact"
            path.write_bytes(data)
            path.chmod(0o400)
        backend = FakeBackend(self.fixture)
        live_source = FakeF0EvidenceSource(self.fixture, baseline)
        # Production captures are sequential; their independently observed times
        # may differ while their signed stable windows still overlap.
        live_source.advance_before_final = 0.25
        verified_bindings = []
        arguments = {
            "input_root": inputs,
            "artifact_root": artifacts,
            "journal_root": journals,
            "receipt_root": receipts,
            "signature_verifier": HMACSHA256Verifier(),
            "backend_factory": lambda _registry, _inspector: backend,
            "inspector": backend,
            "monotonic": self.fixture.clock.monotonic,
            "sleeper": self.fixture.clock.advance,
            "now": self.fixture.clock,
            "verify_binding": verified_bindings.append,
            "evidence_source": live_source,
            "capture_id_factory": lambda: "a" * 64,
            "owner_uid": os.getuid(),
            "secure_root": self.fixture.root,
        }

        def alter_signed_f0(_binding, namespace, payload):
            altered = dict(payload)
            altered["journal_head"] = "sha256:" + "f" * 64
            return signed(altered, namespace)

        with self.assertRaisesRegex(TrustError, "durable candidate"):
            _execute_with(self.anchor, **arguments, signer=alter_signed_f0)
        self.assertIn(self.anchor.executables["artifact-reader"], verified_bindings)
        records = Journal(journals / ATTEMPT, owner_uid=os.getuid()).read_all()
        capture_call_count = len(live_source.calls)
        self.assertEqual(capture_call_count, 8)
        terminal = records[-1]
        self.assertEqual(terminal["action_id"], "f0-established")
        candidate = terminal["payload"]["artifact"]
        self.assertEqual(
            terminal["payload"]["artifact_digest"],
            strict_json.digest(candidate),
        )
        captured = {
            source: strict_json.loads(
                (
                    artifacts
                    / "evidence"
                    / f"{candidate['evidence'][source]['id']}.artifact"
                ).read_bytes()
            )["payload"]
            for source in ("custody", "time")
        }
        self.assertEqual(candidate["f0_at"], captured["custody"]["observed_at"])
        self.assertNotEqual(candidate["f0_at"], captured["time"]["observed_at"])
        self.assertFalse((receipts / "f0.json").exists())

        def rejected_recovery(
            name: str,
            mutate,
            *,
            rebind_candidate: bool = True,
        ) -> None:
            changed = [deepcopy(record) for record in records]
            mutate(changed)
            variant_journals = root / f"journals-{name}"
            variant_receipts = root / f"receipts-{name}"
            attempt_journal = variant_journals / ATTEMPT
            for path in (variant_journals, variant_receipts, attempt_journal):
                self._mkdir(path)
            previous = "sha256:" + "0" * 64
            for sequence, record in enumerate(changed):
                record["sequence"] = sequence
                record["previous_hash"] = previous
                if record["action_id"] == "f0-established":
                    candidate = record["payload"]["artifact"]
                    if rebind_candidate:
                        candidate["journal_head"] = previous
                    record["payload"]["artifact_digest"] = strict_json.digest(
                        candidate
                    )
                unsigned = dict(record)
                unsigned.pop("record_hash", None)
                record["record_hash"] = strict_json.digest(unsigned)
                previous = record["record_hash"]
                self._put(
                    attempt_journal / f"{sequence:016d}.json",
                    record,
                )
            variant_arguments: Any = arguments | {
                "journal_root": variant_journals,
                "receipt_root": variant_receipts,
            }
            with self.assertRaises(VerificationError, msg=name):
                _execute_with(
                    self.anchor,
                    **variant_arguments,
                    signer=lambda _binding, namespace, payload: signed(
                        payload, namespace
                    ),
                )
            self.assertFalse((variant_receipts / "f0.json").exists())

        def bogus_head(changed):
            changed[-1]["payload"]["artifact"]["journal_head"] = (
                "sha256:" + "f" * 64
            )

        def bogus_method(changed):
            changed[-3]["payload"]["method"] = "NO_OP"

        def bogus_timing(changed):
            changed[-2]["payload"]["observed_monotonic"] = (
                changed[-3]["payload"]["observed_monotonic"] + 1
            )

        def trailing_record(changed):
            changed.append(
                {
                    "schema": "phase-b.journal-record.v1",
                    "sequence": 0,
                    "previous_hash": "",
                    "kind": "checkpoint",
                    "action_id": "post-f0-extra",
                    "payload": {},
                    "recorded_at": changed[-1]["recorded_at"],
                    "record_hash": "",
                }
            )

        rejected_recovery("bogus-head", bogus_head, rebind_candidate=False)
        rejected_recovery("bogus-method", bogus_method)
        rejected_recovery("bogus-timing", bogus_timing)
        rejected_recovery("trailing-record", trailing_record)

        # Recovery must bind the exact live registry bytes, not merely observe
        # that each target still has paused semantics.
        registry_path = self.fixture.paths[0]
        current_registry = registry_path.read_bytes()
        drifted_registry = strict_json.loads(current_registry)
        target = next(
            job
            for job in drifted_registry["jobs"]
            if job["id"] == FIXED_DELTAS[0].job_id
        )
        self.assertEqual(target["state"], "paused")
        target["paused_at"] = "2026-08-20T00:00:01+00:00"
        registry_path.write_bytes(strict_json.canonical(drifted_registry) + b"\n")
        try:
            with self.assertRaisesRegex(TrustError, "state drifted before publication"):
                _execute_with(
                    self.anchor,
                    **arguments,
                    signer=lambda _binding, namespace, payload: signed(
                        payload, namespace
                    ),
                )
            self.assertFalse((receipts / "f0.json").exists())
        finally:
            registry_path.write_bytes(current_registry)

        # A bound signer is still untrusted to preserve the live state while it
        # runs; drift after signing must prevent publication.
        preserved = self.fixture.preserved()[0]

        def signer_that_drifts_live_state(_binding, namespace, payload):
            backend.preserved[preserved.name] = type(preserved)(
                preserved.name,
                preserved.healthy,
                "drifted-during-recovered-signing",
            )
            return signed(payload, namespace)

        with self.assertRaisesRegex(TrustError, "drifted before publication"):
            _execute_with(
                self.anchor,
                **arguments,
                signer=signer_that_drifts_live_state,
            )
        self.assertFalse((receipts / "f0.json").exists())
        backend.preserved[preserved.name] = preserved

        _execute_with(
            self.anchor,
            **arguments,
            signer=lambda _binding, namespace, payload: signed(payload, namespace),
        )
        output = strict_json.loads_canonical((receipts / "f0.json").read_bytes())
        self.assertEqual(output["namespace"], "phase-b-f0")
        self.assertEqual(output["payload"]["schema"], "phase-b.f0.v3")
        self.assertEqual(len(live_source.calls), capture_call_count)
        self.assertNotEqual(output["payload"]["evidence"], evidence)

    def test_expired_durable_rollback_grant_completes_exact_remaining_suffix(
        self,
    ) -> None:
        root = self.fixture.root / "rollback-runtime"
        inputs, artifacts, journals, receipts = (
            root / "inputs",
            root / "artifacts",
            root / "journals",
            root / "receipts",
        )
        for path in (inputs, artifacts, journals, receipts, artifacts / "evidence"):
            self._mkdir(path)
        baseline = self.fixture.baseline(self.anchor)
        self._put(inputs / "baseline.json", signed(baseline, "phase-b-baseline"))
        operation_grant = {
            "schema": "phase-b.operation-grant.v1",
            "action": "EXECUTE_PHASE_B_FENCING_QUALIFICATION",
            "attempt_id": ATTEMPT,
            "baseline_digest": strict_json.digest(baseline),
            "issued_at": START.isoformat().replace("+00:00", "Z"),
            "expires_at": (START + timedelta(minutes=15))
            .isoformat()
            .replace("+00:00", "Z"),
            "grant_nonce": "operation-grant-rollback",
        }
        self._put(
            inputs / "operation-grant.json",
            signed(operation_grant, "phase-b-operation-grant"),
        )
        for artifact_id, data in self.fixture.artifacts.items():
            path = artifacts / "evidence" / f"{artifact_id}.artifact"
            path.write_bytes(data)
            path.chmod(0o400)

        backend = FakeBackend(self.fixture)
        registry = RegistrySet(
            self.fixture.expectations(), tuple(str(path) for path in self.fixture.paths)
        )
        registry.acquire()
        journal = Journal(journals / ATTEMPT, clock=self.fixture.clock)
        executor = Executor(
            ATTEMPT,
            registry,
            journal,
            backend,
            self.fixture.units(),
            self.fixture.preserved(),
            monotonic=self.fixture.clock.monotonic,
        )
        executor.preflight()
        executor.run_b1()
        executor.run_b2()
        authorized = tuple(reversed(executor.completed_actions))
        registry.close()
        incident_grant = {
            "schema": "phase-b.incident-rollback-grant.v1",
            "action": "ROLLBACK_INVALID_PHASE_B_ATTEMPT",
            "attempt_id": ATTEMPT,
            "authorized_actions": list(authorized),
            "execution_journal_head": journal.head(),
            "expires_at": (START + timedelta(minutes=1))
            .isoformat()
            .replace("+00:00", "Z"),
            "reason_code": "TEST_RECOVERY",
        }
        self._put(
            inputs / "incident-rollback-grant.json",
            signed(incident_grant, "phase-b-incident-rollback-grant"),
        )
        backend.fail_effect = backend.effect_count + 2
        backend.fail_after_effect = False
        arguments = {
            "input_root": inputs,
            "artifact_root": artifacts,
            "journal_root": journals,
            "receipt_root": receipts,
            "signature_verifier": HMACSHA256Verifier(),
            "backend_factory": lambda _registry, _inspector: backend,
            "inspector": backend,
            "monotonic": self.fixture.clock.monotonic,
            "sleeper": self.fixture.clock.advance,
            "verify_binding": lambda _binding: None,
            "owner_uid": os.getuid(),
            "secure_root": self.fixture.root,
            "signer": lambda _binding, namespace, payload: signed(payload, namespace),
        }
        with self.assertRaises(ExecutionError):
            _execute_with(self.anchor, now=lambda: START, **arguments)
        records = journal.read_all()
        authorization = Executor.rollback_authorization(records)
        self.assertIsNotNone(authorization)
        self.assertEqual(len(journal.pending_intents()), 1)
        self.assertEqual(
            sum(
                record["payload"].get("status") == "restored"
                for record in records
                if record["action_id"].startswith("rollback:")
            ),
            1,
        )

        backend.fail_effect = None
        _execute_with(
            self.anchor,
            now=lambda: START + timedelta(minutes=2),
            **arguments,
        )
        self.assertEqual(journal.pending_intents(), {})
        self.assertFalse(any(backend.job_is_paused(delta) for delta in FIXED_DELTAS))
        durable = Executor.rollback_authorization(journal.read_all())
        self.assertIsNotNone(durable)
        assert durable is not None
        self.assertEqual(tuple(durable[0]["authorized_actions"]), authorized)

    def test_anchor_rejects_normalized_escape_and_production_hmac(self) -> None:
        anchor = self.anchor
        value = {
            "schema": "phase-b.trust.v2",
            "anchor_generation": anchor.anchor_generation,
            "signers": {
                role: {
                    "id": item.signer_id,
                    "algorithm": item.algorithm,
                    "public_key": item.public_key,
                }
                for role, item in anchor.signers.items()
            },
            "namespace_roles": anchor.namespace_roles,
            "executables": {
                name: {
                    "path": str(item.path),
                    "closure": str(item.closure),
                    "digest": item.digest,
                }
                for name, item in anchor.executables.items()
            },
            "source": {
                "uid": anchor.source.uid,
                "gid": anchor.source.gid,
                "user": anchor.source.user,
                "home": anchor.source.home,
                "machine_id": anchor.source.machine_id,
                "host_identity": anchor.source.host_identity,
                "boot_id": anchor.source.boot_id,
                "user_manager_id": anchor.source.user_manager_id,
                "home_generation": anchor.source.home_generation,
                "booted_closure": anchor.source.booted_closure,
                "user_manager_machine": anchor.source.user_manager_machine,
            },
            "registry_paths": list(anchor.registry_paths),
            "collector_identity": anchor.collector_identity,
            "authority_identities": anchor.authority_identities,
            "effect_plan_digest": anchor.effect_plan_digest,
            "rollback_plan_digest": anchor.rollback_plan_digest,
            "canonical_vectors_digest": anchor.canonical_vectors_digest,
            "process_inventory_digest": anchor.process_inventory_digest,
            "listener_inventory_digest": anchor.listener_inventory_digest,
            "runbook_digest": anchor.runbook_digest,
            "schema_digests": anchor.schema_digests,
        }
        changed = deepcopy(value)
        binding = changed["executables"]["executor"]
        binding["path"] = binding["closure"] + "/bin/../bin/executor"
        with self.assertRaises(TrustError):
            _parse_anchor(changed)


if __name__ == "__main__":
    unittest.main()
