"""Stable-parent six-registry validation for Hermes atomic replacement."""

from __future__ import annotations

import fcntl
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from . import strict_json

REGISTRY_COUNT = 6
TARGETS_BY_INDEX: dict[int, tuple[str, ...]] = {
    0: ("bb8d50dc3332", "a9c0b0e9bcca"),
    2: ("81776a5f93c5",),
    3: ("81776a5f93c5",),
}
TARGET_DELTAS = tuple(
    (index, job_id) for index, job_ids in TARGETS_BY_INDEX.items() for job_id in job_ids
)
RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


class RegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegistryExpectation:
    path: str
    owner_uid: int
    mode: int
    device: int
    inode: int
    document: Any


@dataclass
class RegistryHandle:
    expectation: RegistryExpectation
    parent_fd: int
    filename: str
    fd: int
    lock_fd: int
    hermes_home_fd: int
    profile: str | None
    fingerprint: tuple[int, int, int]

    @property
    def identity(self) -> tuple[int, int]:
        metadata = os.fstat(self.fd)
        return metadata.st_dev, metadata.st_ino


@dataclass(frozen=True)
class Delta:
    registry_index: int
    job_id: str


FIXED_DELTAS = tuple(Delta(index, job_id) for index, job_id in TARGET_DELTAS)


def _no_follow() -> int:
    value = getattr(os, "O_NOFOLLOW", 0)
    if not value:
        raise RegistryError("O_NOFOLLOW is required")
    return value


def _parts(path: Path) -> tuple[str, ...]:
    if not path.is_absolute() or any(
        item in {"", ".", ".."} for item in path.parts[1:]
    ):
        raise RegistryError("registry path must be normalized and absolute")
    return path.parts[1:]


def _open_parent(path: Path) -> tuple[int, str]:
    parts = _parts(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | _no_follow()
    directory = os.open("/", flags)
    try:
        for part in parts[:-1]:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        return directory, parts[-1]
    except OSError as exc:
        os.close(directory)
        raise RegistryError(f"cannot securely open registry parent: {path}") from exc


def _open_directory(path: Path) -> int:
    parts = _parts(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | _no_follow()
    directory = os.open("/", flags)
    try:
        for part in parts:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        return directory
    except OSError as exc:
        os.close(directory)
        raise RegistryError(f"cannot securely open Hermes home: {path}") from exc


def _hermes_home(path: Path) -> tuple[Path, str | None]:
    parts = path.parts
    if len(parts) < 3 or parts[-2:] != ("cron", "jobs.json"):
        raise RegistryError("registry is not a Hermes cron/jobs.json path")
    try:
        profile_index = parts.index("profiles")
    except ValueError:
        return path.parent.parent, None
    if profile_index + 3 >= len(parts) or parts[profile_index + 2 :] != (
        "cron",
        "jobs.json",
    ):
        raise RegistryError("profile registry path is malformed")
    return Path(*parts[:profile_index]), parts[profile_index + 1]


def _open_file(parent_fd: int, filename: str) -> int:
    try:
        return os.open(filename, os.O_RDONLY | _no_follow(), dir_fd=parent_fd)
    except OSError as exc:
        raise RegistryError("cannot securely open registry from pinned parent") from exc


def _read(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > strict_json.MAX_JSON_BYTES:
            raise RegistryError("registry exceeds size limit")
        chunks.append(chunk)


def _validate_metadata(
    metadata: os.stat_result,
    expected: RegistryExpectation,
    *,
    identity: tuple[int, int] | None = None,
) -> None:
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise RegistryError("registry is not a single-link regular file")
    if (
        metadata.st_uid != expected.owner_uid
        or stat.S_IMODE(metadata.st_mode) != expected.mode
    ):
        raise RegistryError("registry owner/mode mismatch")
    wanted = identity or (expected.device, expected.inode)
    if (metadata.st_dev, metadata.st_ino) != wanted:
        raise RegistryError("registry identity mismatch")


def _jobs(document: Any) -> list[dict[str, Any]]:
    if (
        not isinstance(document, dict)
        or not set(document) <= {"jobs", "updated_at"}
        or "jobs" not in document
    ):
        raise RegistryError(
            "registry must contain only jobs and optional Hermes updated_at"
        )
    if "updated_at" in document and (
        not isinstance(document["updated_at"], str)
        or RFC3339.fullmatch(document["updated_at"]) is None
    ):
        raise RegistryError("registry updated_at is not RFC3339")
    jobs = document["jobs"]
    if not isinstance(jobs, list):
        raise RegistryError("registry jobs must be an array")
    identifiers: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict) or "id" not in job or "enabled" not in job:
            raise RegistryError("registry job is incomplete")
        if not isinstance(job["id"], str) or not job["id"] or job["id"] in identifiers:
            raise RegistryError("registry job id is invalid or duplicated")
        if not isinstance(job["enabled"], bool):
            raise RegistryError("registry enabled must be boolean")
        identifiers.add(job["id"])
    return jobs


def _validate_applied_document(
    baseline: Any,
    actual: Any,
    applied_ids: tuple[str, ...],
) -> None:
    baseline_jobs = _jobs(baseline)
    actual_jobs = _jobs(actual)
    if [item["id"] for item in actual_jobs] != [item["id"] for item in baseline_jobs]:
        raise RegistryError("Hermes changed job cardinality/order/identity")
    baseline_by_id = {item["id"]: item for item in baseline_jobs}
    actual_by_id = {item["id"]: item for item in actual_jobs}
    for job_id, original in baseline_by_id.items():
        current = actual_by_id[job_id]
        if job_id not in applied_ids:
            if current != original:
                raise RegistryError("non-target job changed")
            continue
        if original.get("enabled") is not True or current.get("enabled") is not False:
            raise RegistryError("target enabled transition is not exact")
        allowed = {"enabled", "state", "paused_at", "paused_reason"}
        changed = {
            key
            for key in set(original) | set(current)
            if key not in original
            or key not in current
            or original[key] != current[key]
        }
        if changed - allowed:
            raise RegistryError("Hermes changed non-pause target metadata")
        if (
            not {"enabled", "state", "paused_at", "paused_reason"} <= set(current)
            or current["state"] != "paused"
            or current["paused_reason"] is not None
        ):
            raise RegistryError("target pause state/reason is not exact")
        paused_at = current.get("paused_at")
        if not isinstance(paused_at, str) or RFC3339.fullmatch(paused_at) is None:
            raise RegistryError("target paused_at is not RFC3339")
    if applied_ids:
        if set(actual) != {"jobs", "updated_at"}:
            raise RegistryError("Hermes atomic save metadata is missing or widened")
    elif actual != baseline:
        raise RegistryError("baseline registry changed before mutation")


def document_after(document: Any, job_ids: tuple[str, ...]) -> Any:
    """Compatibility helper: only the semantic enabled projection is deterministic."""
    import copy

    result = copy.deepcopy(document)
    by_id = {job["id"]: job for job in _jobs(result)}
    for job_id in job_ids:
        if job_id not in by_id or by_id[job_id]["enabled"] is not True:
            raise RegistryError(f"target job missing or not enabled: {job_id}")
        by_id[job_id]["enabled"] = False
        by_id[job_id]["state"] = "paused"
        by_id[job_id]["paused_at"] = "1970-01-01T00:00:00+00:00"
        by_id[job_id]["paused_reason"] = None
    if job_ids:
        result["updated_at"] = "1970-01-01T00:00:00+00:00"
    return result


class RegistrySet:
    """Pinned parent FDs with Hermes lock and atomic-replace accounting."""

    def __init__(
        self,
        expectations: tuple[RegistryExpectation, ...],
        trusted_paths: tuple[str, ...],
    ):
        if len(expectations) != REGISTRY_COUNT or len(trusted_paths) != REGISTRY_COUNT:
            raise RegistryError("exactly six registries are required")
        if (
            tuple(item.path for item in expectations) != trusted_paths
            or len(set(trusted_paths)) != REGISTRY_COUNT
        ):
            raise RegistryError(
                "registry baseline paths do not match six distinct trust paths"
            )
        if len({(item.device, item.inode) for item in expectations}) != REGISTRY_COUNT:
            raise RegistryError("registry baseline aliases a physical file")
        for item in expectations:
            _jobs(item.document)
        for index, ids in TARGETS_BY_INDEX.items():
            found = {job["id"] for job in _jobs(expectations[index].document)}
            if not set(ids) <= found:
                raise RegistryError("baseline is missing a fixed target")
            if any(
                next(
                    job
                    for job in _jobs(expectations[index].document)
                    if job["id"] == job_id
                )["enabled"]
                is not True
                for job_id in ids
            ):
                raise RegistryError("baseline target is not enabled")
        self.expectations = expectations
        self.trusted_paths = trusted_paths
        self.handles: list[RegistryHandle] = []
        self.last_documents: tuple[Any, ...] = tuple(
            item.document for item in expectations
        )
        self.last_digests: tuple[str, ...] = tuple(
            strict_json.digest(item.document) for item in expectations
        )
        self.session_active = False

    @contextmanager
    def _locks(self) -> Iterator[None]:
        if not self.session_active:
            raise RegistryError("six-registry mutation session is not active")
        yield

    def acquire(
        self,
        applied: tuple[Delta, ...] = (),
        *,
        allow_restored_indices: frozenset[int] = frozenset(),
    ) -> None:
        """Acquire the signed baseline or a journal-derived recovery prefix."""
        if self.handles:
            raise RegistryError("registries are already acquired")
        grouped = self._group(applied)
        try:
            for index, expected in enumerate(self.expectations):
                parent_fd, filename = _open_parent(Path(expected.path))
                home_path, profile = _hermes_home(Path(expected.path))
                home_fd = _open_directory(home_path)
                try:
                    fd = _open_file(parent_fd, filename)
                    lock_fd = os.open(
                        ".jobs.lock",
                        os.O_RDWR | _no_follow(),
                        dir_fd=parent_fd,
                    )
                    metadata = os.fstat(fd)
                    _validate_metadata(
                        metadata,
                        expected,
                        identity=(metadata.st_dev, metadata.st_ino),
                    )
                    mutated = bool(grouped.get(index))
                    baseline_identity = (expected.device, expected.inode)
                    current_is_baseline = (
                        metadata.st_dev,
                        metadata.st_ino,
                    ) == baseline_identity
                    # Initial admission must still pin the signed baseline inode.
                    # Recovery with an applied prefix is content/postimage based:
                    # filesystems may legitimately reuse the historical inode after
                    # multiple Hermes atomic replacements.
                    if (
                        not mutated
                        and not current_is_baseline
                        and index not in allow_restored_indices
                    ):
                        raise RegistryError(
                            f"recovery prefix identity mismatch at registry {index}"
                        )
                    document = strict_json.loads(_read(fd))
                    _validate_applied_document(
                        expected.document, document, grouped.get(index, ())
                    )
                except Exception:
                    os.close(home_fd)
                    os.close(parent_fd)
                    raise
                self.handles.append(
                    RegistryHandle(
                        expected,
                        parent_fd,
                        filename,
                        fd,
                        lock_fd,
                        home_fd,
                        profile,
                        (metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns),
                    )
                )
            if len({item.identity for item in self.handles}) != REGISTRY_COUNT:
                raise RegistryError("live registries alias a physical file")
            lock_stats = [os.fstat(item.lock_fd) for item in self.handles]
            if len(
                {(item.st_dev, item.st_ino) for item in lock_stats}
            ) != REGISTRY_COUNT or any(
                not stat.S_ISREG(item.st_mode)
                or item.st_nlink != 1
                or item.st_uid != handle.expectation.owner_uid
                or stat.S_IMODE(item.st_mode) != handle.expectation.mode
                for item, handle in zip(lock_stats, self.handles, strict=True)
            ):
                raise RegistryError("Hermes locks are aliased or have unsafe metadata")
            # Hold all six exact Hermes locks until close; preflight, intent,
            # mutation, postvalidation, and recovery therefore share one session.
            for handle in self.handles:
                fcntl.flock(handle.lock_fd, fcntl.LOCK_EX)
            self.session_active = True
            for handle in self.handles:
                self._validate_lock_path(handle)
            self.revalidate(applied)
        except strict_json.StrictJSONError as exc:
            self.close()
            raise RegistryError("registry is not strict JSON") from exc
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.session_active:
            for handle in reversed(self.handles):
                try:
                    fcntl.flock(handle.lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        self.session_active = False
        for handle in self.handles:
            for fd in (
                handle.fd,
                handle.lock_fd,
                handle.parent_fd,
                handle.hermes_home_fd,
            ):
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.handles.clear()

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def hermes_invocation(
        self, delta: Delta
    ) -> tuple[str, str | None, tuple[int, ...]]:
        if not self.session_active or delta not in FIXED_DELTAS:
            raise RegistryError("Hermes invocation requires the fixed active session")
        handle = self.handles[delta.registry_index]
        # The anchored adapter receives the pinned home and the already-held
        # Hermes lock descriptor. It must reuse that open-file-description;
        # unmodified Hermes binaries that reopen/degrade the lock are rejected.
        return (
            f"/proc/self/fd/{handle.hermes_home_fd}",
            handle.profile,
            (handle.hermes_home_fd, handle.parent_fd, handle.lock_fd),
        )

    @staticmethod
    def _validate_lock_path(handle: RegistryHandle) -> None:
        try:
            reopened = os.open(
                ".jobs.lock", os.O_RDONLY | _no_follow(), dir_fd=handle.parent_fd
            )
        except OSError as exc:
            raise RegistryError("Hermes lock pathname is unavailable") from exc
        try:
            held, current = os.fstat(handle.lock_fd), os.fstat(reopened)
            if (
                (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)
                or not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or current.st_uid != handle.expectation.owner_uid
                or stat.S_IMODE(current.st_mode) != handle.expectation.mode
            ):
                raise RegistryError("Hermes lock pathname was replaced or aliased")
        finally:
            os.close(reopened)

    @staticmethod
    def _group(applied: tuple[Delta, ...]) -> dict[int, tuple[str, ...]]:
        if len(applied) > len(FIXED_DELTAS) or applied != FIXED_DELTAS[: len(applied)]:
            raise RegistryError("deltas are not the fixed cumulative sequence")
        grouped: dict[int, list[str]] = {}
        for delta in applied:
            grouped.setdefault(delta.registry_index, []).append(delta.job_id)
        return {key: tuple(value) for key, value in grouped.items()}

    def expected_documents(self, applied: tuple[Delta, ...]) -> tuple[Any, ...]:
        grouped = self._group(applied)
        return tuple(
            document_after(item.document, grouped.get(index, ()))
            for index, item in enumerate(self.expectations)
        )

    def revalidate(
        self,
        applied: tuple[Delta, ...] = (),
        *,
        changed_delta: Delta | None = None,
    ) -> tuple[str, ...]:
        if len(self.handles) != REGISTRY_COUNT:
            raise RegistryError("all registries must remain acquired")
        grouped = self._group(applied)
        if changed_delta is not None and changed_delta not in FIXED_DELTAS:
            raise RegistryError("changed registry is outside the fixed delta set")
        documents: list[Any] = []
        identities: set[tuple[int, int]] = set()
        with self._locks():
            for index, handle in enumerate(self.handles):
                self._validate_lock_path(handle)
                reopened_parent, reopened_name = _open_parent(
                    Path(handle.expectation.path)
                )
                try:
                    if reopened_name != handle.filename or (
                        os.fstat(reopened_parent).st_dev,
                        os.fstat(reopened_parent).st_ino,
                    ) != (
                        os.fstat(handle.parent_fd).st_dev,
                        os.fstat(handle.parent_fd).st_ino,
                    ):
                        raise RegistryError("registry parent path was replaced")
                finally:
                    os.close(reopened_parent)
                new_fd = _open_file(handle.parent_fd, handle.filename)
                try:
                    before = os.fstat(new_fd)
                    _validate_metadata(
                        before,
                        handle.expectation,
                        identity=(before.st_dev, before.st_ino),
                    )
                    old = os.fstat(handle.fd)
                    replaced = (before.st_dev, before.st_ino) != (
                        old.st_dev,
                        old.st_ino,
                    )
                    expected_replacement = (
                        changed_delta is not None
                        and index == changed_delta.registry_index
                    )
                    if replaced != expected_replacement:
                        raise RegistryError(
                            "unexpected or missing Hermes atomic replacement"
                        )
                    if replaced and old.st_nlink != 0:
                        raise RegistryError("replaced registry preimage remains linked")
                    if not replaced and old.st_nlink != 1:
                        raise RegistryError("current registry hard-link state changed")
                    if (
                        not replaced
                        and (old.st_size, old.st_mtime_ns, old.st_ctime_ns)
                        != handle.fingerprint
                    ):
                        raise RegistryError("registry was modified in place")
                    raw = _read(new_fd)
                    after = os.fstat(new_fd)
                    if (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                        raise RegistryError("registry changed during stable read")
                    document = strict_json.loads(raw)
                    _validate_applied_document(
                        handle.expectation.document, document, grouped.get(index, ())
                    )
                    identity = (after.st_dev, after.st_ino)
                    if identity in identities:
                        raise RegistryError(
                            "post-mutation registries alias a physical file"
                        )
                    identities.add(identity)
                    documents.append(document)
                    self._validate_lock_path(handle)
                    if replaced:
                        os.close(handle.fd)
                        handle.fd = new_fd
                        handle.fingerprint = (
                            after.st_size,
                            after.st_mtime_ns,
                            after.st_ctime_ns,
                        )
                        new_fd = -1
                except strict_json.StrictJSONError as exc:
                    raise RegistryError("registry is no longer strict JSON") from exc
                finally:
                    if new_fd >= 0:
                        os.close(new_fd)
        self.last_documents = tuple(documents)
        self.last_digests = tuple(strict_json.digest(item) for item in documents)
        return self.last_digests

    def evidence(self) -> tuple[dict[str, Any], ...]:
        if len(self.last_documents) != REGISTRY_COUNT:
            raise RegistryError("registry evidence unavailable")
        return tuple(
            {
                "index": index,
                "path": handle.expectation.path,
                "device": os.fstat(handle.fd).st_dev,
                "inode": os.fstat(handle.fd).st_ino,
                "digest": strict_json.digest(document),
                "document": document,
            }
            for index, (handle, document) in enumerate(
                zip(self.handles, self.last_documents, strict=True)
            )
        )
