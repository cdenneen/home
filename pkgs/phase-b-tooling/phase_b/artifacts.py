"""Opaque, digest-bound evidence artifacts read from a fixed owner-only store."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from . import strict_json

ARTIFACT_ID = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
ARTIFACT_REF_FIELDS = frozenset({"id", "digest", "media_type", "owner_only"})
MAX_EVIDENCE_ARTIFACT_BYTES = 64 * 1024 * 1024


class ArtifactError(RuntimeError):
    """An evidence artifact was missing, unsafe, or did not match its binding."""


@dataclass(frozen=True)
class ArtifactRef:
    id: str
    digest: str
    media_type: str
    owner_only: bool

    @classmethod
    def parse(cls, value: Any) -> ArtifactRef:
        try:
            item = strict_json.exact_object(
                value, set(ARTIFACT_REF_FIELDS), "artifact ref"
            )
        except strict_json.StrictJSONError as exc:
            raise ArtifactError("artifact reference shape is invalid") from exc
        if not isinstance(item["id"], str) or ARTIFACT_ID.fullmatch(item["id"]) is None:
            raise ArtifactError("artifact id is not an opaque safe identifier")
        if (
            not isinstance(item["digest"], str)
            or DIGEST.fullmatch(item["digest"]) is None
        ):
            raise ArtifactError("artifact digest is invalid")
        if item["digest"] == "sha256:" + "0" * 64:
            raise ArtifactError("artifact digest is a placeholder")
        if item["media_type"] not in {"application/json", "application/octet-stream"}:
            raise ArtifactError("artifact media type is not fixed")
        if not isinstance(item["owner_only"], bool):
            raise ArtifactError("artifact confidentiality label is not boolean")
        return cls(item["id"], item["digest"], item["media_type"], item["owner_only"])

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "digest": self.digest,
            "media_type": self.media_type,
            "owner_only": self.owner_only,
        }


class ArtifactStore(Protocol):
    """The caller supplies opaque IDs, never paths; production fixes the root."""

    def read(self, artifact_id: str) -> bytes: ...


def bytes_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def read_artifact(
    store: ArtifactStore,
    value: Any,
    *,
    owner_only: bool | None = None,
    maximum: int = strict_json.MAX_JSON_BYTES,
) -> tuple[ArtifactRef, bytes]:
    ref = ArtifactRef.parse(value)
    if owner_only is not None and ref.owner_only is not owner_only:
        raise ArtifactError("artifact confidentiality class mismatch")
    data = store.read(ref.id)
    if not isinstance(data, bytes) or len(data) > maximum:
        raise ArtifactError("artifact is unavailable or exceeds its fixed bound")
    if bytes_digest(data) != ref.digest:
        raise ArtifactError("artifact bytes do not match their bound digest")
    return ref, data


def read_json_artifact(
    store: ArtifactStore,
    value: Any,
    *,
    owner_only: bool | None = None,
    maximum: int = strict_json.MAX_JSON_BYTES,
) -> tuple[ArtifactRef, Any]:
    ref, data = read_artifact(
        store, value, owner_only=owner_only, maximum=maximum
    )
    if ref.media_type != "application/json":
        raise ArtifactError("JSON evidence has the wrong media type")
    try:
        return ref, strict_json.loads_canonical(data, maximum=maximum)
    except strict_json.StrictJSONError as exc:
        raise ArtifactError("artifact is not strict canonical JSON") from exc


class DirectoryArtifactStore:
    """Production store rooted outside operator-controlled state.

    IDs map to ``<root>/<id>.artifact``. Every component is opened no-follow;
    files must be root-owned, mode 0400, regular, and single-linked.
    """

    def __init__(
        self,
        root: Path = Path("/var/lib/phase-b/evidence"),
        *,
        owner_uid: int = 0,
        secure_root: Path = Path("/"),
        maximum: int = MAX_EVIDENCE_ARTIFACT_BYTES,
    ):
        self.root = root
        self.owner_uid = owner_uid
        self.secure_root = secure_root
        self.maximum = maximum

    def _open_root(self) -> int:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        if not no_follow:
            raise ArtifactError("O_NOFOLLOW is required")
        if (
            not self.root.is_absolute()
            or any(part in {"", ".", ".."} for part in self.root.parts[1:])
            or not self.secure_root.is_absolute()
        ):
            raise ArtifactError("artifact root must be normalized and absolute")
        flags = os.O_RDONLY | os.O_DIRECTORY | no_follow
        root_fd = os.open(self.secure_root, flags)
        try:
            relative = self.root.relative_to(self.secure_root)
            for index, part in enumerate(relative.parts):
                child = os.open(part, flags, dir_fd=root_fd)
                os.close(root_fd)
                root_fd = child
                metadata = os.fstat(root_fd)
                forbidden_mode = 0o077 if index == len(relative.parts) - 1 else 0o022
                if metadata.st_uid != self.owner_uid or metadata.st_mode & forbidden_mode:
                    raise ArtifactError("artifact root chain is not owner-controlled")
            return root_fd
        except Exception:
            os.close(root_fd)
            raise

    def write(self, label: str, data: bytes) -> ArtifactRef:
        if (
            not isinstance(data, bytes)
            or not 0 < len(data) <= self.maximum
            or re.fullmatch(r"[a-z][a-z0-9-]{1,23}", label) is None
        ):
            raise ArtifactError("capture artifact label or bytes are invalid")
        content_digest = bytes_digest(data)
        artifact_id = f"{label}-{content_digest.removeprefix('sha256:')[:32]}"
        root_fd = self._open_root()
        temporary = f".{artifact_id}.{os.getpid()}.tmp"
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        try:
            try:
                existing = os.open(
                    artifact_id + ".artifact", os.O_RDONLY | no_follow, dir_fd=root_fd
                )
            except FileNotFoundError:
                existing = None
            if existing is not None:
                os.close(existing)
                current = self.read(artifact_id)
                if current != data:
                    raise ArtifactError("capture artifact id collision")
                return ArtifactRef(artifact_id, content_digest, "application/json", True)
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow,
                0o400,
                dir_fd=root_fd,
            )
            try:
                os.fchmod(fd, 0o400)
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise ArtifactError("capture artifact write was short")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.link(
                    temporary,
                    artifact_id + ".artifact",
                    src_dir_fd=root_fd,
                    dst_dir_fd=root_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                current = self.read(artifact_id)
                if current != data:
                    raise ArtifactError("capture artifact publication raced")
            finally:
                try:
                    os.unlink(temporary, dir_fd=root_fd)
                except FileNotFoundError:
                    pass
            os.fsync(root_fd)
        except OSError as exc:
            try:
                os.unlink(temporary, dir_fd=root_fd)
            except OSError:
                pass
            raise ArtifactError("capture artifact cannot be published safely") from exc
        finally:
            os.close(root_fd)
        if self.read(artifact_id) != data:
            raise ArtifactError("capture artifact durability check failed")
        return ArtifactRef(artifact_id, content_digest, "application/json", True)

    def read(self, artifact_id: str) -> bytes:
        if ARTIFACT_ID.fullmatch(artifact_id) is None:
            raise ArtifactError("unsafe artifact id")
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        root_fd = self._open_root()
        try:
            fd = os.open(
                artifact_id + ".artifact", os.O_RDONLY | no_follow, dir_fd=root_fd
            )
        except OSError as exc:
            raise ArtifactError("artifact cannot be securely opened") from exc
        finally:
            os.close(root_fd)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != self.owner_uid
                or stat.S_IMODE(before.st_mode) != 0o400
                or before.st_nlink != 1
            ):
                raise ArtifactError("artifact owner/mode/type/link count is unsafe")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > self.maximum:
                    raise ArtifactError("artifact exceeds fixed size limit")
                chunks.append(chunk)
            after = os.fstat(fd)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ArtifactError("artifact changed during read")
            return b"".join(chunks)
        finally:
            os.close(fd)
