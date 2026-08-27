"""Off-host authenticated, gap-free continuous evidence collector."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from . import strict_json
from .artifacts import ArtifactError, ArtifactStore, read_json_artifact
from .journal import Journal, format_time
from .strict_json import digest, exact_object
from .trust import (
    SignatureVerifier,
    TrustAnchor,
    TrustError,
    require_safe_attempt_id,
    verify_envelope,
)

OBSERVATION_SECONDS = 24 * 60 * 60 + 15 * 60
SAMPLE_INTERVAL_SECONDS = 120
CONTINUITY_INTERVAL_SECONDS = 120
CLOCK_TOLERANCE_SECONDS = 1.0
STREAMS = (
    "audit",
    "user-journal",
    "systemd",
    "registry",
    "database",
    "provider-route",
    "custody",
    "identity",
    "time",
)
MANDATORY_RAW_TYPES = {
    "audit": frozenset({"process-snapshot"}),
    "user-journal": frozenset({"journal-cursor"}),
    "systemd": frozenset({"systemd-cursor"}),
    "registry": frozenset({"registry-snapshot"}),
    "database": frozenset({"database-snapshot"}),
    "provider-route": frozenset({"route-snapshot"}),
    "custody": frozenset({"custody-snapshot"}),
    "identity": frozenset({"identity-snapshot"}),
    "time": frozenset({"time-anchor"}),
}
MANDATORY_EVENT_CLASSES = {
    "audit": frozenset(
        {"coverage-open", "continuity-checkpoints", "coverage-close", "ack"}
    ),
    "user-journal": frozenset(
        {
            "coverage-open",
            "continuity-checkpoints",
            "coverage-close",
            "ack",
        }
    ),
    "systemd": frozenset(
        {"coverage-open", "continuity-checkpoints", "coverage-close", "ack"}
    ),
    "registry": frozenset(
        {"coverage-open", "continuity-checkpoints", "coverage-close", "ack"}
    ),
    "database": frozenset(
        {"coverage-open", "continuity-checkpoints", "coverage-close", "ack"}
    ),
    "provider-route": frozenset(
        {"coverage-open", "continuity-checkpoints", "coverage-close", "ack"}
    ),
    "custody": frozenset(
        {"coverage-open", "continuity-checkpoints", "coverage-close", "ack"}
    ),
    "identity": frozenset(
        {"coverage-open", "continuity-checkpoints", "coverage-close", "ack"}
    ),
    "time": frozenset(
        {"coverage-open", "continuity-checkpoints", "coverage-close", "ack"}
    ),
}
TERMINAL_EVENT_CLASSES = frozenset(
    {"disconnect", "evidence-loss", "provider-history-loss"}
)
IDENTITY_FIELDS = frozenset(
    {
        "host_identity",
        "machine_id",
        "boot_id",
        "user_manager_id",
        "home_generation",
        "generic_route_identity",
        "generic_service_identity",
        "generic_session_identity",
        "generic_profile_identity",
        "alpha0_route_identity",
        "alpha0_service_identity",
        "alpha0_session_identity",
        "alpha0_profile_identity",
        "dedicated_axis_route",
        "frontier_digest",
        "registry_digests",
        "collector_identity",
    }
)
SAMPLE_FIELDS = frozenset(
    {
        "legacy_axis_new_work_writers",
        "legacy_axis_reprovisioners",
        "effect_capable_descendants",
        "canonical_writers",
        "pending_local_effects",
        "stuck_watchdog_state",
        "custody_remote",
        "custody_total",
        "unknowns",
        *IDENTITY_FIELDS,
    }
)
EVENT_FIELDS = frozenset(
    {
        "schema",
        "attempt_id",
        "stream",
        "generation",
        "offset",
        "previous_token",
        "event_class",
        "observed_at",
        "observed_monotonic",
        "metadata",
    }
)


class CollectorError(RuntimeError):
    pass


class Clock(Protocol):
    def wall(self) -> datetime: ...

    def monotonic(self) -> float: ...


class Receiver(Protocol):
    def append_source(self, sequence: int, envelope_bytes: bytes) -> str: ...

    def current_head(self, attempt_id: str) -> str: ...

    def export_records(self) -> tuple[dict[str, Any], ...]: ...


class SystemClock:
    def wall(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()


class AcceleratedClock:
    def __init__(self, start: datetime):
        if start.tzinfo is None or start.utcoffset() is None:
            raise CollectorError("accelerated clock start must be timezone-aware")
        self._wall = start.astimezone(timezone.utc)
        self._monotonic = 0.0

    def wall(self) -> datetime:
        return self._wall

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float, *, wall_seconds: float | None = None) -> None:
        if seconds < 0:
            raise CollectorError("monotonic clock cannot move backwards")
        self._monotonic += seconds
        self._wall += timedelta(
            seconds=seconds if wall_seconds is None else wall_seconds
        )


@dataclass(frozen=True)
class Cursor:
    stream: str
    generation: int
    offset: int
    token: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "stream": self.stream,
            "generation": self.generation,
            "offset": self.offset,
            "token": self.token,
        }


def derive_raw_batch(
    stream: str, value: Any, identities: dict[str, Any]
) -> tuple[set[str], list[str]]:
    """Strictly parse raw sensor records and derive recurrence/drift failures."""
    raw = exact_object(
        value, {"schema", "stream", "source_cursor", "events"}, "raw batch"
    )
    if raw["schema"] != "phase-b.raw-batch.v1" or raw["stream"] != stream:
        raise CollectorError("raw batch identity/schema mismatch")
    if not isinstance(raw["source_cursor"], str) or not raw["source_cursor"]:
        raise CollectorError("raw batch source cursor is invalid")
    if not isinstance(raw["events"], list):
        raise CollectorError("raw batch events are not an array")
    seen: set[str] = set()
    failures: list[str] = []
    forbidden = {
        "exec",
        "writer-invocation",
        "recovery-invocation",
        "scheduler-claim",
        "unit-transition",
        "registry-write",
        "database-write",
        "canonical-writer",
    }
    allowed = {
        "audit": {"process-snapshot", "exec", "scheduler-claim"},
        "user-journal": {"journal-cursor", "writer-invocation", "recovery-invocation"},
        "systemd": {"systemd-cursor", "unit-transition"},
        "registry": {"registry-snapshot", "registry-write"},
        "database": {"database-snapshot", "database-write", "canonical-writer"},
        "provider-route": {"route-snapshot"},
        "custody": {"custody-snapshot"},
        "identity": {"identity-snapshot"},
        "time": {"time-anchor"},
    }[stream]
    for event in raw["events"]:
        item = exact_object(event, {"type", "data"}, "raw event")
        event_type = item["type"]
        if event_type not in allowed or not isinstance(item["data"], dict):
            raise CollectorError("raw batch contains an unknown typed event")
        seen.add(event_type)
        if event_type in forbidden:
            failures.append(f"forbidden:{stream}:{event_type}")
        elif event_type == "process-snapshot":
            expected = {
                "writers": 0,
                "reprovisioners": 0,
                "effect_capable_descendants": 0,
            }
            if item["data"] != expected:
                failures.append("process-recurrence")
        elif event_type in {"journal-cursor", "systemd-cursor"}:
            if set(item["data"]) != {"cursor"} or not item["data"]["cursor"]:
                failures.append("source-cursor")
        elif event_type == "registry-snapshot":
            if item["data"] != {"registry_digests": identities["registry_digests"]}:
                failures.append("registry-drift")
        elif event_type == "database-snapshot":
            if item["data"] != {"pending": 0, "inflight": 0, "local_only": 0}:
                failures.append("database-pending")
        elif event_type == "route-snapshot":
            expected = {
                "generic_route_identity": identities["generic_route_identity"],
                "alpha0_route_identity": identities["alpha0_route_identity"],
                "dedicated_axis_route": "ABSENT",
            }
            if item["data"] != expected:
                failures.append("route-drift")
        elif event_type == "custody-snapshot":
            expected = {
                "remote": 9,
                "total": 9,
                "pending": 0,
                "inflight": 0,
                "local_only": 0,
                "frontier_digest": identities["frontier_digest"],
            }
            if item["data"] != expected:
                failures.append("custody-regression")
        elif event_type == "identity-snapshot" and item["data"] != identities:
            failures.append("identity-drift")
        elif event_type == "time-anchor" and (
            set(item["data"]) != {"wall_at", "monotonic"}
            or isinstance(item["data"].get("monotonic"), bool)
            or not isinstance(item["data"].get("monotonic"), (int, float))
        ):
            failures.append("time-anchor")
    return seen, failures


class Collector:
    def __init__(
        self,
        attempt_id: str,
        journal: Journal,
        clock: Clock,
        starting_cursors: tuple[Cursor, ...],
        identities: dict[str, Any],
        anchor: TrustAnchor,
        signature_verifier: SignatureVerifier,
        artifact_store: ArtifactStore | None = None,
        receiver: Receiver | None = None,
        *,
        f0_at: datetime | None = None,
        f0_digest: str | None = None,
        receiver_artifact_writer: Callable[[tuple[dict[str, Any], ...]], dict[str, Any]]
        | None = None,
    ):
        require_safe_attempt_id(attempt_id)
        if tuple(item.stream for item in starting_cursors) != STREAMS:
            raise CollectorError("all exact source cursors are required in fixed order")
        if len({item.token for item in starting_cursors}) != len(STREAMS):
            raise CollectorError("source cursors must be distinct")
        if set(identities) != IDENTITY_FIELDS:
            raise CollectorError("observation identities are incomplete")
        if (
            not isinstance(identities["registry_digests"], list)
            or len(identities["registry_digests"]) != 6
        ):
            raise CollectorError("six registry digests are required")
        if identities["collector_identity"] != anchor.collector_identity:
            raise CollectorError("collector identity is not anchored")
        if identities["dedicated_axis_route"] != "ABSENT":
            raise CollectorError("dedicated AXIS route must be absent")
        self.attempt_id = attempt_id
        self.journal = journal
        self.clock = clock
        self.anchor = anchor
        self.signature_verifier = signature_verifier
        self.artifact_store = artifact_store
        self.receiver = receiver
        self.receiver_artifact_writer = receiver_artifact_writer
        self.receiver_sequence = 0
        self.receiver_head = receiver.current_head(attempt_id) if receiver else None
        self.cursors = {item.stream: item for item in starting_cursors}
        self.starting_cursors = starting_cursors
        self.identities = identities
        actual_started_wall = clock.wall()
        actual_started_monotonic = clock.monotonic()
        self.f0_at = (f0_at or actual_started_wall).astimezone(timezone.utc)
        self.f0_digest = f0_digest or digest(
            {"attempt_id": attempt_id, "f0_at": format_time(self.f0_at)}
        )
        f0_delay = (actual_started_wall - self.f0_at).total_seconds()
        if not 0 <= f0_delay <= 120 or not self.f0_digest.startswith("sha256:"):
            raise CollectorError("observation did not start continuously from F0")
        # Source sensors retain signed checkpoints for this bounded restart window.
        self.started_wall = self.f0_at
        self.started_monotonic = actual_started_monotonic - f0_delay
        self.observation_started_at = actual_started_wall
        self.last_wall = actual_started_wall
        self.last_monotonic = actual_started_monotonic
        self.last_sample_monotonic = actual_started_monotonic
        self.sample_count = 0
        self.invalidations: list[str] = []
        self.classes = {stream: set() for stream in STREAMS}
        self.coverage_open: dict[str, float] = {}
        self.coverage_close: dict[str, float] = {}
        self.continuity_end: dict[str, float] = {}
        self.raw_types = {stream: set() for stream in STREAMS}
        self.journal.append(
            "checkpoint",
            "observation-start",
            {
                "attempt_id": attempt_id,
                "f0_at": format_time(self.f0_at),
                "f0_digest": self.f0_digest,
                "f0_monotonic": self.started_monotonic,
                "observation_started_at": format_time(self.observation_started_at),
                "starting_cursors": [item.as_dict() for item in starting_cursors],
                "identity_digest": digest(identities),
                "collector_identity": anchor.collector_identity,
            },
        )

    def _invalidate(self, reason: str) -> None:
        if reason not in self.invalidations:
            self.invalidations.append(reason)
            self.journal.append("invalidation", "observation", {"reason": reason})

    def _check_clock(self) -> None:
        wall, monotonic = self.clock.wall(), self.clock.monotonic()
        wall_delta = (wall - self.last_wall).total_seconds()
        monotonic_delta = monotonic - self.last_monotonic
        if (
            monotonic_delta < 0
            or wall_delta < 0
            or abs(wall_delta - monotonic_delta) > CLOCK_TOLERANCE_SECONDS
        ):
            self._invalidate("clock-discontinuity")
        self.last_wall, self.last_monotonic = wall, monotonic

    @staticmethod
    def _parse_time(value: Any) -> datetime:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise CollectorError("source event time is not canonical UTC")
        try:
            result = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise CollectorError("source event time is invalid") from exc
        return result.astimezone(timezone.utc)

    def ack_challenge(self, stream: str) -> str:
        if stream not in STREAMS:
            raise CollectorError("unknown source stream")
        return self.receiver_head or self.journal.head()

    def _metadata(
        self, stream: str, event_class: str, value: Any, previous: Cursor
    ) -> None:
        if not isinstance(value, dict):
            raise CollectorError("event metadata must be an object")
        if event_class == "coverage-open":
            item = exact_object(value, {"cursor_anchor"}, "coverage open")
            if (
                item["cursor_anchor"]
                != self.starting_cursors[STREAMS.index(stream)].token
                or stream in self.coverage_open
            ):
                raise CollectorError(
                    "coverage opening cursor is not the signed baseline anchor"
                )
        elif event_class == "coverage-close":
            item = exact_object(value, {"cursor_head"}, "coverage close")
            if (
                item["cursor_head"] != previous.token
                or stream not in self.coverage_open
            ):
                raise CollectorError(
                    "coverage close does not bind the current source head"
                )
        elif event_class == "ack":
            item = exact_object(value, {"cursor_token", "receiver_head"}, "source ack")
            if item["cursor_token"] != previous.token or item["receiver_head"] != (
                self.receiver_head or self.journal.head()
            ):
                raise CollectorError(
                    "source ack does not bind current source and receiver heads"
                )
        elif event_class == "continuity-checkpoints":
            item = exact_object(value, {"checkpoints"}, "continuity checkpoints")
            checkpoints = item["checkpoints"]
            if (
                self.artifact_store is None
                or not isinstance(checkpoints, list)
                or not checkpoints
                or len(checkpoints) > 2048
            ):
                raise CollectorError("continuity checkpoint batch is unavailable")
            source_origin = self.coverage_open.get(stream)
            if source_origin is None:
                self._invalidate("continuity-before-coverage-open")
                raise CollectorError("continuity begins before source coverage opens")
            prior_end = self.continuity_end.get(stream, source_origin)
            prior_receiver_time: datetime | None = None
            receiver_ack = self.receiver_head or self.journal.head()
            seen_cursors: set[str] = set()
            for checkpoint in checkpoints:
                raw = exact_object(
                    checkpoint,
                    {
                        "start_monotonic",
                        "end_monotonic",
                        "received_at",
                        "source_cursor",
                        "previous_receiver_ack",
                        "receiver_ack",
                        "batch",
                        "event_count",
                        "lost",
                        "backlog",
                        "replay",
                    },
                    "continuity checkpoint",
                )
                start, end = raw["start_monotonic"], raw["end_monotonic"]
                if any(
                    isinstance(number, bool) or not isinstance(number, (int, float))
                    for number in (start, end)
                ):
                    raise CollectorError("continuity interval is not numeric")
                if (
                    abs(float(start) - float(prior_end)) > CLOCK_TOLERANCE_SECONDS
                    or not 0 < float(end) - float(start) <= CONTINUITY_INTERVAL_SECONDS
                ):
                    self._invalidate("continuous-source-gap")
                received = self._parse_time(raw["received_at"])
                expected_received = self.f0_at + timedelta(
                    seconds=float(end) - source_origin
                )
                if (
                    prior_receiver_time is not None
                    and received <= prior_receiver_time
                    or received
                    > self.clock.wall() + timedelta(seconds=CLOCK_TOLERANCE_SECONDS)
                    or abs((received - expected_received).total_seconds())
                    > CLOCK_TOLERANCE_SECONDS
                ):
                    self._invalidate("receiver-time-discontinuity")
                prior_receiver_time = received
                expected_ack = digest(
                    {
                        "previous": receiver_ack,
                        "stream": stream,
                        "source_cursor": raw["source_cursor"],
                        "batch_digest": raw["batch"].get("digest")
                        if isinstance(raw["batch"], dict)
                        else None,
                        "received_at": raw["received_at"],
                    }
                )
                if (
                    raw["previous_receiver_ack"] != receiver_ack
                    or raw["receiver_ack"] != expected_ack
                    or not isinstance(raw["source_cursor"], str)
                    or not raw["source_cursor"]
                    or raw["source_cursor"] in seen_cursors
                    or any(raw[key] != 0 for key in ("lost", "backlog", "replay"))
                    or isinstance(raw["event_count"], bool)
                    or not isinstance(raw["event_count"], int)
                    or raw["event_count"] < 0
                ):
                    self._invalidate("source-loss-backlog-replay-or-ack")
                seen_cursors.add(raw["source_cursor"])
                try:
                    _ref, batch = read_json_artifact(
                        self.artifact_store, raw["batch"], owner_only=True
                    )
                    seen, failures = derive_raw_batch(stream, batch, self.identities)
                except (ArtifactError, CollectorError, ValueError) as exc:
                    self._invalidate("raw-batch-invalid")
                    raise CollectorError("raw source batch is invalid") from exc
                if (
                    batch["source_cursor"] != raw["source_cursor"]
                    or len(batch["events"]) != raw["event_count"]
                ):
                    self._invalidate("raw-batch-cursor-or-count-mismatch")
                self.raw_types[stream].update(seen)
                for failure in failures:
                    self._invalidate(failure)
                prior_end = float(end)
                receiver_ack = expected_ack
            self.continuity_end[stream] = float(prior_end)
        elif event_class == "rotation":
            if value != {"complete": True}:
                raise CollectorError("source rotation is not complete")
        elif event_class == "heartbeat":
            item = exact_object(value, {"source_state_digest"}, "heartbeat")
            if not isinstance(item["source_state_digest"], str) or not item[
                "source_state_digest"
            ].startswith("sha256:"):
                raise CollectorError("heartbeat has no source-state digest")
        elif event_class in TERMINAL_EVENT_CLASSES:
            exact_object(value, {"reason_code"}, "terminal event")
        elif event_class == "exec-snapshot":
            item = exact_object(
                value,
                {"writers", "reprovisioners", "effect_capable_descendants"},
                "exec snapshot",
            )
            if any(item[key] != 0 for key in item):
                self._invalidate("forbidden-process-recurrence")
        elif event_class == "scheduler-claim":
            item = exact_object(value, {"claim_count"}, "scheduler claim")
            if item["claim_count"] != 0:
                self._invalidate("scheduler-claim-recurrence")
        elif event_class in {"writer-invocation", "recovery-invocation"}:
            item = exact_object(value, {"invocation_count"}, event_class)
            if item["invocation_count"] != 0:
                self._invalidate(f"{event_class}-recurrence")
        elif event_class == "unit-transition":
            item = exact_object(
                value, {"forbidden_transition_count"}, "unit transition"
            )
            if item["forbidden_transition_count"] != 0:
                self._invalidate("forbidden-unit-transition")
        elif event_class == "registry-write":
            item = exact_object(
                value,
                {"registry_digests", "unauthorized_write_count"},
                "registry writes",
            )
            if (
                item["registry_digests"] != self.identities["registry_digests"]
                or item["unauthorized_write_count"] != 0
            ):
                self._invalidate("registry-write-or-digest-drift")
        elif event_class == "database-write":
            item = exact_object(
                value,
                {"pending", "inflight", "local_only", "unauthorized_write_count"},
                "database writes",
            )
            if any(item[key] != 0 for key in item):
                self._invalidate("database-effect-recurrence")
        elif event_class == "route-ownership":
            item = exact_object(
                value,
                {
                    "generic_route_identity",
                    "alpha0_route_identity",
                    "dedicated_axis_route",
                },
                "route ownership",
            )
            if any(item[key] != self.identities[key] for key in item):
                self._invalidate("provider-route-ownership-drift")
        elif event_class == "custody-read":
            item = exact_object(
                value,
                {
                    "remote",
                    "total",
                    "pending",
                    "inflight",
                    "local_only",
                    "frontier_digest",
                },
                "custody read",
            )
            if item != {
                "remote": 9,
                "total": 9,
                "pending": 0,
                "inflight": 0,
                "local_only": 0,
                "frontier_digest": self.identities["frontier_digest"],
            }:
                self._invalidate("custody-regression")
        elif event_class == "identity-snapshot":
            item = exact_object(value, set(IDENTITY_FIELDS), "identity snapshot")
            if item != self.identities:
                self._invalidate("identity-drift")
        elif event_class == "time-anchor":
            item = exact_object(value, {"wall_at", "monotonic"}, "time anchor")
            self._parse_time(item["wall_at"])
            if isinstance(item["monotonic"], bool) or not isinstance(
                item["monotonic"], (int, float)
            ):
                raise CollectorError("time anchor monotonic value is invalid")
        else:
            raise CollectorError("event class is not allowed for Phase B")

    def append_event(self, envelope: dict[str, Any]) -> Cursor:
        self._check_clock()
        # Peek only to select a fixed namespace; authenticity is checked before use.
        if not isinstance(envelope, dict) or not isinstance(
            envelope.get("payload"), dict
        ):
            raise CollectorError("source event envelope is malformed")
        stream = envelope["payload"].get("stream")
        if stream not in STREAMS:
            raise CollectorError("unknown source stream")
        namespace = f"phase-b-source-event.{stream}"
        try:
            event = verify_envelope(
                envelope, self.anchor, self.signature_verifier, namespace
            )
            exact_object(event, set(EVENT_FIELDS), "source event")
        except (TrustError, ValueError) as exc:
            self._invalidate("unauthenticated-source-event")
            raise CollectorError("source event signature/shape is invalid") from exc
        if (
            event["schema"] != "phase-b.source-event.v1"
            or event["attempt_id"] != self.attempt_id
        ):
            raise CollectorError("source event attempt/schema mismatch")
        previous = self.cursors[stream]
        for key in ("generation", "offset"):
            if (
                isinstance(event[key], bool)
                or not isinstance(event[key], int)
                or event[key] < 0
            ):
                raise CollectorError("source cursor is not an unsigned integer")
        same = (
            event["generation"] == previous.generation
            and event["offset"] == previous.offset + 1
        )
        rotation = (
            event["generation"] == previous.generation + 1
            and event["offset"] == 0
            and event["event_class"] == "rotation"
        )
        if event["previous_token"] != previous.token:
            self._invalidate("cursor-predecessor-mismatch")
            raise CollectorError("source cursor predecessor mismatch")
        if not same and not rotation:
            self._invalidate("cursor-gap-replay-rollback-or-rotation-loss")
            raise CollectorError("source cursor is not exactly continuous")
        observed = self._parse_time(event["observed_at"])
        monotonic = event["observed_monotonic"]
        if isinstance(monotonic, bool) or not isinstance(monotonic, (int, float)):
            raise CollectorError("source monotonic time is invalid")
        if observed < self.started_wall or monotonic < 0:
            self._invalidate("source-time-rollback")
        if event["event_class"] == "coverage-open" and observed != self.f0_at:
            self._invalidate("coverage-open-does-not-begin-at-f0")
            raise CollectorError("coverage opening does not begin at signed F0")
        self._metadata(stream, event["event_class"], event["metadata"], previous)
        token = digest(envelope)
        if self.receiver is not None:
            self.receiver_head = self.receiver.append_source(
                self.receiver_sequence, strict_json.canonical(envelope)
            )
            self.receiver_sequence += 1
        self.journal.append(
            "checkpoint",
            f"source:{stream}:{event['generation']}:{event['offset']}",
            {"envelope": envelope, "token": token},
        )
        cursor = Cursor(stream, event["generation"], event["offset"], token)
        self.cursors[stream] = cursor
        self.classes[stream].add(event["event_class"])
        if event["event_class"] == "coverage-open":
            self.coverage_open[stream] = float(monotonic)
        elif event["event_class"] == "coverage-close":
            self.coverage_close[stream] = float(monotonic)
        elif event["event_class"] in TERMINAL_EVENT_CLASSES:
            self._invalidate(f"terminal-source-event:{stream}:{event['event_class']}")
        return cursor

    def disconnected(self) -> None:
        self._check_clock()
        self._invalidate("collector-disconnect")

    def provider_history_unavailable(self) -> None:
        self._check_clock()
        self._invalidate("provider-history-unavailable")

    def sample(self, snapshot: dict[str, Any]) -> None:
        """Supplementary poll; continuous signed source streams remain mandatory."""
        self._check_clock()
        if set(snapshot) != SAMPLE_FIELDS:
            self._invalidate("unknown-or-incomplete-sample")
            raise CollectorError("sample is not complete")
        now = self.clock.monotonic()
        if now - self.last_sample_monotonic > SAMPLE_INTERVAL_SECONDS:
            self._invalidate("sample-gap")
        self.last_sample_monotonic = now
        if any(
            snapshot[key] != 0
            for key in (
                "legacy_axis_new_work_writers",
                "legacy_axis_reprovisioners",
                "effect_capable_descendants",
                "canonical_writers",
                "pending_local_effects",
            )
        ):
            self._invalidate("writer-reprovisioner-descendant-or-effect-recurrence")
        if snapshot["custody_remote"] != 9 or snapshot["custody_total"] != 9:
            self._invalidate("custody-regression")
        if snapshot["stuck_watchdog_state"] != "healthy" or snapshot["unknowns"] != []:
            self._invalidate("consequential-unknown-or-watchdog-failure")
        if any(snapshot[key] != self.identities[key] for key in IDENTITY_FIELDS):
            self._invalidate("identity-drift")
        self.sample_count += 1
        self.journal.append(
            "checkpoint",
            f"sample:{self.sample_count:06d}",
            {
                "sampled_at": format_time(self.clock.wall()),
                "elapsed_monotonic": now - self.started_monotonic,
                "snapshot": snapshot,
                "snapshot_digest": digest(snapshot),
            },
        )

    def finish(self) -> dict[str, Any]:
        self._check_clock()
        elapsed = self.clock.monotonic() - self.started_monotonic
        if elapsed < OBSERVATION_SECONDS:
            self._invalidate("observation-too-short")
        if (
            self.sample_count == 0
            or elapsed - (self.last_sample_monotonic - self.started_monotonic)
            > SAMPLE_INTERVAL_SECONDS
        ):
            self._invalidate("no-samples-or-sample-gap")
        coverage: dict[str, Any] = {}
        for stream in STREAMS:
            missing = MANDATORY_EVENT_CLASSES[stream] - self.classes[stream]
            if missing:
                self._invalidate(f"mandatory-event-classes-missing:{stream}")
            start, end = self.coverage_open.get(stream), self.coverage_close.get(stream)
            continuity_end = self.continuity_end.get(stream)
            if (
                start is None
                or end is None
                or continuity_end is None
                or abs(end - continuity_end) > CLOCK_TOLERANCE_SECONDS
                or end - start < OBSERVATION_SECONDS
            ):
                self._invalidate(f"continuous-coverage-too-short:{stream}")
            if not MANDATORY_RAW_TYPES[stream] <= self.raw_types[stream]:
                self._invalidate(f"mandatory-raw-state-missing:{stream}")
            coverage[stream] = {
                "start_monotonic": start,
                "end_monotonic": end,
                "event_classes": sorted(self.classes[stream]),
                "raw_event_types": sorted(self.raw_types[stream]),
            }
        receiver_custody: dict[str, Any] | None = None
        if self.receiver is not None:
            if self.receiver_artifact_writer is None:
                raise CollectorError("receiver custody artifact writer is unavailable")
            records = self.receiver.export_records()
            if len(records) != self.receiver_sequence:
                raise CollectorError("receiver custody sequence is incomplete")
            receiver_custody = {
                "head": self.receiver_head,
                "sequence": self.receiver_sequence,
                "records": self.receiver_artifact_writer(records),
            }
        artifact = {
            "schema": "phase-b.observation.v2",
            "attempt_id": self.attempt_id,
            "f0_at": format_time(self.f0_at),
            "f0_digest": self.f0_digest,
            "observation_started_at": format_time(self.observation_started_at),
            "observed_through_at": format_time(self.clock.wall()),
            "elapsed_monotonic": elapsed,
            "sample_count": self.sample_count,
            "starting_cursors": [item.as_dict() for item in self.starting_cursors],
            "ending_cursors": [self.cursors[name].as_dict() for name in STREAMS],
            "identities": self.identities,
            "coverage": coverage,
            "derived": {
                "forbidden_recurrence_count": 0 if not self.invalidations else None,
                "history_complete": not self.invalidations,
            },
            "invalidations": list(self.invalidations),
            "chain_head": self.journal.head(),
            "receiver_custody": receiver_custody,
        }
        body = dict(artifact)
        body.pop("chain_head")
        self.journal.append(
            "checkpoint", "observation-finish", {"artifact_body_digest": digest(body)}
        )
        artifact["chain_head"] = self.journal.head()
        return artifact
