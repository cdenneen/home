from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PACKAGE = Path(__file__).resolve().parents[1]
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

from phase_b import strict_json
from phase_b.artifacts import bytes_digest
from phase_b.collector import OBSERVATION_SECONDS, STREAMS, Cursor
from phase_b.executor import (
    FENCED_UNITS,
    PRESERVED_UNITS,
    ExecutionError,
    PreservedIdentity,
    UnitExpectation,
    UnitState,
)
from phase_b.registry import FIXED_DELTAS, Delta, RegistryExpectation
from phase_b.trust import (
    REQUIRED_NAMESPACE_ROLES,
    REQUIRED_ROLES,
    REQUIRED_SCHEMA_NAMES,
    HMACSHA256Verifier,
    TrustAnchor,
    _parse_anchor,
)

ATTEMPT = "phase-b-fixture-001"
START = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
ROLE_KEYS = {
    role: f"{index + 1:02x}" * 32 for index, role in enumerate(sorted(REQUIRED_ROLES))
}
CANONICAL_SEMANTICS = {
    "generic_route_owner": "generic",
    "stuck_cron_declared": True,
    "plugin_set": ["slack"],
}


class MutableClock:
    def __init__(self, value: datetime = START):
        self.value = value
        self.monotonic_value = 0.0

    def __call__(self) -> datetime:
        return self.value

    def monotonic(self) -> float:
        return self.monotonic_value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)
        self.monotonic_value += seconds


class Fixture:
    def __init__(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.registry_dir = self.root / "registries"
        self.registry_dir.mkdir(mode=0o700)
        targets = {
            0: ("bb8d50dc3332", "a9c0b0e9bcca"),
            2: ("81776a5f93c5",),
            3: ("81776a5f93c5",),
        }
        self.paths: list[Path] = []
        self.documents: list[dict[str, Any]] = []
        for index in range(6):
            directory = self.registry_dir / f"root-{index}" / "cron"
            directory.mkdir(parents=True, mode=0o700)
            lock = directory / ".jobs.lock"
            lock.write_text("", encoding="utf-8")
            lock.chmod(0o600)
            jobs = [
                {
                    "id": target,
                    "enabled": True,
                    "schedule": {"kind": "interval", "seconds": 3600},
                    "state": "scheduled",
                }
                for target in targets.get(index, ())
            ]
            jobs.append(
                {
                    "id": f"preserved-{index}",
                    "enabled": True,
                    "schedule": {"kind": "interval", "seconds": 7200},
                    "state": "scheduled",
                }
            )
            document = {"jobs": jobs}
            path = directory / "jobs.json"
            path.write_bytes(strict_json.canonical(document) + b"\n")
            path.chmod(0o600)
            self.paths.append(path)
            self.documents.append(document)
        self.clock = MutableClock()
        self.artifacts: dict[str, bytes] = {}
        self.artifact_counter = 0

    def put_artifact(
        self,
        value: Any,
        *,
        media_type: str = "application/json",
        owner_only: bool = True,
        prefix: str = "evidence",
    ) -> dict[str, Any]:
        data = (
            strict_json.canonical(value)
            if media_type == "application/json"
            else bytes(value)
        )
        self.artifact_counter += 1
        artifact_id = f"{prefix}-{self.artifact_counter:06d}"
        self.artifacts[artifact_id] = data
        return {
            "id": artifact_id,
            "digest": bytes_digest(data),
            "media_type": media_type,
            "owner_only": owner_only,
        }

    def read(self, artifact_id: str) -> bytes:
        return self.artifacts[artifact_id]

    def close(self) -> None:
        self.temporary.cleanup()

    def expectations(self) -> tuple[RegistryExpectation, ...]:
        result = []
        for path, document in zip(self.paths, self.documents, strict=True):
            metadata = path.stat()
            result.append(
                RegistryExpectation(
                    str(path),
                    os.getuid(),
                    0o600,
                    metadata.st_dev,
                    metadata.st_ino,
                    document,
                )
            )
        return tuple(result)

    def units(self) -> tuple[UnitExpectation, ...]:
        return tuple(
            UnitExpectation(
                name,
                f"/nix/store/{'a' * 32}-units/{name}",
                "sha256:" + f"{index:064x}",
                "loaded",
                "active",
                "enabled",
                (),
            )
            for index, name in enumerate(FENCED_UNITS, 1)
        )

    def preserved(self) -> tuple[PreservedIdentity, ...]:
        return tuple(
            PreservedIdentity(name, True, f"start-{index}")
            for index, name in enumerate(PRESERVED_UNITS)
        )

    def effect_plan(self) -> list[dict[str, str]]:
        result = []
        for item in self.units():
            result.extend(
                {"action": f"b1:{item.name}:{operation}", "operation": operation}
                for operation in ("stop", "disable", "mask")
            )
        result.extend(
            {
                "action": f"b2:{item.registry_index}:{item.job_id}:pause",
                "operation": "hermes-pause",
            }
            for item in FIXED_DELTAS
        )
        return result

    def rollback_plan(self) -> list[dict[str, str]]:
        inverse = {
            "stop": "start",
            "disable": "enable",
            "mask": "unmask",
            "hermes-pause": "restore-preimage",
        }
        return [
            {
                "action": "rollback:" + item["action"],
                "operation": inverse[item["operation"]],
            }
            for item in reversed(self.effect_plan())
        ]

    def authority(self) -> dict[str, Any]:
        return {
            "generic": {
                "route_identity": "generic-route-001",
                "service_identity": "generic-service-001",
                "session_identity": "generic-session-001",
                "profile_identity": "generic-profile-001",
            },
            "alpha0": {
                "route_identity": "alpha0-route-001",
                "service_identity": "alpha0-service-001",
                "session_identity": "alpha0-session-001",
                "profile_identity": "alpha0-profile-001",
            },
            "dedicated_axis_route": "ABSENT",
        }

    def anchor(self) -> TrustAnchor:
        schema_dir = PACKAGE / "phase_b" / "schemas"
        schema_digests = {
            name: strict_json.digest(
                strict_json.loads((schema_dir / f"{name}.schema.json").read_bytes())
            )
            for name in REQUIRED_SCHEMA_NAMES
        }
        executables = {}
        for index, name in enumerate(
            (
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
            )
        ):
            closure = f"/nix/store/{chr(97 + index) * 32}-phase-b-{name}"
            executables[name] = {
                "path": f"{closure}/bin/{name}",
                "closure": closure,
                "digest": "sha256:" + f"{index + 1:064x}",
            }
        return _parse_anchor(
            {
                "schema": "phase-b.trust.v2",
                "anchor_generation": 1,
                "signers": {
                    role: {
                        "id": f"fixture-{role}",
                        "algorithm": "hmac-sha256-test",
                        "public_key": ROLE_KEYS[role],
                    }
                    for role in REQUIRED_ROLES
                },
                "namespace_roles": REQUIRED_NAMESPACE_ROLES,
                "executables": executables,
                "source": {
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                    "user": "fixture-user",
                    "home": str(self.root / "home"),
                    "machine_id": "machine-001",
                    "host_identity": "host-001",
                    "boot_id": "boot-001",
                    "user_manager_id": "manager-001",
                    "home_generation": "home-001",
                    "booted_closure": "sha256:" + "c" * 64,
                    "user_manager_machine": ".host",
                },
                "registry_paths": [str(path) for path in self.paths],
                "collector_identity": "offhost-collector-001",
                "authority_identities": self.authority(),
                "effect_plan_digest": strict_json.digest(self.effect_plan()),
                "rollback_plan_digest": strict_json.digest(self.rollback_plan()),
                "canonical_vectors_digest": strict_json.digest(CANONICAL_SEMANTICS),
                "process_inventory_digest": strict_json.digest(
                    self.process_inventory()
                ),
                "listener_inventory_digest": strict_json.digest(
                    self.listener_inventory()
                ),
                "runbook_digest": "sha256:" + "b" * 64,
                "schema_digests": schema_digests,
            }
        )

    @staticmethod
    def process_inventory() -> list[dict[str, str]]:
        return [
            {"identity": "generic-gateway-process", "classification": "preserved"},
            {"identity": "axis-writer-process", "classification": "absent"},
        ]

    @staticmethod
    def listener_inventory() -> list[dict[str, str]]:
        return [
            {"identity": "generic-gateway-listener", "classification": "preserved"},
            {"identity": "dedicated-axis-listener", "classification": "absent"},
        ]

    def baseline(self, anchor: TrustAnchor | None = None) -> dict[str, Any]:
        anchor = anchor or self.anchor()
        expectations = self.expectations()
        entries = []
        target_set = {(item.registry_index, item.job_id) for item in FIXED_DELTAS}
        for index, document in enumerate(self.documents):
            entries.extend(
                {
                    "kind": "job",
                    "physical_registry": index,
                    "identity": job["id"],
                    "classification": "axis-target"
                    if (index, job["id"]) in target_set
                    else "preserved",
                }
                for job in document["jobs"]
            )
        entries.extend(
            {
                "kind": "unit",
                "physical_registry": None,
                "identity": name,
                "classification": "reprovisioner",
            }
            for name in FENCED_UNITS
        )
        backup_kinds = (
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
        automatic_reboot_evidence = self.put_artifact(
            signed(
                {
                    "schema": "phase-b.automatic-reboot-evidence.v1",
                    "attempt_id": ATTEMPT,
                    "captured_at": START.isoformat().replace("+00:00", "Z"),
                    "boot_id": "boot-001",
                    "observation_seconds": OBSERVATION_SECONDS,
                    "systemd_cursor": "systemd-baseline-cursor-001",
                    "scheduled_reboots": [],
                },
                "phase-b-source-event.systemd",
            ),
            prefix="automatic-reboot",
        )
        backups = []
        for index, kind in enumerate(backup_kinds):
            source_bytes = (
                strict_json.canonical(self.documents[index])
                if index < 6
                else f"actual source bytes for {kind}\n".encode()
            )
            source = self.put_artifact(
                source_bytes,
                media_type="application/octet-stream",
                prefix="backup-source",
            )
            backup = self.put_artifact(
                f"encrypted owner-only backup {kind}".encode(),
                media_type="application/octet-stream",
                prefix="backup",
            )
            restored_output = self.put_artifact(
                source_bytes,
                media_type="application/octet-stream",
                prefix="restored-output",
            )
            integrity = {
                "algorithm": "sha256",
                "source_digest": source["digest"],
                "restored_output_digest": restored_output["digest"],
            }
            restore_test_payload = {
                "schema": "phase-b.restore-test.v2",
                "attempt_id": ATTEMPT,
                "kind": kind,
                "backup": backup,
                "backup_digest": backup["digest"],
                "source": source,
                "source_digest": source["digest"],
                "restored_output": restored_output,
                "restored_output_digest": restored_output["digest"],
                "command_exit_code": 0,
                "network_attempts": 0,
                "integrity": integrity,
            }
            restore_test = self.put_artifact(
                restore_test_payload,
                prefix="restore-test",
            )
            restore_payload = {
                "schema": "phase-b.restore-receipt.v2",
                "attempt_id": ATTEMPT,
                "kind": kind,
                "backup": backup,
                "backup_digest": backup["digest"],
                "source": source,
                "source_digest": source["digest"],
                "restored_output": restored_output,
                "restored_output_digest": restored_output["digest"],
                "restore_test": restore_test,
                "restore_test_digest": restore_test["digest"],
                "tested_at": START.isoformat().replace("+00:00", "Z"),
            }
            restore = self.put_artifact(
                signed(restore_payload, "phase-b-backup-restore"),
                prefix="restore",
            )
            backups.append(
                {
                    "kind": kind,
                    "source": source,
                    "backup": backup,
                    "restore_receipt": restore,
                }
            )
        return {
            "schema": "phase-b.baseline.v2",
            "attempt_id": ATTEMPT,
            "captured_at": START.isoformat().replace("+00:00", "Z"),
            "trust_anchor_digest": anchor.anchor_digest,
            "anchor_generation": anchor.anchor_generation,
            "runbook_digest": anchor.runbook_digest,
            "source_revision": "75a8055cd0854e925c50578f22f1ec595cac5bf9",
            "source_identity": {
                "host_identity": "host-001",
                "machine_id": "machine-001",
                "boot_id": "boot-001",
                "user_manager_id": "manager-001",
                "home_generation": "home-001",
                "source_uid": os.getuid(),
                "source_gid": os.getgid(),
                "source_user": "fixture-user",
                "source_home": str(self.root / "home"),
                "wall_at": START.isoformat().replace("+00:00", "Z"),
                "monotonic": 0.0,
                "booted_closure": "sha256:" + "c" * 64,
                "anchor_generation": 1,
            },
            "automatic_reboot_evidence": automatic_reboot_evidence,
            "registries": [
                {
                    "path": item.path,
                    "owner_uid": item.owner_uid,
                    "mode": item.mode,
                    "device": item.device,
                    "inode": item.inode,
                    "document": item.document,
                }
                for item in expectations
            ],
            "backup_artifacts": backups,
            "units": [
                {
                    "name": item.name,
                    "fragment_path": item.fragment_path,
                    "fragment_digest": item.fragment_digest,
                    "load_state": item.load_state,
                    "active_state": item.active_state,
                    "unit_file_state": item.unit_file_state,
                    "trigger_edges": list(item.trigger_edges),
                }
                for item in self.units()
            ],
            "preserved_units": [
                {
                    "name": item.name,
                    "healthy_state": "healthy",
                    "start_identity": item.start_identity,
                }
                for item in self.preserved()
            ],
            "scheduler_inventory": {
                "entries": entries,
                "classification_digest": strict_json.digest(entries),
            },
            "expected_process_inventory": self.process_inventory(),
            "expected_listener_inventory": self.listener_inventory(),
            "effect_plan": self.effect_plan(),
            "rollback_plan": self.rollback_plan(),
            "starting_cursors": [
                Cursor(name, 0, 0, f"cursor-{name}-0").as_dict() for name in STREAMS
            ],
            "authority_identities": self.authority(),
            "collector_identity": "offhost-collector-001",
            "custody": {
                "remote": 9,
                "total": 9,
                "pending": 0,
                "inflight": 0,
                "local_only": 0,
                "frontier_digest": "sha256:" + "e" * 64,
            },
        }


def custody_evidence(method: str) -> dict[str, Any]:
    return {
        "schema": "phase-b.custody-read.v1",
        "method": method,
        "pages": [
            {
                "surface": "lineages",
                "page": 1,
                "last": True,
                "records": [
                    {"id": f"lineage-{index}", "custody": "REMOTE", "consequential": True}
                    for index in range(9)
                ],
            },
            {
                "surface": "custody",
                "page": 1,
                "last": True,
                "records": [
                    {"remote": 9, "total": 9, "pending": 0, "inflight": 0, "local_only": 0}
                ],
            },
            {"surface": "pending-effects", "page": 1, "last": True, "records": []},
            {
                "surface": "residue",
                "page": 1,
                "last": True,
                "records": [
                    {"identity": "checkout-derived", "classification": "STALE_DERIVED"}
                ],
            },
        ],
        "deletions": [],
    }


def live_capture(
    baseline: dict[str, Any],
    request: dict[str, Any],
    source: str,
    evidence: dict[str, Any],
    observed_monotonic: float,
) -> dict[str, Any]:
    observed_at = (
        START + timedelta(seconds=observed_monotonic)
    ).isoformat().replace("+00:00", "Z")
    payload = {
        "schema": "phase-b.f0-live-evidence.v1",
        "attempt_id": request["attempt_id"],
        "baseline_digest": request["baseline_digest"],
        "capture_id": request["capture_id"],
        "phase": request["phase"],
        "journal_head": request["journal_head"],
        "source": source,
        "boot_id": baseline["source_identity"]["boot_id"],
        "observed_at": observed_at,
        "observed_monotonic": observed_monotonic,
        "window": {
            "start_monotonic": max(0.0, observed_monotonic - 1.0),
            "end_monotonic": observed_monotonic,
            "state_digest": strict_json.digest(evidence),
            "invalidating_event_count": 0,
        },
        "evidence": evidence,
    }
    return signed(payload, f"phase-b-source-event.{source}")


def signed(payload: dict[str, Any], namespace: str) -> dict[str, Any]:
    role = REQUIRED_NAMESPACE_ROLES[namespace]
    unsigned = {
        "schema": "phase-b.signed-envelope.v1",
        "namespace": namespace,
        "signer_id": f"fixture-{role}",
        "payload": payload,
    }
    return {
        **unsigned,
        "signature": HMACSHA256Verifier.sign(
            ROLE_KEYS[role], strict_json.canonical(unsigned)
        ),
    }


class FakeF0EvidenceSource:
    def __init__(self, fixture: Fixture, baseline: dict[str, Any]):
        self.fixture = fixture
        self.baseline = baseline
        self.calls: list[tuple[str, str]] = []
        self.mutate: Any = None
        self.advance_before_final = 0.0

    def capture_custody(self, request: Any, method: str) -> dict[str, Any]:
        self.calls.append((request.phase, "custody"))
        envelope = live_capture(
            self.baseline,
            request.as_dict(),
            "custody",
            custody_evidence(method),
            self.fixture.clock.monotonic(),
        )
        return self.mutate(envelope, "custody") if self.mutate else envelope

    def capture_final(self, request: Any) -> dict[str, dict[str, Any]]:
        self.fixture.clock.advance(self.advance_before_final)
        mono = self.fixture.clock.monotonic()
        registry_digests = [
            strict_json.digest(strict_json.loads(path.read_bytes()))
            for path in self.fixture.paths
        ]
        source_identity = {
            key: self.baseline["source_identity"][key]
            for key in (
                "host_identity",
                "machine_id",
                "boot_id",
                "user_manager_id",
                "home_generation",
                "booted_closure",
            )
        }
        evidence = {
            "audit": {
                "schema": "phase-b.audit-evidence.v1",
                "processes": [
                    {
                        "identity": item["identity"],
                        "count": 1 if item["classification"] == "preserved" else 0,
                    }
                    for item in self.baseline["expected_process_inventory"]
                ],
                "listeners": [
                    {
                        "identity": item["identity"],
                        "count": 1 if item["classification"] == "preserved" else 0,
                    }
                    for item in self.baseline["expected_listener_inventory"]
                ],
                "effect_capable_descendants": [],
                "writers": 0,
                "reprovisioners": 0,
                "canonical_writers": 0,
            },
            "registry": {
                "schema": "phase-b.registry-evidence.v1",
                "registries": [
                    {"index": index, "path": str(path), "digest": registry_digests[index]}
                    for index, path in enumerate(self.fixture.paths)
                ],
            },
            "database": {
                "schema": "phase-b.database-evidence.v1",
                "pending": 0,
                "inflight": 0,
                "local_only": 0,
            },
            "provider-route": {
                "schema": "phase-b.route-evidence.v1",
                "authority_identities": self.fixture.authority(),
                "alpha0_authority": "UNCHANGED_NOT_DRAINED",
            },
            "identity": {
                "schema": "phase-b.identity-evidence.v1",
                "source_identity": source_identity,
                "preserved_start_identities": {
                    item.name: item.start_identity for item in self.fixture.preserved()
                },
                "stuck_watchdog_healthy": True,
            },
            "time": {
                "schema": "phase-b.time-evidence.v1",
                "wall": self.fixture.clock().isoformat().replace("+00:00", "Z"),
                "monotonic": mono,
            },
        }
        result = {
            source: live_capture(
                self.baseline, request.as_dict(), source, value, mono
            )
            for source, value in evidence.items()
        }
        for source in result:
            self.calls.append((request.phase, source))
        return (
            {source: self.mutate(envelope, source) for source, envelope in result.items()}
            if self.mutate
            else result
        )


class FakeBackend:
    def __init__(
        self,
        fixture: Fixture,
        *,
        fail_effect: int | None = None,
        fail_after_effect: bool = True,
    ):
        self.fixture = fixture
        self.expectations = {item.name: item for item in fixture.units()}
        self.states = {
            item.name: UnitState(
                item.name,
                item.fragment_path,
                item.fragment_digest,
                item.load_state,
                item.active_state,
                item.unit_file_state,
                False,
                item.trigger_edges,
            )
            for item in fixture.units()
        }
        self.preserved = {item.name: item for item in fixture.preserved()}
        self.processes: tuple[str, ...] = ()
        self.effect_count = 0
        self.fail_effect = fail_effect
        self.fail_after_effect = fail_after_effect

    def _effect(self, operation: Any) -> None:
        self.effect_count += 1
        if self.fail_effect == self.effect_count and not self.fail_after_effect:
            raise ExecutionError("injected before effect")
        operation()
        if self.fail_effect == self.effect_count and self.fail_after_effect:
            raise ExecutionError("injected after effect")

    def inspect_unit(self, name: str) -> UnitState:
        return self.states[name]

    def unit_operation(self, name: str, operation: str) -> None:
        def apply() -> None:
            state = self.states[name]
            if operation == "stop":
                state = replace(state, active_state="inactive")
            elif operation == "disable":
                state = replace(state, unit_file_state="disabled")
            elif operation == "mask":
                state = replace(state, runtime_masked=True)
            elif operation == "unmask":
                state = replace(state, runtime_masked=False)
            elif operation == "enable":
                state = replace(
                    state, unit_file_state=self.expectations[name].unit_file_state
                )
            elif operation == "start":
                state = replace(
                    state, active_state=self.expectations[name].active_state
                )
            else:
                raise ExecutionError("unknown fake unit operation")
            self.states[name] = state

        self._effect(apply)

    def effect_capable_processes(self) -> tuple[str, ...]:
        return self.processes

    def preserved_identity(self, name: str) -> PreservedIdentity:
        return self.preserved[name]

    def _replace_job(self, delta: Delta) -> None:
        path = self.fixture.paths[delta.registry_index]
        document = strict_json.loads(path.read_bytes())
        for job in document["jobs"]:
            if job["id"] == delta.job_id:
                job.update(
                    {
                        "enabled": False,
                        "state": "paused",
                        "paused_at": "2026-08-20T00:00:00+00:00",
                        "paused_reason": None,
                    }
                )
        document["updated_at"] = "2026-08-20T00:00:00+00:00"
        temp = path.with_name(f".jobs_fixture_{self.effect_count}.tmp")
        temp.write_bytes(strict_json.canonical(document) + b"\n")
        temp.chmod(0o600)
        os.replace(temp, path)

    def pause_job(self, delta: Delta) -> None:
        self._effect(lambda: self._replace_job(delta))

    def restore_job_preimage(
        self, delta: Delta, preimage: dict[str, Any], postimage: dict[str, Any]
    ) -> None:
        # Fixture restore models the separately trusted exact journaled-preimage protocol.
        def apply() -> None:
            path = self.fixture.paths[delta.registry_index]
            current = strict_json.loads(path.read_bytes())
            if strict_json.digest(current) != postimage["digest"]:
                raise RuntimeError("fixture postimage mismatch")
            document = preimage["document"]
            temp = path.with_name(f".jobs_restore_{self.effect_count}.tmp")
            temp.write_bytes(strict_json.canonical(document) + b"\n")
            temp.chmod(0o600)
            os.replace(temp, path)

        self._effect(apply)

    def job_is_paused(self, delta: Delta) -> bool:
        document = strict_json.loads(
            self.fixture.paths[delta.registry_index].read_bytes()
        )
        return next(
            job["enabled"] is False
            for job in document["jobs"]
            if job["id"] == delta.job_id
        )
