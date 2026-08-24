"""Offline root-trusted artifact verifier plus online one-time consumption gate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol

from . import strict_json
from .artifacts import (
    MAX_EVIDENCE_ARTIFACT_BYTES,
    ArtifactError,
    ArtifactRef,
    ArtifactStore,
    read_artifact,
    read_json_artifact,
)
from .collector import (
    CLOCK_TOLERANCE_SECONDS,
    CONTINUITY_INTERVAL_SECONDS,
    EVENT_FIELDS,
    IDENTITY_FIELDS,
    MANDATORY_EVENT_CLASSES,
    MANDATORY_RAW_TYPES,
    OBSERVATION_SECONDS,
    SAMPLE_FIELDS,
    SAMPLE_INTERVAL_SECONDS,
    STREAMS,
    TERMINAL_EVENT_CLASSES,
    CollectorError,
    derive_raw_batch,
)
from .executor import CUSTODY_FIELDS, F0_EVIDENCE_FIELDS, FENCED_UNITS, PRESERVED_UNITS
from .journal import ZERO_HASH, Journal, JournalError
from .registry import (
    FIXED_DELTAS,
    RegistryError,
    RegistryExpectation,
    RegistrySet,
    _validate_applied_document,
)
from .trust import (
    SignatureVerifier,
    TrustAnchor,
    TrustError,
    require_safe_attempt_id,
    verify_envelope,
)

SCHEMA_NAMES = ("baseline", "f0", "observation", "reconstruction", "receipt")
NAMESPACES = {name: f"phase-b-{name}" for name in SCHEMA_NAMES}
MAX_RECEIPT_AGE_SECONDS = 5 * 60
BASELINE_FIELDS = frozenset(
    {
        "schema",
        "attempt_id",
        "captured_at",
        "trust_anchor_digest",
        "anchor_generation",
        "runbook_digest",
        "source_revision",
        "source_identity",
        "automatic_reboot_evidence",
        "registries",
        "backup_artifacts",
        "units",
        "preserved_units",
        "scheduler_inventory",
        "expected_process_inventory",
        "expected_listener_inventory",
        "effect_plan",
        "rollback_plan",
        "starting_cursors",
        "authority_identities",
        "collector_identity",
        "custody",
    }
)
REQUIRED_BACKUP_KINDS = (
    *(f"registry-{index}" for index in range(6)),
    "hermes-root",
    "hermes-axis-profile",
    "checkout-hermes-root",
    "checkout-hermes-axis-profile",
    "alpha0-hermes-root",
    "alpha0-hermes-profile",
    "alpha0-core",
    "git-refs",
    "git-worktrees",
    "controller-state",
    "retention-manifest",
)

RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "attempt_id",
        "baseline_digest",
        "f0_digest",
        "observation_digest",
        "reconstruction_digests",
        "execution_journal_head",
        "observation_chain_head",
        "observed_through_at",
        "issued_at",
        "receiver_head",
        "continuation_digest",
        "consumer_nonce",
        "consumer_identity",
        "requested_transition",
        "authorization_grant",
        "authorization_grant_digest",
        "consumption_counter",
        "previous_receipt_digest",
        "verified_chain_accumulator",
    }
)


class VerificationError(RuntimeError):
    pass


class ConsumptionAuthority(Protocol):
    """Off-host receiver's trusted clock/head and atomic one-time nonce store."""

    def trusted_now(self) -> datetime: ...

    def current_head(self, attempt_id: str) -> str: ...

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
    ) -> bool: ...


@dataclass(frozen=True)
class VerificationBundle:
    baseline: dict[str, Any]
    f0: dict[str, Any]
    observation: dict[str, Any]
    reconstructions: tuple[dict[str, Any], dict[str, Any]]
    receipt: dict[str, Any]
    previous_receipts: tuple[dict[str, Any], ...]
    execution_journal: Journal
    observation_journal: Journal
    artifacts: ArtifactStore
    continuation_proof: dict[str, Any] | None = None
    previous_continuations: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Qualification:
    attempt_id: str
    observed_through_at: str
    receipt_digest: str
    receipt_consumptions: int
    phase_b_fencing_qualification: str = "PROVEN"
    source_fence_baseline_contract: str = "PROVEN"
    external_route_identity: str = "PROVEN"
    route_ownership: str = "PROVEN"
    duplicate_scheduler_topology: str = "PROVEN"
    generic_route_reconstruction: str = "PROVEN"
    hermes_semantic_restore: str = "PROVEN"
    live_fencing_observation: str = "PROVEN"
    axis_remote_custody: str = "9/9"
    legacy_axis_new_work_writer: int = 0
    canonical_axis_writer: int = 0
    legacy_alpha0_authority: str = "UNCHANGED_NOT_DRAINED"
    home_generation_changed: bool = False
    canonical_deployment_attestation: str = "NOT_ESTABLISHED_BY_PHASE_B"
    canonical_composition_activated: str = "NO"
    canonical_axis_control_active: str = "NO"
    canonical_alpha0_active: str = "NO"
    safe_drain_ready: str = "YES"
    cutover_ready: str = "NO"


def _parse_time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise VerificationError(f"{label} is not canonical UTC time")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise VerificationError(f"{label} is invalid") from exc
    return parsed.astimezone(timezone.utc)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise VerificationError(f"{label} is missing or invalid")
    if value.upper() in {
        "UNKNOWN",
        "PLACEHOLDER",
        "TODO",
        "NONE",
        "DENIED",
        "ASSERTED",
    }:
        raise VerificationError(f"{label} is not actual evidence")
    return value


def _actual_digest(value: Any, label: str) -> str:
    item = _identifier(value, label)
    if (
        re.fullmatch(r"sha256:[0-9a-f]{64}", item) is None
        or item == "sha256:" + "0" * 64
    ):
        raise VerificationError(f"{label} is not a non-placeholder digest")
    return item


def _schema_path(name: str) -> Path:
    return Path(__file__).with_name("schemas") / f"{name}.schema.json"


def _load_schema(name: str, anchor: TrustAnchor) -> dict[str, Any]:
    try:
        schema = strict_json.loads(_schema_path(name).read_bytes())
    except (OSError, strict_json.StrictJSONError) as exc:
        raise VerificationError(f"cannot load strict {name} schema") from exc
    if anchor.schema_digests.get(name) != strict_json.digest(schema) or not isinstance(
        schema, dict
    ):
        raise VerificationError(f"{name} schema is not trust-bound")
    return schema


def _signed(
    envelope: dict[str, Any],
    name: str,
    anchor: TrustAnchor,
    verifier: SignatureVerifier,
) -> dict[str, Any]:
    try:
        payload = verify_envelope(envelope, anchor, verifier, NAMESPACES[name])
        strict_json.validate(payload, _load_schema(name, anchor))
        return payload
    except (TrustError, strict_json.StrictJSONError) as exc:
        raise VerificationError(f"invalid signed {name} artifact") from exc


def _baseline(
    value: dict[str, Any],
    anchor: TrustAnchor,
    artifacts: ArtifactStore,
    signature_verifier: SignatureVerifier,
) -> tuple[RegistryExpectation, ...]:
    if set(value) != BASELINE_FIELDS or value["schema"] != "phase-b.baseline.v2":
        raise VerificationError("baseline contract is incomplete")
    try:
        require_safe_attempt_id(value["attempt_id"])
    except TrustError as exc:
        raise VerificationError("unsafe attempt id") from exc
    if (
        value["trust_anchor_digest"] != anchor.anchor_digest
        or value["anchor_generation"] != anchor.anchor_generation
    ):
        raise VerificationError("baseline trust anchor identity/generation mismatch")
    if value["runbook_digest"] != anchor.runbook_digest:
        raise VerificationError("baseline runbook mismatch")
    captured_at = _parse_time(value["captured_at"], "baseline capture time")
    _identifier(value["source_revision"], "source revision")
    source = strict_json.exact_object(
        value["source_identity"],
        {
            "host_identity",
            "machine_id",
            "boot_id",
            "user_manager_id",
            "home_generation",
            "source_uid",
            "source_gid",
            "source_user",
            "source_home",
            "wall_at",
            "monotonic",
            "booted_closure",
            "anchor_generation",
        },
        "source identity",
    )
    for key in (
        "host_identity",
        "machine_id",
        "boot_id",
        "user_manager_id",
        "home_generation",
        "source_user",
        "source_home",
    ):
        _identifier(source[key], key)
    _parse_time(source["wall_at"], "baseline wall time")
    if (
        isinstance(source["monotonic"], bool)
        or not isinstance(source["monotonic"], (int, float))
        or source["monotonic"] < 0
    ):
        raise VerificationError("baseline monotonic clock is invalid")
    _actual_digest(source["booted_closure"], "booted closure")
    expected_source = anchor.source
    if (
        source["host_identity"] != expected_source.host_identity
        or source["machine_id"] != expected_source.machine_id
        or source["source_uid"] != expected_source.uid
        or source["source_gid"] != expected_source.gid
        or source["source_user"] != expected_source.user
        or source["source_home"] != expected_source.home
        or source["boot_id"] != expected_source.boot_id
        or source["user_manager_id"] != expected_source.user_manager_id
        or source["home_generation"] != expected_source.home_generation
        or source["booted_closure"] != expected_source.booted_closure
        or source["anchor_generation"] != anchor.anchor_generation
    ):
        raise VerificationError("source host/user identity is not anchor-bound")

    _reboot_ref, reboot_bytes = read_artifact(
        artifacts, value["automatic_reboot_evidence"], owner_only=True
    )
    try:
        reboot_envelope = strict_json.loads_canonical(reboot_bytes)
        reboot = verify_envelope(
            reboot_envelope,
            anchor,
            signature_verifier,
            "phase-b-source-event.systemd",
        )
    except (strict_json.StrictJSONError, TrustError) as exc:
        raise VerificationError(
            "automatic reboot evidence is not source-signed"
        ) from exc
    reboot = strict_json.exact_object(
        reboot,
        {
            "schema",
            "attempt_id",
            "captured_at",
            "boot_id",
            "observation_seconds",
            "systemd_cursor",
            "scheduled_reboots",
        },
        "automatic reboot evidence",
    )
    if (
        reboot["schema"] != "phase-b.automatic-reboot-evidence.v1"
        or reboot["attempt_id"] != value["attempt_id"]
        or reboot["boot_id"] != source["boot_id"]
        or reboot["observation_seconds"] != OBSERVATION_SECONDS
        or reboot["scheduled_reboots"] != []
        or not isinstance(reboot["systemd_cursor"], str)
        or not reboot["systemd_cursor"]
    ):
        raise VerificationError("scheduled reboot conflicts with observation")
    _parse_time(reboot["captured_at"], "automatic reboot evidence time")

    raw_registries = value["registries"]
    backups = value["backup_artifacts"]
    if not isinstance(raw_registries, list) or len(raw_registries) != 6:
        raise VerificationError("baseline does not contain six registries")
    if (
        not isinstance(backups, list)
        or tuple(item.get("kind") for item in backups if isinstance(item, dict))
        != REQUIRED_BACKUP_KINDS
    ):
        raise VerificationError("baseline backup scope is incomplete or reordered")
    backup_receipts: dict[str, dict[str, Any]] = {}
    for entry in backups:
        item = strict_json.exact_object(
            entry, {"kind", "source", "backup", "restore_receipt"}, "backup entry"
        )
        source_ref, source_bytes = read_artifact(
            artifacts,
            item["source"],
            owner_only=True,
            maximum=MAX_EVIDENCE_ARTIFACT_BYTES,
        )
        backup_ref, _backup_bytes = read_artifact(
            artifacts,
            item["backup"],
            owner_only=True,
            maximum=MAX_EVIDENCE_ARTIFACT_BYTES,
        )
        _receipt_ref, receipt_bytes = read_artifact(
            artifacts, item["restore_receipt"], owner_only=True
        )
        try:
            envelope = strict_json.loads_canonical(receipt_bytes)
            receipt = verify_envelope(
                envelope,
                anchor,
                signature_verifier,
                "phase-b-backup-restore",
            )
        except (strict_json.StrictJSONError, TrustError) as exc:
            raise VerificationError("backup restore receipt is not signed") from exc
        receipt = strict_json.exact_object(
            receipt,
            {
                "schema",
                "attempt_id",
                "kind",
                "backup",
                "backup_digest",
                "source",
                "source_digest",
                "restored_output",
                "restored_output_digest",
                "restore_test",
                "restore_test_digest",
                "tested_at",
            },
            "restore receipt",
        )
        restored_ref, restored_bytes = read_artifact(
            artifacts,
            receipt["restored_output"],
            owner_only=True,
            maximum=MAX_EVIDENCE_ARTIFACT_BYTES,
        )
        test_ref, restore_test = read_json_artifact(
            artifacts, receipt["restore_test"], owner_only=True
        )
        tested_at = _parse_time(receipt["tested_at"], "restore test time")
        freshness = (captured_at - tested_at).total_seconds()
        if (
            receipt["schema"] != "phase-b.restore-receipt.v2"
            or receipt["attempt_id"] != value["attempt_id"]
            or receipt["kind"] != item["kind"]
            or receipt["backup"] != backup_ref.as_dict()
            or receipt["backup_digest"] != backup_ref.digest
            or receipt["source"] != source_ref.as_dict()
            or receipt["source_digest"] != source_ref.digest
            or receipt["restored_output"] != restored_ref.as_dict()
            or restored_ref.id == source_ref.id
            or receipt["restored_output_digest"] != restored_ref.digest
            or receipt["restore_test"] != test_ref.as_dict()
            or receipt["restore_test_digest"] != test_ref.digest
            or not 0 <= freshness <= 15 * 60
        ):
            raise VerificationError("backup restore receipt identity/freshness mismatch")
        restore_test = strict_json.exact_object(
            restore_test,
            {
                "schema",
                "attempt_id",
                "kind",
                "backup",
                "backup_digest",
                "source",
                "source_digest",
                "restored_output",
                "restored_output_digest",
                "command_exit_code",
                "network_attempts",
                "integrity",
            },
            "restore test artifact",
        )
        integrity = strict_json.exact_object(
            restore_test["integrity"],
            {"algorithm", "source_digest", "restored_output_digest"},
            "restore test integrity",
        )
        exit_code = restore_test["command_exit_code"]
        network_attempts = restore_test["network_attempts"]
        if (
            restore_test["schema"] != "phase-b.restore-test.v2"
            or restore_test["attempt_id"] != value["attempt_id"]
            or restore_test["kind"] != item["kind"]
            or restore_test["backup"] != backup_ref.as_dict()
            or restore_test["backup_digest"] != backup_ref.digest
            or restore_test["source"] != source_ref.as_dict()
            or restore_test["source_digest"] != source_ref.digest
            or restore_test["restored_output"] != restored_ref.as_dict()
            or restore_test["restored_output_digest"] != restored_ref.digest
            or integrity
            != {
                "algorithm": "sha256",
                "source_digest": source_ref.digest,
                "restored_output_digest": restored_ref.digest,
            }
            or source_bytes != restored_bytes
            or isinstance(exit_code, bool)
            or not isinstance(exit_code, int)
            or exit_code != 0
            or isinstance(network_attempts, bool)
            or not isinstance(network_attempts, int)
            or network_attempts != 0
        ):
            raise VerificationError("restore test does not prove an exact restore")
        if item["kind"].startswith("registry-"):
            try:
                registry_index = int(item["kind"].removeprefix("registry-"))
            except ValueError as exc:
                raise VerificationError("registry backup kind is invalid") from exc
            if source_bytes != strict_json.canonical(
                raw_registries[registry_index]["document"]
            ):
                raise VerificationError(
                    "registry backup source is not the baseline registry document"
                )
        backup_receipts[item["kind"]] = receipt

    registries: list[RegistryExpectation] = []
    for index, item in enumerate(raw_registries):
        raw = strict_json.exact_object(
            item,
            {"path", "owner_uid", "mode", "device", "inode", "document"},
            "registry",
        )
        if any(
            isinstance(raw[key], bool) or not isinstance(raw[key], int)
            for key in ("owner_uid", "mode", "device", "inode")
        ):
            raise VerificationError("registry metadata is not typed")
        if (
            raw["path"] != anchor.registry_paths[index]
            or raw["owner_uid"] != anchor.source.uid
            or raw["mode"] != 0o600
        ):
            raise VerificationError("registry path/owner/mode is not fixed")
        if backup_receipts[f"registry-{index}"]["source_digest"] != strict_json.digest(
            raw["document"]
        ):
            raise VerificationError(
                "registry backup receipt does not bind complete preimage"
            )
        registries.append(
            RegistryExpectation(
                raw["path"],
                raw["owner_uid"],
                raw["mode"],
                raw["device"],
                raw["inode"],
                raw["document"],
            )
        )
    registry_set = RegistrySet(tuple(registries), anchor.registry_paths)
    registry_set.expected_documents(tuple(FIXED_DELTAS))

    units = value["units"]
    if (
        not isinstance(units, list)
        or tuple(item.get("name") for item in units if isinstance(item, dict))
        != FENCED_UNITS
    ):
        raise VerificationError("baseline unit set/order is not exact")
    for item in units:
        raw = strict_json.exact_object(
            item,
            {
                "name",
                "fragment_path",
                "fragment_digest",
                "load_state",
                "active_state",
                "unit_file_state",
                "trigger_edges",
            },
            "unit",
        )
        for key in (
            "fragment_path",
            "fragment_digest",
            "load_state",
            "active_state",
            "unit_file_state",
        ):
            _identifier(raw[key], f"unit {key}")
        if (
            raw["load_state"] != "loaded"
            or raw["active_state"] == "failed"
            or raw["unit_file_state"] in {"masked", "masked-runtime", "not-found"}
        ):
            raise VerificationError("baseline unit failed/missing/pre-masked")
        if not isinstance(raw["trigger_edges"], list) or any(
            not isinstance(edge, str) for edge in raw["trigger_edges"]
        ):
            raise VerificationError("unit trigger edges are invalid")
    preserved = value["preserved_units"]
    if (
        not isinstance(preserved, list)
        or tuple(item.get("name") for item in preserved if isinstance(item, dict))
        != PRESERVED_UNITS
    ):
        raise VerificationError("preserved unit set is not exact")
    for item in preserved:
        raw = strict_json.exact_object(
            item, {"name", "healthy_state", "start_identity"}, "preserved unit"
        )
        if raw["healthy_state"] != "healthy":
            raise VerificationError("preserved unit is not healthy")
        _identifier(raw["start_identity"], "preserved start identity")

    inventory = strict_json.exact_object(
        value["scheduler_inventory"],
        {"entries", "classification_digest"},
        "scheduler inventory",
    )
    if not isinstance(inventory["entries"], list) or not inventory["entries"]:
        raise VerificationError("scheduler inventory is empty")
    expected_entries = []
    for index, registry in enumerate(registries):
        for job in registry.document["jobs"]:
            expected_entries.append(
                {
                    "kind": "job",
                    "physical_registry": index,
                    "identity": job["id"],
                    "classification": "axis-target"
                    if (index, job["id"])
                    in {(d.registry_index, d.job_id) for d in FIXED_DELTAS}
                    else "preserved",
                }
            )
    expected_entries.extend(
        {
            "kind": "unit",
            "physical_registry": None,
            "identity": name,
            "classification": "reprovisioner",
        }
        for name in FENCED_UNITS
    )
    if inventory["entries"] != expected_entries or inventory[
        "classification_digest"
    ] != strict_json.digest(expected_entries):
        raise VerificationError(
            "scheduler classification is incomplete or caller-asserted"
        )

    for name in ("expected_process_inventory", "expected_listener_inventory"):
        items = value[name]
        if (
            not isinstance(items, list)
            or not items
            or any(
                not isinstance(item, dict)
                or set(item) != {"identity", "classification"}
                for item in items
            )
        ):
            raise VerificationError(f"{name} is not a complete typed inventory")
        for item in items:
            _identifier(item["identity"], name)
            if item["classification"] not in {"preserved", "forbidden", "absent"}:
                raise VerificationError(f"{name} classification is invalid")
    if (
        strict_json.digest(value["expected_process_inventory"])
        != anchor.process_inventory_digest
    ):
        raise VerificationError("process inventory is not anchor-bound")
    if (
        strict_json.digest(value["expected_listener_inventory"])
        != anchor.listener_inventory_digest
    ):
        raise VerificationError("listener inventory is not anchor-bound")

    if (
        strict_json.digest(value["effect_plan"]) != anchor.effect_plan_digest
        or strict_json.digest(value["rollback_plan"]) != anchor.rollback_plan_digest
    ):
        raise VerificationError("effect/rollback identities are not anchor-bound")
    if not isinstance(value["effect_plan"], list) or not isinstance(
        value["rollback_plan"], list
    ):
        raise VerificationError("effect/rollback plans are not arrays")
    expected_rollback = []
    inverse = {
        "stop": "start",
        "disable": "enable",
        "mask": "unmask",
        "hermes-pause": "restore-preimage",
    }
    for effect in reversed(value["effect_plan"]):
        item = strict_json.exact_object(effect, {"action", "operation"}, "effect")
        if item["operation"] not in inverse:
            raise VerificationError("effect plan contains arbitrary operation")
        expected_rollback.append(
            {
                "action": "rollback:" + item["action"],
                "operation": inverse[item["operation"]],
            }
        )
    if value["rollback_plan"] != expected_rollback:
        raise VerificationError(
            "rollback plan is not exact reverse preimage restoration"
        )

    cursors = value["starting_cursors"]
    if (
        not isinstance(cursors, list)
        or tuple(item.get("stream") for item in cursors if isinstance(item, dict))
        != STREAMS
    ):
        raise VerificationError("baseline source cursors are incomplete")
    for item in cursors:
        raw = strict_json.exact_object(
            item, {"stream", "generation", "offset", "token"}, "cursor"
        )
        if any(
            isinstance(raw[key], bool) or not isinstance(raw[key], int) or raw[key] < 0
            for key in ("generation", "offset")
        ):
            raise VerificationError("baseline cursor is invalid")
        _identifier(raw["token"], "cursor token")
    if (
        value["authority_identities"] != anchor.authority_identities
        or value["collector_identity"] != anchor.collector_identity
    ):
        raise VerificationError(
            "route/service/session or collector identity is not anchor-bound"
        )
    custody = strict_json.exact_object(
        value["custody"],
        {"remote", "total", "pending", "inflight", "local_only", "frontier_digest"},
        "custody",
    )
    if custody != {
        "remote": 9,
        "total": 9,
        "pending": 0,
        "inflight": 0,
        "local_only": 0,
        "frontier_digest": custody["frontier_digest"],
    }:
        raise VerificationError("baseline custody is not clean 9/9")
    _actual_digest(custody["frontier_digest"], "frontier digest")
    return tuple(registries)


def _unit_state(payload: Any) -> dict[str, Any]:
    return strict_json.exact_object(
        payload,
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
        "unit state",
    )


CUSTODY_SURFACES = ("lineages", "custody", "pending-effects", "residue")
F0_FINAL_SOURCES = (
    "audit",
    "registry",
    "database",
    "provider-route",
    "identity",
    "time",
)
F0_SOURCE_NAMESPACES = {
    "audit": "phase-b-source-event.audit",
    "registry": "phase-b-source-event.registry",
    "database": "phase-b-source-event.database",
    "provider-route": "phase-b-source-event.provider-route",
    "custody": "phase-b-source-event.custody",
    "identity": "phase-b-source-event.identity",
    "time": "phase-b-source-event.time",
}
F0_CAPTURE_WINDOW_SECONDS = 5


def _verify_live_capture(
    envelope: dict[str, Any],
    source: str,
    request: dict[str, Any],
    baseline: dict[str, Any],
    anchor: TrustAnchor,
    verifier: SignatureVerifier,
    received_wall: datetime | None = None,
    received_monotonic: float | None = None,
) -> dict[str, Any]:
    if source not in F0_SOURCE_NAMESPACES:
        raise VerificationError("F0 live capture source is not fixed")
    payload = verify_envelope(
        envelope, anchor, verifier, F0_SOURCE_NAMESPACES[source]
    )
    raw = strict_json.exact_object(
        payload,
        {
            "schema",
            "attempt_id",
            "baseline_digest",
            "capture_id",
            "phase",
            "journal_head",
            "source",
            "boot_id",
            "observed_at",
            "observed_monotonic",
            "window",
            "evidence",
        },
        "F0 live capture",
    )
    wanted_request = strict_json.exact_object(
        request,
        {
            "schema",
            "attempt_id",
            "baseline_digest",
            "capture_id",
            "phase",
            "journal_head",
        },
        "F0 capture request",
    )
    window = strict_json.exact_object(
        raw["window"],
        {
            "start_monotonic",
            "end_monotonic",
            "state_digest",
            "invalidating_event_count",
        },
        "F0 capture window",
    )
    start, end, observed = (
        window["start_monotonic"],
        window["end_monotonic"],
        raw["observed_monotonic"],
    )
    if (
        raw["schema"] != "phase-b.f0-live-evidence.v1"
        or raw["source"] != source
        or wanted_request["schema"] != "phase-b.capture-request.v1"
        or any(raw[key] != wanted_request[key] for key in (
            "attempt_id", "baseline_digest", "capture_id", "phase", "journal_head"
        ))
        or raw["attempt_id"] != baseline["attempt_id"]
        or raw["baseline_digest"] != strict_json.digest(baseline)
        or raw["boot_id"] != baseline["source_identity"]["boot_id"]
        or isinstance(observed, bool)
        or not isinstance(observed, (int, float))
        or isinstance(start, bool)
        or not isinstance(start, (int, float))
        or isinstance(end, bool)
        or not isinstance(end, (int, float))
        or not float(start) <= float(end) <= float(observed)
        or float(observed) - float(end) > CLOCK_TOLERANCE_SECONDS
        or float(end) - float(start) > F0_CAPTURE_WINDOW_SECONDS
        or type(window["invalidating_event_count"]) is not int
        or window["invalidating_event_count"] != 0
        or window["state_digest"] != strict_json.digest(raw["evidence"])
    ):
        raise VerificationError("F0 live capture binding/window is invalid")
    observed_wall = _parse_time(raw["observed_at"], "F0 capture time")
    baseline_wall = _parse_time(
        baseline["source_identity"]["wall_at"], "baseline source time"
    )
    baseline_monotonic = float(baseline["source_identity"]["monotonic"])
    if abs(
        (observed_wall - baseline_wall).total_seconds()
        - (float(observed) - baseline_monotonic)
    ) > CLOCK_TOLERANCE_SECONDS:
        raise VerificationError("F0 capture wall/monotonic mapping drifted")
    if (
        received_wall is not None
        and received_monotonic is not None
        and (
            not 0
            <= (received_wall - observed_wall).total_seconds()
            <= F0_CAPTURE_WINDOW_SECONDS
            or not 0
            <= float(received_monotonic) - float(observed)
            <= F0_CAPTURE_WINDOW_SECONDS
            or not 0
            <= float(received_monotonic) - float(end)
            <= CLOCK_TOLERANCE_SECONDS
        )
    ):
        raise VerificationError("F0 live capture was not received fresh")
    return raw


def _verify_custody_artifact(
    artifacts: ArtifactStore,
    reference: Any,
    method: str,
    *,
    anchor: TrustAnchor,
    verifier: SignatureVerifier,
    expected_capture_id: str,
    expected_attempt_id: str,
) -> dict[str, Any]:
    _ref, value = read_json_artifact(artifacts, reference, owner_only=True)
    payload = verify_envelope(
        value, anchor, verifier, F0_SOURCE_NAMESPACES["custody"]
    )
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "phase-b.f0-live-evidence.v1"
        or payload.get("source") != "custody"
        or payload.get("capture_id") != expected_capture_id
        or payload.get("attempt_id") != expected_attempt_id
    ):
        raise VerificationError("custody capture binding is invalid")
    return _verify_custody_value(payload.get("evidence"), method)


def _verify_custody_value(value: Any, method: str) -> dict[str, Any]:
    raw = strict_json.exact_object(
        value, {"schema", "method", "pages", "deletions"}, "custody artifact"
    )
    if (
        raw["schema"] != "phase-b.custody-read.v1"
        or raw["method"] != method
        or raw["deletions"] != []
        or not isinstance(raw["pages"], list)
        or tuple(page.get("surface") for page in raw["pages"] if isinstance(page, dict))
        != CUSTODY_SURFACES
    ):
        raise VerificationError("custody read is not complete GET-only evidence")
    pages: dict[str, list[Any]] = {}
    for page in raw["pages"]:
        item = strict_json.exact_object(
            page, {"surface", "page", "last", "records"}, "custody page"
        )
        if (
            item["page"] != 1
            or item["last"] is not True
            or not isinstance(item["records"], list)
        ):
            raise VerificationError("custody pagination is incomplete")
        pages[item["surface"]] = item["records"]
    lineages = pages["lineages"]
    if len(lineages) != 9:
        raise VerificationError("custody does not cover exactly 9 lineages")
    ids: set[str] = set()
    for lineage in lineages:
        item = strict_json.exact_object(
            lineage, {"id", "custody", "consequential"}, "lineage"
        )
        _identifier(item["id"], "lineage id")
        if (
            item["id"] in ids
            or item["custody"] != "REMOTE"
            or item["consequential"] is not True
        ):
            raise VerificationError(
                "lineage custody is duplicate/non-remote/non-consequential"
            )
        ids.add(item["id"])
    if pages["pending-effects"] != []:
        raise VerificationError("custody contains pending effects")
    custody = pages["custody"]
    if custody != [
        {"remote": 9, "total": 9, "pending": 0, "inflight": 0, "local_only": 0}
    ]:
        raise VerificationError("custody totals are not clean 9/9")
    residue = pages["residue"]
    if not residue:
        raise VerificationError("residue classification is absent")
    for record in residue:
        item = strict_json.exact_object(
            record, {"identity", "classification"}, "residue"
        )
        _identifier(item["identity"], "residue identity")
        if item["classification"] not in {
            "REMOTE_AUTHORITATIVE",
            "PRESERVED_NON_AXIS",
            "STALE_DERIVED",
        }:
            raise VerificationError("residue contains UNKNOWN or consequential residue")
    return {"lineage_ids": sorted(ids), "custody": custody[0], "residue": residue}


def _verify_execution_journal(
    journal: Journal,
    f0: dict[str, Any],
    baseline: dict[str, Any],
    registries: tuple[RegistryExpectation, ...],
    artifacts: ArtifactStore,
    anchor: TrustAnchor,
    verifier: SignatureVerifier,
) -> tuple[dict[str, Any], ...]:
    if journal.orphan_temps():
        raise VerificationError("execution journal contains interrupted temp records")
    records = journal.read_all()
    actions = [(record["kind"], record["action_id"]) for record in records]
    expected: list[tuple[str, str]] = [
        ("checkpoint", "baseline-verified"),
        ("checkpoint", "preflight"),
    ]
    b1 = [item for item in baseline["effect_plan"] if item["action"].startswith("b1:")]
    b2 = [item for item in baseline["effect_plan"] if item["action"].startswith("b2:")]
    for item in b1:
        expected.extend((("intent", item["action"]), ("outcome", item["action"])))
    expected.append(("checkpoint", "reprovisioners-fenced"))
    for item in b2:
        expected.extend((("intent", item["action"]), ("outcome", item["action"])))
    expected.extend(
        (
            ("checkpoint", "writers-paused"),
            ("checkpoint", "f0-capture-challenge"),
            ("checkpoint", "custody-read-1"),
            ("checkpoint", "custody-read-2"),
            ("checkpoint", "f0-established"),
        )
    )
    if actions != expected:
        raise VerificationError(
            "execution journal is incomplete, reordered, recovered, or widened"
        )
    if records[0]["payload"] != {
        "attempt_id": baseline["attempt_id"],
        "registry_digests": [strict_json.digest(item.document) for item in registries],
    }:
        raise VerificationError("baseline checkpoint mismatch")
    preflight = records[1]["payload"]
    if (
        preflight.get("effect_plan_digest")
        != strict_json.digest(baseline["effect_plan"])
        or preflight.get("rollback_plan_digest")
        != strict_json.digest(baseline["rollback_plan"])
        or preflight.get("effect_capable_processes") != []
    ):
        raise VerificationError(
            "preflight did not bind exact effect plans and zero processes"
        )
    baseline_units = {item["name"]: item for item in baseline["units"]}
    outcomes = {
        record["action_id"]: record["payload"]
        for record in records
        if record["kind"] == "outcome"
    }
    intents = {
        record["action_id"]: record["payload"]
        for record in records
        if record["kind"] == "intent"
    }
    unit_states = {
        name: {
            "name": name,
            "source_fragment_path": unit["fragment_path"],
            "source_fragment_digest": unit["fragment_digest"],
            "source_load_state": unit["load_state"],
            "active_state": unit["active_state"],
            "unit_file_state": unit["unit_file_state"],
            "runtime_masked": False,
            "trigger_edges": unit["trigger_edges"],
        }
        for name, unit in baseline_units.items()
    }
    for item in b1:
        _prefix, name, operation = item["action"].split(":", 2)
        intent = strict_json.exact_object(
            intents[item["action"]], {"operation", "preimage"}, "unit intent"
        )
        if intent["operation"] != operation or intent["preimage"] != unit_states[name]:
            raise VerificationError("unit intent preimage is not the exact prior state")
        outcome = outcomes[item["action"]]
        if outcome.get("status") != "achieved":
            raise VerificationError("unit effect was not achieved")
        state = _unit_state(outcome.get("state"))
        unit = baseline_units[name]
        stable = (
            state["name"] == name
            and state["source_fragment_path"] == unit["fragment_path"]
            and state["source_fragment_digest"] == unit["fragment_digest"]
            and state["source_load_state"] == unit["load_state"]
            and state["trigger_edges"] == unit["trigger_edges"]
        )
        expected_file_state = (
            "disabled"
            if unit["unit_file_state"] == "enabled"
            else unit["unit_file_state"]
        )
        reached = (
            state["active_state"] == "inactive"
            if operation == "stop"
            else state["unit_file_state"] == expected_file_state
            if operation == "disable"
            else (
                state["runtime_masked"] is True
                and state["unit_file_state"] == expected_file_state
                and state["unit_file_state"] not in {"masked", "masked-runtime"}
            )
        )
        if not stable or not reached:
            raise VerificationError("unit atomic outcome actual-state vector mismatch")
        unit_states[name] = state
    current_documents = [item.document for item in registries]
    current_identities = [(item.device, item.inode) for item in registries]
    applied: list[Any] = []
    for item in b2:
        parts = item["action"].split(":")
        delta = next(
            (
                candidate
                for candidate in FIXED_DELTAS
                if candidate.registry_index == int(parts[1])
                and candidate.job_id == parts[2]
            ),
            None,
        )
        if delta is None:
            raise VerificationError("B2 effect is not fixed")
        intent = strict_json.exact_object(
            intents[item["action"]],
            {"operation", "preimage_digests", "preimage"},
            "registry intent",
        )
        expected_pre = {
            "index": delta.registry_index,
            "path": registries[delta.registry_index].path,
            "device": current_identities[delta.registry_index][0],
            "inode": current_identities[delta.registry_index][1],
            "digest": strict_json.digest(current_documents[delta.registry_index]),
            "document": current_documents[delta.registry_index],
        }
        if (
            intent["operation"] != "hermes-internal-pause"
            or intent["preimage_digests"]
            != [strict_json.digest(document) for document in current_documents]
            or intent["preimage"] != expected_pre
        ):
            raise VerificationError("registry intent does not bind the exact preimage")
        applied.append(delta)
        payload = outcomes[item["action"]]
        if payload.get("preimage") != intent["preimage"]:
            raise VerificationError(
                "registry outcome/preimage journal linkage mismatch"
            )
        post = strict_json.exact_object(
            payload.get("postimage"),
            {"index", "path", "device", "inode", "digest", "document"},
            "registry postimage",
        )
        if (
            post["index"] != delta.registry_index
            or post["path"] != registries[delta.registry_index].path
            or (post["device"], post["inode"])
            == current_identities[delta.registry_index]
        ):
            raise VerificationError(
                "Hermes atomic replacement identity was not recorded"
            )
        _validate_applied_document(
            registries[delta.registry_index].document,
            post["document"],
            tuple(
                candidate.job_id
                for candidate in applied
                if candidate.registry_index == delta.registry_index
            ),
        )
        if post["digest"] != strict_json.digest(post["document"]):
            raise VerificationError("registry postimage digest mismatch")
        current_documents[delta.registry_index] = post["document"]
        current_identities[delta.registry_index] = (post["device"], post["inode"])
        expected_digests = [
            strict_json.digest(document) for document in current_documents
        ]
        if (
            payload.get("status") != "achieved"
            or payload.get("registry_digests") != expected_digests
        ):
            raise VerificationError("B2 cumulative registry evidence mismatch")
        if len(set(current_identities)) != 6:
            raise VerificationError("postimage aliases another registry")
    if f0["registry_digests"] != [
        strict_json.digest(document) for document in current_documents
    ]:
        raise VerificationError(
            "F0 registry evidence does not equal the full journal poststate"
        )
    custody = [records[-3]["payload"], records[-2]["payload"]]
    challenge = next(
        record for record in records if record["action_id"] == "f0-capture-challenge"
    )
    if challenge["payload"] != {
        "attempt_id": baseline["attempt_id"],
        "capture_id": f0["capture_id"],
        "baseline_digest": strict_json.digest(baseline),
        "b2_completed_monotonic": next(
            record for record in records if record["action_id"] == "writers-paused"
        )["payload"]["completed_monotonic"],
    }:
        raise VerificationError("F0 capture challenge is not journal-bound")
    writer_checkpoint = next(
        record for record in records if record["action_id"] == "writers-paused"
    )["payload"]
    if (
        [item.get("method") for item in custody] != ["GET", "NO_OP"]
        or custody[0]["observed_monotonic"]
        - writer_checkpoint.get("completed_monotonic", float("inf"))
        < 300
        or custody[1]["observed_monotonic"] - custody[0]["observed_monotonic"] < 300
    ):
        raise VerificationError("B3 lacks two post-B2 stable five-minute reads")
    challenge_head = challenge["record_hash"]
    derived_custody = []
    for index, item in enumerate(custody, 1):
        raw = strict_json.exact_object(
            item, set(CUSTODY_FIELDS) | {"observed_monotonic"}, "custody read"
        )
        _ref, envelope = read_json_artifact(
            artifacts, raw["artifact"], owner_only=True
        )
        capture = _verify_live_capture(
            envelope,
            "custody",
            {
                "schema": "phase-b.capture-request.v1",
                "attempt_id": baseline["attempt_id"],
                "baseline_digest": strict_json.digest(baseline),
                "capture_id": f0["capture_id"],
                "phase": f"custody-{index}",
                "journal_head": challenge_head,
            },
            baseline,
            anchor,
            verifier,
        )
        if capture["observed_monotonic"] != raw["observed_monotonic"]:
            raise VerificationError("custody journal time is not sensor-derived")
        derived_custody.append(_verify_custody_value(capture["evidence"], raw["method"]))
    for reference in f0["evidence"].values():
        _ref, envelope = read_json_artifact(artifacts, reference, owner_only=True)
        payload = envelope.get("payload") if isinstance(envelope, dict) else None
        if not isinstance(payload, dict) or payload.get("journal_head") != challenge_head:
            raise VerificationError("F0 capture does not bind the journaled challenge")
    if (
        derived_custody[0] != derived_custody[1]
        or f0["custody_reads"] != custody
        or f0["evidence"].get("custody") != custody[1]["artifact"]
    ):
        raise VerificationError("B3 complete custody reads are not stable/bound to F0")
    if (
        records[-1]["payload"]
        != {"artifact": f0, "artifact_digest": strict_json.digest(f0)}
        or f0["journal_head"] != records[-2]["record_hash"]
    ):
        raise VerificationError(
            "F0 is not bound to exact pre-establishment journal head"
        )
    return records


def _verify_f0(
    value: dict[str, Any],
    baseline: dict[str, Any],
    registries: tuple[RegistryExpectation, ...],
    artifacts: ArtifactStore,
    anchor: TrustAnchor,
    verifier: SignatureVerifier,
) -> None:
    raw = strict_json.exact_object(
        value,
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
        "F0",
    )
    if (
        raw["schema"] != "phase-b.f0.v3"
        or raw["attempt_id"] != baseline["attempt_id"]
        or not isinstance(raw["capture_id"], str)
        or len(raw["capture_id"]) != 64
        or any(character not in "0123456789abcdef" for character in raw["capture_id"])
    ):
        raise VerificationError("F0 identity/capture mismatch")
    evidence = strict_json.exact_object(
        raw["evidence"], set(F0_EVIDENCE_FIELDS), "F0 evidence"
    )
    captures: dict[str, dict[str, Any]] = {}
    challenge_head: str | None = None
    for source in (*F0_FINAL_SOURCES, "custody"):
        _ref, envelope = read_json_artifact(
            artifacts, evidence[source], owner_only=True
        )
        if not isinstance(envelope, dict):
            raise VerificationError("F0 signed capture is not an object")
        preliminary = verify_envelope(
            envelope, anchor, verifier, F0_SOURCE_NAMESPACES[source]
        )
        if not isinstance(preliminary, dict):
            raise VerificationError("F0 signed capture payload is absent")
        if challenge_head is None:
            challenge_head = preliminary.get("journal_head")
        request = {
            "schema": "phase-b.capture-request.v1",
            "attempt_id": baseline["attempt_id"],
            "baseline_digest": strict_json.digest(baseline),
            "capture_id": raw["capture_id"],
            "phase": "custody-2" if source == "custody" else "f0-final",
            "journal_head": challenge_head,
        }
        captures[source] = _verify_live_capture(
            envelope, source, request, baseline, anchor, verifier
        )
    starts = [float(item["window"]["start_monotonic"]) for item in captures.values()]
    ends = [float(item["window"]["end_monotonic"]) for item in captures.values()]
    observed = [float(item["observed_monotonic"]) for item in captures.values()]
    time_point = float(captures["time"]["observed_monotonic"])
    if (
        max(observed) - min(observed) > F0_CAPTURE_WINDOW_SECONDS
        or max(starts) > min(ends)
        or not max(starts) <= time_point <= min(ends)
        or raw["f0_at"] != captures["time"]["observed_at"]
    ):
        raise VerificationError("F0 captures lack a common stable five-second window")

    audit = strict_json.exact_object(
        captures["audit"]["evidence"],
        {
            "schema", "processes", "listeners", "effect_capable_descendants",
            "writers", "reprovisioners", "canonical_writers",
        },
        "audit evidence",
    )
    if (
        audit["schema"] != "phase-b.audit-evidence.v1"
        or audit["effect_capable_descendants"] != []
        or any(audit[name] != 0 for name in ("writers", "reprovisioners", "canonical_writers"))
    ):
        raise VerificationError("F0 audit predicates failed")

    def cross_check(expected: list[dict[str, Any]], actual: Any, label: str) -> None:
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise VerificationError(f"F0 {label} inventory cardinality mismatch")
        wanted = {item["identity"]: item["classification"] for item in expected}
        counts: dict[str, int] = {}
        for entry in actual:
            item = strict_json.exact_object(entry, {"identity", "count"}, label)
            if item["identity"] in counts or type(item["count"]) is not int:
                raise VerificationError(f"F0 {label} inventory is duplicated/untyped")
            counts[item["identity"]] = item["count"]
        if set(counts) != set(wanted) or any(
            counts[name] != (1 if classification == "preserved" else 0)
            for name, classification in wanted.items()
        ):
            raise VerificationError(f"F0 {label} inventory classification failed")

    cross_check(baseline["expected_process_inventory"], audit["processes"], "process")
    cross_check(baseline["expected_listener_inventory"], audit["listeners"], "listener")

    database = strict_json.exact_object(
        captures["database"]["evidence"],
        {"schema", "pending", "inflight", "local_only"},
        "database evidence",
    )
    if database != {
        "schema": "phase-b.database-evidence.v1",
        "pending": 0,
        "inflight": 0,
        "local_only": 0,
    }:
        raise VerificationError("F0 pending database effects are nonzero")
    route = strict_json.exact_object(
        captures["provider-route"]["evidence"],
        {"schema", "authority_identities", "alpha0_authority"},
        "route evidence",
    )
    if (
        route["schema"] != "phase-b.route-evidence.v1"
        or route["authority_identities"] != baseline["authority_identities"]
        or route["alpha0_authority"] != "UNCHANGED_NOT_DRAINED"
    ):
        raise VerificationError("F0 provider route/Alpha0 authority drifted")
    identity = strict_json.exact_object(
        captures["identity"]["evidence"],
        {"schema", "source_identity", "preserved_start_identities", "stuck_watchdog_healthy"},
        "identity evidence",
    )
    expected_source = {
        key: baseline["source_identity"][key]
        for key in ("host_identity", "machine_id", "boot_id", "user_manager_id", "home_generation", "booted_closure")
    }
    if (
        identity["schema"] != "phase-b.identity-evidence.v1"
        or identity["source_identity"] != expected_source
        or identity["preserved_start_identities"] != {
            item["name"]: item["start_identity"] for item in baseline["preserved_units"]
        }
        or identity["stuck_watchdog_healthy"] is not True
    ):
        raise VerificationError("F0 boot/user-manager/Home/preserved identity drifted")
    registry = strict_json.exact_object(
        captures["registry"]["evidence"], {"schema", "registries"}, "registry evidence"
    )
    if registry["schema"] != "phase-b.registry-evidence.v1" or not isinstance(registry["registries"], list) or len(registry["registries"]) != 6:
        raise VerificationError("F0 registry evidence is incomplete")
    actual_digests: list[str] = []
    for index, item in enumerate(registry["registries"]):
        entry = strict_json.exact_object(item, {"index", "path", "digest"}, "registry evidence entry")
        if entry["index"] != index or entry["path"] != registries[index].path:
            raise VerificationError("F0 registry evidence path/index mismatch")
        actual_digests.append(_actual_digest(entry["digest"], "F0 registry digest"))
    if raw["registry_digests"] != actual_digests:
        raise VerificationError("F0 registry summary is not derived from signed evidence")
    final_custody = _verify_custody_value(captures["custody"]["evidence"], "NO_OP")
    if not isinstance(raw["custody_reads"], list) or len(raw["custody_reads"]) != 2:
        raise VerificationError("F0 custody evidence cardinality mismatch")
    historical = [
        _verify_custody_artifact(
            artifacts, item["artifact"], item["method"], anchor=anchor, verifier=verifier,
            expected_capture_id=raw["capture_id"], expected_attempt_id=raw["attempt_id"]
        )
        for item in raw["custody_reads"]
    ]
    if historical[0] != historical[1] or historical[1] != final_custody:
        raise VerificationError("F0 custody captures are not stable")
    time_evidence = strict_json.exact_object(
        captures["time"]["evidence"], {"schema", "wall", "monotonic"}, "time evidence"
    )
    if time_evidence != {
        "schema": "phase-b.time-evidence.v1",
        "wall": captures["time"]["observed_at"],
        "monotonic": captures["time"]["observed_monotonic"],
    }:
        raise VerificationError("F0 signed time evidence is inconsistent")


def _verify_event_metadata(
    event: dict[str, Any],
    identities: dict[str, Any],
    stream: str,
    previous_token: str,
    receiver_previous_head: str,
    starting_token: str,
) -> list[str]:
    event_class, value = event["event_class"], event["metadata"]
    failures: list[str] = []
    if event_class == "coverage-open":
        if value != {"cursor_anchor": starting_token}:
            failures.append("coverage-open")
    elif event_class == "coverage-close":
        if value != {"cursor_head": previous_token}:
            failures.append("coverage-close")
    elif event_class == "ack":
        if value != {
            "cursor_token": previous_token,
            "receiver_head": receiver_previous_head,
        }:
            failures.append("ack")
    elif event_class == "continuity-checkpoints":
        if set(value) != {"checkpoints"} or not isinstance(value["checkpoints"], list):
            failures.append("continuity-checkpoints")
    elif event_class == "rotation":
        if value != {"complete": True}:
            failures.append("rotation")
    elif event_class == "heartbeat":
        if set(value) != {"source_state_digest"}:
            failures.append("heartbeat")
        else:
            try:
                _actual_digest(value["source_state_digest"], "heartbeat state")
            except VerificationError:
                failures.append("heartbeat")
    elif event_class in TERMINAL_EVENT_CLASSES:
        failures.append("terminal")
    elif event_class == "exec-snapshot":
        if value != {
            "writers": 0,
            "reprovisioners": 0,
            "effect_capable_descendants": 0,
        }:
            failures.append("process-recurrence")
    elif event_class == "scheduler-claim":
        if value != {"claim_count": 0}:
            failures.append("scheduler-recurrence")
    elif event_class in {"writer-invocation", "recovery-invocation"}:
        if value != {"invocation_count": 0}:
            failures.append("invocation-recurrence")
    elif event_class == "unit-transition":
        if value != {"forbidden_transition_count": 0}:
            failures.append("unit-transition")
    elif event_class == "registry-write":
        if value != {
            "registry_digests": identities["registry_digests"],
            "unauthorized_write_count": 0,
        }:
            failures.append("registry-write")
    elif event_class == "database-write":
        if value != {
            "pending": 0,
            "inflight": 0,
            "local_only": 0,
            "unauthorized_write_count": 0,
        }:
            failures.append("database-write")
    elif event_class == "route-ownership":
        if value != {
            "generic_route_identity": identities["generic_route_identity"],
            "alpha0_route_identity": identities["alpha0_route_identity"],
            "dedicated_axis_route": "ABSENT",
        }:
            failures.append("route-ownership")
    elif event_class == "custody-read":
        if value != {
            "remote": 9,
            "total": 9,
            "pending": 0,
            "inflight": 0,
            "local_only": 0,
            "frontier_digest": identities["frontier_digest"],
        }:
            failures.append("custody")
    elif event_class == "identity-snapshot":
        if value != identities:
            failures.append("identity")
    elif event_class == "time-anchor":
        if set(value) != {"wall_at", "monotonic"}:
            failures.append("time")
        else:
            _parse_time(value["wall_at"], "source time anchor")
    else:
        failures.append("unknown-event-class")
    return failures


def _verify_receiver_source_records(
    records: Any,
    expected_envelopes: list[dict[str, Any]],
    *,
    first_sequence: int,
    previous_head: str,
    expected_head: str,
    earliest: datetime,
    latest: datetime,
    previous_monotonic: float | None = None,
) -> tuple[str, float]:
    if not isinstance(records, list) or len(records) != len(expected_envelopes):
        raise VerificationError("receiver source custody cardinality mismatch")
    head = previous_head
    terminal_monotonic = previous_monotonic
    for offset, (record, envelope) in enumerate(
        zip(records, expected_envelopes, strict=True)
    ):
        item = strict_json.exact_object(
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
            "receiver source record",
        )
        sequence = first_sequence + offset
        payload = strict_json.exact_object(
            item["payload"],
            {
                "sequence",
                "envelope",
                "receiver_received_at",
                "receiver_received_monotonic",
            },
            "receiver source payload",
        )
        unsigned = dict(item)
        record_hash = unsigned.pop("record_hash")
        received = _parse_time(
            payload["receiver_received_at"], "receiver source reception"
        )
        monotonic = payload["receiver_received_monotonic"]
        source_payload = envelope.get("payload")
        if not isinstance(source_payload, dict):
            raise VerificationError("receiver source envelope payload is invalid")
        source_observed = _parse_time(
            source_payload.get("observed_at"), "receiver source observed time"
        )
        source_delay = (received - source_observed).total_seconds()
        if (
            item["schema"] != "phase-b.journal-record.v1"
            or item["sequence"] != sequence
            or item["previous_hash"] != head
            or item["kind"] != "checkpoint"
            or item["action_id"] != f"source-{sequence:020d}"
            or payload["sequence"] != sequence
            or payload["envelope"] != envelope
            or strict_json.digest(unsigned) != record_hash
            or received < earliest
            or received > latest
            or source_delay < 0
            or source_delay > 120
            or isinstance(monotonic, bool)
            or not isinstance(monotonic, (int, float))
            or terminal_monotonic is not None
            and (
                float(monotonic) < terminal_monotonic
                or offset == 0
                and previous_monotonic is not None
                and float(monotonic) <= previous_monotonic
            )
        ):
            raise VerificationError("receiver source custody chain is invalid")
        if envelope.get("payload", {}).get("event_class") == "continuity-checkpoints":
            checkpoints = envelope["payload"].get("metadata", {}).get("checkpoints")
            if not isinstance(checkpoints, list) or any(
                not 0
                <= (
                    received
                    - _parse_time(
                        checkpoint.get("received_at"),
                        "receiver checkpoint reception",
                    )
                ).total_seconds()
                <= 120
                for checkpoint in checkpoints
            ):
                raise VerificationError("receiver accepted buffered checkpoint history")
        head = record_hash
        terminal_monotonic = float(monotonic)
    if head != expected_head or terminal_monotonic is None:
        raise VerificationError("receiver source custody head mismatch")
    return head, terminal_monotonic


def _verify_observation_chain(
    journal: Journal,
    observation: dict[str, Any],
    baseline: dict[str, Any],
    f0: dict[str, Any],
    anchor: TrustAnchor,
    signature_verifier: SignatureVerifier,
    artifacts: ArtifactStore,
) -> tuple[float, dict[str, str]]:
    if journal.orphan_temps():
        raise VerificationError("observation journal contains interrupted temp records")
    records = journal.read_all()
    observation_start = _parse_time(
        observation["observation_started_at"], "observation start"
    )
    observation_end = _parse_time(observation["observed_through_at"], "observation end")
    if (
        not records
        or records[-1]["record_hash"] != observation["chain_head"]
        or any(record["kind"] == "invalidation" for record in records)
    ):
        raise VerificationError("observation chain is invalidated or not head-bound")
    if (
        records[0]["action_id"] != "observation-start"
        or records[0]["payload"].get("f0_at") != observation["f0_at"]
        or records[0]["payload"].get("f0_digest") != observation["f0_digest"]
        or records[0]["payload"].get("observation_started_at")
        != observation["observation_started_at"]
        or records[-1]["action_id"] != "observation-finish"
    ):
        raise VerificationError("observation boundaries are malformed")
    body = dict(observation)
    body.pop("chain_head")
    if records[-1]["payload"] != {"artifact_body_digest": strict_json.digest(body)}:
        raise VerificationError("observation finish does not bind artifact body")
    f0_time = _parse_time(observation["f0_at"], "signed F0")
    f0_monotonic = records[0]["payload"].get("f0_monotonic")
    if isinstance(f0_monotonic, bool) or not isinstance(f0_monotonic, (int, float)):
        raise VerificationError("observation synthetic F0 origin is invalid")
    f0_monotonic = float(f0_monotonic)
    starting = baseline["starting_cursors"]
    cursors = {item["stream"]: dict(item) for item in starting}
    classes = {stream: set() for stream in STREAMS}
    coverage_open: dict[str, float] = {}
    coverage_close: dict[str, float] = {}
    continuity_end: dict[str, float] = {}
    raw_types = {stream: set() for stream in STREAMS}
    sample_elapsed: list[float] = []
    custody = observation["receiver_custody"]
    _receiver_ref, receiver_records = read_json_artifact(
        artifacts,
        custody["records"],
        owner_only=True,
        maximum=MAX_EVIDENCE_ARTIFACT_BYTES,
    )
    if not isinstance(receiver_records, list):
        raise VerificationError("receiver source custody artifact is not a record list")
    failures: list[str] = []
    source_envelopes: list[dict[str, Any]] = []
    source_ordinal = 0
    for index, record in enumerate(records[1:-1], 1):
        action, payload = record["action_id"], record["payload"]
        if action.startswith("source:"):
            item = strict_json.exact_object(
                payload, {"envelope", "token"}, "source chain record"
            )
            envelope = item["envelope"]
            if not isinstance(envelope, dict) or not isinstance(
                envelope.get("payload"), dict
            ):
                raise VerificationError("source envelope is malformed")
            stream = envelope["payload"].get("stream")
            if stream not in STREAMS:
                raise VerificationError("source stream is unknown")
            try:
                event = verify_envelope(
                    envelope,
                    anchor,
                    signature_verifier,
                    f"phase-b-source-event.{stream}",
                )
                strict_json.exact_object(
                    event,
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
                    },
                    "source event",
                )
            except (TrustError, strict_json.StrictJSONError) as exc:
                raise VerificationError(
                    "source event role signature is invalid"
                ) from exc
            if (
                event["schema"] != "phase-b.source-event.v1"
                or event["attempt_id"] != baseline["attempt_id"]
                or event["stream"] != stream
            ):
                raise VerificationError("source event identity mismatch")
            previous = cursors[stream]
            same = (
                event["generation"] == previous["generation"]
                and event["offset"] == previous["offset"] + 1
            )
            rotation = (
                event["generation"] == previous["generation"] + 1
                and event["offset"] == 0
                and event["event_class"] == "rotation"
            )
            if (
                event["previous_token"] != previous["token"]
                or not (same or rotation)
                or item["token"] != strict_json.digest(envelope)
            ):
                raise VerificationError("source cursor gap/replay/token mismatch")
            receiver_previous = (
                receiver_records[source_ordinal].get("previous_hash")
                if source_ordinal < len(receiver_records)
                and isinstance(receiver_records[source_ordinal], dict)
                else ""
            )
            failures.extend(
                _verify_event_metadata(
                    event,
                    observation["identities"],
                    stream,
                    previous["token"],
                    receiver_previous,
                    starting[STREAMS.index(stream)]["token"],
                )
            )
            if event["event_class"] == "continuity-checkpoints":
                checkpoints = event["metadata"]["checkpoints"]
                if not checkpoints or len(checkpoints) > 2048:
                    failures.append("continuity-cardinality")
                source_origin = coverage_open.get(stream)
                if source_origin is None:
                    raise VerificationError(
                        "source continuity begins before coverage opens"
                    )
                prior_end = continuity_end.get(stream, source_origin)
                previous_received: datetime | None = None
                receiver_ack = receiver_previous
                source_cursors: set[str] = set()
                for checkpoint in checkpoints:
                    raw = strict_json.exact_object(
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
                    start_value, end_value = (
                        raw["start_monotonic"],
                        raw["end_monotonic"],
                    )
                    if any(
                        isinstance(number, bool) or not isinstance(number, (int, float))
                        for number in (start_value, end_value)
                    ):
                        raise VerificationError("continuity interval is untyped")
                    if (
                        abs(float(start_value) - float(prior_end)) > 1
                        or not 0
                        < float(end_value) - float(start_value)
                        <= CONTINUITY_INTERVAL_SECONDS
                    ):
                        failures.append("continuous-source-gap")
                    received = _parse_time(
                        raw["received_at"], "receiver checkpoint time"
                    )
                    expected_received = f0_time + timedelta(
                        seconds=float(end_value) - source_origin
                    )
                    if (
                        received < observation_start
                        or received > observation_end
                        or previous_received is not None
                        and received <= previous_received
                        or abs((received - expected_received).total_seconds()) > 1
                    ):
                        failures.append("receiver-time-rollback-or-out-of-bounds")
                    previous_received = received
                    expected_ack = strict_json.digest(
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
                        or raw["source_cursor"] in source_cursors
                        or any(raw[key] != 0 for key in ("lost", "backlog", "replay"))
                        or isinstance(raw["event_count"], bool)
                        or not isinstance(raw["event_count"], int)
                        or raw["event_count"] < 0
                    ):
                        failures.append("loss-backlog-replay-ack-or-cursor")
                    source_cursors.add(raw["source_cursor"])
                    _ref, batch = read_json_artifact(
                        artifacts, raw["batch"], owner_only=True
                    )
                    seen, derived_failures = derive_raw_batch(
                        stream, batch, observation["identities"]
                    )
                    if (
                        batch["source_cursor"] != raw["source_cursor"]
                        or len(batch["events"]) != raw["event_count"]
                    ):
                        failures.append("raw-batch-cursor-count")
                    raw_types[stream].update(seen)
                    failures.extend(derived_failures)
                    prior_end = float(end_value)
                    receiver_ack = expected_ack
                continuity_end[stream] = float(prior_end)
            source_envelopes.append(envelope)
            source_ordinal += 1
            classes[stream].add(event["event_class"])
            monotonic = event["observed_monotonic"]
            if isinstance(monotonic, bool) or not isinstance(monotonic, (int, float)):
                raise VerificationError("source monotonic time is invalid")
            if event["event_class"] == "coverage-open":
                if stream in coverage_open:
                    failures.append("duplicate-coverage-open")
                if (
                    _parse_time(event["observed_at"], "coverage opening")
                    != f0_time
                ):
                    failures.append("coverage-open-not-at-f0")
                coverage_open[stream] = float(monotonic)
            elif event["event_class"] == "coverage-close":
                coverage_close[stream] = float(monotonic)
            cursors[stream] = {
                "stream": stream,
                "generation": event["generation"],
                "offset": event["offset"],
                "token": item["token"],
            }
        elif action.startswith("sample:"):
            item = strict_json.exact_object(
                payload,
                {"sampled_at", "elapsed_monotonic", "snapshot", "snapshot_digest"},
                "sample",
            )
            snapshot = item["snapshot"]
            if (
                not isinstance(snapshot, dict)
                or set(snapshot) != SAMPLE_FIELDS
                or item["snapshot_digest"] != strict_json.digest(snapshot)
            ):
                raise VerificationError("supplementary sample is incomplete")
            elapsed = item["elapsed_monotonic"]
            if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
                raise VerificationError("sample monotonic is invalid")
            sample_elapsed.append(float(elapsed))
            if (
                any(
                    snapshot[key] != 0
                    for key in (
                        "legacy_axis_new_work_writers",
                        "legacy_axis_reprovisioners",
                        "effect_capable_descendants",
                        "canonical_writers",
                        "pending_local_effects",
                    )
                )
                or snapshot["unknowns"] != []
                or snapshot["custody_remote"] != 9
                or snapshot["custody_total"] != 9
                or snapshot["stuck_watchdog_state"] != "healthy"
                or any(
                    snapshot[key] != observation["identities"][key]
                    for key in IDENTITY_FIELDS
                )
            ):
                failures.append("sample-recurrence-or-drift")
        else:
            raise VerificationError("observation chain contains unknown record")
    if failures:
        raise VerificationError(
            "continuous evidence derives forbidden recurrence, loss, or drift: "
            + ",".join(sorted(set(failures)))
        )
    if (
        not sample_elapsed
        or len(sample_elapsed) != observation["sample_count"]
        or sample_elapsed[0] > SAMPLE_INTERVAL_SECONDS
        or observation["elapsed_monotonic"] - sample_elapsed[-1]
        > SAMPLE_INTERVAL_SECONDS
        or any(
            not 0 < right - left <= SAMPLE_INTERVAL_SECONDS
            for left, right in pairwise(sample_elapsed)
        )
    ):
        raise VerificationError("supplementary sampling has a gap")
    for stream in STREAMS:
        if (
            not MANDATORY_EVENT_CLASSES[stream] <= classes[stream]
            or not MANDATORY_RAW_TYPES[stream] <= raw_types[stream]
            or abs(coverage_close.get(stream, -1) - continuity_end.get(stream, -2)) > 1
            or coverage_close.get(stream, -1) - coverage_open.get(stream, 0)
            < OBSERVATION_SECONDS
        ):
            raise VerificationError(
                "heartbeat-only or incomplete continuous source coverage"
            )
        if observation["coverage"].get(stream) != {
            "start_monotonic": coverage_open[stream],
            "end_monotonic": coverage_close[stream],
            "event_classes": sorted(classes[stream]),
            "raw_event_types": sorted(raw_types[stream]),
        }:
            raise VerificationError("collector coverage summary is not recomputed")
    if set(observation["coverage"]) != set(STREAMS):
        raise VerificationError("collector coverage contains unknown/missing streams")
    if observation["starting_cursors"] != starting or observation["ending_cursors"] != [
        cursors[name] for name in STREAMS
    ]:
        raise VerificationError("observation cursor summary mismatch")
    _receiver_head, terminal_receiver_monotonic = _verify_receiver_source_records(
        receiver_records,
        source_envelopes,
        first_sequence=0,
        previous_head=ZERO_HASH,
        expected_head=custody["head"],
        earliest=observation_start,
        latest=observation_end,
    )
    if custody["sequence"] != len(source_envelopes):
        raise VerificationError("receiver source custody sequence mismatch")
    source_walls: dict[str, str] = {}
    for envelope in source_envelopes:
        source = envelope["payload"]
        source_walls[source["stream"]] = source["observed_at"]
    if set(source_walls) != set(STREAMS):
        raise VerificationError("observation terminal source wall state is incomplete")
    return terminal_receiver_monotonic, source_walls


def _verify_observation(
    value: dict[str, Any],
    baseline: dict[str, Any],
    f0: dict[str, Any],
    anchor: TrustAnchor,
) -> None:
    raw = strict_json.exact_object(
        value,
        {
            "schema",
            "attempt_id",
            "f0_at",
            "f0_digest",
            "observation_started_at",
            "observed_through_at",
            "elapsed_monotonic",
            "sample_count",
            "starting_cursors",
            "ending_cursors",
            "identities",
            "coverage",
            "derived",
            "invalidations",
            "chain_head",
            "receiver_custody",
        },
        "observation",
    )
    if (
        raw["schema"] != "phase-b.observation.v2"
        or raw["attempt_id"] != baseline["attempt_id"]
        or raw["f0_at"] != f0["f0_at"]
        or raw["f0_digest"] != strict_json.digest(f0)
    ):
        raise VerificationError("observation identity/F0 mismatch")
    f0_time = _parse_time(raw["f0_at"], "source F0")
    started, ended = (
        _parse_time(raw["observation_started_at"], "observation start"),
        _parse_time(raw["observed_through_at"], "observation end"),
    )
    if (
        f0_time > started
        or (started - f0_time).total_seconds() > 120
        or (ended - f0_time).total_seconds() < OBSERVATION_SECONDS
        or raw["elapsed_monotonic"] < OBSERVATION_SECONDS
        or abs((ended - f0_time).total_seconds() - raw["elapsed_monotonic"]) > 1
    ):
        raise VerificationError("observation duration/clocks are invalid")
    if raw["invalidations"] != []:
        raise VerificationError("observation is invalidated")
    identities = strict_json.exact_object(
        raw["identities"], set(IDENTITY_FIELDS), "observation identities"
    )
    source = baseline["source_identity"]
    expected_authority = baseline["authority_identities"]
    expected = {
        "host_identity": source["host_identity"],
        "machine_id": source["machine_id"],
        "boot_id": source["boot_id"],
        "user_manager_id": source["user_manager_id"],
        "home_generation": source["home_generation"],
        "generic_route_identity": expected_authority["generic"]["route_identity"],
        "generic_service_identity": expected_authority["generic"]["service_identity"],
        "generic_session_identity": expected_authority["generic"]["session_identity"],
        "generic_profile_identity": expected_authority["generic"]["profile_identity"],
        "alpha0_route_identity": expected_authority["alpha0"]["route_identity"],
        "alpha0_service_identity": expected_authority["alpha0"]["service_identity"],
        "alpha0_session_identity": expected_authority["alpha0"]["session_identity"],
        "alpha0_profile_identity": expected_authority["alpha0"]["profile_identity"],
        "dedicated_axis_route": "ABSENT",
        "frontier_digest": baseline["custody"]["frontier_digest"],
        "registry_digests": f0["registry_digests"],
        "collector_identity": anchor.collector_identity,
    }
    if identities != expected:
        raise VerificationError("observation identities drifted from anchor/B0/F0")
    custody = strict_json.exact_object(
        raw["receiver_custody"], {"head", "sequence", "records"}, "receiver custody"
    )
    if (
        not isinstance(custody["sequence"], int)
        or isinstance(custody["sequence"], bool)
        or custody["sequence"] < 1
        or not isinstance(custody["head"], str)
        or not custody["head"].startswith("sha256:")
    ):
        raise VerificationError("observation lacks durable receiver custody")
    try:
        ArtifactRef.parse(custody["records"])
    except ArtifactError as exc:
        raise VerificationError(
            "observation receiver custody artifact is invalid"
        ) from exc
    if custody["records"]["owner_only"] is not True:
        raise VerificationError("observation lacks durable receiver custody")
    if raw["derived"] != {"forbidden_recurrence_count": 0, "history_complete": True}:
        raise VerificationError(
            "collector summary is not acceptable (and is not sole evidence)"
        )


def _verify_reconstruction(
    value: dict[str, Any],
    baseline: dict[str, Any],
    anchor: TrustAnchor,
    artifacts: ArtifactStore,
) -> tuple[str, tuple[str, str, str], str, str]:
    raw = strict_json.exact_object(
        value,
        {
            "schema",
            "attempt_id",
            "home_id",
            "execution_identity",
            "home_mode",
            "source_revision",
            "runner",
            "clean_roots",
            "transcript",
            "network_monitor",
            "write_monitor",
            "environment",
            "imported_state",
            "semantics",
        },
        "reconstruction",
    )
    if (
        raw["schema"] != "phase-b.reconstruction.v2"
        or raw["attempt_id"] != baseline["attempt_id"]
    ):
        raise VerificationError("reconstruction identity mismatch")
    _identifier(raw["home_id"], "disposable home")
    execution_identity = _identifier(
        raw["execution_identity"], "reconstruction execution identity"
    )
    if (
        raw["home_mode"] != 0o700
        or raw["source_revision"] != baseline["source_revision"]
    ):
        raise VerificationError("reconstruction source/home mismatch")

    def binding(name: str, value: Any) -> None:
        item = strict_json.exact_object(value, {"closure", "path", "digest"}, name)
        expected = anchor.executables[name]
        if item != {
            "closure": str(expected.closure),
            "path": str(expected.path),
            "digest": expected.digest,
        }:
            raise VerificationError(f"reconstruction selected a non-anchored {name}")

    binding("reconstruction-runner", raw["runner"])
    roots = strict_json.exact_object(
        raw["clean_roots"],
        {"home", "runtime", "workspace", "preexisting_entries"},
        "clean roots",
    )
    if (
        roots["preexisting_entries"] != 0
        or len({roots["home"], roots["runtime"], roots["workspace"]}) != 3
    ):
        raise VerificationError("reconstruction roots are not clean and distinct")
    root_tuple = (
        _identifier(roots["home"], "clean root"),
        _identifier(roots["runtime"], "clean root"),
        _identifier(roots["workspace"], "clean root"),
    )

    transcript = strict_json.exact_object(
        raw["transcript"], {"input", "output", "transcript"}, "transcript"
    )
    opened: dict[str, str] = {}
    for key, reference in transcript.items():
        ref, _data = read_artifact(artifacts, reference, owner_only=True)
        opened[key] = ref.digest

    network = strict_json.exact_object(
        raw["network_monitor"], {"binding", "artifact"}, "network monitor"
    )
    write = strict_json.exact_object(
        raw["write_monitor"], {"binding", "artifact"}, "write monitor"
    )
    binding("network-monitor", network["binding"])
    binding("write-monitor", write["binding"])
    _network_ref, network_log = read_json_artifact(
        artifacts, network["artifact"], owner_only=True
    )
    _write_ref, write_log = read_json_artifact(
        artifacts, write["artifact"], owner_only=True
    )
    network_log = strict_json.exact_object(
        network_log, {"schema", "events"}, "network log"
    )
    write_log = strict_json.exact_object(write_log, {"schema", "events"}, "write log")
    if (
        network_log["schema"] != "phase-b.network-monitor.v1"
        or not isinstance(network_log["events"], list)
        or not network_log["events"]
    ):
        raise VerificationError("network monitor log is absent")
    for event in network_log["events"]:
        item = strict_json.exact_object(
            event, {"operation", "destination_class", "result"}, "network event"
        )
        if (
            item["operation"] != "connect"
            or item["result"] != "DENIED"
            or item["destination_class"] != "network"
        ):
            raise VerificationError(
                "network monitor did not prove denied network access"
            )
    if (
        write_log["schema"] != "phase-b.write-monitor.v1"
        or not isinstance(write_log["events"], list)
        or not write_log["events"]
    ):
        raise VerificationError("write monitor log is absent")
    for event in write_log["events"]:
        item = strict_json.exact_object(
            event, {"operation", "path_class", "result"}, "write event"
        )
        if (
            item["operation"] != "write"
            or item["result"] != "DENIED"
            or item["path_class"] != "outside-clean-roots"
        ):
            raise VerificationError("write monitor did not prove denied outside writes")

    environment = strict_json.exact_object(
        raw["environment"], {"mode", "variable_names", "artifact"}, "environment"
    )
    if (
        environment["mode"] != 0o600
        or not isinstance(environment["variable_names"], list)
        or not environment["variable_names"]
    ):
        raise VerificationError("managed environment metadata is incomplete")
    read_artifact(artifacts, environment["artifact"], owner_only=True)
    _import_ref, imported = read_json_artifact(
        artifacts, raw["imported_state"], owner_only=True
    )
    if imported != {"schema": "phase-b.imported-state.v1", "imports": []}:
        raise VerificationError("reconstruction imported durable state")
    _semantic_ref, semantics = read_json_artifact(
        artifacts, raw["semantics"], owner_only=True
    )
    semantic_digest = strict_json.digest(semantics)
    if semantic_digest != anchor.canonical_vectors_digest:
        raise VerificationError(
            "reconstruction semantics differ from anchored canonical vectors"
        )
    return semantic_digest, root_tuple, opened["transcript"], execution_identity


def _receipt_chain(
    envelopes: tuple[dict[str, Any], ...],
    anchor: TrustAnchor,
    verifier: SignatureVerifier,
    attempt_id: str,
) -> tuple[dict[str, Any], ...]:
    payloads: list[dict[str, Any]] = []
    previous: str | None = None
    nonces: set[str] = set()
    accumulator = strict_json.digest([])
    for counter, envelope in enumerate(envelopes, 1):
        payload = _signed(envelope, "receipt", anchor, verifier)
        if (
            set(payload) != RECEIPT_FIELDS
            or payload["schema"] != "phase-b.receipt.v2"
            or payload["attempt_id"] != attempt_id
        ):
            raise VerificationError("receipt chain identity/schema mismatch")
        if (
            payload["previous_receipt_digest"] != previous
            or payload["consumption_counter"] != counter
            or payload["verified_chain_accumulator"] != accumulator
        ):
            raise VerificationError(
                "receipt chain link/counter/accumulator is missing or replayed"
            )
        nonce = _identifier(payload["consumer_nonce"], "consumer nonce")
        _identifier(payload["consumer_identity"], "consumer identity")
        if nonce in nonces:
            raise VerificationError("consumer nonce was reused")
        nonces.add(nonce)
        _parse_time(payload["issued_at"], "receipt issue time")
        payloads.append(payload)
        previous = strict_json.digest(payload)
        accumulator = strict_json.digest(
            {"previous": accumulator, "signed_receipt": strict_json.digest(envelope)}
        )
    return tuple(payloads)


def _verify_continuation(
    envelope: dict[str, Any],
    receipt: dict[str, Any],
    attempt_id: str,
    observation: dict[str, Any],
    initial_receiver_head: str,
    initial_receiver_sequence: int,
    initial_cursors: dict[str, dict[str, Any]],
    initial_continuity: dict[str, float],
    initial_source_walls: dict[str, str],
    initial_time: datetime,
    initial_receiver_monotonic: float,
    anchor: TrustAnchor,
    verifier: SignatureVerifier,
    artifacts: ArtifactStore,
) -> tuple[
    str,
    datetime,
    float,
    dict[str, dict[str, Any]],
    dict[str, float],
    dict[str, str],
    int,
]:
    try:
        proof = verify_envelope(envelope, anchor, verifier, "phase-b-observation")
    except TrustError as exc:
        raise VerificationError(
            "receiver continuation is not collector-signed"
        ) from exc
    proof = strict_json.exact_object(
        proof,
        {
            "schema",
            "attempt_id",
            "observation_head",
            "source_records",
            "extensions",
            "current_head",
            "terminal_cursors",
            "terminal_continuity",
            "terminal_source_walls",
        },
        "continuation proof",
    )
    if (
        proof["schema"] != "phase-b.receiver-continuation.v1"
        or proof["attempt_id"] != attempt_id
        or proof["observation_head"] != initial_receiver_head
    ):
        raise VerificationError("continuation proof identity/head mismatch")
    if (
        not isinstance(proof["extensions"], list)
        or len(proof["extensions"]) != 1
        or not isinstance(proof["source_records"], list)
        or not proof["source_records"]
        or len(proof["source_records"]) % len(STREAMS) != 0
    ):
        raise VerificationError("receipt freshness is not extended through consumption")
    previous_time = initial_time
    preview = strict_json.exact_object(
        proof["extensions"][0],
        {"sequence", "previous_head", "head", "event", "receiver_record"},
        "receiver extension preview",
    )
    _preview_ref, preview_event = read_json_artifact(
        artifacts, preview["event"], owner_only=True
    )
    if not isinstance(preview_event, dict) or not isinstance(
        preview_event.get("source_events"), list
    ):
        raise VerificationError("receiver continuation event is malformed")
    source_head, terminal_receiver_monotonic = _verify_receiver_source_records(
        proof["source_records"],
        preview_event["source_events"],
        first_sequence=initial_receiver_sequence,
        previous_head=initial_receiver_head,
        expected_head=preview["previous_head"],
        earliest=previous_time,
        latest=_parse_time(
            preview.get("receiver_record", {}).get("recorded_at"),
            "receiver extension record time",
        ),
        previous_monotonic=initial_receiver_monotonic,
    )
    previous = source_head
    cursors = {name: dict(value) for name, value in initial_cursors.items()}
    continuity = dict(initial_continuity)
    source_walls = dict(initial_source_walls)
    challenge_seen = False
    first_sequence = initial_receiver_sequence + len(proof["source_records"])
    for sequence, extension in enumerate(proof["extensions"], first_sequence):
        item = strict_json.exact_object(
            extension,
            {"sequence", "previous_head", "head", "event", "receiver_record"},
            "receiver extension",
        )
        _ref, event = read_json_artifact(artifacts, item["event"], owner_only=True)
        event = strict_json.exact_object(
            event,
            {
                "schema",
                "attempt_id",
                "refresh_id",
                "refresh_counter",
                "segment_count",
                "segment_artifacts",
                "observed_at",
                "source_events",
                "sample",
                "consumer_identity",
                "consumer_nonce",
                "requested_transition",
                "authorization_grant_digest",
                "invalidating_event_count",
            },
            "receiver continuation event",
        )
        segment_refs = event["segment_artifacts"]
        if (
            not isinstance(segment_refs, list)
            or isinstance(event["segment_count"], bool)
            or event["segment_count"] != len(segment_refs)
            or not segment_refs
        ):
            raise VerificationError("receiver refresh segment manifest is invalid")
        segment_sources: list[dict[str, Any]] = []
        verified_segments: list[dict[str, Any]] = []
        previous_segment_time = initial_time
        for segment_index, segment_ref in enumerate(segment_refs, 1):
            _segment_ref, segment = read_json_artifact(
                artifacts, segment_ref, owner_only=True
            )
            try:
                strict_json.validate(
                    segment, _load_schema("receiver-refresh-segment", anchor)
                )
            except strict_json.StrictJSONError as exc:
                raise VerificationError(
                    "receiver refresh segment schema is invalid"
                ) from exc
            segment = strict_json.exact_object(
                segment,
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
            segment_time = _parse_time(
                segment["observed_at"], "receiver refresh segment time"
            )
            if (
                segment["schema"] != "phase-b.receiver-refresh-segment.v1"
                or segment["attempt_id"] != attempt_id
                or segment["refresh_id"] != event["refresh_id"]
                or segment["refresh_counter"] != event["refresh_counter"]
                or segment["segment_index"] != segment_index
                or segment_time <= previous_segment_time
                or segment["final"] != (segment_index == len(segment_refs))
                or segment["invalidating_event_count"] != 0
                or not isinstance(segment["source_events"], list)
                or len(segment["source_events"]) != len(STREAMS)
                or any(
                    segment[key] != event[key]
                    for key in (
                        "consumer_identity",
                        "consumer_nonce",
                        "requested_transition",
                        "authorization_grant_digest",
                    )
                )
                or (segment["sample"] is None) == segment["final"]
                or any(
                    abs(
                        (
                            _parse_time(
                                source.get("payload", {}).get("observed_at"),
                                "refresh segment source time",
                            )
                            - segment_time
                        ).total_seconds()
                    )
                    > CLOCK_TOLERANCE_SECONDS
                    for source in segment["source_events"]
                )
            ):
                raise VerificationError("receiver refresh segment chain is invalid")
            segment_sources.extend(segment["source_events"])
            verified_segments.append(segment)
            previous_segment_time = segment_time
        if (
            segment_sources != event["source_events"]
            or verified_segments[-1]["observed_at"] != event["observed_at"]
            or verified_segments[-1]["sample"] != event["sample"]
        ):
            raise VerificationError("receiver refresh aggregate is not segment-bound")
        record = strict_json.exact_object(
            item["receiver_record"],
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
            "receiver continuation record",
        )
        unsigned_record = dict(record)
        claimed_head = unsigned_record.pop("record_hash")
        if (
            item["sequence"] != sequence
            or item["previous_head"] != previous
            or item["head"] != claimed_head
            or record["schema"] != "phase-b.journal-record.v1"
            or record["sequence"] != sequence
            or record["previous_hash"] != previous
            or record["kind"] != "checkpoint"
            or record["action_id"] != f"extension-{sequence:020d}"
            or record["payload"] != {"event": item["event"]}
            or claimed_head != strict_json.digest(unsigned_record)
        ):
            raise VerificationError("receiver continuation has a gap/fork/replay")
        expected_head = claimed_head
        observed = _parse_time(event["observed_at"], "receiver extension time")
        if observed <= previous_time:
            raise VerificationError("receiver extension time does not strictly advance")
        previous_time = observed
        source_event_values = event["source_events"]
        if (
            event["schema"] != "phase-b.receiver-extension.v3"
            or event["attempt_id"] != attempt_id
            or isinstance(event["refresh_counter"], bool)
            or not isinstance(event["refresh_counter"], int)
            or event["refresh_counter"] != receipt["consumption_counter"]
            or not isinstance(event["refresh_id"], str)
            or not event["refresh_id"]
            or event["invalidating_event_count"] != 0
            or not isinstance(source_event_values, list)
            or not source_event_values
            or len(source_event_values) % len(STREAMS) != 0
        ):
            raise VerificationError(
                "receiver continuation contains loss or missing source coverage"
            )
        for ordinal, source_envelope in enumerate(source_event_values):
            stream = STREAMS[ordinal % len(STREAMS)]
            source = verify_envelope(
                source_envelope, anchor, verifier, f"phase-b-source-event.{stream}"
            )
            source = strict_json.exact_object(
                source, set(EVENT_FIELDS), "continuation source event"
            )
            cursor = cursors[stream]
            source_observed = _parse_time(
                source["observed_at"], "continuation source time"
            )
            if (
                source["schema"] != "phase-b.source-event.v1"
                or source["attempt_id"] != attempt_id
                or source["stream"] != stream
                or source["generation"] != cursor["generation"]
                or source["offset"] != cursor["offset"] + 1
                or source["previous_token"] != cursor["token"]
                or source["event_class"] != "continuity-checkpoints"
                or source_observed > observed
            ):
                raise VerificationError(
                    "continuation source cursor/signature is incomplete"
                )
            prior_continuity = continuity[stream]
            prior_source_wall = _parse_time(
                source_walls[stream], "prior continuation source time"
            )
            metadata = strict_json.exact_object(
                source["metadata"], {"checkpoints"}, "continuation metadata"
            )
            if (
                not isinstance(metadata["checkpoints"], list)
                or len(metadata["checkpoints"]) != 1
            ):
                raise VerificationError(
                    "continuation source event must contain exactly one checkpoint"
                )
            seen: set[str] = set()
            for checkpoint in metadata["checkpoints"]:
                checkpoint = strict_json.exact_object(
                    checkpoint,
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
                received = _parse_time(
                    checkpoint["received_at"], "continuation receiver time"
                )
                if (
                    isinstance(start, bool)
                    or isinstance(end, bool)
                    or not isinstance(start, (int, float))
                    or not isinstance(end, (int, float))
                    or abs(float(start) - continuity[stream]) > 1
                    or not 0 < float(end) - float(start) <= CONTINUITY_INTERVAL_SECONDS
                    or abs((source_observed - received).total_seconds())
                    > CLOCK_TOLERANCE_SECONDS
                    or any(
                        checkpoint[key] != 0 for key in ("lost", "backlog", "replay")
                    )
                    or isinstance(checkpoint["event_count"], bool)
                    or not isinstance(checkpoint["event_count"], int)
                ):
                    raise VerificationError("continuation source has a gap/loss/replay")
                _batch_ref, batch = read_json_artifact(
                    artifacts, checkpoint["batch"], owner_only=True
                )
                raw_types, failures = derive_raw_batch(
                    stream, batch, observation["identities"]
                )
                if (
                    batch["source_cursor"] != checkpoint["source_cursor"]
                    or len(batch["events"]) != checkpoint["event_count"]
                    or failures
                ):
                    raise VerificationError("continuation raw source batch is invalid")
                seen.update(raw_types)
                continuity[stream] = float(end)
            if not MANDATORY_RAW_TYPES[stream] <= seen:
                raise VerificationError("continuation raw source class is missing")
            wall_delta = (source_observed - prior_source_wall).total_seconds()
            monotonic_delta = continuity[stream] - prior_continuity
            if (
                wall_delta < 0
                or abs(wall_delta - monotonic_delta) > CLOCK_TOLERANCE_SECONDS
            ):
                raise VerificationError(
                    "continuation wall/monotonic coverage has a blind gap"
                )
            cursors[stream] = {
                "stream": stream,
                "generation": source["generation"],
                "offset": source["offset"],
                "token": strict_json.digest(source_envelope),
            }
            source_walls[stream] = source["observed_at"]
        snapshot = strict_json.exact_object(
            event["sample"], set(SAMPLE_FIELDS), "continuation sample"
        )
        if (
            any(
                snapshot[key] != 0
                for key in (
                    "legacy_axis_new_work_writers",
                    "legacy_axis_reprovisioners",
                    "effect_capable_descendants",
                    "canonical_writers",
                    "pending_local_effects",
                )
            )
            or snapshot["unknowns"] != []
            or snapshot["custody_remote"] != 9
            or snapshot["custody_total"] != 9
            or snapshot["stuck_watchdog_state"] != "healthy"
            or any(
                snapshot[key] != observation["identities"][key]
                for key in IDENTITY_FIELDS
            )
        ):
            raise VerificationError("continuation sample derives recurrence or drift")
        if {
            "consumer_identity": event["consumer_identity"],
            "consumer_nonce": event["consumer_nonce"],
            "requested_transition": event["requested_transition"],
            "authorization_grant_digest": event["authorization_grant_digest"],
        } != {
            "consumer_identity": receipt["consumer_identity"],
            "consumer_nonce": receipt["consumer_nonce"],
            "requested_transition": receipt["requested_transition"],
            "authorization_grant_digest": receipt["authorization_grant_digest"],
        }:
            raise VerificationError("receiver challenge does not bind consumption")
        challenge_seen = True
        previous = expected_head
    if not challenge_seen or previous != proof["current_head"] or previous_time is None:
        raise VerificationError("receiver continuation lacks the consuming challenge")
    if any(
        abs(
            (
                previous_time - _parse_time(value, "terminal continuation source time")
            ).total_seconds()
        )
        > CLOCK_TOLERANCE_SECONDS
        for value in source_walls.values()
    ):
        raise VerificationError(
            "receiver extension has a trailing uncovered source gap"
        )
    if (
        proof["terminal_cursors"] != [cursors[name] for name in STREAMS]
        or proof["terminal_continuity"] != continuity
        or proof["terminal_source_walls"] != source_walls
    ):
        raise VerificationError("continuation terminal source state is not bound")
    if strict_json.digest(proof) != receipt["continuation_digest"]:
        raise VerificationError("receipt does not bind the receiver continuation")
    return (
        previous,
        previous_time,
        terminal_receiver_monotonic,
        cursors,
        continuity,
        source_walls,
        first_sequence + len(proof["extensions"]),
    )


class Verifier:
    def __init__(
        self,
        anchor: TrustAnchor,
        signature_verifier: SignatureVerifier,
        consumption_authority: ConsumptionAuthority,
    ):
        self.anchor = anchor
        self.signature_verifier = signature_verifier
        self.consumption_authority = consumption_authority

    def verify(self, bundle: VerificationBundle) -> Qualification:
        try:
            return self._verify(bundle)
        except VerificationError:
            raise
        except (
            ArtifactError,
            CollectorError,
            JournalError,
            RegistryError,
            TrustError,
            strict_json.StrictJSONError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise VerificationError("artifact verification failed safely") from exc

    def _verify(self, bundle: VerificationBundle) -> Qualification:
        baseline = _signed(
            bundle.baseline, "baseline", self.anchor, self.signature_verifier
        )
        registries = _baseline(
            baseline, self.anchor, bundle.artifacts, self.signature_verifier
        )
        f0 = _signed(bundle.f0, "f0", self.anchor, self.signature_verifier)
        _verify_f0(
            f0,
            baseline,
            registries,
            bundle.artifacts,
            self.anchor,
            self.signature_verifier,
        )
        execution_records = _verify_execution_journal(
            bundle.execution_journal,
            f0,
            baseline,
            registries,
            bundle.artifacts,
            self.anchor,
            self.signature_verifier,
        )
        observation = _signed(
            bundle.observation, "observation", self.anchor, self.signature_verifier
        )
        _verify_observation(observation, baseline, f0, self.anchor)
        terminal_receiver_monotonic, source_walls = _verify_observation_chain(
            bundle.observation_journal,
            observation,
            baseline,
            f0,
            self.anchor,
            self.signature_verifier,
            bundle.artifacts,
        )
        reconstructions = tuple(
            _signed(item, "reconstruction", self.anchor, self.signature_verifier)
            for item in bundle.reconstructions
        )
        proofs = tuple(
            _verify_reconstruction(item, baseline, self.anchor, bundle.artifacts)
            for item in reconstructions
        )
        if (
            proofs[0][0] != proofs[1][0]
            or reconstructions[0]["home_id"] == reconstructions[1]["home_id"]
            or set(proofs[0][1]) & set(proofs[1][1])
            or proofs[0][2] == proofs[1][2]
            or proofs[0][3] == proofs[1][3]
        ):
            raise VerificationError(
                "two clean reconstructions are not independently rooted/executed/transcribed"
            )
        receipts = _receipt_chain(
            (*bundle.previous_receipts, bundle.receipt),
            self.anchor,
            self.signature_verifier,
            baseline["attempt_id"],
        )
        receipt = receipts[-1]
        if bundle.continuation_proof is None:
            raise VerificationError("receiver continuation proof is absent")
        continuation_proofs = (
            *bundle.previous_continuations,
            bundle.continuation_proof,
        )
        if len(continuation_proofs) != len(receipts):
            raise VerificationError("receipt continuation history is incomplete")
        receiver_head = observation["receiver_custody"]["head"]
        receiver_sequence = observation["receiver_custody"]["sequence"]
        cursors = {item["stream"]: dict(item) for item in observation["ending_cursors"]}
        continuity = {
            name: float(observation["coverage"][name]["end_monotonic"])
            for name in STREAMS
        }
        continued_at = _parse_time(
            observation["observed_through_at"], "observation end"
        )
        receiver_monotonic = terminal_receiver_monotonic
        common_expected = {
            "baseline_digest": strict_json.digest(baseline),
            "f0_digest": strict_json.digest(f0),
            "observation_digest": strict_json.digest(observation),
            "reconstruction_digests": [
                strict_json.digest(item) for item in reconstructions
            ],
            "execution_journal_head": execution_records[-1]["record_hash"],
            "observation_chain_head": observation["chain_head"],
            "observed_through_at": observation["observed_through_at"],
        }
        for chained_receipt, continuation in zip(
            receipts, continuation_proofs, strict=True
        ):
            (
                receiver_head,
                continued_at,
                receiver_monotonic,
                cursors,
                continuity,
                source_walls,
                receiver_sequence,
            ) = _verify_continuation(
                continuation,
                chained_receipt,
                baseline["attempt_id"],
                observation,
                receiver_head,
                receiver_sequence,
                cursors,
                continuity,
                source_walls,
                continued_at,
                receiver_monotonic,
                self.anchor,
                self.signature_verifier,
                bundle.artifacts,
            )
            expected = {**common_expected, "receiver_head": receiver_head}
            for key, value in expected.items():
                if chained_receipt[key] != value:
                    raise VerificationError(f"receipt does not bind actual {key}")
        if receipt["requested_transition"] != "PHASE_B_FENCING_QUALIFICATION":
            raise VerificationError("receipt requests an unauthorized transition")
        grant_ref, grant_bytes = read_artifact(
            bundle.artifacts, receipt["authorization_grant"], owner_only=True
        )
        if grant_ref.digest != receipt["authorization_grant_digest"]:
            raise VerificationError("receipt grant digest does not bind actual bytes")
        try:
            grant_envelope = strict_json.loads_canonical(grant_bytes)
            grant = verify_envelope(
                grant_envelope,
                self.anchor,
                self.signature_verifier,
                "phase-b-consumption-grant",
            )
        except (strict_json.StrictJSONError, TrustError) as exc:
            raise VerificationError(
                "consumption grant is not source-authorized"
            ) from exc
        grant = strict_json.exact_object(
            grant,
            {
                "schema",
                "attempt_id",
                "consumer_identity",
                "consumer_nonce",
                "requested_transition",
                "expires_at",
            },
            "consumption grant",
        )
        if grant != {
            "schema": "phase-b.consumption-grant.v1",
            "attempt_id": baseline["attempt_id"],
            "consumer_identity": receipt["consumer_identity"],
            "consumer_nonce": receipt["consumer_nonce"],
            "requested_transition": receipt["requested_transition"],
            "expires_at": grant["expires_at"],
        }:
            raise VerificationError("consumption grant identity/scope mismatch")
        observed = _parse_time(
            receipt["observed_through_at"], "receipt observation end"
        )
        issued = _parse_time(receipt["issued_at"], "receipt issue time")
        expires = _parse_time(grant["expires_at"], "consumption grant expiry")
        now = self.consumption_authority.trusted_now().astimezone(timezone.utc)
        if (
            not observed <= issued <= continued_at <= now <= expires
            or (now - continued_at).total_seconds() > MAX_RECEIPT_AGE_SECONDS
        ):
            raise VerificationError(
                "receipt freshness is not chained through trusted consumption"
            )
        if (
            self.consumption_authority.current_head(baseline["attempt_id"])
            != receiver_head
        ):
            raise VerificationError("receipt receiver-head challenge is stale")
        receipt_digest = strict_json.digest(receipt)
        if not self.consumption_authority.compare_and_set(
            baseline["attempt_id"],
            len(bundle.previous_receipts),
            receipt["previous_receipt_digest"],
            receipt["consumer_nonce"],
            receipt["consumer_identity"],
            receipt["requested_transition"],
            receipt["authorization_grant_digest"],
            receiver_head,
            receipt_digest,
            continued_at.isoformat().replace("+00:00", "Z"),
            grant["expires_at"],
        ):
            raise VerificationError(
                "receipt nonce/head/grant was already consumed or CAS raced"
            )
        return Qualification(
            baseline["attempt_id"],
            receipt["observed_through_at"],
            receipt_digest,
            receipt["consumption_counter"],
        )
