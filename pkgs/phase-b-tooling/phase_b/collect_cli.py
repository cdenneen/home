"""Fixed-boundary off-host continuous evidence collector entry point."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import strict_json
from .artifacts import DirectoryArtifactStore, read_json_artifact
from .cli_common import run_without_options
from .collector import (
    CLOCK_TOLERANCE_SECONDS,
    EVENT_FIELDS,
    STREAMS,
    Collector,
    Cursor,
    SystemClock,
    derive_raw_batch,
)
from .execute_cli import _read_fixed, _write_fixed
from .journal import Journal
from .receiver import DurableReceiverState
from .trust import (
    BoundExecutableVerifier,
    ExecutableBinding,
    SignatureVerifier,
    TrustAnchor,
    TrustError,
    verify_envelope,
    verify_executable,
)

INBOX_ROOT = Path("/var/lib/phase-b-collector/inbox")
ARTIFACT_ROOT = Path("/var/lib/phase-b-collector/artifacts")
STATE_ROOT = Path("/var/lib/phase-b-collector/state")


def _sign(binding: ExecutableBinding, payload: dict[str, Any]) -> dict[str, Any]:
    result = subprocess.run(
        [str(binding.path), "sign", "phase-b-observation"],
        input=strict_json.canonical(payload),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        env={"PATH": "", "LANG": "C", "LC_ALL": "C"},
    )
    if result.returncode:
        raise RuntimeError("bound observation signer failed")
    value = strict_json.loads_canonical(result.stdout)
    if not isinstance(value, dict):
        raise TypeError("bound observation signer returned non-object")
    return value


def _write_receiver_artifact(
    artifact_root: Path,
    records: tuple[dict[str, Any], ...],
    owner_uid: int,
    secure_root: Path,
) -> dict[str, Any]:
    data = strict_json.canonical(list(records))
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    artifact_id = "receiver-chain-" + digest.removeprefix("sha256:")[:24]
    _write_fixed(
        artifact_root / "evidence",
        artifact_id + ".artifact",
        list(records),
        owner_uid,
        secure_root,
        mode=0o400,
    )
    return {
        "id": artifact_id,
        "digest": digest,
        "media_type": "application/json",
        "owner_only": True,
    }


def _wait_until(clock: Any, target: float, sleeper: Callable[[float], None]) -> None:
    """Drive the bounded initial observation fixture/runtime plan."""
    remaining = target - float(clock.monotonic())
    if remaining <= 0:
        return
    advance = getattr(clock, "advance", None)
    if callable(advance):
        advance(remaining)
    else:
        sleeper(remaining)


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TrustError(f"{label} is not canonical UTC")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise TrustError(f"{label} is invalid") from exc


def _validate_refresh_segment_schema(
    segment: Any, anchor: TrustAnchor
) -> dict[str, Any]:
    schema = strict_json.loads(
        (
            Path(__file__).with_name("schemas") / "receiver-refresh-segment.schema.json"
        ).read_bytes()
    )
    if (
        not isinstance(schema, dict)
        or strict_json.digest(schema)
        != anchor.schema_digests["receiver-refresh-segment"]
    ):
        raise TrustError("receiver refresh segment schema is not root-bound")
    strict_json.validate(segment, schema)
    if not isinstance(segment, dict):
        raise TrustError("receiver refresh segment is malformed")
    return segment


def _artifact_ref(artifact_id: str, value: Any) -> dict[str, Any]:
    data = strict_json.canonical(value)
    return {
        "id": artifact_id,
        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        "media_type": "application/json",
        "owner_only": True,
    }


def _read_named_artifact(
    path: Path, artifact_id: str, *, owner_uid: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != owner_uid
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_nlink != 1
            or metadata.st_size > 16 * 1024 * 1024
        ):
            raise TrustError("durable refresh segment artifact is unsafe")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                raise TrustError("durable refresh segment artifact was truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    value = strict_json.loads_canonical(data)
    if not isinstance(value, dict):
        raise TrustError("durable refresh segment artifact is malformed")
    return _artifact_ref(artifact_id, value), value


def _persist_artifact(
    root: Path,
    artifact_id: str,
    value: Any,
    *,
    owner_uid: int,
    secure_root: Path,
) -> dict[str, Any]:
    ref = _artifact_ref(artifact_id, value)
    store = DirectoryArtifactStore(root, owner_uid=owner_uid, secure_root=secure_root)
    path = root / f"{artifact_id}.artifact"
    if os.path.lexists(path):
        _ref, existing = read_json_artifact(store, ref, owner_only=True)
        if existing != value:
            raise TrustError("durable refresh artifact conflicts with accepted input")
    else:
        _write_fixed(
            root,
            f"{artifact_id}.artifact",
            value,
            owner_uid,
            secure_root,
            mode=0o400,
        )
    return ref


def _continue_with(
    anchor: TrustAnchor,
    observation_envelope: dict[str, Any],
    *,
    inbox_root: Path,
    artifact_root: Path,
    state_root: Path,
    owner_uid: int,
    secure_root: Path,
    verifier: SignatureVerifier,
    signer: Callable[[ExecutableBinding, dict[str, Any]], dict[str, Any]],
    clock: Any,
    sleeper: Callable[[float], None] = time.sleep,
    fault: Callable[[str], None] | None = None,
) -> None:
    # The continuation path is deliberately invocation-driven. It never waits for a
    # signed future timestamp to become current.
    del sleeper
    inject = fault or (lambda _stage: None)
    observation = verify_envelope(
        observation_envelope, anchor, verifier, "phase-b-observation"
    )
    attempt_id = observation.get("attempt_id")
    if not isinstance(attempt_id, str):
        raise TrustError("observation attempt is absent")
    receiver_root = state_root / attempt_id / "receiver"
    receiver = DurableReceiverState(
        receiver_root,
        owner_uid=owner_uid,
        secure_root=secure_root,
        wall=clock.wall,
        monotonic=clock.monotonic,
    )
    if receiver.attempt_invalid(attempt_id):
        raise TrustError("continuation attempt was terminally invalidated")
    observation_custody = observation.get("receiver_custody")
    if not isinstance(observation_custody, dict):
        raise TrustError("observation receiver custody is absent")
    records = receiver.export_records()
    observation_sequence = observation_custody.get("sequence")
    if (
        isinstance(observation_sequence, bool)
        or not isinstance(observation_sequence, int)
        or observation_sequence < 0
        or len(records) < observation_sequence
        or (
            observation_sequence
            and records[observation_sequence - 1].get("record_hash")
            != observation_custody.get("head")
        )
    ):
        raise TrustError("receiver no longer contains observation custody")

    counter = 1
    prior_complete: dict[str, Any] | None = None
    while os.path.lexists(receiver_root / f"refresh-complete-{counter:08d}.json"):
        prior_complete = _read_fixed(
            receiver_root,
            f"refresh-complete-{counter:08d}.json",
            owner_uid,
            secure_root,
        )
        if (
            not isinstance(prior_complete, dict)
            or prior_complete.get("schema") != "phase-b.refresh-complete.v2"
            or prior_complete.get("counter") != counter
        ):
            raise TrustError("receiver refresh completion state is invalid")
        counter += 1

    if prior_complete is None:
        base_sequence = observation_sequence
        base_head = observation_custody.get("head")
        cursors = {item["stream"]: dict(item) for item in observation["ending_cursors"]}
        continuity = {
            name: float(observation["coverage"][name]["end_monotonic"])
            for name in STREAMS
        }
        source_walls: dict[str, str] = {}
        for record in records[:observation_sequence]:
            payload = record.get("payload")
            envelope = payload.get("envelope") if isinstance(payload, dict) else None
            source = envelope.get("payload") if isinstance(envelope, dict) else None
            if isinstance(source, dict) and source.get("stream") in STREAMS:
                source_walls[source["stream"]] = source["observed_at"]
        if set(source_walls) != set(STREAMS):
            raise TrustError("observation lacks terminal source wall state")
    else:
        base_sequence = prior_complete["current_sequence"]
        base_head = prior_complete["current_head"]
        cursors = {
            item["stream"]: dict(item) for item in prior_complete["terminal_cursors"]
        }
        continuity = {
            name: float(value)
            for name, value in prior_complete["terminal_continuity"].items()
        }
        source_walls = dict(prior_complete["terminal_source_walls"])
    if (
        not isinstance(base_head, str)
        or len(records) < base_sequence
        or (
            base_sequence and records[base_sequence - 1].get("record_hash") != base_head
        )
    ):
        raise TrustError("receiver prior refresh state is not at the durable head")
    intent_name = f"refresh-intent-{counter:08d}.json"
    intent: dict[str, Any] | None = None
    if os.path.lexists(receiver_root / intent_name):
        intent = strict_json.exact_object(
            _read_fixed(receiver_root, intent_name, owner_uid, secure_root),
            {
                "schema",
                "counter",
                "attempt_id",
                "refresh_id",
                "next_segment",
                "base_head",
                "base_sequence",
                "source_count",
                "segments",
                "terminal_cursors",
                "terminal_continuity",
                "terminal_source_walls",
                "consumer_identity",
                "consumer_nonce",
                "requested_transition",
                "authorization_grant_digest",
                "final",
            },
            "receiver refresh intent",
        )
        if (
            intent["schema"] != "phase-b.refresh-intent.v2"
            or intent["counter"] != counter
            or intent["attempt_id"] != attempt_id
            or intent["base_head"] != base_head
            or intent["base_sequence"] != base_sequence
            or not isinstance(intent["segments"], list)
            or not intent["segments"]
            or intent["next_segment"] != len(intent["segments"]) + 1
            or intent["source_count"] != len(intent["segments"]) * len(STREAMS)
        ):
            raise TrustError("receiver refresh intent identity is invalid")
        cursors = {item["stream"]: dict(item) for item in intent["terminal_cursors"]}
        continuity = {
            name: float(value) for name, value in intent["terminal_continuity"].items()
        }
        source_walls = dict(intent["terminal_source_walls"])

    if prior_complete is not None and intent is None:
        consumption, live_head = receiver.consumption_snapshot(attempt_id)
        if (
            consumption is None
            or consumption["counter"] != counter - 1
            or consumption["receiver_head"] != base_head
            or live_head != base_head
        ):
            raise TrustError("prior receipt is not durably consumed at the live head")

    store = DirectoryArtifactStore(
        artifact_root / "evidence", owner_uid=owner_uid, secure_root=secure_root
    )

    def read_segment(ref: dict[str, Any]) -> dict[str, Any]:
        _ref, value = read_json_artifact(store, ref, owner_only=True)
        return _validate_refresh_segment_schema(value, anchor)

    def ensure_current(segment: dict[str, Any]) -> None:
        now = clock.wall().astimezone(timezone.utc)
        event_time = _utc(segment.get("observed_at"), "refresh segment time")
        event_delay = (now - event_time).total_seconds()
        if not 0 <= event_delay <= receiver.MAX_RECEPTION_SKEW_SECONDS:
            raise TrustError("refresh segment was not received in real time")
        events = segment.get("source_events")
        if not isinstance(events, list):
            raise TrustError("refresh segment source events are absent")
        for envelope in events:
            payload = envelope.get("payload") if isinstance(envelope, dict) else None
            if not isinstance(payload, dict):
                raise TrustError("refresh segment source event is malformed")
            observed = _utc(payload.get("observed_at"), "refresh source time")
            delay = (now - observed).total_seconds()
            if (
                not 0 <= delay <= receiver.MAX_RECEPTION_SKEW_SECONDS
                or abs((event_time - observed).total_seconds())
                > CLOCK_TOLERANCE_SECONDS
            ):
                raise TrustError("refresh source event was not received in real time")
            metadata = payload.get("metadata")
            checkpoints = (
                metadata.get("checkpoints") if isinstance(metadata, dict) else None
            )
            if not isinstance(checkpoints, list) or len(checkpoints) != 1:
                raise TrustError(
                    "refresh source event must contain exactly one checkpoint"
                )
            checkpoint_time = _utc(
                checkpoints[0].get("received_at")
                if isinstance(checkpoints[0], dict)
                else None,
                "refresh checkpoint time",
            )
            checkpoint_delay = (now - checkpoint_time).total_seconds()
            if (
                not 0 <= checkpoint_delay <= receiver.MAX_RECEPTION_SKEW_SECONDS
                or abs((observed - checkpoint_time).total_seconds())
                > CLOCK_TOLERANCE_SECONDS
            ):
                raise TrustError("refresh checkpoint was not received in real time")

    def append_pending(current: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        all_records = receiver.export_records()
        tail = list(all_records[base_sequence:])
        expected = current["source_count"]
        previous = expected - len(STREAMS)
        extension_allowance = 1 if current["final"] else 0
        if len(tail) not in {previous, expected, expected + extension_allowance}:
            receiver.invalidate_attempt(attempt_id, "partial-continuation-append")
            raise TrustError("receiver refresh append is partial")
        refs = current["segments"]
        all_events: list[dict[str, Any]] = []
        for ref in refs:
            all_events.extend(read_segment(ref)["source_events"])
        if any(
            record.get("payload", {}).get("envelope") != all_events[index]
            for index, record in enumerate(tail[: min(len(tail), expected)])
        ):
            receiver.invalidate_attempt(attempt_id, "continuation-input-mismatch")
            raise TrustError("receiver refresh records do not match durable segments")
        if previous < len(tail) < expected:
            receiver.invalidate_attempt(attempt_id, "partial-continuation-append")
            raise TrustError("receiver refresh append is partial")
        if len(tail) == previous:
            segment = read_segment(refs[-1])
            try:
                ensure_current(segment)
            except TrustError:
                receiver.invalidate_attempt(
                    attempt_id, "stale-unappended-refresh-segment"
                )
                raise TrustError(
                    "unappended refresh segment is stale; attempt invalidated"
                )
            segment_events = segment["source_events"]
            for ordinal, envelope in enumerate(segment_events):
                absolute = previous + ordinal
                inject(f"before-receiver-append-{absolute}")
                receiver.append_source(
                    base_sequence + absolute, strict_json.canonical(envelope)
                )
                inject(f"after-receiver-append-{absolute}")
            tail = list(receiver.export_records()[base_sequence:])
        return tuple(tail)

    # Recover the last durably accepted segment before consulting mutable inbox.
    if intent is not None:
        tail = append_pending(intent)
        if intent["final"]:
            if len(tail) not in {intent["source_count"], intent["source_count"] + 1}:
                receiver.invalidate_attempt(attempt_id, "partial-continuation-append")
                raise TrustError("receiver final refresh tail is invalid")
        else:
            tail = tuple(tail)
    else:
        tail = tuple(receiver.export_records()[base_sequence:])
        if tail:
            receiver.invalidate_attempt(attempt_id, "unowned-continuation-tail")
            raise TrustError("receiver has continuation records without durable intent")

    if intent is None or not intent["final"]:
        segment_index = 1 if intent is None else intent["next_segment"]
        segment_id = f"refresh-segment-{counter:08d}-{segment_index:08d}"
        fixed_segment_path = artifact_root / "evidence" / f"{segment_id}.artifact"
        recovered_fixed_segment = os.path.lexists(fixed_segment_path)
        if recovered_fixed_segment:
            segment_ref, segment = _read_named_artifact(
                fixed_segment_path, segment_id, owner_uid=owner_uid
            )
            _stored_ref, stored_segment = read_json_artifact(
                store, segment_ref, owner_only=True
            )
            if stored_segment != segment:
                raise TrustError("durable refresh segment artifact changed while open")
        else:
            segment = _read_fixed(
                inbox_root, "continuation-event.json", owner_uid, secure_root
            )
            if not isinstance(segment, dict):
                raise TrustError("receiver refresh segment is malformed")

        segment = strict_json.exact_object(
            _validate_refresh_segment_schema(segment, anchor),
            {
                "schema",
                "attempt_id",
                "refresh_id",
                "refresh_counter",
                "segment_index",
                "final",
                "observed_at",
                "source_events",
                "sample",
                "consumer_identity",
                "consumer_nonce",
                "requested_transition",
                "authorization_grant_digest",
                "invalidating_event_count",
            },
            "receiver refresh segment",
        )
        if (
            segment["schema"] != "phase-b.receiver-refresh-segment.v1"
            or segment["attempt_id"] != attempt_id
            or segment["refresh_counter"] != counter
            or segment["segment_index"] != segment_index
            or not isinstance(segment["final"], bool)
            or segment["invalidating_event_count"] != 0
            or not isinstance(segment["source_events"], list)
            or len(segment["source_events"]) != len(STREAMS)
            or not isinstance(segment["refresh_id"], str)
            or not segment["refresh_id"]
            or len(segment["refresh_id"]) > 128
            or (segment["sample"] is None) == segment["final"]
        ):
            raise TrustError("continuation refresh segment is incomplete")
        if intent is not None and any(
            segment[key] != intent[key]
            for key in (
                "refresh_id",
                "consumer_identity",
                "consumer_nonce",
                "requested_transition",
                "authorization_grant_digest",
            )
        ):
            raise TrustError("continuation refresh identity or grant changed")
        try:
            ensure_current(segment)
        except TrustError as exc:
            receiver.invalidate_attempt(
                attempt_id,
                "stale-or-future-refresh-segment",
            )
            if not recovered_fixed_segment:
                _persist_artifact(
                    artifact_root / "evidence",
                    f"rejected-refresh-segment-{counter:08d}-{segment_index:08d}",
                    segment,
                    owner_uid=owner_uid,
                    secure_root=secure_root,
                )
            raise TrustError(
                "refresh segment is not current; attempt invalidated"
            ) from exc

        validated: list[dict[str, Any]] = []
        next_cursors = {name: dict(value) for name, value in cursors.items()}
        next_continuity = dict(continuity)
        next_source_walls = dict(source_walls)
        for ordinal, envelope in enumerate(segment["source_events"]):
            if not isinstance(envelope, dict) or not isinstance(
                envelope.get("payload"), dict
            ):
                raise TrustError("continuation source event is malformed")
            stream = STREAMS[ordinal]
            payload = verify_envelope(
                envelope, anchor, verifier, f"phase-b-source-event.{stream}"
            )
            payload = strict_json.exact_object(
                payload, set(EVENT_FIELDS), "continuation source event"
            )
            previous = next_cursors[stream]
            if (
                payload["schema"] != "phase-b.source-event.v1"
                or payload["attempt_id"] != attempt_id
                or payload["stream"] != stream
                or payload["generation"] != previous["generation"]
                or payload["offset"] != previous["offset"] + 1
                or payload["previous_token"] != previous["token"]
                or payload["event_class"] != "continuity-checkpoints"
            ):
                raise TrustError("continuation source cursor/signature is invalid")
            observed = _utc(payload["observed_at"], "continuation source time")
            checkpoint = strict_json.exact_object(
                strict_json.exact_object(
                    payload["metadata"], {"checkpoints"}, "continuation metadata"
                )["checkpoints"][0],
                {
                    "start_monotonic",
                    "end_monotonic",
                    "received_at",
                    "source_cursor",
                    "batch",
                    "event_count",
                    "lost",
                    "backlog",
                    "replay",
                },
                "continuation checkpoint",
            )
            start, end = checkpoint["start_monotonic"], checkpoint["end_monotonic"]
            prior_continuity = next_continuity[stream]
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or abs(float(start) - prior_continuity) > 1
                or not 0 < float(end) - float(start) <= 120
                or any(checkpoint[key] != 0 for key in ("lost", "backlog", "replay"))
            ):
                raise TrustError("continuation checkpoint has a gap/loss/replay")
            _ref, batch = read_json_artifact(
                store, checkpoint["batch"], owner_only=True
            )
            seen, failures = derive_raw_batch(stream, batch, observation["identities"])
            if (
                failures
                or not seen
                or batch.get("source_cursor") != checkpoint["source_cursor"]
                or len(batch.get("events", [])) != checkpoint["event_count"]
            ):
                raise TrustError("continuation raw source batch is invalid")
            prior_wall = _utc(
                next_source_walls[stream], "prior continuation source time"
            )
            wall_delta = (observed - prior_wall).total_seconds()
            monotonic_delta = float(end) - prior_continuity
            if (
                wall_delta < 0
                or abs(wall_delta - monotonic_delta) > CLOCK_TOLERANCE_SECONDS
            ):
                raise TrustError("continuation wall/monotonic coverage has a blind gap")
            next_continuity[stream] = float(end)
            next_source_walls[stream] = payload["observed_at"]
            next_cursors[stream] = {
                "stream": stream,
                "generation": payload["generation"],
                "offset": payload["offset"],
                "token": strict_json.digest(envelope),
            }
            validated.append(envelope)

        segment_ref = _persist_artifact(
            artifact_root / "evidence",
            segment_id,
            segment,
            owner_uid=owner_uid,
            secure_root=secure_root,
        )
        inject("after-refresh-segment-artifact")
        segments = (
            [segment_ref] if intent is None else [*intent["segments"], segment_ref]
        )
        updated_intent = {
            "schema": "phase-b.refresh-intent.v2",
            "counter": counter,
            "attempt_id": attempt_id,
            "refresh_id": segment["refresh_id"],
            "next_segment": segment_index + 1,
            "base_head": base_head,
            "base_sequence": base_sequence,
            "source_count": len(segments) * len(STREAMS),
            "segments": segments,
            "terminal_cursors": [next_cursors[name] for name in STREAMS],
            "terminal_continuity": next_continuity,
            "terminal_source_walls": next_source_walls,
            "consumer_identity": segment["consumer_identity"],
            "consumer_nonce": segment["consumer_nonce"],
            "requested_transition": segment["requested_transition"],
            "authorization_grant_digest": segment["authorization_grant_digest"],
            "final": segment["final"],
        }
        _write_fixed(receiver_root, intent_name, updated_intent, owner_uid, secure_root)
        inject("after-refresh-intent")
        intent = updated_intent
        cursors, continuity, source_walls = (
            next_cursors,
            next_continuity,
            next_source_walls,
        )
        tail = append_pending(intent)
        if not segment["final"]:
            return

    if intent is None or not intent["final"]:
        raise TrustError("receiver final refresh intent is absent")
    source_count = intent["source_count"]
    source_records = list(tail[:source_count])
    if len(source_records) != source_count:
        receiver.invalidate_attempt(attempt_id, "partial-continuation-append")
        raise TrustError("continuation receiver source custody is incomplete")
    segments = [read_segment(ref) for ref in intent["segments"]]
    final_segment = segments[-1]
    all_source_events = [
        event for segment in segments for event in segment["source_events"]
    ]
    aggregate_event = {
        "schema": "phase-b.receiver-extension.v3",
        "attempt_id": attempt_id,
        "refresh_id": intent["refresh_id"],
        "refresh_counter": counter,
        "segment_count": len(segments),
        "segment_artifacts": intent["segments"],
        "observed_at": final_segment["observed_at"],
        "source_events": all_source_events,
        "sample": final_segment["sample"],
        "consumer_identity": intent["consumer_identity"],
        "consumer_nonce": intent["consumer_nonce"],
        "requested_transition": intent["requested_transition"],
        "authorization_grant_digest": intent["authorization_grant_digest"],
        "invalidating_event_count": 0,
    }
    event_id = f"refresh-event-{counter:08d}"
    event_ref = _persist_artifact(
        artifact_root / "evidence",
        event_id,
        aggregate_event,
        owner_uid=owner_uid,
        secure_root=secure_root,
    )
    if len(tail) == source_count:
        inject("before-receiver-extension")
        extension = receiver.append_extension(event_ref)
        inject("after-receiver-extension")
        tail = tuple(receiver.export_records()[base_sequence:])
    elif len(tail) == source_count + 1:
        record = tail[source_count]
        if record.get("payload") != {"event": event_ref}:
            receiver.invalidate_attempt(attempt_id, "continuation-extension-mismatch")
            raise TrustError("receiver extension does not match durable refresh")
        extension = {
            "sequence": record["sequence"],
            "previous_head": record["previous_hash"],
            "head": record["record_hash"],
            "event": event_ref,
            "receiver_record": record,
        }
    else:
        receiver.invalidate_attempt(attempt_id, "partial-continuation-append")
        raise TrustError("receiver continuation tail is invalid")

    continuation_payload = {
        "schema": "phase-b.receiver-continuation.v1",
        "attempt_id": attempt_id,
        "observation_head": base_head,
        "source_records": source_records,
        "extensions": [extension],
        "current_head": extension["head"],
        "terminal_cursors": intent["terminal_cursors"],
        "terminal_continuity": intent["terminal_continuity"],
        "terminal_source_walls": intent["terminal_source_walls"],
    }
    continuation_name = f"continuation-{counter:08d}.json"
    continuation_path = artifact_root / continuation_name
    if os.path.lexists(continuation_path):
        continuation = _read_fixed(
            artifact_root, continuation_name, owner_uid, secure_root
        )
        if (
            verify_envelope(continuation, anchor, verifier, "phase-b-observation")
            != continuation_payload
        ):
            receiver.invalidate_attempt(attempt_id, "continuation-publication-conflict")
            raise TrustError("published continuation conflicts with durable refresh")
    else:
        continuation = signer(
            anchor.executables["observation-signer"], continuation_payload
        )
        verify_envelope(continuation, anchor, verifier, "phase-b-observation")
        inject("before-continuation-publication")
        _write_fixed(
            artifact_root, continuation_name, continuation, owner_uid, secure_root
        )
        inject("after-continuation-publication")
    evidence = [
        {
            "name": path.name,
            "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted((artifact_root / "evidence").glob("*.artifact"))
    ]
    current_sequence = base_sequence + source_count + 1
    manifest_payload = {
        "schema": "phase-b.collector-import-manifest.v4",
        "consumption_counter": counter,
        "attempt_id": attempt_id,
        "observation_digest": strict_json.digest(observation_envelope),
        "observation_journal_head": observation["chain_head"],
        "receiver_head": base_head,
        "receiver_sequence": base_sequence,
        "continuation_digest": strict_json.digest(continuation),
        "current_receiver_head": extension["head"],
        "current_receiver_sequence": current_sequence,
        "terminal_cursors": intent["terminal_cursors"],
        "terminal_continuity": intent["terminal_continuity"],
        "terminal_source_walls": intent["terminal_source_walls"],
        "evidence": evidence,
    }
    manifest_name = f"bundle-manifest-{counter:08d}.json"
    manifest_path = artifact_root / manifest_name
    if os.path.lexists(manifest_path):
        manifest = _read_fixed(artifact_root, manifest_name, owner_uid, secure_root)
        if (
            verify_envelope(manifest, anchor, verifier, "phase-b-observation")
            != manifest_payload
        ):
            receiver.invalidate_attempt(attempt_id, "manifest-publication-conflict")
            raise TrustError("published manifest conflicts with durable refresh")
    else:
        manifest = signer(anchor.executables["observation-signer"], manifest_payload)
        inject("before-manifest-publication")
        _write_fixed(artifact_root, manifest_name, manifest, owner_uid, secure_root)
        inject("after-manifest-publication")
    complete = {
        "schema": "phase-b.refresh-complete.v2",
        "counter": counter,
        "attempt_id": attempt_id,
        "refresh_id": intent["refresh_id"],
        "current_head": extension["head"],
        "current_sequence": current_sequence,
        "terminal_cursors": intent["terminal_cursors"],
        "terminal_continuity": intent["terminal_continuity"],
        "terminal_source_walls": intent["terminal_source_walls"],
        "continuation_digest": strict_json.digest(continuation),
        "manifest_digest": strict_json.digest(manifest),
    }
    _write_fixed(
        receiver_root,
        f"refresh-complete-{counter:08d}.json",
        complete,
        owner_uid,
        secure_root,
    )


def _collect_with(
    anchor: TrustAnchor,
    *,
    inbox_root: Path = INBOX_ROOT,
    artifact_root: Path = ARTIFACT_ROOT,
    state_root: Path = STATE_ROOT,
    owner_uid: int = 0,
    secure_root: Path = Path("/"),
    signature_verifier: SignatureVerifier | None = None,
    clock: Any | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    verify_binding: Callable[[ExecutableBinding], None] = verify_executable,
    signer: Callable[[ExecutableBinding, dict[str, Any]], dict[str, Any]] = _sign,
    fault: Callable[[str], None] | None = None,
) -> None:
    for name in ("collector", "signature-verifier", "observation-signer"):
        verify_binding(anchor.executables[name])
    verifier = signature_verifier or BoundExecutableVerifier(
        anchor.executables["signature-verifier"],
        next(iter(anchor.signers.values())).algorithm,
    )
    active_clock: Any = clock if clock is not None else SystemClock()
    if os.path.lexists(artifact_root / "observation.json"):
        existing = _read_fixed(
            artifact_root, "observation.json", owner_uid, secure_root
        )
        if not isinstance(existing, dict):
            raise TrustError("existing observation is not a signed envelope")
        _continue_with(
            anchor,
            existing,
            inbox_root=inbox_root,
            artifact_root=artifact_root,
            state_root=state_root,
            owner_uid=owner_uid,
            secure_root=secure_root,
            verifier=verifier,
            signer=signer,
            clock=active_clock,
            sleeper=sleeper,
            fault=fault,
        )
        return
    start_envelope = _read_fixed(
        inbox_root, "observation-start.json", owner_uid, secure_root
    )
    start = verify_envelope(start_envelope, anchor, verifier, "phase-b-observation")
    start = strict_json.exact_object(
        start,
        {"schema", "attempt_id", "starting_cursors", "identities", "plan"},
        "observation start",
    )
    if start["schema"] != "phase-b.observation-start.v1" or not isinstance(
        start["plan"], list
    ):
        raise TrustError("observation start schema/plan is invalid")
    f0_envelope = _read_fixed(inbox_root, "f0.json", owner_uid, secure_root)
    f0 = verify_envelope(f0_envelope, anchor, verifier, "phase-b-f0")
    f0 = strict_json.exact_object(
        f0,
        {
            "schema",
            "attempt_id",
            "capture_id",
            "f0_at",
            "evidence",
            "custody_reads",
            "registry_digests",
            "journal_head",
        },
        "signed F0",
    )
    if f0["schema"] != "phase-b.f0.v3" or f0["attempt_id"] != start["attempt_id"]:
        raise TrustError("collector F0 does not bind the observation attempt")
    try:
        f0_at = datetime.fromisoformat(f0["f0_at"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise TrustError("collector F0 time is invalid") from exc
    cursors = tuple(
        Cursor(item["stream"], item["generation"], item["offset"], item["token"])
        for item in start["starting_cursors"]
    )
    journal_root = state_root / start["attempt_id"] / "observation"
    if journal_root.exists() and any(journal_root.iterdir()):
        raise TrustError("interrupted observation is terminal; use a fresh attempt")
    receiver = DurableReceiverState(
        state_root / start["attempt_id"] / "receiver",
        owner_uid=owner_uid,
        secure_root=secure_root,
        wall=active_clock.wall,
        monotonic=active_clock.monotonic,
    )
    journal = Journal(journal_root, owner_uid=owner_uid, clock=active_clock.wall)
    store = DirectoryArtifactStore(
        artifact_root / "evidence", owner_uid=owner_uid, secure_root=secure_root
    )
    collector = Collector(
        start["attempt_id"],
        journal,
        active_clock,
        cursors,
        start["identities"],
        anchor,
        verifier,
        store,
        receiver,
        f0_at=f0_at,
        f0_digest=strict_json.digest(f0),
        receiver_artifact_writer=lambda records: _write_receiver_artifact(
            artifact_root, records, owner_uid, secure_root
        ),
    )
    for step in start["plan"]:
        item = strict_json.exact_object(
            step, {"at_monotonic", "kind", "file"}, "collector plan step"
        )
        if isinstance(item["at_monotonic"], bool) or not isinstance(
            item["at_monotonic"], (int, float)
        ):
            raise TrustError("collector plan time is invalid")
        if (
            not isinstance(item["file"], str)
            or not item["file"].endswith(".json")
            or "/" in item["file"]
            or ".." in item["file"]
        ):
            raise TrustError("collector plan file is unsafe")
        _wait_until(active_clock, float(item["at_monotonic"]), sleeper)
        value = _read_fixed(inbox_root / "events", item["file"], owner_uid, secure_root)
        if item["kind"] == "source-event":
            collector.append_event(value)
        elif item["kind"] == "sample":
            if not isinstance(value, dict):
                raise TrustError("supplementary sample is not an object")
            collector.sample(value)
        elif item["kind"] == "disconnect":
            collector.disconnected()
        else:
            raise TrustError("collector plan operation is not fixed")
    observation = collector.finish()
    envelope = signer(anchor.executables["observation-signer"], observation)
    verify_envelope(envelope, anchor, verifier, "phase-b-observation")
    _write_fixed(artifact_root, "observation.json", envelope, owner_uid, secure_root)
    evidence = []
    for path in sorted((artifact_root / "evidence").glob("*.artifact")):
        evidence.append(
            {
                "name": path.name,
                "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest_payload = {
        "schema": "phase-b.collector-import-manifest.v1",
        "attempt_id": start["attempt_id"],
        "observation_digest": strict_json.digest(envelope),
        "observation_journal_head": journal.head(),
        "receiver_head": receiver.current_head(start["attempt_id"]),
        "receiver_sequence": collector.receiver_sequence,
        "evidence": evidence,
    }
    manifest = signer(anchor.executables["observation-signer"], manifest_payload)
    verify_envelope(manifest, anchor, verifier, "phase-b-observation")
    _write_fixed(
        artifact_root, "bundle-manifest.json", manifest, owner_uid, secure_root
    )


def _collect(anchor: TrustAnchor) -> None:
    _collect_with(anchor)


def main() -> int:
    return run_without_options(_collect)


if __name__ == "__main__":
    sys.exit(main())
