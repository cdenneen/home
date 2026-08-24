from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import ATTEMPT, Fixture, signed
from phase_b import strict_json
from phase_b.collector import (
    OBSERVATION_SECONDS,
    STREAMS,
    AcceleratedClock,
    Collector,
    CollectorError,
    Cursor,
)
from phase_b.journal import Journal
from phase_b.receiver import DurableReceiverState
from phase_b.trust import HMACSHA256Verifier


def identities(fixture: Fixture) -> dict[str, object]:
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
        "registry_digests": ["sha256:" + f"{index + 1:064x}" for index in range(6)],
        "collector_identity": "offhost-collector-001",
    }


def snapshot(fixture: Fixture) -> dict[str, object]:
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
        **identities(fixture),
    }


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Fixture()
        self.addCleanup(self.fixture.close)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.clock = AcceleratedClock(datetime(2026, 8, 20, tzinfo=timezone.utc))
        self.journal = Journal(
            Path(self.temporary.name) / "chain", clock=self.clock.wall
        )
        self.anchor = self.fixture.anchor()
        self.receiver = DurableReceiverState(
            Path(self.temporary.name) / "receiver",
            owner_uid=os.getuid(),
            secure_root=Path(self.temporary.name),
            wall=self.clock.wall,
            monotonic=self.clock.monotonic,
        )
        cursors = tuple(Cursor(name, 0, 0, f"cursor-{name}-0") for name in STREAMS)
        self.collector = Collector(
            ATTEMPT,
            self.journal,
            self.clock,
            cursors,
            identities(self.fixture),
            self.anchor,
            HMACSHA256Verifier(),
            self.fixture,
            self.receiver,
            receiver_artifact_writer=lambda records: self.fixture.put_artifact(
                list(records), prefix="receiver-chain"
            ),
        )

    def emit(
        self,
        stream: str,
        event_class: str,
        metadata: dict[str, object],
        *,
        offset: int | None = None,
        namespace: str | None = None,
    ) -> None:
        previous = self.collector.cursors[stream]
        payload = {
            "schema": "phase-b.source-event.v1",
            "attempt_id": ATTEMPT,
            "stream": stream,
            "generation": previous.generation,
            "offset": previous.offset + 1 if offset is None else offset,
            "previous_token": previous.token,
            "event_class": event_class,
            "observed_at": self.clock.wall().isoformat().replace("+00:00", "Z"),
            "observed_monotonic": self.clock.monotonic(),
            "metadata": metadata,
        }
        self.collector.append_event(
            signed(payload, namespace or f"phase-b-source-event.{stream}")
        )

    def open_all(self) -> None:
        for stream in STREAMS:
            self.emit(stream, "coverage-open", {"cursor_anchor": f"cursor-{stream}-0"})

    def _state_event(self, stream: str) -> dict[str, object]:
        return {
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
                "data": {
                    "registry_digests": identities(self.fixture)["registry_digests"]
                },
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
            "identity": {"type": "identity-snapshot", "data": identities(self.fixture)},
            "time": {
                "type": "time-anchor",
                "data": {
                    "wall_at": self.clock.wall().isoformat().replace("+00:00", "Z"),
                    "monotonic": self.clock.monotonic(),
                },
            },
        }[stream]

    def run_duration(self, *, forbidden_stream: str | None = None) -> None:
        forbidden = {
            "audit": {"type": "exec", "data": {}},
            "user-journal": {"type": "writer-invocation", "data": {}},
            "systemd": {"type": "unit-transition", "data": {}},
            "registry": {"type": "registry-write", "data": {}},
            "database": {"type": "database-write", "data": {}},
        }
        elapsed = 0.0
        number = 0
        while elapsed < OBSERVATION_SECONDS:
            step = min(120.0, OBSERVATION_SECONDS - elapsed)
            start = elapsed
            self.clock.advance(step)
            elapsed += step
            for stream in STREAMS:
                source_cursor = f"{stream}-source-{number:04d}"
                events = [self._state_event(stream)] if number == 0 else []
                if stream == forbidden_stream and number == 0:
                    events.append(forbidden[stream])
                batch = self.fixture.put_artifact(
                    {
                        "schema": "phase-b.raw-batch.v1",
                        "stream": stream,
                        "source_cursor": source_cursor,
                        "events": events,
                    },
                    prefix="raw",
                )
                received_at = self.clock.wall().isoformat().replace("+00:00", "Z")
                previous_ack = self.collector.ack_challenge(stream)
                receiver_ack = strict_json.digest(
                    {
                        "previous": previous_ack,
                        "stream": stream,
                        "source_cursor": source_cursor,
                        "batch_digest": batch["digest"],
                        "received_at": received_at,
                    }
                )
                checkpoint = {
                    "start_monotonic": start,
                    "end_monotonic": elapsed,
                    "received_at": received_at,
                    "source_cursor": source_cursor,
                    "previous_receiver_ack": previous_ack,
                    "receiver_ack": receiver_ack,
                    "batch": batch,
                    "event_count": len(events),
                    "lost": 0,
                    "backlog": 0,
                    "replay": 0,
                }
                self.emit(
                    stream, "continuity-checkpoints", {"checkpoints": [checkpoint]}
                )
            number += 1
            self.collector.sample(snapshot(self.fixture))

    def close_all(self) -> None:
        for stream in STREAMS:
            previous = self.collector.cursors[stream]
            self.emit(stream, "coverage-close", {"cursor_head": previous.token})
            previous = self.collector.cursors[stream]
            challenge = self.collector.ack_challenge(stream)
            self.emit(
                stream,
                "ack",
                {"cursor_token": previous.token, "receiver_head": challenge},
            )

    def test_observation_rejects_unobserved_f0_start_gap(self) -> None:
        root = Path(self.temporary.name)
        with self.assertRaisesRegex(CollectorError, "continuously from F0"):
            Collector(
                ATTEMPT,
                Journal(root / "late-chain", clock=self.clock.wall),
                self.clock,
                tuple(Cursor(name, 0, 0, f"late-{name}") for name in STREAMS),
                identities(self.fixture),
                self.anchor,
                HMACSHA256Verifier(),
                self.fixture,
                DurableReceiverState(
                    root / "late-receiver",
                    owner_uid=os.getuid(),
                    secure_root=root,
                    wall=self.clock.wall,
                    monotonic=self.clock.monotonic,
                ),
                f0_at=self.clock.wall() - timedelta(seconds=121),
                f0_digest="sha256:" + "1" * 64,
            )

    def test_delayed_collector_requires_receiver_attested_f0_coverage_open(
        self,
    ) -> None:
        f0_at = self.clock.wall()
        self.clock.advance(30)
        root = Path(self.temporary.name)
        collector = Collector(
            ATTEMPT,
            Journal(root / "delayed-chain", clock=self.clock.wall),
            self.clock,
            tuple(Cursor(name, 0, 0, f"delayed-{name}") for name in STREAMS),
            identities(self.fixture),
            self.anchor,
            HMACSHA256Verifier(),
            self.fixture,
            DurableReceiverState(
                root / "delayed-receiver",
                owner_uid=os.getuid(),
                secure_root=root,
                wall=self.clock.wall,
                monotonic=self.clock.monotonic,
            ),
            f0_at=f0_at,
            f0_digest="sha256:" + "2" * 64,
        )
        source_origins = {
            stream: 10_000.0 + index * 1_000
            for index, stream in enumerate(STREAMS)
        }
        for stream in STREAMS:
            previous = collector.cursors[stream]
            collector.append_event(
                signed(
                    {
                        "schema": "phase-b.source-event.v1",
                        "attempt_id": ATTEMPT,
                        "stream": stream,
                        "generation": previous.generation,
                        "offset": previous.offset + 1,
                        "previous_token": previous.token,
                        "event_class": "coverage-open",
                        "observed_at": f0_at.isoformat().replace("+00:00", "Z"),
                        "observed_monotonic": source_origins[stream],
                        "metadata": {"cursor_anchor": previous.token},
                    },
                    f"phase-b-source-event.{stream}",
                )
            )
        self.assertEqual(set(collector.coverage_open), set(STREAMS))
        self.assertEqual(collector.coverage_open, source_origins)
        self.assertNotIn(collector.started_monotonic, source_origins.values())
        for stream in STREAMS:
            previous = collector.cursors[stream]
            source_cursor = f"{stream}-delayed-start"
            batch = self.fixture.put_artifact(
                {
                    "schema": "phase-b.raw-batch.v1",
                    "stream": stream,
                    "source_cursor": source_cursor,
                    "events": [self._state_event(stream)],
                },
                prefix="delayed-raw",
            )
            received_at = self.clock.wall().isoformat().replace("+00:00", "Z")
            previous_ack = collector.ack_challenge(stream)
            receiver_ack = strict_json.digest(
                {
                    "previous": previous_ack,
                    "stream": stream,
                    "source_cursor": source_cursor,
                    "batch_digest": batch["digest"],
                    "received_at": received_at,
                }
            )
            collector.append_event(
                signed(
                    {
                        "schema": "phase-b.source-event.v1",
                        "attempt_id": ATTEMPT,
                        "stream": stream,
                        "generation": previous.generation,
                        "offset": previous.offset + 1,
                        "previous_token": previous.token,
                        "event_class": "continuity-checkpoints",
                        "observed_at": received_at,
                        "observed_monotonic": source_origins[stream] + 30,
                        "metadata": {
                            "checkpoints": [
                                {
                                    "start_monotonic": source_origins[stream],
                                    "end_monotonic": source_origins[stream] + 30,
                                    "received_at": received_at,
                                    "source_cursor": source_cursor,
                                    "previous_receiver_ack": previous_ack,
                                    "receiver_ack": receiver_ack,
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
        self.assertEqual(
            collector.continuity_end,
            {stream: origin + 30 for stream, origin in source_origins.items()},
        )

    def test_coverage_open_with_gap_after_f0_is_rejected(self) -> None:
        previous = self.collector.cursors["audit"]
        payload = {
            "schema": "phase-b.source-event.v1",
            "attempt_id": ATTEMPT,
            "stream": "audit",
            "generation": previous.generation,
            "offset": previous.offset + 1,
            "previous_token": previous.token,
            "event_class": "coverage-open",
            "observed_at": (self.clock.wall() + timedelta(seconds=2))
            .isoformat()
            .replace("+00:00", "Z"),
            "observed_monotonic": self.clock.monotonic() + 2,
            "metadata": {"cursor_anchor": previous.token},
        }
        with self.assertRaisesRegex(CollectorError, "begin at signed F0"):
            self.collector.append_event(signed(payload, "phase-b-source-event.audit"))

    def test_complete_accelerated_authenticated_observation(self) -> None:
        self.open_all()
        self.run_duration()
        self.close_all()
        artifact = self.collector.finish()
        self.assertEqual(artifact["invalidations"], [])
        self.assertEqual(
            artifact["derived"],
            {"forbidden_recurrence_count": 0, "history_complete": True},
        )
        self.assertEqual(artifact["chain_head"], self.journal.head())

    def test_raw_forbidden_event_invalidates_despite_zero_summaries(self) -> None:
        self.open_all()
        self.run_duration(forbidden_stream="audit")
        self.close_all()
        artifact = self.collector.finish()
        self.assertTrue(
            any("forbidden:audit:exec" in item for item in artifact["invalidations"])
        )

    def test_heartbeat_only_never_proves_history_complete(self) -> None:
        self.open_all()
        for stream in STREAMS:
            self.emit(
                stream, "heartbeat", {"source_state_digest": "sha256:" + "a" * 64}
            )
        elapsed = 0.0
        while elapsed < OBSERVATION_SECONDS:
            step = min(120.0, OBSERVATION_SECONDS - elapsed)
            self.clock.advance(step)
            elapsed += step
            self.collector.sample(snapshot(self.fixture))
        self.close_all()
        artifact = self.collector.finish()
        self.assertTrue(
            any(
                reason.startswith("mandatory-event-classes-missing")
                for reason in artifact["invalidations"]
            )
        )

    def test_cross_role_signature_and_unsigned_event_fail(self) -> None:
        previous = self.collector.cursors["audit"]
        payload = {
            "schema": "phase-b.source-event.v1",
            "attempt_id": ATTEMPT,
            "stream": "audit",
            "generation": 0,
            "offset": 1,
            "previous_token": previous.token,
            "event_class": "coverage-open",
            "observed_at": self.clock.wall().isoformat().replace("+00:00", "Z"),
            "observed_monotonic": 0.0,
            "metadata": {"cursor_anchor": previous.token},
        }
        with self.assertRaises(CollectorError):
            self.collector.append_event(signed(payload, "phase-b-source-event.systemd"))
        with self.assertRaises(CollectorError):
            self.collector.append_event({"payload": payload})

    def test_periodic_checkpoint_gap_is_terminal(self) -> None:
        self.emit("audit", "coverage-open", {"cursor_anchor": "cursor-audit-0"})
        self.clock.advance(121)
        receiver_ack = self.collector.ack_challenge("audit")
        batch = self.fixture.put_artifact(
            {
                "schema": "phase-b.raw-batch.v1",
                "stream": "audit",
                "source_cursor": "audit-gap-1",
                "events": [
                    {
                        "type": "process-snapshot",
                        "data": {
                            "writers": 0,
                            "reprovisioners": 0,
                            "effect_capable_descendants": 0,
                        },
                    }
                ],
            },
            prefix="raw",
        )
        received_at = self.clock.wall().isoformat().replace("+00:00", "Z")
        next_ack = strict_json.digest(
            {
                "previous": receiver_ack,
                "stream": "audit",
                "source_cursor": "audit-gap-1",
                "batch_digest": batch["digest"],
                "received_at": received_at,
            }
        )
        self.emit(
            "audit",
            "continuity-checkpoints",
            {
                "checkpoints": [
                    {
                        "start_monotonic": 0,
                        "end_monotonic": 121,
                        "received_at": received_at,
                        "source_cursor": "audit-gap-1",
                        "previous_receiver_ack": receiver_ack,
                        "receiver_ack": next_ack,
                        "batch": batch,
                        "event_count": 1,
                        "lost": 0,
                        "backlog": 0,
                        "replay": 0,
                    }
                ]
            },
        )
        self.assertIn("continuous-source-gap", self.collector.invalidations)

    def test_gap_replay_and_source_loss_are_terminal(self) -> None:
        with self.assertRaises(CollectorError):
            self.emit(
                "audit", "coverage-open", {"cursor_anchor": "cursor-audit-0"}, offset=2
            )
        self.assertTrue(self.collector.invalidations)
        self.collector.disconnected()
        self.collector.provider_history_unavailable()
        self.assertIn("collector-disconnect", self.collector.invalidations)
        self.assertIn("provider-history-unavailable", self.collector.invalidations)

    def test_signed_terminal_and_recurrence_events_invalidate(self) -> None:
        self.emit("audit", "coverage-open", {"cursor_anchor": "cursor-audit-0"})
        self.emit(
            "audit",
            "exec-snapshot",
            {"writers": 1, "reprovisioners": 0, "effect_capable_descendants": 0},
        )
        self.emit("audit", "evidence-loss", {"reason_code": "cursor-evicted"})
        self.assertIn("forbidden-process-recurrence", self.collector.invalidations)
        self.assertTrue(
            any(
                "terminal-source-event" in reason
                for reason in self.collector.invalidations
            )
        )

    def test_identity_or_route_alias_drift_invalidates(self) -> None:
        self.emit(
            "provider-route",
            "coverage-open",
            {"cursor_anchor": "cursor-provider-route-0"},
        )
        self.emit(
            "provider-route",
            "route-ownership",
            {
                "generic_route_identity": "generic-route-001",
                "alpha0_route_identity": "generic-route-001",
                "dedicated_axis_route": "ABSENT",
            },
        )
        self.assertIn("provider-route-ownership-drift", self.collector.invalidations)


if __name__ == "__main__":
    unittest.main()
