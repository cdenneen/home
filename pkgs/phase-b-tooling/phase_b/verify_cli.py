"""Fixed-boundary Phase B verifier entry point."""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import strict_json
from .artifacts import MAX_EVIDENCE_ARTIFACT_BYTES, DirectoryArtifactStore
from .cli_common import run_without_options
from .journal import Journal
from .receiver import BoundReceiverClient
from .trust import (
    BoundExecutableVerifier,
    ExecutableBinding,
    SignatureVerifier,
    TrustAnchor,
    TrustError,
    _open_root_owned,
    _read_fd,
    require_safe_attempt_id,
    verify_envelope,
    verify_executable,
)
from .verifier import VerificationBundle, Verifier

ARTIFACT_ROOT = Path("/var/lib/phase-b/artifacts")
JOURNAL_ROOT = Path("/var/lib/phase-b/journals")


def _artifact(
    name: str,
    root: Path = ARTIFACT_ROOT,
    owner_uid: int = 0,
    secure_root: Path = Path("/"),
) -> Any:
    fd, _metadata = _open_root_owned(
        root / name,
        root=secure_root,
        owner_uid=owner_uid,
        final_mode=0o600,
    )
    try:
        return strict_json.loads_canonical(_read_fd(fd))
    finally:
        os.close(fd)


def _envelope(
    name: str,
    root: Path = ARTIFACT_ROOT,
    owner_uid: int = 0,
    secure_root: Path = Path("/"),
) -> dict[str, Any]:
    value = _artifact(name, root, owner_uid, secure_root)
    if not isinstance(value, dict):
        raise TrustError(f"fixed artifact {name} is not an object")
    return value


def _verify_with(
    anchor: TrustAnchor,
    *,
    authority: Any | None = None,
    artifact_root: Path = ARTIFACT_ROOT,
    journal_root: Path = JOURNAL_ROOT,
    output_fd: int | None = None,
    owner_uid: int = 0,
    secure_root: Path = Path("/"),
    signature_verifier: SignatureVerifier | None = None,
    verify_binding: Callable[[ExecutableBinding], None] = verify_executable,
) -> None:
    binding = anchor.executables.get("signature-verifier")
    if binding is None:
        raise TrustError("trust anchor does not bind signature-verifier")
    verify_binding(binding)
    verifier_binding = anchor.executables.get("verifier")
    if verifier_binding is None:
        raise TrustError("trust anchor does not bind verifier")
    verify_binding(verifier_binding)
    algorithm = next(iter(anchor.signers.values())).algorithm
    active_verifier = signature_verifier or BoundExecutableVerifier(binding, algorithm)
    receipt_envelope = _envelope("receipt.json", artifact_root, owner_uid, secure_root)
    raw_receipt = receipt_envelope.get("payload")
    if not isinstance(raw_receipt, dict):
        raise TrustError("fixed receipt payload is absent")
    raw_counter = raw_receipt.get("consumption_counter")
    if (
        isinstance(raw_counter, bool)
        or not isinstance(raw_counter, int)
        or not 1 <= raw_counter <= 1000
    ):
        raise TrustError("fixed receipt counter is invalid")
    raw_attempt = raw_receipt.get("attempt_id")
    attempt_id = require_safe_attempt_id(raw_attempt)
    observation_envelope = _envelope(
        "observation.json", artifact_root, owner_uid, secure_root
    )
    manifests: list[dict[str, Any]] = []
    continuation_envelopes: list[dict[str, Any]] = []
    for counter in range(1, raw_counter + 1):
        suffix = f"{counter:08d}.json"
        manifest_envelope = _envelope(
            "bundle-manifest-" + suffix, artifact_root, owner_uid, secure_root
        )
        manifest = verify_envelope(
            manifest_envelope, anchor, active_verifier, "phase-b-observation"
        )
        manifest = strict_json.exact_object(
            manifest,
            {
                "schema",
                "consumption_counter",
                "attempt_id",
                "observation_digest",
                "observation_journal_head",
                "receiver_head",
                "receiver_sequence",
                "continuation_digest",
                "current_receiver_head",
                "current_receiver_sequence",
                "terminal_cursors",
                "terminal_continuity",
                "terminal_source_walls",
                "evidence",
            },
            "collector import manifest",
        )
        continuation = _envelope(
            "continuation-" + suffix, artifact_root, owner_uid, secure_root
        )
        if (
            manifest["schema"] != "phase-b.collector-import-manifest.v4"
            or manifest["consumption_counter"] != counter
            or manifest["attempt_id"] != attempt_id
            or manifest["observation_digest"]
            != strict_json.digest(observation_envelope)
            or manifest["continuation_digest"] != strict_json.digest(continuation)
            or continuation.get("payload", {}).get("current_head")
            != manifest["current_receiver_head"]
            or continuation.get("payload", {}).get("terminal_cursors")
            != manifest["terminal_cursors"]
            or continuation.get("payload", {}).get("terminal_continuity")
            != manifest["terminal_continuity"]
            or continuation.get("payload", {}).get("terminal_source_walls")
            != manifest["terminal_source_walls"]
            or not isinstance(manifest["evidence"], list)
        ):
            raise TrustError("collector import manifest chain is invalid")
        if manifests and (
            manifest["receiver_head"] != manifests[-1]["current_receiver_head"]
            or manifest["receiver_sequence"]
            != manifests[-1]["current_receiver_sequence"]
        ):
            raise TrustError("collector import manifest has a gap/fork/replay")
        manifests.append(manifest)
        continuation_envelopes.append(continuation)
    manifest = manifests[-1]
    for item in manifest["evidence"]:
        item = strict_json.exact_object(item, {"name", "digest"}, "manifest evidence")
        name = item["name"]
        if (
            not isinstance(name, str)
            or not name.endswith(".artifact")
            or "/" in name
            or ".." in name
        ):
            raise TrustError("collector import manifest has unsafe evidence name")
        fd, _ = _open_root_owned(
            artifact_root / "evidence" / name,
            root=secure_root,
            owner_uid=owner_uid,
            final_mode=0o400,
        )
        try:
            actual = (
                "sha256:"
                + hashlib.sha256(
                    _read_fd(fd, maximum=MAX_EVIDENCE_ARTIFACT_BYTES)
                ).hexdigest()
            )
        finally:
            os.close(fd)
        if actual != item["digest"]:
            raise TrustError("collector import evidence digest mismatch")
    continuation_envelope = continuation_envelopes[-1]
    previous = _artifact(
        "previous-receipts.json", artifact_root, owner_uid, secure_root
    )
    if (
        not isinstance(previous, list)
        or len(previous) != raw_counter - 1
        or any(not isinstance(item, dict) for item in previous)
    ):
        raise TrustError("fixed previous receipt chain is invalid")
    bundle = VerificationBundle(
        baseline=_envelope("baseline.json", artifact_root, owner_uid, secure_root),
        f0=_envelope("f0.json", artifact_root, owner_uid, secure_root),
        observation=observation_envelope,
        reconstructions=(
            _envelope("reconstruction-1.json", artifact_root, owner_uid, secure_root),
            _envelope("reconstruction-2.json", artifact_root, owner_uid, secure_root),
        ),
        receipt=receipt_envelope,
        previous_receipts=tuple(previous),
        execution_journal=Journal(
            journal_root / attempt_id,
            owner_uid=owner_uid,
        ),
        observation_journal=Journal(
            journal_root / "observation" / attempt_id,
            owner_uid=owner_uid,
        ),
        artifacts=DirectoryArtifactStore(
            artifact_root / "evidence",
            owner_uid=owner_uid,
            secure_root=secure_root,
        ),
        previous_continuations=tuple(continuation_envelopes[:-1]),
        continuation_proof=continuation_envelope,
    )
    imported_custody = observation_envelope.get("payload", {}).get("receiver_custody")
    if (
        bundle.observation_journal.head() != manifest["observation_journal_head"]
        or not isinstance(imported_custody, dict)
        or imported_custody.get("head") != manifests[0]["receiver_head"]
        or imported_custody.get("sequence") != manifests[0]["receiver_sequence"]
    ):
        raise TrustError("collector import journals/custody do not match manifest")
    receiver_binding = anchor.executables.get("receiver-client")
    if receiver_binding is None:
        raise TrustError("trust anchor does not bind receiver-client")
    verify_binding(receiver_binding)
    result = Verifier(
        anchor, active_verifier, authority or BoundReceiverClient(receiver_binding)
    ).verify(bundle)
    public = {
        "axis_remote_custody": result.axis_remote_custody,
        "canonical_alpha0_active": result.canonical_alpha0_active,
        "canonical_axis_control_active": result.canonical_axis_control_active,
        "canonical_axis_writer": result.canonical_axis_writer,
        "canonical_composition_activated": result.canonical_composition_activated,
        "canonical_deployment_attestation": result.canonical_deployment_attestation,
        "cutover_ready": result.cutover_ready,
        "duplicate_scheduler_topology": result.duplicate_scheduler_topology,
        "external_route_identity": result.external_route_identity,
        "generic_route_reconstruction": result.generic_route_reconstruction,
        "hermes_semantic_restore": result.hermes_semantic_restore,
        "home_generation_changed": result.home_generation_changed,
        "legacy_alpha0_authority": result.legacy_alpha0_authority,
        "legacy_axis_new_work_writer": result.legacy_axis_new_work_writer,
        "live_fencing_observation": result.live_fencing_observation,
        "phase_b_fencing_qualification": result.phase_b_fencing_qualification,
        "route_ownership": result.route_ownership,
        "safe_drain_ready": result.safe_drain_ready,
        "source_fence_baseline_contract": result.source_fence_baseline_contract,
    }
    os.write(
        output_fd if output_fd is not None else sys.stdout.fileno(),
        strict_json.canonical(public) + b"\n",
    )


def _verify(anchor: TrustAnchor) -> None:
    _verify_with(anchor)


def main() -> int:
    return run_without_options(_verify)


if __name__ == "__main__":
    sys.exit(main())
