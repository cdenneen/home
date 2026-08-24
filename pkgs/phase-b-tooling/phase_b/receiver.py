"""Fixed off-host receiver chain and bound online consumption client."""

from __future__ import annotations

import fcntl
import os
import re
import stat
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import strict_json
from .journal import Journal
from .trust import ExecutableBinding, require_safe_attempt_id


class ReceiverError(RuntimeError):
    pass


CANONICAL_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)


def _utc(value: str) -> datetime:
    if not isinstance(value, str) or CANONICAL_UTC.fullmatch(value) is None:
        raise ReceiverError("receiver time is not canonical UTC")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise ReceiverError("receiver time is invalid") from exc


class BoundReceiverClient:
    """Online authority selected only by the root anchor's immutable closure."""

    def __init__(self, binding: ExecutableBinding):
        self.binding = binding

    def _request(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = subprocess.run(
            [str(self.binding.path), operation],
            input=strict_json.canonical(payload),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            env={"PATH": "", "LANG": "C", "LC_ALL": "C"},
        )
        if result.returncode:
            raise ReceiverError("bound receiver rejected request")
        try:
            value = strict_json.loads_canonical(result.stdout)
        except strict_json.StrictJSONError as exc:
            raise ReceiverError("bound receiver returned ambiguous output") from exc
        if not isinstance(value, dict):
            raise ReceiverError("bound receiver returned non-object")
        return value

    def trusted_now(self) -> datetime:
        value = strict_json.exact_object(
            self._request("trusted-now", {}), {"now"}, "receiver time"
        )
        return _utc(value["now"])

    def current_head(self, attempt_id: str) -> str:
        require_safe_attempt_id(attempt_id)
        value = strict_json.exact_object(
            self._request("current-head", {"attempt_id": attempt_id}),
            {"head"},
            "receiver head",
        )
        if not isinstance(value["head"], str) or not value["head"].startswith(
            "sha256:"
        ):
            raise ReceiverError("receiver head is invalid")
        return value["head"]

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
        if isinstance(expected_counter, bool) or not isinstance(expected_counter, int):
            raise ReceiverError("consumption counter is not an integer")
        _utc(continued_at)
        _utc(grant_expires_at)
        request = {
            "schema": "phase-b.consumption.v1",
            "attempt_id": require_safe_attempt_id(attempt_id),
            "expected_counter": expected_counter,
            "previous_receipt_digest": previous_receipt_digest,
            "consumer_nonce": consumer_nonce,
            "consumer_identity": consumer_identity,
            "requested_transition": requested_transition,
            "authorization_grant_digest": authorization_grant_digest,
            "receiver_head": receiver_head,
            "receipt_digest": receipt_digest,
            "continued_at": continued_at,
            "grant_expires_at": grant_expires_at,
        }
        value = strict_json.exact_object(
            self._request("compare-and-set", request), {"accepted"}, "receiver CAS"
        )
        if not isinstance(value["accepted"], bool):
            raise ReceiverError("receiver CAS result is not boolean")
        return value["accepted"]


class DurableReceiverState:
    """Off-host append-only chain plus locked, fsynced one-time CAS state."""

    MAX_RECEPTION_SKEW_SECONDS = 125

    def __init__(
        self,
        root: Path,
        *,
        owner_uid: int,
        secure_root: Path = Path("/"),
        wall: Any = lambda: datetime.now(timezone.utc),
        monotonic: Any = time.monotonic,
    ):
        if (
            not root.is_absolute()
            or ".." in root.parts
            or not secure_root.is_absolute()
        ):
            raise ReceiverError("receiver root is not normalized absolute")
        self.root = root
        self.owner_uid = owner_uid
        self.secure_root = secure_root
        self.wall = wall
        self.monotonic = monotonic
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory = os.open(secure_root, flags)
        try:
            parts = root.relative_to(secure_root).parts
            for index, part in enumerate(parts):
                try:
                    child = os.open(part, flags, dir_fd=directory)
                except FileNotFoundError:
                    os.mkdir(part, 0o700, dir_fd=directory)
                    os.fsync(directory)
                    child = os.open(part, flags, dir_fd=directory)
                os.close(directory)
                directory = child
                metadata = os.fstat(directory)
                forbidden_mode = 0o077 if index == len(parts) - 1 else 0o022
                if metadata.st_uid != owner_uid or metadata.st_mode & forbidden_mode:
                    raise ReceiverError("receiver root chain is not owner-controlled")
            try:
                lock = os.open(
                    "cas.lock",
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
            except FileExistsError:
                lock = os.open(
                    "cas.lock", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory
                )
            os.close(lock)
        finally:
            os.close(directory)
        self.chain = Journal(root / "chain", owner_uid=owner_uid, clock=wall)
        records = self.chain.read_all()
        self._sequence = len(records)
        self._head = records[-1]["record_hash"] if records else self.chain.head()
        self.lock_path = root / "cas.lock"

    def _reload_tail_locked(self) -> None:
        """Refresh another process's durable appends while cas.lock is held."""
        while True:
            path = self.chain.directory / f"{self._sequence:016d}.json"
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            except FileNotFoundError:
                return
            try:
                record = strict_json.loads(os.read(fd, 1024 * 1024))
            finally:
                os.close(fd)
            expected = strict_json.exact_object(
                record,
                {
                    "schema",
                    "sequence",
                    "previous_hash",
                    "kind",
                    "action_id",
                    "payload",
                    "recorded_at",
                    "record_hash",
                },
                "receiver journal tail",
            )
            unsigned = dict(expected)
            record_hash = unsigned.pop("record_hash")
            if (
                expected["sequence"] != self._sequence
                or expected["previous_hash"] != self._head
                or strict_json.digest(unsigned) != record_hash
            ):
                raise ReceiverError("receiver durable tail is forked or corrupt")
            self._sequence += 1
            self._head = record_hash

    @contextmanager
    def _exclusive(self):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _check_reception_time(self, envelope: Any, received_at: datetime) -> None:
        if not isinstance(envelope, dict) or not isinstance(
            envelope.get("payload"), dict
        ):
            return
        payload = envelope["payload"]
        observed_at = payload.get("observed_at")
        if not isinstance(observed_at, str):
            if "event_class" not in payload:
                return
            raise ReceiverError("source event has no observed time")
        source_delay = (received_at - _utc(observed_at)).total_seconds()
        if not 0 <= source_delay <= self.MAX_RECEPTION_SKEW_SECONDS:
            raise ReceiverError("source event was not received in real time")
        if payload.get("event_class") != "continuity-checkpoints":
            return
        metadata = payload.get("metadata")
        checkpoints = (
            metadata.get("checkpoints") if isinstance(metadata, dict) else None
        )
        if not isinstance(checkpoints, list) or not checkpoints:
            raise ReceiverError("continuity event has no checkpoints")
        for checkpoint in checkpoints:
            if not isinstance(checkpoint, dict):
                raise ReceiverError("continuity checkpoint is malformed")
            checkpoint_time = checkpoint.get("received_at")
            if not isinstance(checkpoint_time, str):
                raise ReceiverError("continuity checkpoint has no receiver time")
            observed = _utc(checkpoint_time)
            delay = (received_at - observed).total_seconds()
            if not 0 <= delay <= self.MAX_RECEPTION_SKEW_SECONDS:
                raise ReceiverError(
                    "continuity checkpoint was not received in real time"
                )

    def append_source(self, sequence: int, envelope_bytes: bytes) -> str:
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ReceiverError("receiver sequence is not unsigned integer")
        envelope = strict_json.loads_canonical(envelope_bytes)
        received_wall = self.wall().astimezone(timezone.utc)
        received_monotonic = float(self.monotonic())
        self._check_reception_time(envelope, received_wall)
        with self._exclusive():
            self._reload_tail_locked()
            if sequence != self._sequence:
                raise ReceiverError("receiver source sequence has a gap/fork/replay")
            record = self.chain.append_checkpoint_fast(
                f"source-{sequence:020d}",
                {
                    "sequence": sequence,
                    "envelope": envelope,
                    "receiver_received_at": received_wall.isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "receiver_received_monotonic": received_monotonic,
                },
                expected_sequence=self._sequence,
                expected_head=self._head,
            )
            self._sequence += 1
            self._head = record["record_hash"]
            return self._head

    def append_extension(self, event: dict[str, Any]) -> dict[str, Any]:
        """Append a collector-signed continuation artifact under the receiver/CAS lock."""
        if not isinstance(event, dict):
            raise ReceiverError("receiver extension artifact reference is invalid")
        with self._exclusive():
            self._reload_tail_locked()
            previous = self._head
            record = self.chain.append_checkpoint_fast(
                f"extension-{self._sequence:020d}",
                {"event": event},
                expected_sequence=self._sequence,
                expected_head=previous,
            )
            self._sequence += 1
            self._head = record["record_hash"]
            return {
                "sequence": record["sequence"],
                "previous_head": previous,
                "head": self._head,
                "event": event,
                "receiver_record": record,
            }

    def export_records(self) -> tuple[dict[str, Any], ...]:
        """Return the fully verified durable chain while excluding concurrent appends."""
        with self._exclusive():
            self._reload_tail_locked()
            records = self.chain.read_all()
            if len(records) != self._sequence or (
                records and records[-1]["record_hash"] != self._head
            ):
                raise ReceiverError("receiver chain no longer matches its durable head")
            return records

    def trusted_now(self) -> datetime:
        return self.wall().astimezone(timezone.utc)

    def current_head(self, attempt_id: str) -> str:
        require_safe_attempt_id(attempt_id)
        with self._exclusive():
            self._reload_tail_locked()
            return self._head

    def _state_path(self, attempt_id: str) -> Path:
        return self.root / f"consumption-{attempt_id}.json"

    def _invalidation_path(self, attempt_id: str) -> Path:
        return self.root / f"invalid-{attempt_id}.json"

    def attempt_invalid(self, attempt_id: str) -> bool:
        require_safe_attempt_id(attempt_id)
        with self._exclusive():
            return self._invalidation_path(attempt_id).exists()

    def invalidate_attempt(self, attempt_id: str, reason: str) -> None:
        """Durably make a partially appended receiver attempt unconsumable."""
        require_safe_attempt_id(attempt_id)
        if not isinstance(reason, str) or not reason:
            raise ReceiverError("receiver invalidation reason is absent")
        with self._exclusive():
            self._reload_tail_locked()
            path = self._invalidation_path(attempt_id)
            if not path.exists():
                tmp = self.root / f".{path.name}.{os.getpid()}.tmp"
                fd = os.open(
                    tmp,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                try:
                    os.write(
                        fd,
                        strict_json.canonical(
                            {
                                "schema": "phase-b.receiver-invalidation.v1",
                                "attempt_id": attempt_id,
                                "reason": reason,
                                "head": self._head,
                            }
                        ),
                    )
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.replace(tmp, path)
                directory = os.open(
                    self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                )
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            record = self.chain.append_checkpoint_fast(
                f"terminal-invalidation-{self._sequence:020d}",
                {"attempt_id": attempt_id, "reason": reason},
                expected_sequence=self._sequence,
                expected_head=self._head,
            )
            self._sequence += 1
            self._head = record["record_hash"]

    def consumption_snapshot(
        self, attempt_id: str
    ) -> tuple[dict[str, Any] | None, str]:
        """Return strict receipt state and live head under one receiver lock."""
        require_safe_attempt_id(attempt_id)
        with self._exclusive():
            self._reload_tail_locked()
            path = self._state_path(attempt_id)
            if not path.exists():
                return None, self._head
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                metadata = os.fstat(fd)
                if (
                    metadata.st_uid != self.owner_uid
                    or metadata.st_nlink != 1
                    or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_mode & 0o077
                ):
                    raise ReceiverError("receiver consumption state permissions are unsafe")
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            finally:
                os.close(fd)
            value = strict_json.exact_object(
                strict_json.loads_canonical(b"".join(chunks)),
                {
                    "counter",
                    "receipt_digest",
                    "nonces",
                    "consumer_identity_digest",
                    "authorization_grant_digest",
                    "receiver_head",
                },
                "receiver consumption state",
            )
            if (
                isinstance(value["counter"], bool)
                or not isinstance(value["counter"], int)
                or value["counter"] < 1
                or not isinstance(value["receipt_digest"], str)
                or not isinstance(value["receiver_head"], str)
                or not isinstance(value["nonces"], list)
                or not all(isinstance(item, str) for item in value["nonces"])
            ):
                raise ReceiverError("receiver consumption state is invalid")
            return value, self._head

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
        require_safe_attempt_id(attempt_id)
        if (
            isinstance(expected_counter, bool)
            or not isinstance(expected_counter, int)
            or expected_counter < 0
        ):
            return False
        if requested_transition != "PHASE_B_FENCING_QUALIFICATION":
            return False
        try:
            continued = _utc(continued_at)
            grant_expires = _utc(grant_expires_at)
        except ReceiverError:
            return False
        with self._exclusive():
            self._reload_tail_locked()
            trusted_now = self.wall().astimezone(timezone.utc)
            if (
                trusted_now > continued + timedelta(minutes=5)
                or trusted_now >= grant_expires
                or continued > trusted_now
                or self._invalidation_path(attempt_id).exists()
                or receiver_head != self._head
            ):
                return False
            path = self._state_path(attempt_id)
            if path.exists():
                data = path.read_bytes()
                current = strict_json.loads_canonical(data)
            else:
                current = {"counter": 0, "receipt_digest": None, "nonces": []}
            if (
                current.get("counter") != expected_counter
                or current.get("receipt_digest") != previous_receipt_digest
                or not isinstance(current.get("nonces"), list)
                or consumer_nonce in current["nonces"]
            ):
                return False
            new = {
                "counter": expected_counter + 1,
                "receipt_digest": receipt_digest,
                "nonces": [*current["nonces"], consumer_nonce],
                "consumer_identity_digest": strict_json.digest(consumer_identity),
                "authorization_grant_digest": authorization_grant_digest,
                "receiver_head": receiver_head,
            }
            tmp = self.root / f".{path.name}.{os.getpid()}.tmp"
            out = os.open(
                tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            try:
                os.write(out, strict_json.canonical(new))
                os.fsync(out)
            finally:
                os.close(out)
            os.replace(tmp, path)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return True
