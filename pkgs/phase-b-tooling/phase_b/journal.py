"""Crash-safe append-only intent/outcome journal."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import re
import stat
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import strict_json

ZERO_HASH = "sha256:" + "0" * 64
RECORD_NAME = re.compile(r"^([0-9]{16})\.json$")
LEGACY_TEMP_NAME = re.compile(
    r"^(?:\.pending-|\.seal-|\.sealed-|\.quarantine-|sealed-orphan-)"
)
KINDS = frozenset({"intent", "outcome", "recovery", "invalidation", "checkpoint"})
AT_EMPTY_PATH = 0x1000


class JournalError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_time(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise JournalError("journal clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _no_follow() -> int:
    value = getattr(os, "O_NOFOLLOW", 0)
    if not value:
        raise JournalError("O_NOFOLLOW is required")
    return value


def _open_anonymous(directory: int) -> int:
    flags = getattr(os, "O_TMPFILE", 0)
    if not flags:
        raise JournalError("Linux O_TMPFILE is required")
    try:
        return os.open(
            ".",
            os.O_WRONLY | os.O_CLOEXEC | flags,
            0o600,
            dir_fd=directory,
        )
    except OSError as exc:
        raise JournalError("anonymous journal inode creation failed") from exc


def _link_anonymous(fd: int, directory: int, final_name: str) -> None:
    try:
        linkat = ctypes.CDLL(None, use_errno=True).linkat
    except (AttributeError, OSError) as exc:
        raise JournalError("linkat with AT_EMPTY_PATH is required") from exc
    linkat.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    linkat.restype = ctypes.c_int
    if linkat(fd, b"", directory, os.fsencode(final_name), AT_EMPTY_PATH) == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, os.strerror(error), final_name)
    raise JournalError("anonymous journal inode publication failed") from OSError(
        error, os.strerror(error)
    )


class Journal:
    def __init__(
        self,
        directory: Path,
        *,
        clock: Callable[[], datetime] = utc_now,
        owner_uid: int | None = None,
        fault: Callable[[str], None] | None = None,
    ):
        self.directory = directory
        self.clock = clock
        self.owner_uid = os.getuid() if owner_uid is None else owner_uid
        self._fault_enabled = fault is not None
        self.fault = fault or (lambda _stage: None)
        self._checkpoint_tail: tuple[int, str] | None = None

    def _initialize_fd(self) -> int:
        _no_follow()
        name = self.directory.name
        if name in {"", ".", ".."} or "/" in name:
            raise JournalError("journal directory name is unsafe")
        parent = os.open(
            self.directory.parent,
            os.O_RDONLY | os.O_DIRECTORY | _no_follow(),
        )
        child = -1
        try:
            parent_metadata = os.fstat(parent)
            if (
                parent_metadata.st_uid != self.owner_uid
                or parent_metadata.st_mode & 0o022
            ):
                raise JournalError("journal parent is not owner-controlled")
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent)
            except FileExistsError:
                pass
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | _no_follow(),
                dir_fd=parent,
            )
            metadata = os.fstat(child)
            if (
                metadata.st_uid != self.owner_uid
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise JournalError(
                    "journal directory must be owner-only and unsymlinked"
                )
            os.fsync(child)
            # Durably publish the per-attempt directory before any intent can
            # authorize an external effect.
            os.fsync(parent)
            result, child = child, -1
            return result
        finally:
            if child >= 0:
                os.close(child)
            os.close(parent)

    def initialize(self) -> None:
        fd = self._initialize_fd()
        os.close(fd)

    def _directory_fd(self) -> int:
        return self._initialize_fd()

    @staticmethod
    def _names(directory: int) -> tuple[list[tuple[int, str]], list[str]]:
        numbered: list[tuple[int, str]] = []
        orphans: list[str] = []
        for name in os.listdir(directory):
            match = RECORD_NAME.fullmatch(name)
            if match is not None:
                numbered.append((int(match.group(1)), name))
            elif LEGACY_TEMP_NAME.match(name) is not None:
                orphans.append(name)
            else:
                raise JournalError("journal contains an unexpected entry")
        numbered.sort()
        orphans.sort()
        return numbered, orphans

    def orphan_temps(self) -> tuple[str, ...]:
        directory = self._directory_fd()
        try:
            _records, orphans = self._names(directory)
            return tuple(orphans)
        finally:
            os.close(directory)

    def read_all(self) -> tuple[dict[str, Any], ...]:
        """Read the committed chain and reject unsupported named temp artifacts."""
        directory = self._directory_fd()
        try:
            numbered, orphans = self._names(directory)
            if orphans:
                raise JournalError(
                    "journal contains unsupported named temporary evidence"
                )
            records: list[dict[str, Any]] = []
            previous = ZERO_HASH
            for expected_sequence, (sequence, name) in enumerate(numbered):
                if sequence != expected_sequence:
                    raise JournalError("journal sequence has a gap or rollback")
                fd = os.open(name, os.O_RDONLY | _no_follow(), dir_fd=directory)
                try:
                    metadata = os.fstat(fd)
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or metadata.st_nlink != 1
                        or metadata.st_uid != self.owner_uid
                        or stat.S_IMODE(metadata.st_mode) != 0o600
                    ):
                        raise JournalError("journal record metadata is unsafe")
                    chunks: list[bytes] = []
                    size = 0
                    while True:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > 1024 * 1024:
                            raise JournalError("journal record is too large")
                        chunks.append(chunk)
                finally:
                    os.close(fd)
                try:
                    record = strict_json.exact_object(
                        strict_json.loads(b"".join(chunks), maximum=1024 * 1024),
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
                        "journal record",
                    )
                except strict_json.StrictJSONError as exc:
                    raise JournalError("invalid journal record") from exc
                if record["schema"] != "phase-b.journal-record.v1":
                    raise JournalError("unsupported journal record schema")
                if (
                    record["sequence"] != sequence
                    or record["previous_hash"] != previous
                ):
                    raise JournalError("journal sequence/hash predecessor mismatch")
                if record["kind"] not in KINDS:
                    raise JournalError("unknown journal record kind")
                if not isinstance(record["action_id"], str) or not record["action_id"]:
                    raise JournalError("journal action id is invalid")
                unsigned = dict(record)
                claimed = unsigned.pop("record_hash")
                if claimed != strict_json.digest(unsigned):
                    raise JournalError("journal record hash mismatch")
                previous = claimed
                records.append(record)
            self._validate_actions(records)
            return tuple(records)
        finally:
            os.close(directory)

    @staticmethod
    def _validate_actions(records: list[dict[str, Any]]) -> None:
        intents: set[str] = set()
        terminals: set[str] = set()
        for record in records:
            action = record["action_id"]
            if record["kind"] == "intent":
                if action in intents:
                    raise JournalError("duplicate action intent")
                intents.add(action)
            elif record["kind"] in {"outcome", "recovery"}:
                if action not in intents or action in terminals:
                    raise JournalError("orphan or duplicate action outcome")
                terminals.add(action)

    def _publish_record(
        self,
        directory: int,
        final_name: str,
        data: bytes,
    ) -> None:
        fd = -1
        try:
            self.fault("before-anonymous-create")
            fd = _open_anonymous(directory)
            self.fault("after-anonymous-create")
            os.fchmod(fd, 0o600)
            self.fault("before-anonymous-write")
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise JournalError("short journal write")
                view = view[written:]
            self.fault("after-anonymous-write")
            self.fault("before-anonymous-fsync")
            os.fsync(fd)
            self.fault("after-anonymous-fsync")
            self.fault("before-link")
            _link_anonymous(fd, directory, final_name)
            self.fault("after-link")
            final = os.open(final_name, os.O_RDONLY | _no_follow(), dir_fd=directory)
            try:
                source_metadata = os.fstat(fd)
                final_metadata = os.fstat(final)
                if (
                    (source_metadata.st_dev, source_metadata.st_ino)
                    != (final_metadata.st_dev, final_metadata.st_ino)
                    or not stat.S_ISREG(final_metadata.st_mode)
                    or final_metadata.st_nlink != 1
                    or final_metadata.st_uid != self.owner_uid
                    or stat.S_IMODE(final_metadata.st_mode) != 0o600
                ):
                    raise JournalError(
                        "published journal inode differs from anonymous source"
                    )
            finally:
                os.close(final)
            self.fault("before-directory-fsync")
            os.fsync(directory)
            self.fault("after-directory-fsync")
        finally:
            if fd >= 0:
                os.close(fd)

    def append(self, kind: str, action_id: str, payload: Any) -> dict[str, Any]:
        if kind not in KINDS or not isinstance(action_id, str) or not action_id:
            raise JournalError("invalid journal append request")
        if not strict_json.is_json_value(payload):
            raise JournalError("journal payload is not JSON")
        if kind == "checkpoint" and not self._fault_enabled:
            if self._checkpoint_tail is None:
                existing = self.read_all()
                self._checkpoint_tail = (
                    len(existing),
                    existing[-1]["record_hash"] if existing else ZERO_HASH,
                )
            sequence, head = self._checkpoint_tail
            record = self.append_checkpoint_fast(
                action_id, payload, expected_sequence=sequence, expected_head=head
            )
            self._checkpoint_tail = (sequence + 1, record["record_hash"])
            return record
        directory = self._directory_fd()
        try:
            fcntl.flock(directory, fcntl.LOCK_EX)
            existing = self.read_all()
            pending = {
                record["action_id"] for record in existing if record["kind"] == "intent"
            }
            pending -= {
                record["action_id"]
                for record in existing
                if record["kind"] in {"outcome", "recovery"}
            }
            if kind in {"outcome", "recovery"} and action_id not in pending:
                raise JournalError("orphan or duplicate action outcome")
            if kind == "intent" and any(
                record["kind"] == "intent" and record["action_id"] == action_id
                for record in existing
            ):
                raise JournalError("duplicate action intent")
            sequence = len(existing)
            previous = existing[-1]["record_hash"] if existing else ZERO_HASH
            unsigned = {
                "schema": "phase-b.journal-record.v1",
                "sequence": sequence,
                "previous_hash": previous,
                "kind": kind,
                "action_id": action_id,
                "payload": payload,
                "recorded_at": format_time(self.clock()),
            }
            record = {**unsigned, "record_hash": strict_json.digest(unsigned)}
            data = strict_json.canonical(record) + b"\n"
            final_name = f"{sequence:016d}.json"
            try:
                self._publish_record(directory, final_name, data)
            except Exception:
                self._checkpoint_tail = None
                raise
            self._checkpoint_tail = (sequence + 1, record["record_hash"])
            return record
        except FileExistsError as exc:
            raise JournalError("journal sequence was concurrently claimed") from exc
        finally:
            os.close(directory)

    def append_checkpoint_fast(
        self,
        action_id: str,
        payload: Any,
        *,
        expected_sequence: int,
        expected_head: str,
    ) -> dict[str, Any]:
        """Append a checkpoint after a caller already verified and serialized the chain."""
        if not action_id or not strict_json.is_json_value(payload):
            raise JournalError("invalid fast checkpoint append")
        directory = self._directory_fd()
        try:
            fcntl.flock(directory, fcntl.LOCK_EX)
            if expected_sequence:
                tail_name = f"{expected_sequence - 1:016d}.json"
                tail_fd = os.open(
                    tail_name, os.O_RDONLY | _no_follow(), dir_fd=directory
                )
                try:
                    tail = strict_json.loads(os.read(tail_fd, 1024 * 1024))
                finally:
                    os.close(tail_fd)
                if tail.get("record_hash") != expected_head:
                    raise JournalError("checkpoint head changed concurrently")
            elif expected_head != ZERO_HASH:
                raise JournalError("checkpoint empty head mismatch")
            unsigned = {
                "schema": "phase-b.journal-record.v1",
                "sequence": expected_sequence,
                "previous_hash": expected_head,
                "kind": "checkpoint",
                "action_id": action_id,
                "payload": payload,
                "recorded_at": format_time(self.clock()),
            }
            record = {**unsigned, "record_hash": strict_json.digest(unsigned)}
            data = strict_json.canonical(record) + b"\n"
            final_name = f"{expected_sequence:016d}.json"
            try:
                self._publish_record(directory, final_name, data)
            except FileExistsError as exc:
                self._checkpoint_tail = None
                raise JournalError(
                    "journal sequence was concurrently claimed"
                ) from exc
            except Exception:
                self._checkpoint_tail = None
                raise
            return record
        finally:
            os.close(directory)

    def pending_intents(self) -> dict[str, dict[str, Any]]:
        pending: dict[str, dict[str, Any]] = {}
        for record in self.read_all():
            if record["kind"] == "intent":
                pending[record["action_id"]] = record
            elif record["kind"] in {"outcome", "recovery"}:
                pending.pop(record["action_id"], None)
        return pending

    def head(self) -> str:
        if self._checkpoint_tail is not None:
            return self._checkpoint_tail[1]
        records = self.read_all()
        return records[-1]["record_hash"] if records else ZERO_HASH
