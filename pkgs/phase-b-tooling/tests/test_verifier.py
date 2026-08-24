from __future__ import annotations

import hashlib
import os
import shutil
import unittest
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from common import (
    ATTEMPT,
    CANONICAL_SEMANTICS,
    FakeBackend,
    FakeF0EvidenceSource,
    Fixture,
    signed,
)
from phase_b import strict_json
from phase_b.artifacts import ArtifactError
from phase_b.collector import (
    OBSERVATION_SECONDS,
    STREAMS,
    AcceleratedClock,
    Collector,
    Cursor,
)
from phase_b.execute_cli import CaptureRequest, _validate_f0_candidate
from phase_b.executor import Executor
from phase_b.journal import Journal
from phase_b.receiver import DurableReceiverState
from phase_b.registry import FIXED_DELTAS, RegistrySet
from phase_b.trust import HMACSHA256Verifier, TrustError
from phase_b.verifier import (
    VerificationBundle,
    VerificationError,
    Verifier,
    _baseline,
    _verify_execution_journal,
    _verify_live_capture,
    _verify_observation,
    _verify_receiver_source_records,
)


class DictStore:
    def __init__(self, values: dict[str, bytes]):
        self.values = values

    def read(self, artifact_id: str) -> bytes:
        return self.values[artifact_id]


class MemoryConsumption:
    def __init__(self, now, head: str, *, counter: int = 0):
        self.now = now
        self.head = head
        self.counter = counter
        self.nonces: set[str] = set()
        self.previous: str | None = None

    def trusted_now(self):
        return self.now

    def current_head(self, attempt_id: str) -> str:
        del attempt_id
        return self.head

    def compare_and_set(
        self,
        attempt_id: str,
        expected_counter: int,
        previous_receipt_digest: str | None,
        consumer_nonce: str,
        consumer_identity: str,
        requested_transition: str,
        authorization_grant_digest: str,
        receiver_head: str,
        receipt_digest: str,
        continued_at: str,
        grant_expires_at: str,
    ) -> bool:
        del (
            attempt_id,
            consumer_identity,
            requested_transition,
            authorization_grant_digest,
        )
        continued = datetime.fromisoformat(continued_at.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(grant_expires_at.replace("Z", "+00:00"))
        if (
            self.now > continued + timedelta(minutes=5)
            or self.now >= expires
            or continued > self.now
            or expected_counter != self.counter
            or previous_receipt_digest != self.previous
            or consumer_nonce in self.nonces
            or receiver_head != self.head
        ):
            return False
        self.counter += 1
        self.previous = receipt_digest
        self.nonces.add(consumer_nonce)
        return True


def identities(fixture: Fixture, registry_digests: list[str]) -> dict[str, object]:
    authority = fixture.authority()
    return {
        "host_identity": "host-001",
        "machine_id": "machine-001",
        "boot_id": "boot-001",
        "user_manager_id": "manager-001",
        "home_generation": "home-001",
        "generic_route_identity": authority["generic"]["route_identity"],
        "generic_service_identity": authority["generic"]["service_identity"],
        "generic_session_identity": authority["generic"]["session_identity"],
        "generic_profile_identity": authority["generic"]["profile_identity"],
        "alpha0_route_identity": authority["alpha0"]["route_identity"],
        "alpha0_service_identity": authority["alpha0"]["service_identity"],
        "alpha0_session_identity": authority["alpha0"]["session_identity"],
        "alpha0_profile_identity": authority["alpha0"]["profile_identity"],
        "dedicated_axis_route": "ABSENT",
        "frontier_digest": "sha256:" + "e" * 64,
        "registry_digests": registry_digests,
        "collector_identity": "offhost-collector-001",
    }


def sample(value: dict[str, object]) -> dict[str, object]:
    return {
        "legacy_axis_new_work_writers": 0,
        "legacy_axis_reprovisioners": 0,
        "effect_capable_descendants": 0,
        "canonical_writers": 0,
        "pending_local_effects": 0,
        "stuck_watchdog_state": "healthy",
        "custody_remote": 9,
        "custody_total": 9,
        "unknowns": [],
        **value,
    }


def binding(anchor, name: str) -> dict[str, str]:
    item = anchor.executables[name]
    return {"closure": str(item.closure), "path": str(item.path), "digest": item.digest}


class VerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = Fixture()
        cls.anchor = cls.fixture.anchor()
        cls.baseline = cls.fixture.baseline(cls.anchor)
        cls.expectations = cls.fixture.expectations()
        cls.registry = RegistrySet(
            cls.expectations, tuple(str(path) for path in cls.fixture.paths)
        )
        cls.registry.acquire()
        cls.execution_journal = Journal(
            cls.fixture.root / "execution", clock=cls.fixture.clock
        )
        executor = Executor(
            ATTEMPT,
            cls.registry,
            cls.execution_journal,
            FakeBackend(cls.fixture),
            cls.fixture.units(),
            cls.fixture.preserved(),
            monotonic=cls.fixture.clock.monotonic,
            effect_plan_digest=cls.anchor.effect_plan_digest,
            rollback_plan_digest=cls.anchor.rollback_plan_digest,
        )
        executor.preflight()
        executor.run_b1()
        executor.run_b2()

        capture_id = "a" * 64
        baseline_digest = strict_json.digest(cls.baseline)
        challenge_head = executor.record_capture_challenge(
            capture_id, baseline_digest
        )
        source = FakeF0EvidenceSource(cls.fixture, cls.baseline)

        def capture_request(phase: str) -> CaptureRequest:
            return CaptureRequest(
                ATTEMPT, baseline_digest, capture_id, phase, challenge_head
            )

        final_custody_reference: dict[str, Any] | None = None
        for index, method in enumerate(("GET", "NO_OP"), 1):
            cls.fixture.clock.advance(300)
            envelope = source.capture_custody(
                capture_request(f"custody-{index}"), method
            )
            reference = cls.fixture.put_artifact(envelope, prefix="custody")
            payload = envelope["payload"]
            executor.record_custody_read(
                {"method": method, "artifact": reference},
                payload["observed_monotonic"],
            )
            if index == 2:
                final_custody_reference = reference
        if final_custody_reference is None:
            raise AssertionError("final custody reference was not captured")
        registry_digests = list(cls.registry.revalidate(FIXED_DELTAS))
        final_envelopes = source.capture_final(capture_request("f0-final"))
        f0_evidence = {
            name: cls.fixture.put_artifact(envelope, prefix="f0-" + name)
            for name, envelope in final_envelopes.items()
        }
        f0_evidence["custody"] = final_custody_reference
        cls.f0 = executor.establish_f0_candidate(
            f0_evidence,
            capture_id,
            final_envelopes["time"]["payload"]["observed_at"],
            lambda candidate: _validate_f0_candidate(
                candidate,
                cls.baseline,
                cls.fixture.expectations(),
                cls.fixture,
                cls.anchor,
                HMACSHA256Verifier(),
            ),
        )

        cls.observation_clock = AcceleratedClock(cls.fixture.clock())
        cls.observation_journal = Journal(
            cls.fixture.root / "observation", clock=cls.observation_clock.wall
        )
        cursor_values = tuple(
            Cursor(item["stream"], item["generation"], item["offset"], item["token"])
            for item in cls.baseline["starting_cursors"]
        )
        cls.identities = identities(cls.fixture, registry_digests)
        cls.receiver = DurableReceiverState(
            cls.fixture.root / "receiver",
            owner_uid=os.getuid(),
            secure_root=cls.fixture.root,
            wall=cls.observation_clock.wall,
            monotonic=cls.observation_clock.monotonic,
        )
        collector = Collector(
            ATTEMPT,
            cls.observation_journal,
            cls.observation_clock,
            cursor_values,
            cls.identities,
            cls.anchor,
            HMACSHA256Verifier(),
            cls.fixture,
            cls.receiver,
            f0_at=datetime.fromisoformat(cls.f0["f0_at"].replace("Z", "+00:00")),
            f0_digest=strict_json.digest(cls.f0),
            receiver_artifact_writer=lambda records: cls.fixture.put_artifact(
                list(records), prefix="receiver-chain"
            ),
        )

        source_monotonic_epoch = 10_000.0

        def emit(stream: str, event_class: str, metadata: dict[str, Any]) -> None:
            previous = collector.cursors[stream]
            payload = {
                "schema": "phase-b.source-event.v1",
                "attempt_id": ATTEMPT,
                "stream": stream,
                "generation": previous.generation,
                "offset": previous.offset + 1,
                "previous_token": previous.token,
                "event_class": event_class,
                "observed_at": cls.observation_clock.wall()
                .isoformat()
                .replace("+00:00", "Z"),
                "observed_monotonic": (
                    source_monotonic_epoch + cls.observation_clock.monotonic()
                ),
                "metadata": metadata,
            }
            collector.append_event(signed(payload, f"phase-b-source-event.{stream}"))

        state_events = {
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
            "systemd": {"type": "systemd-cursor", "data": {"cursor": "systemd-head"}},
            "registry": {
                "type": "registry-snapshot",
                "data": {"registry_digests": registry_digests},
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
            "identity": {"type": "identity-snapshot", "data": cls.identities},
            "time": {
                "type": "time-anchor",
                "data": {
                    "wall_at": cls.observation_clock.wall()
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "monotonic": cls.observation_clock.monotonic(),
                },
            },
        }
        for stream in STREAMS:
            emit(stream, "coverage-open", {"cursor_anchor": f"cursor-{stream}-0"})
        elapsed = 0.0
        sequence = 0
        while elapsed < OBSERVATION_SECONDS:
            step = min(120.0, OBSERVATION_SECONDS - elapsed)
            interval_start = elapsed
            cls.observation_clock.advance(step)
            elapsed += step
            for stream in STREAMS:
                source_cursor = f"{stream}-source-{sequence:04d}"
                events = [state_events[stream]] if sequence == 0 else []
                batch = cls.fixture.put_artifact(
                    {
                        "schema": "phase-b.raw-batch.v1",
                        "stream": stream,
                        "source_cursor": source_cursor,
                        "events": events,
                    },
                    prefix="raw",
                )
                received_at = (
                    cls.observation_clock.wall().isoformat().replace("+00:00", "Z")
                )
                receiver_ack = collector.ack_challenge(stream)
                next_ack = strict_json.digest(
                    {
                        "previous": receiver_ack,
                        "stream": stream,
                        "source_cursor": source_cursor,
                        "batch_digest": batch["digest"],
                        "received_at": received_at,
                    }
                )
                emit(
                    stream,
                    "continuity-checkpoints",
                    {
                        "checkpoints": [
                            {
                                "start_monotonic": source_monotonic_epoch
                                + interval_start,
                                "end_monotonic": source_monotonic_epoch + elapsed,
                                "received_at": received_at,
                                "source_cursor": source_cursor,
                                "previous_receiver_ack": receiver_ack,
                                "receiver_ack": next_ack,
                                "batch": batch,
                                "event_count": len(events),
                                "lost": 0,
                                "backlog": 0,
                                "replay": 0,
                            }
                        ]
                    },
                )
            sequence += 1
            collector.sample(sample(cls.identities))
        for stream in STREAMS:
            emit(
                stream,
                "coverage-close",
                {"cursor_head": collector.cursors[stream].token},
            )
            emit(
                stream,
                "ack",
                {
                    "cursor_token": collector.cursors[stream].token,
                    "receiver_head": collector.ack_challenge(stream),
                },
            )
        cls.observation = collector.finish()

        cls.reconstructions = []
        for index in (1, 2):
            transcript = {
                name: cls.fixture.put_artifact(
                    f"{name}-{index}".encode(),
                    media_type="application/octet-stream",
                    prefix=name,
                )
                for name in ("input", "output", "transcript")
            }
            cls.reconstructions.append(
                {
                    "schema": "phase-b.reconstruction.v2",
                    "attempt_id": ATTEMPT,
                    "home_id": f"disposable-{index}",
                    "execution_identity": f"execution-{index}",
                    "home_mode": 0o700,
                    "source_revision": cls.baseline["source_revision"],
                    "runner": binding(cls.anchor, "reconstruction-runner"),
                    "clean_roots": {
                        "home": f"/fixture/home-{index}",
                        "runtime": f"/fixture/runtime-{index}",
                        "workspace": f"/fixture/work-{index}",
                        "preexisting_entries": 0,
                    },
                    "transcript": transcript,
                    "network_monitor": {
                        "binding": binding(cls.anchor, "network-monitor"),
                        "artifact": cls.fixture.put_artifact(
                            {
                                "schema": "phase-b.network-monitor.v1",
                                "events": [
                                    {
                                        "operation": "connect",
                                        "destination_class": "network",
                                        "result": "DENIED",
                                    }
                                ],
                            },
                            prefix="network",
                        ),
                    },
                    "write_monitor": {
                        "binding": binding(cls.anchor, "write-monitor"),
                        "artifact": cls.fixture.put_artifact(
                            {
                                "schema": "phase-b.write-monitor.v1",
                                "events": [
                                    {
                                        "operation": "write",
                                        "path_class": "outside-clean-roots",
                                        "result": "DENIED",
                                    }
                                ],
                            },
                            prefix="write",
                        ),
                    },
                    "environment": {
                        "mode": 0o600,
                        "variable_names": ["HERMES_TOKEN", "SLACK_APP_TOKEN"],
                        "artifact": cls.fixture.put_artifact(
                            b"fixture-secret-canary",
                            media_type="application/octet-stream",
                            prefix="environment",
                        ),
                    },
                    "imported_state": cls.fixture.put_artifact(
                        {"schema": "phase-b.imported-state.v1", "imports": []},
                        prefix="imports",
                    ),
                    "semantics": cls.fixture.put_artifact(
                        CANONICAL_SEMANTICS, prefix="semantics"
                    ),
                }
            )
        cls.reconstructions = (cls.reconstructions[0], cls.reconstructions[1])

        observed_at = cls.observation_clock.wall()
        source_received_at = observed_at + timedelta(seconds=30)
        cls.observation_clock.advance(30)
        continued_at = cls.observation_clock.wall()
        grant_payload = {
            "schema": "phase-b.consumption-grant.v1",
            "attempt_id": ATTEMPT,
            "consumer_identity": "phase-b-consumer-001",
            "consumer_nonce": "consumer-nonce-0001",
            "requested_transition": "PHASE_B_FENCING_QUALIFICATION",
            "expires_at": (continued_at + timedelta(seconds=300))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        grant = cls.fixture.put_artifact(
            signed(grant_payload, "phase-b-consumption-grant"), prefix="grant"
        )
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
                "data": {"cursor": "continuation-journal-head"},
            },
            "systemd": {
                "type": "systemd-cursor",
                "data": {"cursor": "continuation-systemd-head"},
            },
            "registry": {
                "type": "registry-snapshot",
                "data": {
                    "registry_digests": cls.observation["identities"][
                        "registry_digests"
                    ]
                },
            },
            "database": {
                "type": "database-snapshot",
                "data": {"pending": 0, "inflight": 0, "local_only": 0},
            },
            "provider-route": {
                "type": "route-snapshot",
                "data": {
                    "generic_route_identity": cls.observation["identities"][
                        "generic_route_identity"
                    ],
                    "alpha0_route_identity": cls.observation["identities"][
                        "alpha0_route_identity"
                    ],
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
                    "frontier_digest": cls.observation["identities"]["frontier_digest"],
                },
            },
            "identity": {
                "type": "identity-snapshot",
                "data": cls.observation["identities"],
            },
            "time": {
                "type": "time-anchor",
                "data": {
                    "wall_at": continued_at.isoformat().replace("+00:00", "Z"),
                    "monotonic": OBSERVATION_SECONDS + 1,
                },
            },
        }
        source_events = []
        ending = {item["stream"]: item for item in cls.observation["ending_cursors"]}
        for stream in STREAMS:
            source_cursor = f"{stream}-continuation-0001"
            batch = cls.fixture.put_artifact(
                {
                    "schema": "phase-b.raw-batch.v1",
                    "stream": stream,
                    "source_cursor": source_cursor,
                    "events": [raw_events[stream]],
                },
                prefix="continuation-raw",
            )
            cursor = ending[stream]
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
                        "observed_at": continued_at.isoformat().replace("+00:00", "Z"),
                        "observed_monotonic": (
                            source_monotonic_epoch + OBSERVATION_SECONDS + 30
                        ),
                        "metadata": {
                            "checkpoints": [
                                {
                                    "start_monotonic": (
                                        source_monotonic_epoch + OBSERVATION_SECONDS
                                    ),
                                    "end_monotonic": (
                                        source_monotonic_epoch
                                        + OBSERVATION_SECONDS
                                        + 30
                                    ),
                                    "received_at": continued_at.isoformat().replace(
                                        "+00:00", "Z"
                                    ),
                                    "source_cursor": source_cursor,
                                    "batch": batch,
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
        first_sequence = cls.observation["receiver_custody"]["sequence"]
        for ordinal, source_envelope in enumerate(source_events):
            cls.receiver.append_source(
                first_sequence + ordinal, strict_json.canonical(source_envelope)
            )
        source_records = list(cls.receiver.export_records()[first_sequence:])
        refresh_segment = {
            "schema": "phase-b.receiver-refresh-segment.v1",
            "attempt_id": ATTEMPT,
            "refresh_id": "fixture-refresh-1",
            "refresh_counter": 1,
            "segment_index": 1,
            "final": True,
            "observed_at": continued_at.isoformat().replace("+00:00", "Z"),
            "source_events": source_events,
            "sample": sample(cls.observation["identities"]),
            "consumer_identity": grant_payload["consumer_identity"],
            "consumer_nonce": grant_payload["consumer_nonce"],
            "requested_transition": grant_payload["requested_transition"],
            "authorization_grant_digest": grant["digest"],
            "invalidating_event_count": 0,
        }
        refresh_segment_ref = cls.fixture.put_artifact(
            refresh_segment, prefix="refresh-segment"
        )
        receiver_event = cls.fixture.put_artifact(
            {
                "schema": "phase-b.receiver-extension.v3",
                "attempt_id": ATTEMPT,
                "refresh_id": "fixture-refresh-1",
                "refresh_counter": 1,
                "segment_count": 1,
                "segment_artifacts": [refresh_segment_ref],
                "observed_at": continued_at.isoformat().replace("+00:00", "Z"),
                "source_events": source_events,
                "sample": sample(cls.observation["identities"]),
                "consumer_identity": grant_payload["consumer_identity"],
                "consumer_nonce": grant_payload["consumer_nonce"],
                "requested_transition": grant_payload["requested_transition"],
                "authorization_grant_digest": grant["digest"],
                "invalidating_event_count": 0,
            },
            prefix="receiver",
        )
        observation_receiver_head = cls.observation["receiver_custody"]["head"]
        extension = cls.receiver.append_extension(receiver_event)
        receiver_head = extension["head"]
        cls.continuation_payload = {
            "schema": "phase-b.receiver-continuation.v1",
            "attempt_id": ATTEMPT,
            "observation_head": observation_receiver_head,
            "source_records": source_records,
            "extensions": [extension],
            "current_head": receiver_head,
            "terminal_cursors": [
                {
                    "stream": envelope["payload"]["stream"],
                    "generation": envelope["payload"]["generation"],
                    "offset": envelope["payload"]["offset"],
                    "token": strict_json.digest(envelope),
                }
                for envelope in source_events
            ],
            "terminal_continuity": {
                stream: source_monotonic_epoch + OBSERVATION_SECONDS + 30
                for stream in STREAMS
            },
            "terminal_source_walls": {
                stream: continued_at.isoformat().replace("+00:00", "Z")
                for stream in STREAMS
            },
        }
        cls.continuation = signed(cls.continuation_payload, "phase-b-observation")
        self_check = source_received_at == continued_at
        if not self_check:
            raise AssertionError("fixture processing delay clock mismatch")
        when = continued_at.isoformat().replace("+00:00", "Z")
        cls.receipt = {
            "schema": "phase-b.receipt.v2",
            "attempt_id": ATTEMPT,
            "baseline_digest": strict_json.digest(cls.baseline),
            "f0_digest": strict_json.digest(cls.f0),
            "observation_digest": strict_json.digest(cls.observation),
            "reconstruction_digests": [
                strict_json.digest(item) for item in cls.reconstructions
            ],
            "execution_journal_head": cls.execution_journal.head(),
            "observation_chain_head": cls.observation["chain_head"],
            "observed_through_at": cls.observation["observed_through_at"],
            "issued_at": when,
            "receiver_head": receiver_head,
            "continuation_digest": strict_json.digest(cls.continuation_payload),
            "consumer_nonce": grant_payload["consumer_nonce"],
            "consumer_identity": grant_payload["consumer_identity"],
            "requested_transition": grant_payload["requested_transition"],
            "authorization_grant": grant,
            "authorization_grant_digest": grant["digest"],
            "consumption_counter": 1,
            "previous_receipt_digest": None,
            "verified_chain_accumulator": strict_json.digest([]),
        }
        cls.bundle = VerificationBundle(
            signed(cls.baseline, "phase-b-baseline"),
            signed(cls.f0, "phase-b-f0"),
            signed(cls.observation, "phase-b-observation"),
            (
                signed(cls.reconstructions[0], "phase-b-reconstruction"),
                signed(cls.reconstructions[1], "phase-b-reconstruction"),
            ),
            signed(cls.receipt, "phase-b-receipt"),
            (),
            cls.execution_journal,
            cls.observation_journal,
            cls.fixture,
            cls.continuation,
        )
        cls.now = continued_at
        cls.receiver_head = receiver_head

    @classmethod
    def tearDownClass(cls) -> None:
        cls.registry.close()
        cls.fixture.close()

    def verifier(
        self,
        *,
        bundle: VerificationBundle | None = None,
        head: str | None = None,
        age: int = 0,
        counter: int = 0,
        previous: str | None = None,
    ):
        del bundle
        authority = MemoryConsumption(
            self.now + timedelta(seconds=age),
            head or self.receiver_head,
            counter=counter,
        )
        authority.previous = previous
        return Verifier(self.anchor, HMACSHA256Verifier(), authority), authority

    def second_refresh(
        self,
        *,
        wall_seconds: int = 1,
        monotonic_seconds: int | None = None,
        rounds: int = 1,
        trailing_seconds: int = 0,
    ):
        previous_receipt = self.bundle.receipt
        previous_payload = previous_receipt["payload"]
        previous_head = previous_payload["receiver_head"]
        previous_proof = self.bundle.continuation_proof
        assert previous_proof is not None
        previous_sources = previous_proof["payload"]["source_records"]
        observed = self.now + timedelta(seconds=wall_seconds)
        when = observed.isoformat().replace("+00:00", "Z")
        extension_observed = observed + timedelta(seconds=trailing_seconds)
        extension_when = extension_observed.isoformat().replace("+00:00", "Z")
        cursors = {
            envelope["payload"]["stream"]: envelope
            for envelope in [
                record["payload"]["envelope"] for record in previous_sources
            ]
        }
        if monotonic_seconds is None:
            first_prior = cursors[STREAMS[0]]["payload"]
            prior_wall = datetime.fromisoformat(
                first_prior["observed_at"].replace("Z", "+00:00")
            )
            monotonic_seconds = int((observed - prior_wall).total_seconds())
        if (
            isinstance(rounds, bool)
            or rounds < 1
            or monotonic_seconds % rounds
            or wall_seconds % rounds
        ):
            raise AssertionError("refresh fixture rounds must divide the interval")
        monotonic_step = monotonic_seconds // rounds
        wall_step = wall_seconds // rounds
        source_events = []
        for round_number in range(1, rounds + 1):
            round_when = self.now + timedelta(seconds=wall_step * round_number)
            round_text = round_when.isoformat().replace("+00:00", "Z")
            for stream in STREAMS:
                prior = cursors[stream]
                prior_checkpoint = prior["payload"]["metadata"]["checkpoints"][-1]
                envelope = signed(
                    {
                        **prior["payload"],
                        "offset": prior["payload"]["offset"] + 1,
                        "previous_token": strict_json.digest(prior),
                        "observed_at": round_text,
                        "observed_monotonic": prior_checkpoint["end_monotonic"]
                        + monotonic_step,
                        "metadata": {
                            "checkpoints": [
                                {
                                    **prior_checkpoint,
                                    "start_monotonic": prior_checkpoint[
                                        "end_monotonic"
                                    ],
                                    "end_monotonic": prior_checkpoint["end_monotonic"]
                                    + monotonic_step,
                                    "received_at": round_text,
                                }
                            ]
                        },
                    },
                    f"phase-b-source-event.{stream}",
                )
                source_events.append(envelope)
                cursors[stream] = envelope
        first_sequence = self.observation["receiver_custody"]["sequence"] + 10
        head = previous_head
        source_records = []
        for ordinal, envelope in enumerate(source_events):
            sequence = first_sequence + ordinal
            unsigned = {
                "schema": "phase-b.journal-record.v1",
                "sequence": sequence,
                "previous_hash": head,
                "kind": "checkpoint",
                "action_id": f"source-{sequence:020d}",
                "payload": {
                    "sequence": sequence,
                    "envelope": envelope,
                    "receiver_received_at": envelope["payload"]["observed_at"],
                    "receiver_received_monotonic": envelope["payload"][
                        "observed_monotonic"
                    ],
                },
                "recorded_at": envelope["payload"]["observed_at"],
            }
            record = {**unsigned, "record_hash": strict_json.digest(unsigned)}
            source_records.append(record)
            head = record["record_hash"]
        grant_payload = {
            "schema": "phase-b.consumption-grant.v1",
            "attempt_id": ATTEMPT,
            "consumer_identity": "consumer-002",
            "consumer_nonce": "consumer-nonce-002",
            "requested_transition": "PHASE_B_FENCING_QUALIFICATION",
            "expires_at": (extension_observed + timedelta(minutes=5))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        grant = self.fixture.put_artifact(
            signed(grant_payload, "phase-b-consumption-grant"), prefix="grant-2"
        )
        refresh_id = "fixture-refresh-2"
        segment_refs = []
        for segment_index in range(1, rounds + 1):
            final = segment_index == rounds
            segment_events = source_events[
                (segment_index - 1) * len(STREAMS) : segment_index * len(STREAMS)
            ]
            segment_refs.append(
                self.fixture.put_artifact(
                    {
                        "schema": "phase-b.receiver-refresh-segment.v1",
                        "attempt_id": ATTEMPT,
                        "refresh_id": refresh_id,
                        "refresh_counter": 2,
                        "segment_index": segment_index,
                        "final": final,
                        "observed_at": segment_events[-1]["payload"]["observed_at"],
                        "source_events": segment_events,
                        "sample": sample(self.observation["identities"])
                        if final
                        else None,
                        "consumer_identity": grant_payload["consumer_identity"],
                        "consumer_nonce": grant_payload["consumer_nonce"],
                        "requested_transition": grant_payload["requested_transition"],
                        "authorization_grant_digest": grant["digest"],
                        "invalidating_event_count": 0,
                    },
                    prefix=f"refresh-2-segment-{segment_index}",
                )
            )
        event = self.fixture.put_artifact(
            {
                "schema": "phase-b.receiver-extension.v3",
                "attempt_id": ATTEMPT,
                "refresh_id": refresh_id,
                "refresh_counter": 2,
                "segment_count": rounds,
                "segment_artifacts": segment_refs,
                "observed_at": extension_when,
                "source_events": source_events,
                "sample": sample(self.observation["identities"]),
                "consumer_identity": grant_payload["consumer_identity"],
                "consumer_nonce": grant_payload["consumer_nonce"],
                "requested_transition": grant_payload["requested_transition"],
                "authorization_grant_digest": grant["digest"],
                "invalidating_event_count": 0,
            },
            prefix="receiver-2",
        )
        extension_sequence = first_sequence + len(source_events)
        unsigned_extension = {
            "schema": "phase-b.journal-record.v1",
            "sequence": extension_sequence,
            "previous_hash": head,
            "kind": "checkpoint",
            "action_id": f"extension-{extension_sequence:020d}",
            "payload": {"event": event},
            "recorded_at": extension_when,
        }
        extension_record = {
            **unsigned_extension,
            "record_hash": strict_json.digest(unsigned_extension),
        }
        extension = {
            "sequence": extension_sequence,
            "previous_head": head,
            "head": extension_record["record_hash"],
            "event": event,
            "receiver_record": extension_record,
        }
        continuation_payload = {
            "schema": "phase-b.receiver-continuation.v1",
            "attempt_id": ATTEMPT,
            "observation_head": previous_head,
            "source_records": source_records,
            "extensions": [extension],
            "current_head": extension["head"],
            "terminal_cursors": [
                {
                    "stream": stream,
                    "generation": cursors[stream]["payload"]["generation"],
                    "offset": cursors[stream]["payload"]["offset"],
                    "token": strict_json.digest(cursors[stream]),
                }
                for stream in STREAMS
            ],
            "terminal_continuity": {
                stream: cursors[stream]["payload"]["metadata"]["checkpoints"][-1][
                    "end_monotonic"
                ]
                for stream in STREAMS
            },
            "terminal_source_walls": {stream: when for stream in STREAMS},
        }
        continuation = signed(continuation_payload, "phase-b-observation")
        accumulator = strict_json.digest(
            {
                "previous": strict_json.digest([]),
                "signed_receipt": strict_json.digest(previous_receipt),
            }
        )
        receipt = {
            **previous_payload,
            "issued_at": extension_when,
            "receiver_head": extension["head"],
            "continuation_digest": strict_json.digest(continuation_payload),
            "consumer_nonce": grant_payload["consumer_nonce"],
            "consumer_identity": grant_payload["consumer_identity"],
            "authorization_grant": grant,
            "authorization_grant_digest": grant["digest"],
            "consumption_counter": 2,
            "previous_receipt_digest": strict_json.digest(previous_payload),
            "verified_chain_accumulator": accumulator,
        }
        bundle = replace(
            self.bundle,
            receipt=signed(receipt, "phase-b-receipt"),
            previous_receipts=(previous_receipt,),
            continuation_proof=continuation,
            previous_continuations=(self.continuation,),
        )
        return bundle, extension_observed, extension["head"]

    def resign_receipt(
        self, bundle: VerificationBundle, **updates: object
    ) -> VerificationBundle:
        payload = deepcopy(bundle.receipt["payload"])
        payload.update(updates)
        return replace(bundle, receipt=signed(payload, "phase-b-receipt"))

    def restore_variant(
        self,
        *,
        entry_index: int = 0,
        mutate_test: Callable[[dict[str, Any]], None] | None = None,
        raw_test: bytes | None = None,
        omit_ref: bool = False,
        receipt_updates: dict[str, object] | None = None,
        source_bytes: bytes | None = None,
        backup_bytes: bytes | None = None,
        restored_bytes: bytes | None = None,
        reuse_source_as_restored: bool = False,
    ) -> VerificationBundle:
        values = dict(self.fixture.artifacts)
        changed = deepcopy(self.baseline)
        entry = changed["backup_artifacts"][entry_index]
        receipt_ref = entry["restore_receipt"]
        receipt_envelope = strict_json.loads_canonical(values[receipt_ref["id"]])
        receipt_payload = deepcopy(receipt_envelope["payload"])
        restore_ref = deepcopy(receipt_payload["restore_test"])
        restore_test = strict_json.loads_canonical(values[restore_ref["id"]])

        if backup_bytes is not None:
            backup_ref = deepcopy(entry["backup"])
            values[backup_ref["id"]] = backup_bytes
            backup_ref["digest"] = (
                "sha256:" + hashlib.sha256(backup_bytes).hexdigest()
            )
            entry["backup"] = backup_ref
            receipt_payload.update(
                {"backup": backup_ref, "backup_digest": backup_ref["digest"]}
            )
            restore_test.update(
                {"backup": backup_ref, "backup_digest": backup_ref["digest"]}
            )
        if source_bytes is not None:
            source_ref = deepcopy(entry["source"])
            values[source_ref["id"]] = source_bytes
            source_ref["digest"] = "sha256:" + hashlib.sha256(source_bytes).hexdigest()
            entry["source"] = source_ref
            receipt_payload.update(
                {"source": source_ref, "source_digest": source_ref["digest"]}
            )
            restore_test.update(
                {"source": source_ref, "source_digest": source_ref["digest"]}
            )
            restore_test["integrity"]["source_digest"] = source_ref["digest"]
        if restored_bytes is not None:
            restored_ref = deepcopy(receipt_payload["restored_output"])
            values[restored_ref["id"]] = restored_bytes
            restored_ref["digest"] = (
                "sha256:" + hashlib.sha256(restored_bytes).hexdigest()
            )
            receipt_payload.update(
                {
                    "restored_output": restored_ref,
                    "restored_output_digest": restored_ref["digest"],
                }
            )
            restore_test.update(
                {
                    "restored_output": restored_ref,
                    "restored_output_digest": restored_ref["digest"],
                }
            )
            restore_test["integrity"]["restored_output_digest"] = restored_ref[
                "digest"
            ]
        if reuse_source_as_restored:
            source_ref = deepcopy(entry["source"])
            receipt_payload.update(
                {
                    "restored_output": source_ref,
                    "restored_output_digest": source_ref["digest"],
                }
            )
            restore_test.update(
                {
                    "restored_output": source_ref,
                    "restored_output_digest": source_ref["digest"],
                }
            )
            restore_test["integrity"]["restored_output_digest"] = source_ref[
                "digest"
            ]
        if mutate_test is not None:
            mutate_test(restore_test)
        replacement = raw_test or strict_json.canonical(restore_test)
        values[restore_ref["id"]] = replacement
        restore_ref["digest"] = "sha256:" + hashlib.sha256(replacement).hexdigest()
        if omit_ref:
            receipt_payload.pop("restore_test")
        else:
            receipt_payload["restore_test"] = restore_ref
        receipt_payload["restore_test_digest"] = restore_ref["digest"]
        if receipt_updates:
            receipt_payload.update(receipt_updates)
        replacement_receipt = strict_json.canonical(
            signed(receipt_payload, "phase-b-backup-restore")
        )
        values[receipt_ref["id"]] = replacement_receipt
        entry["restore_receipt"]["digest"] = (
            "sha256:" + hashlib.sha256(replacement_receipt).hexdigest()
        )
        bundle = replace(
            self.bundle,
            baseline=signed(changed, "phase-b-baseline"),
            artifacts=DictStore(values),
        )
        return self.resign_receipt(
            bundle, baseline_digest=strict_json.digest(changed)
        )

    def test_executor_f0_validation_loads_evidence_and_stable_custody(self) -> None:
        _validate_f0_candidate(
            self.f0,
            self.baseline,
            self.fixture.expectations(),
            self.fixture,
            self.anchor,
            HMACSHA256Verifier(),
        )

        wrong_evidence = deepcopy(self.f0)
        wrong_evidence["evidence"]["audit"] = wrong_evidence["evidence"]["identity"]
        with self.assertRaises(
            (TrustError, VerificationError, strict_json.StrictJSONError)
        ):
            _validate_f0_candidate(
                wrong_evidence,
                self.baseline,
                self.fixture.expectations(),
                self.fixture,
                self.anchor,
                HMACSHA256Verifier(),
            )

        tampered = dict(self.fixture.artifacts)
        process_ref = self.f0["evidence"]["audit"]
        tampered[process_ref["id"]] = b"{}"
        with self.assertRaises(
            (ArtifactError, VerificationError, strict_json.StrictJSONError)
        ):
            _validate_f0_candidate(
                self.f0,
                self.baseline,
                self.fixture.expectations(),
                DictStore(tampered),
                self.anchor,
                HMACSHA256Verifier(),
            )

        contradictory = deepcopy(self.f0)
        second = contradictory["custody_reads"][1]
        changed_envelope = deepcopy(
            strict_json.loads(self.fixture.read(second["artifact"]["id"]))
        )
        changed = changed_envelope["payload"]
        changed["evidence"]["pages"][-1]["records"][0]["classification"] = (
            "PRESERVED_NON_AXIS"
        )
        changed["window"]["state_digest"] = strict_json.digest(changed["evidence"])
        second["artifact"] = self.fixture.put_artifact(
            signed(changed, "phase-b-source-event.custody"), prefix="custody-drift"
        )
        with self.assertRaisesRegex(VerificationError, "not stable"):
            _validate_f0_candidate(
                contradictory,
                self.baseline,
                self.fixture.expectations(),
                self.fixture,
                self.anchor,
                HMACSHA256Verifier(),
            )

    def test_f0_custody_ref_must_equal_second_no_op_journal_capture(self) -> None:
        changed = deepcopy(self.f0)
        changed["evidence"]["custody"] = changed["custody_reads"][0]["artifact"]
        with self.assertRaisesRegex(VerificationError, "stable/bound"):
            _verify_execution_journal(
                self.execution_journal,
                changed,
                self.baseline,
                self.expectations,
                self.fixture,
                self.anchor,
                HMACSHA256Verifier(),
            )

    def test_live_capture_rejects_wrong_binding_role_and_freshness(self) -> None:
        reference = self.f0["evidence"]["audit"]
        envelope = strict_json.loads(self.fixture.read(reference["id"]))
        payload = envelope["payload"]
        request = {
            "schema": "phase-b.capture-request.v1",
            **{
                key: payload[key]
                for key in (
                    "attempt_id",
                    "baseline_digest",
                    "capture_id",
                    "phase",
                    "journal_head",
                )
            },
        }
        observed_wall = datetime.fromisoformat(
            payload["observed_at"].replace("Z", "+00:00")
        )
        _verify_live_capture(
            envelope,
            "audit",
            request,
            self.baseline,
            self.anchor,
            HMACSHA256Verifier(),
            observed_wall,
            payload["observed_monotonic"],
        )

        for field, value in (
            ("capture_id", "b" * 64),
            ("journal_head", "sha256:" + "f" * 64),
        ):
            wrong_request = dict(request)
            wrong_request[field] = value
            with self.subTest(field=field), self.assertRaises(VerificationError):
                _verify_live_capture(
                    envelope,
                    "audit",
                    wrong_request,
                    self.baseline,
                    self.anchor,
                    HMACSHA256Verifier(),
                )
        with self.assertRaises(TrustError):
            _verify_live_capture(
                signed(payload, "phase-b-source-event.identity"),
                "audit",
                request,
                self.baseline,
                self.anchor,
                HMACSHA256Verifier(),
            )
        for delta in (-6, 6):
            with self.subTest(receiver_delta=delta), self.assertRaises(
                VerificationError
            ):
                _verify_live_capture(
                    envelope,
                    "audit",
                    request,
                    self.baseline,
                    self.anchor,
                    HMACSHA256Verifier(),
                    observed_wall + timedelta(seconds=delta),
                    payload["observed_monotonic"] + delta,
                )
        with self.assertRaisesRegex(VerificationError, "received fresh"):
            _verify_live_capture(
                envelope,
                "audit",
                request,
                self.baseline,
                self.anchor,
                HMACSHA256Verifier(),
                observed_wall + timedelta(seconds=2),
                payload["observed_monotonic"] + 2,
            )
        changed = deepcopy(payload)
        changed["boot_id"] = "wrong-boot"
        with self.assertRaises(VerificationError):
            _verify_live_capture(
                signed(changed, "phase-b-source-event.audit"),
                "audit",
                request,
                self.baseline,
                self.anchor,
                HMACSHA256Verifier(),
            )
        changed = deepcopy(payload)
        changed["window"]["invalidating_event_count"] = 1
        with self.assertRaises(VerificationError):
            _verify_live_capture(
                signed(changed, "phase-b-source-event.audit"),
                "audit",
                request,
                self.baseline,
                self.anchor,
                HMACSHA256Verifier(),
            )
        prospective = deepcopy(payload)
        prospective["window"]["end_monotonic"] = (
            prospective["observed_monotonic"] + 0.001
        )
        with self.assertRaisesRegex(VerificationError, "binding/window"):
            _verify_live_capture(
                signed(prospective, "phase-b-source-event.audit"),
                "audit",
                request,
                self.baseline,
                self.anchor,
                HMACSHA256Verifier(),
                observed_wall,
                payload["observed_monotonic"],
            )
        stale = deepcopy(payload)
        stale["window"]["end_monotonic"] = stale["observed_monotonic"] - 2
        stale["window"]["start_monotonic"] = stale["observed_monotonic"] - 3
        with self.assertRaisesRegex(VerificationError, "binding/window"):
            _verify_live_capture(
                signed(stale, "phase-b-source-event.audit"),
                "audit",
                request,
                self.baseline,
                self.anchor,
                HMACSHA256Verifier(),
                observed_wall,
                payload["observed_monotonic"],
            )

    def test_f0_rejects_disjoint_signed_capture_windows(self) -> None:
        changed_f0 = deepcopy(self.f0)
        reference = changed_f0["evidence"]["audit"]
        envelope = strict_json.loads(self.fixture.read(reference["id"]))
        payload = deepcopy(envelope["payload"])
        payload["observed_monotonic"] += 4
        payload["observed_at"] = (
            datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
            + timedelta(seconds=4)
        ).isoformat().replace("+00:00", "Z")
        payload["window"]["start_monotonic"] += 4
        payload["window"]["end_monotonic"] += 4
        changed_f0["evidence"]["audit"] = self.fixture.put_artifact(
            signed(payload, "phase-b-source-event.audit"), prefix="disjoint-audit"
        )
        with self.assertRaisesRegex(VerificationError, "common stable"):
            _validate_f0_candidate(
                changed_f0,
                self.baseline,
                self.fixture.expectations(),
                self.fixture,
                self.anchor,
                HMACSHA256Verifier(),
            )

    def test_accepts_actual_artifacts_and_consumes_once(self) -> None:
        verifier, authority = self.verifier()
        result = verifier.verify(self.bundle)
        self.assertEqual(result.phase_b_fencing_qualification, "PROVEN")
        self.assertEqual(result.cutover_ready, "NO")
        with self.assertRaises(VerificationError):
            verifier.verify(self.bundle)
        self.assertEqual(authority.counter, 1)

    def test_restore_output_must_be_a_distinct_artifact(self) -> None:
        bundle = self.restore_variant(reuse_source_as_restored=True)
        with self.assertRaisesRegex(
            VerificationError, "receipt identity/freshness mismatch"
        ):
            self.verifier()[0].verify(bundle)

    def test_large_binary_restore_artifacts_use_evidence_limit(self) -> None:
        large = b"x" * (4 * 1024 * 1024 + 1)
        bundle = self.restore_variant(
            entry_index=6,
            source_bytes=large,
            backup_bytes=large,
            restored_bytes=large,
        )
        expectations = _baseline(
            bundle.baseline["payload"],
            self.anchor,
            bundle.artifacts,
            HMACSHA256Verifier(),
        )
        self.assertEqual(len(expectations), 6)

    def test_backup_bytes_and_restore_receipts_are_mandatory(self) -> None:
        values = dict(self.fixture.artifacts)
        backup_id = self.baseline["backup_artifacts"][0]["backup"]["id"]
        values[backup_id] = b"tampered"
        with self.assertRaises(VerificationError):
            self.verifier()[0].verify(replace(self.bundle, artifacts=DictStore(values)))
        changed = deepcopy(self.baseline)
        changed["backup_artifacts"].pop()
        bundle = replace(self.bundle, baseline=signed(changed, "phase-b-baseline"))
        bundle = self.resign_receipt(
            bundle, baseline_digest=strict_json.digest(changed)
        )
        with self.assertRaises(VerificationError):
            self.verifier()[0].verify(bundle)

        restore_ref = strict_json.loads_canonical(
            self.fixture.artifacts[
                self.baseline["backup_artifacts"][0]["restore_receipt"]["id"]
            ]
        )["payload"]["restore_test"]
        values = dict(self.fixture.artifacts)
        values[restore_ref["id"]] = b"{}"
        with self.assertRaises((ArtifactError, VerificationError)):
            self.verifier()[0].verify(
                replace(self.bundle, artifacts=DictStore(values))
            )

        fake_digest = "sha256:" + "f" * 64
        captured_at = datetime.fromisoformat(
            self.baseline["captured_at"].replace("Z", "+00:00")
        )
        second_source = deepcopy(self.baseline["backup_artifacts"][1]["source"])
        cases = {
            "absent-ref": self.restore_variant(omit_ref=True),
            "arbitrary-test-bytes": self.restore_variant(raw_test=b"not-json"),
            "matching-claims-without-matching-bytes": self.restore_variant(
                mutate_test=lambda value: value.update(
                    {
                        "source_digest": fake_digest,
                        "restored_output_digest": fake_digest,
                        "integrity": {
                            "algorithm": "sha256",
                            "source_digest": fake_digest,
                            "restored_output_digest": fake_digest,
                        },
                    }
                ),
                receipt_updates={
                    "source_digest": fake_digest,
                    "restored_output_digest": fake_digest,
                },
            ),
            "source-restored-byte-mismatch": self.restore_variant(
                restored_bytes=b"different restored bytes"
            ),
            "noncanonical-registry-source": self.restore_variant(
                source_bytes=b"arbitrary registry source",
                restored_bytes=b"arbitrary registry source",
            ),
            "swapped-source-ref": self.restore_variant(
                mutate_test=lambda value: value.update(
                    {
                        "source": second_source,
                        "source_digest": second_source["digest"],
                    }
                ),
                receipt_updates={
                    "source": second_source,
                    "source_digest": second_source["digest"],
                },
            ),
            "wrong-receipt-attempt": self.restore_variant(
                receipt_updates={"attempt_id": "other-attempt"}
            ),
            "wrong-test-attempt": self.restore_variant(
                mutate_test=lambda value: value.__setitem__(
                    "attempt_id", "other-attempt"
                )
            ),
            "ancient-tested-at": self.restore_variant(
                receipt_updates={"tested_at": "2000-01-01T00:00:00Z"}
            ),
            "future-tested-at": self.restore_variant(
                receipt_updates={
                    "tested_at": (captured_at + timedelta(seconds=1))
                    .isoformat()
                    .replace("+00:00", "Z")
                }
            ),
            "stale-tested-at": self.restore_variant(
                receipt_updates={
                    "tested_at": (captured_at - timedelta(minutes=16))
                    .isoformat()
                    .replace("+00:00", "Z")
                }
            ),
            "nonzero-network": self.restore_variant(
                mutate_test=lambda value: value.__setitem__("network_attempts", 1)
            ),
            "nonzero-exit": self.restore_variant(
                mutate_test=lambda value: value.__setitem__("command_exit_code", 1)
            ),
            "failed-integrity": self.restore_variant(
                mutate_test=lambda value: value["integrity"].__setitem__(
                    "algorithm", "claimed-match"
                )
            ),
        }
        for name, changed_bundle in cases.items():
            with self.subTest(name=name), self.assertRaises(
                (ArtifactError, VerificationError, strict_json.StrictJSONError)
            ):
                self.verifier()[0].verify(changed_bundle)

    def test_automatic_reboot_conflict_is_derived_from_signed_artifact(self) -> None:
        artifact_id = self.baseline["automatic_reboot_evidence"]["id"]
        original = self.fixture.artifacts[artifact_id]
        value = strict_json.loads_canonical(original)
        payload = dict(value["payload"])
        payload["scheduled_reboots"] = [
            {"unit": "nixos-upgrade.service", "at": self.observation["f0_at"]}
        ]
        replacement = strict_json.canonical(
            signed(payload, "phase-b-source-event.systemd")
        )
        values = dict(self.fixture.artifacts)
        values[artifact_id] = replacement
        changed = deepcopy(self.baseline)
        changed["automatic_reboot_evidence"]["digest"] = (
            "sha256:" + hashlib.sha256(replacement).hexdigest()
        )
        bundle = replace(
            self.bundle,
            baseline=signed(changed, "phase-b-baseline"),
            artifacts=DictStore(values),
        )
        bundle = self.resign_receipt(
            bundle, baseline_digest=strict_json.digest(changed)
        )
        with self.assertRaises(VerificationError):
            self.verifier()[0].verify(bundle)
        self.assertEqual(self.fixture.artifacts[artifact_id], original)

    def test_f0_is_derived_from_typed_artifacts_not_claimed_facts(self) -> None:
        values = dict(self.fixture.artifacts)
        process_ref = self.f0["evidence"]["audit"]
        values[process_ref["id"]] = b"{}"
        with self.assertRaises(VerificationError):
            self.verifier()[0].verify(replace(self.bundle, artifacts=DictStore(values)))
        changed = deepcopy(self.f0)
        changed["facts"] = {"passed": True}
        with self.assertRaises(VerificationError):
            self.verifier()[0].verify(
                replace(self.bundle, f0=signed(changed, "phase-b-f0"))
            )

    def test_journal_intent_preimage_and_b3_artifacts_are_recomputed(self) -> None:
        copied_path = self.fixture.root / "tampered-execution"
        shutil.copytree(self.execution_journal.directory, copied_path)
        copied = Journal(copied_path, clock=self.fixture.clock)
        records = copied.read_all()
        intent = next(
            item
            for item in records
            if item["kind"] == "intent" and item["action_id"].startswith("b2:")
        )
        intent["payload"]["preimage"]["inode"] += 1
        # Even recomputing a local record cannot repair the following chain head.
        path = copied.directory / f"{intent['sequence']:016d}.json"
        path.write_bytes(strict_json.canonical(intent) + b"\n")
        with self.assertRaises(VerificationError):
            self.verifier()[0].verify(replace(self.bundle, execution_journal=copied))

    def test_execution_journal_rejects_persistent_mask_as_runtime_fence(self) -> None:
        copied_path = self.fixture.root / "persistently-masked-execution"
        shutil.copytree(self.execution_journal.directory, copied_path)
        copied = Journal(copied_path, clock=self.fixture.clock)
        records = [deepcopy(item) for item in copied.read_all()]
        outcome = next(
            item
            for item in records
            if item["kind"] == "outcome" and item["action_id"].endswith(":mask")
        )
        outcome["payload"]["state"]["unit_file_state"] = "masked"
        changed_f0 = deepcopy(self.f0)
        previous = "sha256:" + "0" * 64
        for record in records[:-1]:
            record["previous_hash"] = previous
            unsigned = dict(record)
            unsigned.pop("record_hash")
            record["record_hash"] = strict_json.digest(unsigned)
            previous = record["record_hash"]
        changed_f0["journal_head"] = previous
        records[-1]["payload"] = {
            "artifact": changed_f0,
            "artifact_digest": strict_json.digest(changed_f0),
        }
        records[-1]["previous_hash"] = previous
        unsigned = dict(records[-1])
        unsigned.pop("record_hash")
        records[-1]["record_hash"] = strict_json.digest(unsigned)
        for record in records:
            (copied_path / f"{record['sequence']:016d}.json").write_bytes(
                strict_json.canonical(record) + b"\n"
            )
        with self.assertRaisesRegex(VerificationError, "actual-state vector"):
            _verify_execution_journal(
                copied,
                changed_f0,
                self.baseline,
                self.expectations,
                DictStore(self.fixture.artifacts),
                self.anchor,
                HMACSHA256Verifier(),
            )

    def test_byte_identical_reconstructions_are_rejected(self) -> None:
        identical = replace(
            self.bundle,
            reconstructions=(
                self.bundle.reconstructions[0],
                self.bundle.reconstructions[0],
            ),
        )
        verifier, _authority = self.verifier()
        with self.assertRaises(VerificationError):
            verifier.verify(identical)

    def test_reconstruction_requires_actual_monitor_logs_and_distinct_roots(
        self,
    ) -> None:
        changed = deepcopy(self.reconstructions[1])
        changed["execution_identity"] = self.reconstructions[0]["execution_identity"]
        receipt = deepcopy(self.receipt)
        receipt["reconstruction_digests"][1] = strict_json.digest(changed)
        bundle = replace(
            self.bundle,
            reconstructions=(
                self.bundle.reconstructions[0],
                signed(changed, "phase-b-reconstruction"),
            ),
            receipt=signed(receipt, "phase-b-receipt"),
        )
        with self.assertRaises(VerificationError):
            self.verifier()[0].verify(bundle)

    def test_verifier_rejects_unobserved_f0_start_gap(self) -> None:
        changed = deepcopy(self.observation)
        changed["observation_started_at"] = (
            (
                datetime.fromisoformat(changed["f0_at"].replace("Z", "+00:00"))
                + timedelta(minutes=5)
            )
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self.assertRaisesRegex(VerificationError, "duration/clocks"):
            _verify_observation(changed, self.baseline, self.f0, self.anchor)

    def test_receiver_reception_bounds_reject_future_and_buffered_sources(self) -> None:
        original_record = self.continuation_payload["source_records"][0]
        original_envelope = original_record["payload"]["envelope"]
        received = datetime.fromisoformat(
            original_record["payload"]["receiver_received_at"].replace("Z", "+00:00")
        )
        for observed in (
            received + timedelta(seconds=1),
            received - timedelta(seconds=121),
        ):
            with self.subTest(observed=observed):
                envelope = deepcopy(original_envelope)
                envelope["payload"]["observed_at"] = observed.isoformat().replace(
                    "+00:00", "Z"
                )
                record = deepcopy(original_record)
                record["payload"]["envelope"] = envelope
                unsigned = dict(record)
                unsigned.pop("record_hash")
                record["record_hash"] = strict_json.digest(unsigned)
                with self.assertRaises(VerificationError):
                    _verify_receiver_source_records(
                        [record],
                        [envelope],
                        first_sequence=record["sequence"],
                        previous_head=record["previous_hash"],
                        expected_head=record["record_hash"],
                        earliest=datetime.fromisoformat(
                            self.observation["observed_through_at"].replace(
                                "Z", "+00:00"
                            )
                        ),
                        latest=datetime.fromisoformat(
                            self.continuation_payload["extensions"][0][
                                "receiver_record"
                            ]["recorded_at"].replace("Z", "+00:00")
                        ),
                    )

    def test_continuation_rejects_receiver_monotonic_rollback(self) -> None:
        record = self.continuation_payload["source_records"][0]
        envelope = record["payload"]["envelope"]
        received = datetime.fromisoformat(
            record["payload"]["receiver_received_at"].replace("Z", "+00:00")
        )
        with self.assertRaises(VerificationError):
            _verify_receiver_source_records(
                [record],
                [envelope],
                first_sequence=record["sequence"],
                previous_head=record["previous_hash"],
                expected_head=record["record_hash"],
                earliest=received - timedelta(seconds=1),
                latest=received + timedelta(seconds=1),
                previous_monotonic=record["payload"]["receiver_received_monotonic"],
            )

    def test_second_receipt_refresh_extends_prior_receiver_chain_once(self) -> None:
        bundle, observed, head = self.second_refresh()
        authority = MemoryConsumption(observed, head, counter=1)
        authority.previous = strict_json.digest(self.receipt)
        result = Verifier(self.anchor, HMACSHA256Verifier(), authority).verify(bundle)
        self.assertEqual(result.receipt_consumptions, 2)
        with self.assertRaises(VerificationError):
            Verifier(self.anchor, HMACSHA256Verifier(), authority).verify(bundle)

    def test_four_minute_refresh_uses_two_periodic_receiver_attestations(self) -> None:
        bundle, now, head = self.second_refresh(
            wall_seconds=240, monotonic_seconds=240, rounds=2
        )
        authority = MemoryConsumption(now, head, counter=1)
        authority.previous = strict_json.digest(self.receipt)
        result = Verifier(self.anchor, HMACSHA256Verifier(), authority).verify(bundle)
        self.assertEqual(result.receipt_consumptions, 2)
        proof = bundle.continuation_proof
        assert proof is not None
        self.assertEqual(len(proof["payload"]["source_records"]), 2 * len(STREAMS))
        self.assertTrue(
            all(
                len(record["payload"]["envelope"]["payload"]["metadata"]["checkpoints"])
                == 1
                for record in proof["payload"]["source_records"]
            )
        )

    def test_refresh_rejects_blind_wall_gap_and_missing_or_stale_prior_chain(
        self,
    ) -> None:
        blind, observed, head = self.second_refresh(
            wall_seconds=300, monotonic_seconds=1
        )
        authority = MemoryConsumption(observed, head, counter=1)
        authority.previous = strict_json.digest(self.receipt)
        with self.assertRaises(VerificationError):
            Verifier(self.anchor, HMACSHA256Verifier(), authority).verify(blind)

        trailing, observed, head = self.second_refresh(trailing_seconds=2)
        authority = MemoryConsumption(observed, head, counter=1)
        authority.previous = strict_json.digest(self.receipt)
        with self.assertRaisesRegex(VerificationError, "not segment-bound"):
            Verifier(self.anchor, HMACSHA256Verifier(), authority).verify(trailing)

        valid, observed, head = self.second_refresh()
        missing = replace(valid, previous_continuations=())
        authority = MemoryConsumption(observed, head, counter=1)
        authority.previous = strict_json.digest(self.receipt)
        with self.assertRaises(VerificationError):
            Verifier(self.anchor, HMACSHA256Verifier(), authority).verify(missing)
        stale = MemoryConsumption(observed, self.receiver_head, counter=1)
        stale.previous = strict_json.digest(self.receipt)
        with self.assertRaises(VerificationError):
            Verifier(self.anchor, HMACSHA256Verifier(), stale).verify(valid)

    def test_continuation_nonce_transition_grant_and_cas_are_bound(self) -> None:
        changed = self.resign_receipt(self.bundle, requested_transition="CUTOVER")
        with self.assertRaises(VerificationError):
            self.verifier()[0].verify(changed)
        with self.assertRaises(VerificationError):
            self.verifier(head="sha256:" + "f" * 64)[0].verify(self.bundle)
        with self.assertRaises(VerificationError):
            self.verifier(age=301)[0].verify(self.bundle)
        wrong_accumulator = self.resign_receipt(
            self.bundle, verified_chain_accumulator="sha256:" + "f" * 64
        )
        with self.assertRaises(VerificationError):
            self.verifier()[0].verify(wrong_accumulator)

    def test_public_qualification_is_allowlisted(self) -> None:
        result = self.verifier()[0].verify(self.bundle)
        self.assertEqual(
            set(result.__dict__),
            {
                "attempt_id",
                "observed_through_at",
                "receipt_digest",
                "receipt_consumptions",
                "phase_b_fencing_qualification",
                "source_fence_baseline_contract",
                "external_route_identity",
                "route_ownership",
                "duplicate_scheduler_topology",
                "generic_route_reconstruction",
                "hermes_semantic_restore",
                "live_fencing_observation",
                "axis_remote_custody",
                "legacy_axis_new_work_writer",
                "canonical_axis_writer",
                "legacy_alpha0_authority",
                "home_generation_changed",
                "canonical_deployment_attestation",
                "canonical_composition_activated",
                "canonical_axis_control_active",
                "canonical_alpha0_active",
                "safe_drain_ready",
                "cutover_ready",
            },
        )
        self.assertNotIn("HERMES_TOKEN", repr(result))


if __name__ == "__main__":
    unittest.main()
