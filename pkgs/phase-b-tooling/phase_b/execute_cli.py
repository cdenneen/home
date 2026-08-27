"""Fixed-boundary Phase B executor entry point."""

from __future__ import annotations

import os
import secrets
import select
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from . import strict_json
from .artifacts import ArtifactStore, DirectoryArtifactStore
from .cli_common import run_without_options
from .executor import (
    BoundCommandBackend,
    ExecutionBackend,
    Executor,
    PreservedIdentity,
    UnitExpectation,
    UnitState,
)
from .journal import Journal
from .registry import FIXED_DELTAS, RegistryExpectation, RegistrySet
from .trust import (
    BoundExecutableVerifier,
    ExecutableBinding,
    SignatureVerifier,
    TrustAnchor,
    TrustError,
    _open_root_owned,
    _read_fd,
    verify_envelope,
    verify_executable,
)
from .verifier import (
    _baseline,
    _load_schema,
    _verify_custody_artifact,
    _verify_execution_journal,
    _verify_f0,
    _verify_live_capture,
)

INPUT_ROOT = Path("/var/lib/phase-b/inputs")
ARTIFACT_ROOT = Path("/var/lib/phase-b/artifacts")
JOURNAL_ROOT = Path("/var/lib/phase-b/journals")
RECEIPT_ROOT = Path("/var/lib/phase-b/receipts")
CAPTURE_TIMEOUT_SECONDS = 30


def _grant_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TrustError(f"{label} is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise TrustError(f"{label} is invalid") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise TrustError(f"{label} is not UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if canonical != value:
        raise TrustError(f"{label} is not canonical UTC")
    return parsed.astimezone(timezone.utc)


def _validate_operation_grant_interval(
    grant: dict[str, Any], trusted_now: datetime, *, existing_journal: bool
) -> tuple[datetime, datetime]:
    issued = _grant_time(grant.get("issued_at"), "operation grant issued_at")
    expires = _grant_time(grant.get("expires_at"), "operation grant expires_at")
    now = trusted_now.astimezone(timezone.utc)
    if issued > now or issued >= expires:
        raise TrustError("operation grant validity interval is invalid")
    if not existing_journal and expires <= now:
        raise TrustError("operation grant is expired")
    return issued, expires


@dataclass(frozen=True)
class CaptureRequest:
    attempt_id: str
    baseline_digest: str
    capture_id: str
    phase: str
    journal_head: str

    def as_dict(self) -> dict[str, str]:
        return {
            "schema": "phase-b.capture-request.v1",
            "attempt_id": self.attempt_id,
            "baseline_digest": self.baseline_digest,
            "capture_id": self.capture_id,
            "phase": self.phase,
            "journal_head": self.journal_head,
        }


class F0EvidenceSource(Protocol):
    def capture_custody(
        self, request: CaptureRequest, method: str
    ) -> dict[str, Any]: ...

    def capture_final(self, request: CaptureRequest) -> dict[str, dict[str, Any]]: ...


class BoundF0EvidenceSource:
    """Fixed clients for separately sandboxed, socket-activated capture services.

    Each exact Nix-store client has a compiled fixed AF_UNIX endpoint. The
    executor supplies only a canonical request on stdin; neither signed input,
    environment, nor an operator argument can select a socket or capture path.
    Private sensor signing keys remain outside the executor sandbox.
    """

    def __init__(self, anchor: TrustAnchor):
        self.custody_reader = anchor.executables["custody-reader"]
        self.source_sensor = anchor.executables["source-sensor"]
        self.process_inspector = anchor.executables["process-inspector"]

    @staticmethod
    def _run(arguments: list[str], payload: dict[str, Any]) -> Any:
        try:
            process = subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env={"PATH": "", "LANG": "C", "LC_ALL": "C"},
            )
        except OSError as exc:
            raise TrustError("fixed live evidence capture failed") from exc
        output = bytearray()
        deadline = time.monotonic() + CAPTURE_TIMEOUT_SECONDS
        try:
            if process.stdin is None or process.stdout is None:
                raise TrustError("fixed capture pipes are unavailable")
            process.stdin.write(strict_json.canonical(payload))
            process.stdin.close()
            descriptor = process.stdout.fileno()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TrustError("fixed live evidence capture timed out")
                ready, _write, _error = select.select(
                    [descriptor], [], [], remaining
                )
                if not ready:
                    raise TrustError("fixed live evidence capture timed out")
                chunk = os.read(
                    descriptor,
                    min(65536, strict_json.MAX_JSON_BYTES + 1 - len(output)),
                )
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > strict_json.MAX_JSON_BYTES:
                    raise TrustError("fixed live evidence capture exceeded its bound")
            returncode = process.wait(max(0.0, deadline - time.monotonic()))
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired, TrustError) as exc:
            process.kill()
            process.wait()
            if process.stdout is not None:
                process.stdout.close()
            if isinstance(exc, TrustError):
                raise
            raise TrustError("fixed live evidence capture failed") from exc
        if process.stdout is not None:
            process.stdout.close()
        if returncode:
            raise TrustError("fixed live evidence capture returned unsafe output")
        value = strict_json.loads_canonical(bytes(output))
        if not isinstance(value, dict):
            raise TrustError("fixed live evidence capture is not an object")
        return value

    def capture_custody(
        self, request: CaptureRequest, method: str
    ) -> dict[str, Any]:
        if method not in {"GET", "NO_OP"}:
            raise TrustError("custody capture method is not fixed")
        return self._run(
            [str(self.custody_reader.path), "request"],
            {**request.as_dict(), "operation": "capture-custody", "method": method},
        )

    def capture_final(self, request: CaptureRequest) -> dict[str, dict[str, Any]]:
        audit = self._run(
            [str(self.process_inspector.path), "capture-f0"], request.as_dict()
        )
        sensor = self._run(
            [str(self.source_sensor.path), "request"],
            {**request.as_dict(), "operation": "capture-f0"},
        )
        result = {**sensor, "audit": audit}
        if set(result) != {
            "audit",
            "registry",
            "database",
            "provider-route",
            "identity",
            "time",
        } or any(not isinstance(item, dict) for item in result.values()):
            raise TrustError("fixed final capture source set is not exact")
        return result


def _validate_f0_candidate(
    candidate: dict[str, Any],
    baseline: dict[str, Any],
    expectations: tuple[RegistryExpectation, ...],
    evidence_store: ArtifactStore,
    anchor: TrustAnchor,
    verifier: SignatureVerifier,
) -> None:
    _verify_f0(
        candidate, baseline, expectations, evidence_store, anchor, verifier
    )
    derived = [
        _verify_custody_artifact(
            evidence_store,
            item["artifact"],
            item["method"],
            anchor=anchor,
            verifier=verifier,
            expected_capture_id=candidate["capture_id"],
            expected_attempt_id=candidate["attempt_id"],
        )
        for item in candidate["custody_reads"]
    ]
    if derived[0] != derived[1]:
        raise TrustError("F0 custody artifacts are contradictory")


class BoundInspector(ExecutionBackend):
    """Typed read-only adapter to the immutable process/state inspector."""

    def __init__(self, binding: ExecutableBinding):
        self.binding = binding

    def _call(self, operation: str, payload: dict[str, Any]) -> Any:
        result = subprocess.run(
            [str(self.binding.path), operation],
            input=strict_json.canonical(payload),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            env={"PATH": "", "LANG": "C", "LC_ALL": "C"},
        )
        if result.returncode:
            raise RuntimeError("bound state inspector failed")
        return strict_json.loads_canonical(result.stdout)

    def inspect_unit(self, name: str) -> UnitState:
        value = strict_json.exact_object(
            self._call("inspect-unit", {"name": name}),
            {
                "name",
                "source_fragment_path",
                "source_fragment_digest",
                "source_load_state",
                "active_state",
                "unit_file_state",
                "runtime_masked",
                "trigger_edges",
            },
            "unit inspection",
        )
        if not isinstance(value["runtime_masked"], bool) or not isinstance(
            value["trigger_edges"], list
        ):
            raise TypeError("unit inspection is not typed")
        return UnitState(
            value["name"],
            value["source_fragment_path"],
            value["source_fragment_digest"],
            value["source_load_state"],
            value["active_state"],
            value["unit_file_state"],
            value["runtime_masked"],
            tuple(value["trigger_edges"]),
        )

    def unit_operation(self, name: str, operation: str) -> None:
        raise RuntimeError("read-only inspector cannot mutate systemd")

    def effect_capable_processes(self) -> tuple[str, ...]:
        value = strict_json.exact_object(
            self._call("effect-capable-processes", {}),
            {"identities"},
            "process inspection",
        )
        if not isinstance(value["identities"], list) or any(
            not isinstance(x, str) for x in value["identities"]
        ):
            raise RuntimeError("process inspection is not typed")
        return tuple(value["identities"])

    def preserved_identity(self, name: str) -> PreservedIdentity:
        value = strict_json.exact_object(
            self._call("preserved-identity", {"name": name}),
            {"name", "healthy", "start_identity"},
            "preserved inspection",
        )
        if not isinstance(value["healthy"], bool):
            raise TypeError("preserved inspection is not typed")
        return PreservedIdentity(
            value["name"], value["healthy"], value["start_identity"]
        )

    def pause_job(self, delta: Any) -> None:
        raise RuntimeError("read-only inspector cannot pause jobs")

    def restore_job_preimage(
        self, delta: Any, preimage: dict[str, Any], postimage: dict[str, Any]
    ) -> None:
        raise RuntimeError("read-only inspector cannot restore jobs")

    def job_is_paused(self, delta: Any) -> bool:
        value = strict_json.exact_object(
            self._call(
                "job-is-paused",
                {"registry_index": delta.registry_index, "job_id": delta.job_id},
            ),
            {"paused"},
            "job inspection",
        )
        if not isinstance(value["paused"], bool):
            raise TypeError("job inspection is not typed")
        return value["paused"]


def _read_fixed(
    root: Path, name: str, owner_uid: int = 0, secure_root: Path = Path("/")
) -> Any:
    fd, _ = _open_root_owned(
        root / name, root=secure_root, owner_uid=owner_uid, final_mode=0o600
    )
    try:
        return strict_json.loads_canonical(_read_fd(fd))
    finally:
        os.close(fd)


def _write_fixed(
    root: Path,
    name: str,
    value: Any,
    owner_uid: int = 0,
    secure_root: Path = Path("/"),
    mode: int = 0o600,
) -> None:
    if "/" in name or name in {"", ".", ".."}:
        raise TrustError("fixed output name is unsafe")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(secure_root, flags)
    try:
        parts = root.relative_to(secure_root).parts
        for index, part in enumerate(parts):
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
            metadata = os.fstat(directory)
            forbidden_mode = 0o077 if index == len(parts) - 1 else 0o022
            if metadata.st_uid != owner_uid or metadata.st_mode & forbidden_mode:
                raise TrustError("fixed output chain is not owner-controlled")
        temporary = f".{name}.{os.getpid()}.tmp"
        fd = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            mode,
            dir_fd=directory,
        )
        try:
            os.fchmod(fd, mode)
            view = memoryview(strict_json.canonical(value))
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise TrustError("fixed output write was short")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(directory)


def _signed_output(
    binding: ExecutableBinding, namespace: str, payload: dict[str, Any]
) -> dict[str, Any]:
    result = subprocess.run(
        [str(binding.path), "sign", namespace],
        input=strict_json.canonical(payload),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        env={"PATH": "", "LANG": "C", "LC_ALL": "C"},
    )
    if result.returncode:
        raise RuntimeError("bound source signer failed")
    value = strict_json.loads_canonical(result.stdout)
    if not isinstance(value, dict):
        raise TypeError("bound source signer returned non-object")
    return value


def _execute_with(
    anchor: TrustAnchor,
    *,
    input_root: Path = INPUT_ROOT,
    artifact_root: Path = ARTIFACT_ROOT,
    journal_root: Path = JOURNAL_ROOT,
    receipt_root: Path = RECEIPT_ROOT,
    signature_verifier: SignatureVerifier | None = None,
    inspector: ExecutionBackend | None = None,
    backend_factory: Callable[[RegistrySet, ExecutionBackend], ExecutionBackend]
    | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    verify_binding: Callable[[ExecutableBinding], None] = verify_executable,
    owner_uid: int = 0,
    secure_root: Path = Path("/"),
    signer: Callable[
        [ExecutableBinding, str, dict[str, Any]], dict[str, Any]
    ] = _signed_output,
    evidence_source: F0EvidenceSource | None = None,
    capture_id_factory: Callable[[], str] = lambda: secrets.token_hex(32),
) -> None:
    for name in (
        "executor",
        "signature-verifier",
        "systemctl",
        "hermes",
        "process-inspector",
        "custody-reader",
        "source-sensor",
        "artifact-reader",
        "hermes-mutation-adapter",
        "privilege-dropper",
        "source-signer",
    ):
        verify_binding(anchor.executables[name])
    verifier = signature_verifier or BoundExecutableVerifier(
        anchor.executables["signature-verifier"],
        next(iter(anchor.signers.values())).algorithm,
    )
    live_source = evidence_source or BoundF0EvidenceSource(anchor)
    baseline_envelope = _read_fixed(input_root, "baseline.json", owner_uid, secure_root)
    baseline = verify_envelope(baseline_envelope, anchor, verifier, "phase-b-baseline")
    strict_json.validate(baseline, _load_schema("baseline", anchor))
    grant_envelope = _read_fixed(
        input_root, "operation-grant.json", owner_uid, secure_root
    )
    grant = verify_envelope(grant_envelope, anchor, verifier, "phase-b-operation-grant")
    strict_json.validate(grant, _load_schema("operation-grant", anchor))
    if (
        grant["schema"] != "phase-b.operation-grant.v1"
        or grant["action"] != "EXECUTE_PHASE_B_FENCING_QUALIFICATION"
        or grant["attempt_id"] != baseline["attempt_id"]
        or grant["baseline_digest"] != strict_json.digest(baseline)
    ):
        raise TrustError("operation grant does not bind this exact attempt/baseline")
    trusted_now = now().astimezone(timezone.utc)

    evidence_store = DirectoryArtifactStore(
        artifact_root / "evidence", owner_uid=owner_uid, secure_root=secure_root
    )
    expectations = _baseline(baseline, anchor, evidence_store, verifier)
    registry = RegistrySet(expectations, anchor.registry_paths)
    journal = Journal(journal_root / baseline["attempt_id"], owner_uid=owner_uid)
    records = journal.read_all()
    _grant_issued, operation_grant_expires = _validate_operation_grant_interval(
        grant, trusted_now, existing_journal=bool(records)
    )
    try:
        units = tuple(
            UnitExpectation(
                item["name"],
                item["fragment_path"],
                item["fragment_digest"],
                item["load_state"],
                item["active_state"],
                item["unit_file_state"],
                tuple(item["trigger_edges"]),
            )
            for item in baseline["units"]
        )
        preserved = tuple(
            PreservedIdentity(
                item["name"], item["healthy_state"] == "healthy", item["start_identity"]
            )
            for item in baseline["preserved_units"]
        )
        read_backend = inspector or BoundInspector(
            anchor.executables["process-inspector"]
        )
        if backend_factory is not None:
            backend = backend_factory(registry, read_backend)
        else:
            backend = BoundCommandBackend(
                anchor.executables["systemctl"],
                anchor.executables["hermes"],
                anchor.executables["hermes-mutation-adapter"],
                anchor.executables["privilege-dropper"],
                read_backend,
                registry,
                source_uid=anchor.source.uid,
                source_gid=anchor.source.gid,
                source_user=anchor.source.user,
                source_home=anchor.source.home,
                user_manager_machine=anchor.source.user_manager_machine,
            )
        if records:
            f0_records = [
                record
                for record in records
                if record["kind"] == "checkpoint"
                and record["action_id"] == "f0-established"
            ]
            if f0_records:
                if len(f0_records) != 1:
                    raise TrustError("F0 terminal checkpoint is duplicated")
                terminal = strict_json.exact_object(
                    f0_records[0]["payload"],
                    {"artifact", "artifact_digest"},
                    "durable F0 checkpoint",
                )
                candidate = terminal["artifact"]
                if (
                    not isinstance(candidate, dict)
                    or terminal["artifact_digest"] != strict_json.digest(candidate)
                    or candidate.get("attempt_id") != baseline["attempt_id"]
                ):
                    raise TrustError("durable F0 candidate is corrupt")
                registry.acquire(FIXED_DELTAS)
                _validate_f0_candidate(
                    candidate,
                    baseline,
                    expectations,
                    evidence_store,
                    anchor,
                    verifier,
                )
                _verify_execution_journal(
                    journal,
                    candidate,
                    baseline,
                    expectations,
                    evidence_store,
                    anchor,
                    verifier,
                )

                def revalidate_recovery_state() -> None:
                    if (
                        list(registry.revalidate(FIXED_DELTAS))
                        != candidate["registry_digests"]
                        or any(
                            not Executor._fenced(backend.inspect_unit(item.name), item)
                            for item in units
                        )
                        or any(
                            not backend.job_is_paused(delta) for delta in FIXED_DELTAS
                        )
                        or any(
                            backend.preserved_identity(item.name) != item
                            for item in preserved
                        )
                        or backend.effect_capable_processes()
                    ):
                        raise TrustError(
                            "actual B1/B2/F0 state drifted before publication"
                        )

                revalidate_recovery_state()
                output_path = receipt_root / "f0.json"
                if os.path.lexists(output_path):
                    existing = _read_fixed(
                        receipt_root, "f0.json", owner_uid, secure_root
                    )
                    if (
                        not isinstance(existing, dict)
                        or verify_envelope(existing, anchor, verifier, "phase-b-f0")
                        != candidate
                    ):
                        raise TrustError(
                            "existing F0 output differs from durable candidate"
                        )
                else:
                    revalidate_recovery_state()
                    envelope = signer(
                        anchor.executables["source-signer"], "phase-b-f0", candidate
                    )
                    if (
                        verify_envelope(envelope, anchor, verifier, "phase-b-f0")
                        != candidate
                    ):
                        raise TrustError("source signer changed durable F0 candidate")
                    revalidate_recovery_state()
                    _write_fixed(
                        receipt_root, "f0.json", envelope, owner_uid, secure_root
                    )
                return
            recovery = Executor(
                baseline["attempt_id"],
                registry,
                journal,
                backend,
                units,
                preserved,
                monotonic=monotonic,
                effect_plan_digest=anchor.effect_plan_digest,
                rollback_plan_digest=anchor.rollback_plan_digest,
            )
            durable_authorization = Executor.rollback_authorization(records)
            rollback_envelope = _read_fixed(
                input_root,
                "incident-rollback-grant.json",
                owner_uid,
                secure_root,
            )
            rollback = verify_envelope(
                rollback_envelope,
                anchor,
                verifier,
                "phase-b-incident-rollback-grant",
            )
            strict_json.validate(
                rollback, _load_schema("incident-rollback-grant", anchor)
            )
            grant_digest = strict_json.digest(rollback_envelope)
            expires = datetime.fromisoformat(
                rollback["expires_at"].replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            if (
                rollback["schema"] != "phase-b.incident-rollback-grant.v1"
                or rollback["action"] != "ROLLBACK_INVALID_PHASE_B_ATTEMPT"
                or rollback["attempt_id"] != baseline["attempt_id"]
            ):
                raise TrustError("incident rollback grant identity is invalid")

            if durable_authorization is not None:
                authorization, _record_identity = durable_authorization
                if (
                    authorization["attempt_id"] != baseline["attempt_id"]
                    or authorization["authorization_grant_digest"] != grant_digest
                    or tuple(authorization["authorized_actions"])
                    != tuple(rollback["authorized_actions"])
                    or authorization["execution_journal_head"]
                    != rollback["execution_journal_head"]
                ):
                    raise TrustError(
                        "incident rollback grant does not match durable authorization"
                    )
                # The signed grant is reverified above on every restart. Expiry
                # cannot revoke an already-started exact rollback transaction.
                recovery.recover(
                    allow_incident_rollback=True,
                    verified_rollback_grant_digest=grant_digest,
                )
            else:
                recovery.recover(allow_incident_rollback=True)
                required = tuple(reversed(recovery.completed_actions))
                if (
                    tuple(rollback["authorized_actions"]) != required
                    or rollback["execution_journal_head"] != journal.head()
                    or expires <= now().astimezone(timezone.utc)
                ):
                    raise TrustError("incident rollback grant is not exact/current")
                recovery.persist_rollback_authorization(
                    required,
                    grant_digest,
                    rollback["execution_journal_head"],
                )

            required_actions = tuple(reversed(recovery.completed_actions))
            if required_actions:
                recovery.rollback_before_f0(required_actions, grant_digest)
            return
        registry.acquire()
        executor = Executor(
            baseline["attempt_id"],
            registry,
            journal,
            backend,
            units,
            preserved,
            monotonic=monotonic,
            effect_plan_digest=anchor.effect_plan_digest,
            rollback_plan_digest=anchor.rollback_plan_digest,
        )
        executor.preflight()
        if operation_grant_expires <= now().astimezone(timezone.utc):
            raise TrustError("operation grant expired before first mutation")
        executor.run_b1()
        executor.run_b2()
        if executor.b2_completed_monotonic is None:
            raise RuntimeError("B2 completion time was not recorded")
        baseline_digest = strict_json.digest(baseline)
        capture_id = capture_id_factory()
        challenge_head = executor.record_capture_challenge(
            capture_id, baseline_digest
        )

        def request(phase: str) -> CaptureRequest:
            return CaptureRequest(
                baseline["attempt_id"],
                baseline_digest,
                capture_id,
                phase,
                challenge_head,
            )

        final_custody_capture: dict[str, Any] | None = None
        final_custody_ref: dict[str, Any] | None = None
        for index, method in enumerate(("GET", "NO_OP"), 1):
            target = (
                executor.b2_completed_monotonic + 300
                if index == 1
                else executor.custody_reads[0]["observed_monotonic"] + 300
            )
            delay = target - monotonic()
            if delay > 0:
                sleeper(delay)
            capture_request = request(f"custody-{index}")
            custody_envelope = live_source.capture_custody(capture_request, method)
            custody_capture = _verify_live_capture(
                custody_envelope,
                "custody",
                capture_request.as_dict(),
                baseline,
                anchor,
                verifier,
                now().astimezone(timezone.utc),
                float(monotonic()),
            )
            if custody_capture["evidence"].get("method") != method:
                raise TrustError("live custody method differs from fixed request")
            custody_ref = evidence_store.write(
                f"custody-{index}", strict_json.canonical(custody_envelope)
            ).as_dict()
            executor.record_custody_read(
                {"method": method, "artifact": custody_ref},
                custody_capture["observed_monotonic"],
            )
            if index == 2:
                final_custody_capture = custody_capture
                final_custody_ref = custody_ref

        if final_custody_capture is None or final_custody_ref is None:
            raise TrustError("final live custody capture is absent")
        final_request = request("f0-final")
        final_envelopes = live_source.capture_final(final_request)
        captures: dict[str, dict[str, Any]] = {}
        evidence: dict[str, dict[str, Any]] = {}
        for source in sorted(final_envelopes):
            envelope_value = final_envelopes[source]
            captures[source] = _verify_live_capture(
                envelope_value,
                source,
                final_request.as_dict(),
                baseline,
                anchor,
                verifier,
                now().astimezone(timezone.utc),
                float(monotonic()),
            )
            evidence[source] = evidence_store.write(
                "f0-" + source, strict_json.canonical(envelope_value)
            ).as_dict()
        captures["custody"] = final_custody_capture
        evidence["custody"] = final_custody_ref
        common_end = min(
            float(capture["window"]["end_monotonic"])
            for capture in captures.values()
        )
        time_capture = captures["time"]
        f0_at = (
            _grant_time(time_capture["observed_at"], "F0 time")
            + timedelta(
                seconds=common_end - float(time_capture["observed_monotonic"])
            )
        ).isoformat().replace("+00:00", "Z")
        f0 = executor.establish_f0_candidate(
            evidence,
            capture_id,
            f0_at,
            lambda candidate: _validate_f0_candidate(
                candidate,
                baseline,
                expectations,
                evidence_store,
                anchor,
                verifier,
            ),
        )
        _verify_execution_journal(
            journal,
            f0,
            baseline,
            expectations,
            evidence_store,
            anchor,
            verifier,
        )
        if list(executor.revalidate_f0_live_state()) != f0["registry_digests"]:
            raise TrustError("F0 live state drifted before signing")
        envelope = signer(anchor.executables["source-signer"], "phase-b-f0", f0)
        if verify_envelope(envelope, anchor, verifier, "phase-b-f0") != f0:
            raise TrustError("signed F0 payload differs from the durable candidate")
        if list(executor.revalidate_f0_live_state()) != f0["registry_digests"]:
            raise TrustError("F0 live state drifted before publication")
        _write_fixed(receipt_root, "f0.json", envelope, owner_uid, secure_root)
    finally:
        registry.close()


def _execute(anchor: TrustAnchor) -> None:
    _execute_with(anchor)


def main() -> int:
    return run_without_options(_execute)


if __name__ == "__main__":
    sys.exit(main())
