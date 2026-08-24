"""Root-anchored trust and role-separated signature verification."""

from __future__ import annotations

import hashlib
import hmac
import os
import posixpath
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from . import strict_json

TRUST_ANCHOR_PATH = Path("/etc/phase-b/trust.json")
TRUST_SCHEMA = "phase-b.trust.v2"
NIX_STORE_OBJECT = re.compile(r"^/nix/store/[0-9a-z]{32}-[^/]+$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_NAME = re.compile(r"^[a-z][a-z0-9.-]{0,63}$")
SAFE_ATTEMPT = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")

REQUIRED_ROLES = frozenset(
    {
        "source-authorization",
        "source-execution",
        "offhost-collection",
        "reconstruction",
        "receipt",
        "sensor-audit",
        "sensor-user-journal",
        "sensor-systemd",
        "sensor-registry",
        "sensor-database",
        "sensor-provider-route",
        "sensor-custody",
        "sensor-identity",
        "sensor-time",
    }
)
REQUIRED_SCHEMA_NAMES = frozenset(
    {
        "trust",
        "signed-envelope",
        "operation-grant",
        "incident-rollback-grant",
        "baseline",
        "journal",
        "source-event",
        "raw-batch",
        "f0",
        "observation",
        "reconstruction",
        "receipt",
        "consumption",
        "receiver-refresh-segment",
    }
)
REQUIRED_NAMESPACE_ROLES = {
    "phase-b-baseline": "source-authorization",
    "phase-b-f0": "source-execution",
    "phase-b-observation": "offhost-collection",
    "phase-b-reconstruction": "reconstruction",
    "phase-b-receipt": "receipt",
    "phase-b-backup-restore": "source-authorization",
    "phase-b-consumption-grant": "source-authorization",
    "phase-b-operation-grant": "source-authorization",
    "phase-b-incident-rollback-grant": "source-authorization",
    "phase-b-source-event.audit": "sensor-audit",
    "phase-b-source-event.user-journal": "sensor-user-journal",
    "phase-b-source-event.systemd": "sensor-systemd",
    "phase-b-source-event.registry": "sensor-registry",
    "phase-b-source-event.database": "sensor-database",
    "phase-b-source-event.provider-route": "sensor-provider-route",
    "phase-b-source-event.custody": "sensor-custody",
    "phase-b-source-event.identity": "sensor-identity",
    "phase-b-source-event.time": "sensor-time",
}


class TrustError(RuntimeError):
    """Trust root, binding, or signature verification failed."""


@dataclass(frozen=True)
class ExecutableBinding:
    name: str
    path: Path
    closure: Path
    digest: str


@dataclass(frozen=True)
class SignerBinding:
    role: str
    signer_id: str
    algorithm: str
    public_key: str


@dataclass(frozen=True)
class SourceBinding:
    uid: int
    gid: int
    user: str
    home: str
    machine_id: str
    host_identity: str
    boot_id: str
    user_manager_id: str
    home_generation: str
    booted_closure: str
    user_manager_machine: str


@dataclass(frozen=True)
class TrustAnchor:
    signers: dict[str, SignerBinding]
    namespace_roles: dict[str, str]
    executables: dict[str, ExecutableBinding]
    registry_paths: tuple[str, ...]
    source: SourceBinding
    collector_identity: str
    authority_identities: dict[str, Any]
    effect_plan_digest: str
    rollback_plan_digest: str
    canonical_vectors_digest: str
    process_inventory_digest: str
    listener_inventory_digest: str
    runbook_digest: str
    schema_digests: dict[str, str]
    anchor_generation: int
    anchor_digest: str

    @property
    def source_uid(self) -> int:
        return self.source.uid


class SignatureVerifier(Protocol):
    algorithm: str

    def verify(self, public_key: str, message: bytes, signature: str) -> bool: ...


def require_safe_attempt_id(value: Any) -> str:
    if not isinstance(value, str) or SAFE_ATTEMPT.fullmatch(value) is None:
        raise TrustError("attempt id is not a public opaque identifier")
    return value


def _no_follow() -> int:
    value = getattr(os, "O_NOFOLLOW", 0)
    if not value:
        raise TrustError("O_NOFOLLOW is required")
    return value


def _relative_parts(path: Path, root: Path) -> tuple[str, ...]:
    if not path.is_absolute() or not root.is_absolute():
        raise TrustError("secure paths must be absolute")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise TrustError("path escapes trusted root") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise TrustError("invalid trusted path")
    return relative.parts


def _open_root_owned(
    path: Path,
    *,
    root: Path,
    owner_uid: int,
    final_mode: int | None,
    single_link: bool = True,
) -> tuple[int, os.stat_result]:
    parts = _relative_parts(path, root)
    no_follow = _no_follow()
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | no_follow
    fd = os.open(root, directory_flags)
    try:
        root_stat = os.fstat(fd)
        if root_stat.st_uid != owner_uid or root_stat.st_mode & 0o022:
            raise TrustError("trusted root is not owner-controlled")
        for part in parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=fd)
            os.close(fd)
            fd = child
            current = os.fstat(fd)
            if current.st_uid != owner_uid or current.st_mode & 0o022:
                raise TrustError("trust-anchor parent is not owner-controlled")
        result = os.open(parts[-1], os.O_RDONLY | no_follow, dir_fd=fd)
    except OSError as exc:
        raise TrustError("cannot securely open trusted path") from exc
    finally:
        os.close(fd)
    metadata = os.fstat(result)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(result)
        raise TrustError("trusted path is not a regular file")
    if metadata.st_uid != owner_uid or (
        final_mode is not None and stat.S_IMODE(metadata.st_mode) != final_mode
    ):
        os.close(result)
        raise TrustError("trusted file has wrong owner or mode")
    if single_link and metadata.st_nlink != 1:
        os.close(result)
        raise TrustError("trusted file must have exactly one link")
    return result, metadata


def _read_fd(fd: int, maximum: int = strict_json.MAX_JSON_BYTES) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(65536, maximum + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise TrustError("trusted file exceeds size limit")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise TrustError(f"invalid {label}")
    if value.upper() in {"UNKNOWN", "PLACEHOLDER", "TODO", "NONE", "DENIED"}:
        raise TrustError(f"placeholder {label}")
    return value


def _parse_anchor(value: Any) -> TrustAnchor:
    fields = {
        "schema",
        "anchor_generation",
        "signers",
        "namespace_roles",
        "executables",
        "source",
        "registry_paths",
        "collector_identity",
        "authority_identities",
        "effect_plan_digest",
        "rollback_plan_digest",
        "canonical_vectors_digest",
        "process_inventory_digest",
        "listener_inventory_digest",
        "runbook_digest",
        "schema_digests",
    }
    root = strict_json.exact_object(value, fields, "trust anchor")
    if root["schema"] != TRUST_SCHEMA:
        raise TrustError("unsupported trust-anchor schema")
    generation = root["anchor_generation"]
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
    ):
        raise TrustError("invalid anchor generation")

    raw_signers = root["signers"]
    if not isinstance(raw_signers, dict) or set(raw_signers) != REQUIRED_ROLES:
        raise TrustError("trust anchor does not contain the exact independent roles")
    signers: dict[str, SignerBinding] = {}
    signer_ids: set[str] = set()
    keys: set[str] = set()
    for role, raw in raw_signers.items():
        if SAFE_NAME.fullmatch(role) is None:
            raise TrustError("invalid signer role")
        item = strict_json.exact_object(raw, {"id", "algorithm", "public_key"}, role)
        signer_id = _identifier(item["id"], "signer identity")
        algorithm = _identifier(item["algorithm"], "signature algorithm")
        public_key = _identifier(item["public_key"], "public key")
        if signer_id in signer_ids or public_key in keys:
            raise TrustError("signer identities and keys must be distinct across roles")
        signer_ids.add(signer_id)
        keys.add(public_key)
        signers[role] = SignerBinding(role, signer_id, algorithm, public_key)
    if len({item.algorithm for item in signers.values()}) != 1:
        raise TrustError(
            "the fixed signature verifier must support every role algorithm"
        )

    namespace_roles = root["namespace_roles"]
    if namespace_roles != REQUIRED_NAMESPACE_ROLES:
        raise TrustError("namespace-to-role mapping is not the fixed policy")

    source = strict_json.exact_object(
        root["source"],
        {
            "uid",
            "gid",
            "user",
            "home",
            "machine_id",
            "host_identity",
            "boot_id",
            "user_manager_id",
            "home_generation",
            "booted_closure",
            "user_manager_machine",
        },
        "source binding",
    )
    for key in ("uid", "gid"):
        if (
            isinstance(source[key], bool)
            or not isinstance(source[key], int)
            or source[key] < 0
        ):
            raise TrustError("invalid source numeric identity")
    for key in (
        "user",
        "home",
        "machine_id",
        "host_identity",
        "boot_id",
        "user_manager_id",
        "home_generation",
        "user_manager_machine",
    ):
        _identifier(source[key], f"source {key}")
    if (
        not isinstance(source["booted_closure"], str)
        or SHA256.fullmatch(source["booted_closure"]) is None
    ):
        raise TrustError("source booted closure is not digest-bound")
    if (
        not source["home"].startswith("/")
        or posixpath.normpath(source["home"]) != source["home"]
    ):
        raise TrustError("source home must be normalized and absolute")

    registry_paths = root["registry_paths"]
    if (
        not isinstance(registry_paths, list)
        or len(registry_paths) != 6
        or len(set(registry_paths)) != 6
        or any(
            not isinstance(item, str)
            or not item.startswith("/")
            or posixpath.normpath(item) != item
            for item in registry_paths
        )
    ):
        raise TrustError("trust anchor must bind six distinct absolute registries")

    raw_executables = root["executables"]
    required_executables = {
        "executor",
        "collector",
        "verifier",
        "signature-verifier",
        "systemctl",
        "hermes",
        "reconstruction-runner",
        "network-monitor",
        "write-monitor",
        "process-inspector",
        "custody-reader",
        "source-sensor",
        "artifact-reader",
        "hermes-mutation-adapter",
        "privilege-dropper",
        "receiver-client",
        "observation-signer",
        "source-signer",
    }
    if (
        not isinstance(raw_executables, dict)
        or set(raw_executables) != required_executables
    ):
        raise TrustError("trusted executable closure set is incomplete")
    executables: dict[str, ExecutableBinding] = {}
    for name, raw in raw_executables.items():
        if not isinstance(name, str) or SAFE_NAME.fullmatch(name) is None:
            raise TrustError("invalid executable name")
        item = strict_json.exact_object(raw, {"path", "closure", "digest"}, name)
        if not all(isinstance(item[key], str) for key in item):
            raise TrustError("invalid executable binding")
        if (
            posixpath.normpath(item["path"]) != item["path"]
            or posixpath.normpath(item["closure"]) != item["closure"]
        ):
            raise TrustError("executable path contains non-normalized components")
        path, closure = Path(item["path"]), Path(item["closure"])
        if NIX_STORE_OBJECT.fullmatch(str(closure)) is None:
            raise TrustError("executable closure is not an exact Nix store object")
        try:
            path.relative_to(closure)
        except ValueError as exc:
            raise TrustError("executable path is outside its bound closure") from exc
        if path == closure or SHA256.fullmatch(item["digest"]) is None:
            raise TrustError("invalid executable path or digest")
        executables[name] = ExecutableBinding(name, path, closure, item["digest"])

    for key in (
        "effect_plan_digest",
        "rollback_plan_digest",
        "canonical_vectors_digest",
        "process_inventory_digest",
        "listener_inventory_digest",
        "runbook_digest",
    ):
        if not isinstance(root[key], str) or SHA256.fullmatch(root[key]) is None:
            raise TrustError(f"invalid {key}")
    schema_digests = root["schema_digests"]
    if (
        not isinstance(schema_digests, dict)
        or set(schema_digests) != REQUIRED_SCHEMA_NAMES
        or any(
            not isinstance(name, str)
            or SAFE_NAME.fullmatch(name) is None
            or not isinstance(item, str)
            or SHA256.fullmatch(item) is None
            for name, item in schema_digests.items()
        )
    ):
        raise TrustError("invalid schema bindings")

    authority = strict_json.exact_object(
        root["authority_identities"],
        {"generic", "alpha0", "dedicated_axis_route"},
        "authority identities",
    )
    for name in ("generic", "alpha0"):
        item = strict_json.exact_object(
            authority[name],
            {
                "route_identity",
                "service_identity",
                "session_identity",
                "profile_identity",
            },
            name,
        )
        for key, identity in item.items():
            _identifier(identity, f"{name} {key}")
    if authority["dedicated_axis_route"] != "ABSENT":
        raise TrustError("anchor must bind absence of a dedicated AXIS route")
    authority_values = [
        *authority["generic"].values(),
        *authority["alpha0"].values(),
    ]
    if len(set(authority_values)) != 8:
        raise TrustError("generic and Alpha0 authority identities are not all distinct")

    collector_identity = _identifier(root["collector_identity"], "collector identity")
    return TrustAnchor(
        signers=signers,
        namespace_roles=dict(namespace_roles),
        executables=executables,
        registry_paths=tuple(registry_paths),
        source=SourceBinding(
            source["uid"],
            source["gid"],
            source["user"],
            source["home"],
            source["machine_id"],
            source["host_identity"],
            source["boot_id"],
            source["user_manager_id"],
            source["home_generation"],
            source["booted_closure"],
            source["user_manager_machine"],
        ),
        collector_identity=collector_identity,
        authority_identities=authority,
        effect_plan_digest=root["effect_plan_digest"],
        rollback_plan_digest=root["rollback_plan_digest"],
        canonical_vectors_digest=root["canonical_vectors_digest"],
        process_inventory_digest=root["process_inventory_digest"],
        listener_inventory_digest=root["listener_inventory_digest"],
        runbook_digest=root["runbook_digest"],
        schema_digests=dict(schema_digests),
        anchor_generation=generation,
        anchor_digest=strict_json.digest(root),
    )


def _load_trust_anchor_at(
    path: Path, *, root: Path, owner_uid: int, allow_test_algorithms: bool = True
) -> TrustAnchor:
    fd, before = _open_root_owned(
        path, root=root, owner_uid=owner_uid, final_mode=0o400
    )
    try:
        data = _read_fd(fd)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise TrustError("trust anchor changed while being read")
    try:
        anchor = _parse_anchor(strict_json.loads(data))
        if not allow_test_algorithms and any(
            item.algorithm == "hmac-sha256-test" for item in anchor.signers.values()
        ):
            raise TrustError("fixture HMAC signatures are forbidden in production")
        return anchor
    except strict_json.StrictJSONError as exc:
        raise TrustError("invalid trust anchor") from exc


def load_trust_anchor() -> TrustAnchor:
    return _load_trust_anchor_at(
        TRUST_ANCHOR_PATH,
        root=Path("/"),
        owner_uid=0,
        allow_test_algorithms=False,
    )


def verify_executable(
    binding: ExecutableBinding,
    *,
    store_root: Path = Path("/nix/store"),
    owner_uid: int = 0,
) -> None:
    expected_closure = store_root / binding.closure.name
    try:
        relative = binding.path.relative_to(binding.closure)
    except ValueError as exc:
        raise TrustError("executable escaped its closure") from exc
    fd, metadata = _open_root_owned(
        expected_closure / relative,
        root=store_root,
        owner_uid=owner_uid,
        final_mode=None,
        single_link=False,
    )
    try:
        if not metadata.st_mode & 0o111 or metadata.st_mode & 0o022:
            raise TrustError("bound executable is not immutable and executable")
        actual = "sha256:" + hashlib.sha256(_read_fd(fd)).hexdigest()
    finally:
        os.close(fd)
    if not hmac.compare_digest(actual, binding.digest):
        raise TrustError("bound executable digest mismatch")


class HMACSHA256Verifier:
    """Deterministic fixture verifier; HMAC keys are forbidden in production anchors."""

    algorithm = "hmac-sha256-test"

    def verify(self, public_key: str, message: bytes, signature: str) -> bool:
        try:
            expected = hmac.new(
                bytes.fromhex(public_key), message, hashlib.sha256
            ).hexdigest()
        except ValueError:
            return False
        return hmac.compare_digest(signature, "hmac-sha256:" + expected)

    @staticmethod
    def sign(key_hex: str, message: bytes) -> str:
        value = hmac.new(bytes.fromhex(key_hex), message, hashlib.sha256).hexdigest()
        return "hmac-sha256:" + value


class BoundExecutableVerifier:
    def __init__(self, binding: ExecutableBinding, algorithm: str):
        self.binding = binding
        self.algorithm = algorithm

    def verify(self, public_key: str, message: bytes, signature: str) -> bool:
        result = subprocess.run(
            [str(self.binding.path), "verify", public_key, signature],
            input=message,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            env={"PATH": "", "LANG": "C", "LC_ALL": "C"},
        )
        return result.returncode == 0


def verify_envelope(
    envelope: Any,
    anchor: TrustAnchor,
    verifier: SignatureVerifier,
    namespace: str,
) -> dict[str, Any]:
    item = strict_json.exact_object(
        envelope,
        {"schema", "namespace", "signer_id", "payload", "signature"},
        "signed envelope",
    )
    if item["schema"] != "phase-b.signed-envelope.v1":
        raise TrustError("unsupported signed-envelope schema")
    role = anchor.namespace_roles.get(namespace)
    if role is None or item["namespace"] != namespace:
        raise TrustError("signature namespace is not trusted")
    signer = anchor.signers[role]
    if item["signer_id"] != signer.signer_id:
        raise TrustError("signature signer role is confused")
    if verifier.algorithm != signer.algorithm:
        raise TrustError("signature algorithm is not anchored")
    if not isinstance(item["signature"], str):
        raise TrustError("invalid signature encoding")
    message = strict_json.canonical(
        {
            "schema": item["schema"],
            "namespace": item["namespace"],
            "signer_id": item["signer_id"],
            "payload": item["payload"],
        }
    )
    if not verifier.verify(signer.public_key, message, item["signature"]):
        raise TrustError("signature verification failed")
    if not isinstance(item["payload"], dict):
        raise TrustError("signed payload must be an object")
    return item["payload"]
