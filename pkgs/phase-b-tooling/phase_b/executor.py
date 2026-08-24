"""Deterministic B0-B3 source executor with atomic-effect recovery."""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from . import strict_json
from .artifacts import ArtifactError, ArtifactRef
from .journal import Journal, JournalError
from .registry import FIXED_DELTAS, Delta, RegistrySet
from .strict_json import digest
from .trust import SHA256, ExecutableBinding, require_safe_attempt_id

FENCED_UNITS = (
    "hermes-supervisor-cron.service",
    "hermes-watchdog-cron.service",
    "hermes-watchdog-cutover.service",
    "axis-development-watchdog-backup.timer",
    "axis-development-watchdog-backup.service",
    "axis-development-watchdog-monitor.service",
    "hermes-axis-control-scheduler-watchdog.timer",
    "hermes-axis-control-scheduler-watchdog.service",
)
PRESERVED_UNITS = ("hermes-gateway.service", "hermes-stuck-cron-watchdog.timer")
F0_EVIDENCE_FIELDS = frozenset(
    {"audit", "registry", "database", "provider-route", "custody", "identity", "time"}
)
CUSTODY_FIELDS = frozenset({"method", "artifact"})
UNIT_OPERATIONS = ("stop", "disable", "mask")
ROLLBACK_OPERATIONS = {"mask": "unmask", "disable": "enable", "stop": "start"}


class ExecutionError(RuntimeError):
    pass


class Stage(Enum):
    CREATED = "created"
    BASELINE_VERIFIED = "baseline-verified"
    PREFLIGHT_VERIFIED = "preflight-verified"
    REPROVISIONERS_FENCED = "reprovisioners-fenced"
    WRITERS_PAUSED = "writers-paused"
    CUSTODY_CONVERGING = "custody-converging"
    F0_ELIGIBLE = "f0-eligible"
    F0_ESTABLISHED = "f0-established"
    OBSERVATION_EXTERNAL = "observation-external"
    INVALID = "invalid"
    ROLLED_BACK = "rolled-back"


@dataclass(frozen=True)
class UnitExpectation:
    name: str
    fragment_path: str
    fragment_digest: str
    load_state: str
    active_state: str
    unit_file_state: str
    trigger_edges: tuple[str, ...]


@dataclass(frozen=True)
class UnitState:
    name: str
    source_fragment_path: str
    source_fragment_digest: str
    source_load_state: str
    active_state: str
    unit_file_state: str
    runtime_masked: bool
    trigger_edges: tuple[str, ...]


@dataclass(frozen=True)
class PreservedIdentity:
    name: str
    healthy: bool
    start_identity: str


class ExecutionBackend(Protocol):
    def inspect_unit(self, name: str) -> UnitState: ...

    def unit_operation(self, name: str, operation: str) -> None: ...

    def effect_capable_processes(self) -> tuple[str, ...]: ...

    def preserved_identity(self, name: str) -> PreservedIdentity: ...

    def pause_job(self, delta: Delta) -> None: ...

    def restore_job_preimage(
        self, delta: Delta, preimage: dict[str, Any], postimage: dict[str, Any]
    ) -> None: ...

    def job_is_paused(self, delta: Delta) -> bool: ...


class BoundCommandBackend:
    """Fixed executables, fixed systemd targets, and FD-pinned Hermes mutations."""

    def __init__(
        self,
        systemctl: ExecutableBinding,
        hermes: ExecutableBinding,
        mutation_adapter: ExecutableBinding,
        privilege_dropper: ExecutableBinding,
        inspector: ExecutionBackend,
        registry: RegistrySet,
        *,
        source_uid: int,
        source_gid: int,
        source_user: str,
        source_home: str,
        user_manager_machine: str,
    ):
        self.systemctl = systemctl
        self.hermes = hermes
        self.mutation_adapter = mutation_adapter
        self.privilege_dropper = privilege_dropper
        self.inspector = inspector
        self.registry = registry
        self.source_uid = source_uid
        self.source_gid = source_gid
        self.source_user = source_user
        self.source_home = source_home
        self.user_manager_machine = user_manager_machine

    def _systemctl(self, operation: str, name: str) -> None:
        if name not in FENCED_UNITS or operation not in {
            "stop",
            "disable",
            "mask",
            "unmask",
            "enable",
            "start",
        }:
            raise ExecutionError("systemd operation is outside the fixed policy")
        arguments = [
            str(self.systemctl.path),
            "--user",
            f"--machine={self.source_user}@{self.user_manager_machine}",
            operation,
        ]
        if operation in {"mask", "unmask"}:
            arguments.append("--runtime")
        arguments.append(name)
        result = subprocess.run(
            arguments,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={"PATH": "", "LANG": "C", "LC_ALL": "C"},
        )
        if result.returncode:
            raise ExecutionError("fixed source-user systemd operation failed")

    def inspect_unit(self, name: str) -> UnitState:
        if name not in FENCED_UNITS:
            raise ExecutionError("unit is outside the fixed set")
        return self.inspector.inspect_unit(name)

    def unit_operation(self, name: str, operation: str) -> None:
        self._systemctl(operation, name)

    def effect_capable_processes(self) -> tuple[str, ...]:
        return self.inspector.effect_capable_processes()

    def preserved_identity(self, name: str) -> PreservedIdentity:
        if name not in PRESERVED_UNITS:
            raise ExecutionError("unit is outside the preserved set")
        return self.inspector.preserved_identity(name)

    def _run_hermes_adapter(
        self, delta: Delta, operation: str, payload: bytes | None = None
    ) -> None:
        if delta not in FIXED_DELTAS or operation not in {"pause", "restore-preimage"}:
            raise ExecutionError("Hermes mutation is outside the fixed policy")
        home, profile, inherited_fds = self.registry.hermes_invocation(delta)
        arguments = [
            str(self.privilege_dropper.path),
            "--reuid",
            str(self.source_uid),
            "--regid",
            str(self.source_gid),
            "--clear-groups",
            "--",
            str(self.mutation_adapter.path),
            operation,
            "--hermes",
            str(self.hermes.path),
            "--lock-fd",
            str(inherited_fds[-1]),
            "--job-id",
            delta.job_id,
        ]
        if profile is not None:
            arguments.extend(("--profile", profile))
        environment = {
            "PATH": "",
            "LANG": "C",
            "LC_ALL": "C",
            "HOME": self.source_home,
            "USER": self.source_user,
            "LOGNAME": self.source_user,
            "HERMES_HOME": home,
            "PHASE_B_HERMES_LOCK_FD": str(inherited_fds[-1]),
        }
        result = subprocess.run(
            arguments,
            check=False,
            input=payload,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=environment,
            pass_fds=inherited_fds,
            close_fds=True,
        )
        if result.returncode:
            raise ExecutionError(f"fixed internal-locking Hermes {operation} failed")

    def pause_job(self, delta: Delta) -> None:
        self._run_hermes_adapter(delta, "pause")

    def restore_job_preimage(
        self, delta: Delta, preimage: dict[str, Any], postimage: dict[str, Any]
    ) -> None:
        current = self.registry.evidence()[delta.registry_index]
        matches = [
            job for job in current["document"]["jobs"] if job.get("id") == delta.job_id
        ]
        if (
            len(matches) != 1
            or matches[0].get("enabled") is not False
            or current.get("digest") != postimage.get("digest")
            or current.get("document") != postimage.get("document")
            or preimage.get("path") != current.get("path")
            or not isinstance(preimage.get("document"), dict)
        ):
            raise ExecutionError("registry is not at the exact journaled postimage")
        payload = strict_json.canonical(
            {
                "schema": "phase-b.hermes-restore.v1",
                "registry_index": delta.registry_index,
                "job_id": delta.job_id,
                "expected_postimage_digest": postimage["digest"],
                "exact_preimage": preimage["document"],
            }
        )
        self._run_hermes_adapter(delta, "restore-preimage", payload)

    def job_is_paused(self, delta: Delta) -> bool:
        return self.inspector.job_is_paused(delta)


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class Executor:
    def __init__(
        self,
        attempt_id: str,
        registry: RegistrySet,
        journal: Journal,
        backend: ExecutionBackend,
        units: tuple[UnitExpectation, ...],
        preserved: tuple[PreservedIdentity, ...],
        *,
        monotonic: Any = time.monotonic,
        effect_plan_digest: str | None = None,
        rollback_plan_digest: str | None = None,
    ):
        require_safe_attempt_id(attempt_id)
        if tuple(item.name for item in units) != FENCED_UNITS:
            raise ExecutionError("baseline must bind the exact ordered B1 unit set")
        if any(
            item.load_state != "loaded"
            or item.active_state == "failed"
            or item.unit_file_state in {"masked", "masked-runtime", "not-found"}
            for item in units
        ):
            raise ExecutionError(
                "baseline contains failed, missing, or pre-masked units"
            )
        if tuple(item.name for item in preserved) != PRESERVED_UNITS:
            raise ExecutionError("baseline must bind both exact preserved units")
        self.attempt_id = attempt_id
        self.registry = registry
        self.journal = journal
        self.backend = backend
        self.units = units
        self.preserved = preserved
        self.monotonic = monotonic
        self.stage = Stage.CREATED
        self.completed_actions: list[str] = []
        self.completed_deltas: list[Delta] = []
        self.custody_reads: list[dict[str, Any]] = []
        self.b2_completed_monotonic: float | None = None
        self.capture_id: str | None = None
        self.capture_journal_head: str | None = None
        plan = self.effect_plan()
        rollback = self.rollback_plan()
        if effect_plan_digest is not None and effect_plan_digest != digest(plan):
            raise ExecutionError("anchored effect plan digest mismatch")
        if rollback_plan_digest is not None and rollback_plan_digest != digest(
            rollback
        ):
            raise ExecutionError("anchored rollback plan digest mismatch")

    def effect_plan(self) -> tuple[dict[str, str], ...]:
        result: list[dict[str, str]] = []
        for item in self.units:
            if item.active_state == "active":
                result.append({"action": f"b1:{item.name}:stop", "operation": "stop"})
            if item.unit_file_state == "enabled":
                result.append(
                    {"action": f"b1:{item.name}:disable", "operation": "disable"}
                )
            result.append({"action": f"b1:{item.name}:mask", "operation": "mask"})
        result.extend(
            {
                "action": f"b2:{item.registry_index}:{item.job_id}:pause",
                "operation": "hermes-pause",
            }
            for item in FIXED_DELTAS
        )
        return tuple(result)

    def rollback_plan(self) -> tuple[dict[str, str], ...]:
        result: list[dict[str, str]] = []
        for item in reversed(self.effect_plan()):
            action, operation = item["action"], item["operation"]
            inverse = (
                "restore-preimage"
                if operation == "hermes-pause"
                else ROLLBACK_OPERATIONS[operation]
            )
            result.append({"action": f"rollback:{action}", "operation": inverse})
        return tuple(result)

    @property
    def completed_units(self) -> list[str]:
        return [
            name for name in FENCED_UNITS if f"b1:{name}:mask" in self.completed_actions
        ]

    @staticmethod
    def _same_preflight(actual: UnitState, expected: UnitExpectation) -> bool:
        return actual == UnitState(
            expected.name,
            expected.fragment_path,
            expected.fragment_digest,
            expected.load_state,
            expected.active_state,
            expected.unit_file_state,
            False,
            expected.trigger_edges,
        )

    @staticmethod
    def _state_payload(actual: UnitState) -> dict[str, Any]:
        return {
            "name": actual.name,
            "source_fragment_path": actual.source_fragment_path,
            "source_fragment_digest": actual.source_fragment_digest,
            "source_load_state": actual.source_load_state,
            "active_state": actual.active_state,
            "unit_file_state": actual.unit_file_state,
            "runtime_masked": actual.runtime_masked,
            "trigger_edges": list(actual.trigger_edges),
        }

    @staticmethod
    def _operation_achieved(
        actual: UnitState, expected: UnitExpectation, operation: str
    ) -> bool:
        stable = (
            actual.name == expected.name
            and actual.source_fragment_path == expected.fragment_path
            and actual.source_fragment_digest == expected.fragment_digest
            and actual.source_load_state == expected.load_state
            and actual.trigger_edges == expected.trigger_edges
        )
        if not stable:
            return False
        if operation == "stop":
            return actual.active_state == "inactive"
        expected_file_state = (
            "disabled"
            if expected.unit_file_state == "enabled"
            else expected.unit_file_state
        )
        if operation == "disable":
            return actual.unit_file_state == expected_file_state
        if operation == "mask":
            return (
                actual.runtime_masked
                and actual.unit_file_state == expected_file_state
                and actual.unit_file_state not in {"masked", "masked-runtime"}
            )
        if operation == "unmask":
            return not actual.runtime_masked
        if operation == "enable":
            return actual.unit_file_state == expected.unit_file_state
        if operation == "start":
            return actual.active_state == expected.active_state
        return False

    @classmethod
    def _fenced(cls, actual: UnitState, expected: UnitExpectation) -> bool:
        return (
            cls._operation_achieved(actual, expected, "stop")
            and cls._operation_achieved(actual, expected, "disable")
            and cls._operation_achieved(actual, expected, "mask")
        )

    def preflight(self) -> None:
        if self.stage is not Stage.CREATED:
            raise ExecutionError("preflight is only valid from a created attempt")
        baseline_digests = self.registry.revalidate()
        self.journal.append(
            "checkpoint",
            "baseline-verified",
            {"attempt_id": self.attempt_id, "registry_digests": list(baseline_digests)},
        )
        self.stage = Stage.BASELINE_VERIFIED
        observed = tuple(self.backend.inspect_unit(name) for name in FENCED_UNITS)
        if any(
            not self._same_preflight(actual, expected)
            for actual, expected in zip(observed, self.units, strict=True)
        ):
            raise ExecutionError("B1 unit preflight mismatch")
        processes = self.backend.effect_capable_processes()
        if processes:
            raise ExecutionError("unowned effect-capable process exists")
        actual_preserved = tuple(
            self.backend.preserved_identity(name) for name in PRESERVED_UNITS
        )
        if actual_preserved != self.preserved or any(
            not item.healthy for item in actual_preserved
        ):
            raise ExecutionError("preserved generic continuity preflight mismatch")
        self.journal.append(
            "checkpoint",
            "preflight",
            {
                "attempt_id": self.attempt_id,
                "registry_digests": list(self.registry.revalidate()),
                "unit_state_digest": digest(
                    [self._state_payload(item) for item in observed]
                ),
                "effect_capable_processes": [],
                "preserved_identities": [
                    {
                        "name": item.name,
                        "health_state": "healthy",
                        "start_identity": item.start_identity,
                    }
                    for item in actual_preserved
                ],
                "effect_plan_digest": digest(self.effect_plan()),
                "rollback_plan_digest": digest(self.rollback_plan()),
            },
        )
        self.stage = Stage.PREFLIGHT_VERIFIED

    def _unit_steps(self) -> tuple[tuple[UnitExpectation, str], ...]:
        result: list[tuple[UnitExpectation, str]] = []
        for expected in self.units:
            if expected.active_state == "active":
                result.append((expected, "stop"))
            if expected.unit_file_state == "enabled":
                result.append((expected, "disable"))
            result.append((expected, "mask"))
        return tuple(result)

    def run_b1(self) -> None:
        if self.stage is not Stage.PREFLIGHT_VERIFIED:
            raise ExecutionError("B1 requires a complete zero-effect preflight")
        for expected, operation in self._unit_steps():
            action = f"b1:{expected.name}:{operation}"
            self.journal.append(
                "intent",
                action,
                {
                    "operation": operation,
                    "preimage": self._state_payload(
                        self.backend.inspect_unit(expected.name)
                    ),
                },
            )
            try:
                self.backend.unit_operation(expected.name, operation)
                actual = self.backend.inspect_unit(expected.name)
                if not self._operation_achieved(actual, expected, operation):
                    raise ExecutionError("unit operation actual state mismatch")
                self.registry.revalidate(tuple(self.completed_deltas))
                self.completed_actions.append(action)
                self.journal.append(
                    "outcome",
                    action,
                    {"status": "achieved", "state": self._state_payload(actual)},
                )
            except Exception as exc:
                self.stage = Stage.INVALID
                raise ExecutionError(f"B1 failed at {action}") from exc
        if any(
            not self._fenced(self.backend.inspect_unit(item.name), item)
            for item in self.units
        ):
            raise ExecutionError("B1 final state vector is not fully fenced")
        self.stage = Stage.REPROVISIONERS_FENCED
        self.journal.append(
            "checkpoint",
            "reprovisioners-fenced",
            {"completed_actions": list(self.completed_actions)},
        )

    def run_b2(self) -> None:
        if self.stage is not Stage.REPROVISIONERS_FENCED:
            raise ExecutionError("B2 cannot run before complete B1 acceptance")
        for delta in FIXED_DELTAS:
            action = f"b2:{delta.registry_index}:{delta.job_id}:pause"
            self.registry.revalidate(tuple(self.completed_deltas))
            preimage = self.registry.evidence()[delta.registry_index]
            self.journal.append(
                "intent",
                action,
                {
                    "operation": "hermes-internal-pause",
                    "preimage_digests": list(self.registry.last_digests),
                    "preimage": preimage,
                },
            )
            try:
                self.backend.pause_job(delta)
                cumulative = (*self.completed_deltas, delta)
                if not self.backend.job_is_paused(delta):
                    raise ExecutionError(
                        "Hermes did not pause the exact physical record"
                    )
                digests = self.registry.revalidate(cumulative, changed_delta=delta)
                self.completed_deltas.append(delta)
                self.completed_actions.append(action)
                postimage = self.registry.evidence()[delta.registry_index]
                self.journal.append(
                    "outcome",
                    action,
                    {
                        "status": "achieved",
                        "registry_digests": list(digests),
                        "preimage": preimage,
                        "postimage": postimage,
                    },
                )
            except Exception as exc:
                self.stage = Stage.INVALID
                raise ExecutionError(f"B2 failed at {action}") from exc
        self.stage = Stage.WRITERS_PAUSED
        self.b2_completed_monotonic = float(self.monotonic())
        self.journal.append(
            "checkpoint",
            "writers-paused",
            {
                "attempt_id": self.attempt_id,
                "status": "b1-b2-accepted-not-f0",
                "completed_monotonic": self.b2_completed_monotonic,
            },
        )
        self.stage = Stage.CUSTODY_CONVERGING

    def record_capture_challenge(
        self, capture_id: str, baseline_digest: str
    ) -> str:
        if (
            self.stage is not Stage.CUSTODY_CONVERGING
            or self.capture_id is not None
            or re.fullmatch(r"[0-9a-f]{64}", capture_id) is None
            or SHA256.fullmatch(baseline_digest) is None
        ):
            raise ExecutionError("F0 capture challenge is invalid or out of order")
        record = self.journal.append(
            "checkpoint",
            "f0-capture-challenge",
            {
                "attempt_id": self.attempt_id,
                "capture_id": capture_id,
                "baseline_digest": baseline_digest,
                "b2_completed_monotonic": self.b2_completed_monotonic,
            },
        )
        self.capture_id = capture_id
        self.capture_journal_head = record["record_hash"]
        return record["record_hash"]

    def record_custody_read(
        self, value: dict[str, Any], observed_monotonic: float
    ) -> None:
        if self.stage is not Stage.CUSTODY_CONVERGING or self.capture_id is None:
            raise ExecutionError("custody read is only valid after the capture challenge")
        if set(value) != CUSTODY_FIELDS:
            raise ExecutionError("custody evidence fields are incomplete or widened")
        expected_method = "GET" if not self.custody_reads else "NO_OP"
        if value["method"] != expected_method:
            raise ExecutionError("custody reads must be stable GET then NO_OP")
        try:
            ArtifactRef.parse(value["artifact"])
        except ArtifactError as exc:
            raise ExecutionError(
                "custody read does not reference actual bytes"
            ) from exc
        if (
            isinstance(observed_monotonic, bool)
            or not isinstance(observed_monotonic, (int, float))
            or observed_monotonic < 0
            or self.b2_completed_monotonic is None
        ):
            raise ExecutionError("custody source monotonic time is invalid")
        observed_monotonic = float(observed_monotonic)
        if observed_monotonic - self.b2_completed_monotonic < 300:
            raise ExecutionError("custody read is less than five source minutes after B2")
        if self.custody_reads and (
            observed_monotonic - self.custody_reads[0]["observed_monotonic"] < 300
        ):
            raise ExecutionError("custody reads are less than five source minutes apart")
        enriched = {**value, "observed_monotonic": observed_monotonic}
        self.custody_reads.append(enriched)
        self.journal.append(
            "checkpoint", f"custody-read-{len(self.custody_reads)}", enriched
        )
        if len(self.custody_reads) == 2:
            self.stage = Stage.F0_ELIGIBLE

    @staticmethod
    def _delta_from_action(action: str) -> Delta:
        parts = action.split(":")
        try:
            delta = Delta(int(parts[1]), parts[2])
        except (ValueError, IndexError) as exc:
            raise ExecutionError("journal contains invalid B2 action") from exc
        if delta not in FIXED_DELTAS:
            raise ExecutionError("journal names a non-fixed job")
        return delta

    def _account_records(self, records: tuple[dict[str, Any], ...]) -> None:
        completed: list[str] = []
        for record in records:
            if record["kind"] not in {"outcome", "recovery"}:
                continue
            action = record["action_id"]
            status = record["payload"].get("status")
            if action.startswith("rollback:"):
                original = action.removeprefix("rollback:")
                if status in {"restored", "restored-before-crash"}:
                    if not completed or completed[-1] != original:
                        raise ExecutionError(
                            "journal rollback is not the exact achieved suffix"
                        )
                    completed.pop()
            elif status in {"achieved", "achieved-before-crash"}:
                if action not in {item["action"] for item in self.effect_plan()}:
                    raise ExecutionError(
                        "journal contains an unplanned achieved effect"
                    )
                if action in completed:
                    raise ExecutionError("journal duplicates an achieved effect")
                completed.append(action)
        expected_prefix = [item["action"] for item in self.effect_plan()][
            : len(completed)
        ]
        if completed != expected_prefix:
            raise ExecutionError("journal effects are not a fixed cumulative prefix")
        self.completed_actions = completed
        self.completed_deltas = [
            self._delta_from_action(action)
            for action in completed
            if action.startswith("b2:")
        ]

    @staticmethod
    def _b2_images(
        records: tuple[dict[str, Any], ...], action: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        outcome = next(
            (
                record
                for record in reversed(records)
                if record["kind"] in {"outcome", "recovery"}
                and record["action_id"] == action
                and record["payload"].get("status")
                in {"achieved", "achieved-before-crash"}
            ),
            None,
        )
        if outcome is None:
            raise ExecutionError("rollback lacks the exact achieved B2 outcome")
        preimage = outcome["payload"].get("preimage")
        postimage = outcome["payload"].get("postimage")
        if not isinstance(preimage, dict) or not isinstance(postimage, dict):
            raise ExecutionError("rollback lacks exact journaled images")
        return preimage, postimage

    @staticmethod
    def rollback_authorization(
        records: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], str] | None:
        matches = [
            record
            for record in records
            if record["kind"] == "checkpoint"
            and record["action_id"] == "rollback-authorization"
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise ExecutionError("rollback authorization checkpoint is duplicated")
        record = matches[0]
        payload = record["payload"]
        if not isinstance(payload, dict) or set(payload) != {
            "schema",
            "attempt_id",
            "authorized_actions",
            "authorization_grant_digest",
            "execution_journal_head",
            "authorization_identity",
        }:
            raise ExecutionError("rollback authorization checkpoint is malformed")
        base = dict(payload)
        identity = base.pop("authorization_identity")
        actions = payload["authorized_actions"]
        if (
            payload["schema"] != "phase-b.rollback-authorization.v1"
            or not isinstance(payload["attempt_id"], str)
            or not isinstance(actions, list)
            or not actions
            or any(not isinstance(action, str) for action in actions)
            or not isinstance(payload["authorization_grant_digest"], str)
            or SHA256.fullmatch(payload["authorization_grant_digest"]) is None
            or not isinstance(payload["execution_journal_head"], str)
            or SHA256.fullmatch(payload["execution_journal_head"]) is None
            or record["previous_hash"] != payload["execution_journal_head"]
            or identity != strict_json.digest(base)
        ):
            raise ExecutionError("rollback authorization checkpoint is inconsistent")
        return payload, record["record_hash"]

    def persist_rollback_authorization(
        self,
        authorized_actions: tuple[str, ...],
        authorization_grant_digest: str,
        execution_journal_head: str,
    ) -> str:
        if self.rollback_authorization(self.journal.read_all()) is not None:
            raise ExecutionError("rollback authorization is already durable")
        if (
            authorization_grant_digest is None
            or SHA256.fullmatch(authorization_grant_digest) is None
            or SHA256.fullmatch(execution_journal_head) is None
            or execution_journal_head != self.journal.head()
            or authorized_actions != tuple(reversed(self.completed_actions))
            or not authorized_actions
        ):
            raise ExecutionError("rollback authorization does not bind exact state")
        base = {
            "schema": "phase-b.rollback-authorization.v1",
            "attempt_id": self.attempt_id,
            "authorized_actions": list(authorized_actions),
            "authorization_grant_digest": authorization_grant_digest,
            "execution_journal_head": execution_journal_head,
        }
        record = self.journal.append(
            "checkpoint",
            "rollback-authorization",
            {**base, "authorization_identity": strict_json.digest(base)},
        )
        verified = self.rollback_authorization(self.journal.read_all())
        if verified is None or verified[1] != record["record_hash"]:
            raise ExecutionError("rollback authorization was not durably verified")
        return record["record_hash"]

    def recover(
        self,
        *,
        allow_incident_rollback: bool = False,
        verified_rollback_grant_digest: str | None = None,
    ) -> None:
        """Derive achieved state from the journal and actual unit/registry state."""
        try:
            records = self.journal.read_all()
            pending = self.journal.pending_intents()
            self._account_records(records)
        except (JournalError, ExecutionError) as exc:
            self.stage = Stage.INVALID
            raise ExecutionError("journal cannot be recovered safely") from exc

        pending_b2: Delta | None = None
        pending_rollback_b2: dict[str, tuple[Delta, bool]] = {}
        pending_rollbacks = tuple(
            action for action in pending if action.startswith("rollback:")
        )
        pending_reconciliations = tuple(
            action for action in pending if action.startswith("rollback-reconcile:")
        )
        if len(pending_reconciliations) > 1:
            raise ExecutionError("multiple rollback reconciliations are pending")
        if pending_rollbacks:
            authorization = self.rollback_authorization(records)
            restored = tuple(
                record["action_id"].removeprefix("rollback:")
                for record in records
                if record["kind"] in {"outcome", "recovery"}
                and record["action_id"].startswith("rollback:")
                and record["payload"].get("status")
                in {"restored", "restored-before-crash"}
            )
            remaining = tuple(reversed(self.completed_actions))
            if (
                len(pending_rollbacks) != 1
                or not remaining
                or pending_rollbacks[0].removeprefix("rollback:")
                != remaining[0]
                or authorization is None
                or tuple(authorization[0]["authorized_actions"])
                != (*restored, *remaining)
                or authorization[0]["authorization_grant_digest"]
                != verified_rollback_grant_digest
            ):
                raise ExecutionError(
                    "pending rollback is not the exact durably authorized suffix"
                )
        for action in pending:
            restoring = action.startswith("rollback:")
            original = action.removeprefix("rollback:")
            if original.startswith("b2:"):
                candidate = self._delta_from_action(original)
                paused = self.backend.job_is_paused(candidate)
                if restoring:
                    if candidate not in self.completed_deltas:
                        raise ExecutionError("rollback intent names an unachieved job")
                    pending_rollback_b2[action] = (candidate, paused)
                elif paused:
                    pending_b2 = candidate

        opened_for_recovery = not self.registry.handles
        if opened_for_recovery:
            recovery_prefix = list(self.completed_deltas)
            if pending_b2 is not None:
                recovery_prefix.append(pending_b2)
            for delta, paused in pending_rollback_b2.values():
                if not paused:
                    if not recovery_prefix or recovery_prefix[-1] != delta:
                        raise ExecutionError(
                            "restored registry is not the exact achieved suffix"
                        )
                    recovery_prefix.pop()
            restored_indices = {
                self._delta_from_action(
                    record["action_id"].removeprefix("rollback:")
                ).registry_index
                for record in records
                if record["action_id"].startswith("rollback:b2:")
                and record["kind"] in {"outcome", "recovery"}
                and record["payload"].get("status")
                in {"restored", "restored-before-crash"}
            }
            restored_indices.update(
                delta.registry_index
                for delta, paused in pending_rollback_b2.values()
                if not paused
            )
            try:
                self.registry.acquire(
                    tuple(recovery_prefix),
                    allow_restored_indices=frozenset(restored_indices),
                )
            except Exception as exc:
                self.stage = Stage.INVALID
                raise ExecutionError(
                    "registry recovery prefix does not match actual state"
                ) from exc

        unresolved_pending = False
        forward_pending = False
        for action in pending:
            reconciling = action.startswith("rollback-reconcile:")
            restoring = action.startswith("rollback:") or reconciling
            intent_payload = pending[action]["payload"]
            if reconciling:
                parts = action.split(":", 3)
                if (
                    len(parts) != 4
                    or len(parts[1]) != 4
                    or not parts[1].isdigit()
                    or not isinstance(intent_payload, dict)
                    or set(intent_payload)
                    != {"restores", "operation", "authorization_grant_digest"}
                    or intent_payload["restores"] != parts[2] + ":" + parts[3]
                ):
                    raise ExecutionError("pending rollback reconciliation is malformed")
                original = intent_payload["restores"]
            else:
                original = action.removeprefix("rollback:")
            forward_pending = forward_pending or not restoring
            changed_delta: Delta | None = None
            paused = False
            authorization_digest: str | None = None
            if restoring:
                authorization_digest = intent_payload.get(
                    "authorization_grant_digest"
                )
                authorization = self.rollback_authorization(records)
                if (
                    not isinstance(authorization_digest, str)
                    or SHA256.fullmatch(authorization_digest) is None
                    or verified_rollback_grant_digest != authorization_digest
                    or authorization is None
                    or authorization[0]["attempt_id"] != self.attempt_id
                    or authorization[0]["authorization_grant_digest"]
                    != authorization_digest
                    or original not in authorization[0]["authorized_actions"]
                ):
                    raise ExecutionError(
                        "pending rollback lacks reverified durable authorization"
                    )
            if original.startswith("b1:"):
                _prefix, name, operation = original.split(":", 2)
                if name not in FENCED_UNITS or operation not in UNIT_OPERATIONS:
                    raise ExecutionError("journal names a non-fixed unit operation")
                expected = self.units[FENCED_UNITS.index(name)]
                expected_inverse = ROLLBACK_OPERATIONS[operation]
                actual_operation = expected_inverse if restoring else operation
                if reconciling and intent_payload.get("operation") != expected_inverse:
                    raise ExecutionError(
                        "pending reconciliation is not the authorized exact inverse"
                    )
                achieved = self._operation_achieved(
                    self.backend.inspect_unit(name), expected, actual_operation
                )
                if restoring and not achieved:
                    self.backend.unit_operation(name, actual_operation)
                    achieved = self._operation_achieved(
                        self.backend.inspect_unit(name), expected, actual_operation
                    )
            elif original.startswith("b2:"):
                changed_delta = self._delta_from_action(original)
                paused = self.backend.job_is_paused(changed_delta)
                if restoring:
                    if changed_delta not in self.completed_deltas:
                        raise ExecutionError("rollback recovered an unachieved job")
                    provisional = list(self.completed_deltas)
                    provisional.remove(changed_delta)
                    preimage, postimage = self._b2_images(records, original)
                    if paused:
                        self.registry.revalidate(tuple(self.completed_deltas))
                        self.backend.restore_job_preimage(
                            changed_delta, preimage, postimage
                        )
                        self.registry.revalidate(
                            tuple(provisional), changed_delta=changed_delta
                        )
                        paused = self.backend.job_is_paused(changed_delta)
                    else:
                        self.registry.revalidate(tuple(provisional))
                    achieved = not paused
                else:
                    achieved = paused
            else:
                raise ExecutionError("journal contains unrecoverable intent")
            status = (
                "achieved-before-crash"
                if achieved and not restoring
                else "restored-before-crash"
                if achieved
                else "unknown-postimage-after-crash"
                if changed_delta is not None and paused and not restoring
                else "unknown-or-not-achieved"
            )
            recovery_payload: dict[str, Any] = {"status": status}
            if authorization_digest is not None:
                recovery_payload["authorization_grant_digest"] = authorization_digest
            if reconciling:
                recovery_payload["restores"] = original
                recovery_payload["operation"] = intent_payload["operation"]
            if changed_delta is not None and paused and not restoring:
                observed_postimage = self.registry.evidence()[
                    changed_delta.registry_index
                ]
                recovery_payload["observed_postimage"] = observed_postimage
                recovery_payload["postimage"] = observed_postimage
                intent_preimage = pending[action]["payload"].get("preimage")
                if not isinstance(intent_preimage, dict):
                    raise ExecutionError("pending B2 intent lacks exact preimage")
                recovery_payload["preimage"] = intent_preimage
            self.journal.append("recovery", action, recovery_payload)
            self.journal.append(
                "invalidation",
                "attempt",
                {"reason": "crash-recovery", "action": action, "status": status},
            )
            if not achieved:
                unresolved_pending = True
            elif changed_delta is not None and not restoring:
                provisional = [*self.completed_deltas, changed_delta]
                self.registry.revalidate(
                    tuple(provisional),
                    changed_delta=None if opened_for_recovery else changed_delta,
                )
        current_records = self.journal.read_all()
        self._account_records(current_records)
        for action in self.completed_actions:
            if action.startswith("b1:"):
                _prefix, name, operation = action.split(":", 2)
                expected = self.units[FENCED_UNITS.index(name)]
                if not self._operation_achieved(
                    self.backend.inspect_unit(name), expected, operation
                ):
                    raise ExecutionError(
                        "journaled unit effect is absent from actual state"
                    )
        self.stage = Stage.INVALID
        if unresolved_pending or forward_pending:
            raise ExecutionError(
                "interrupted attempt is durably invalid and requires another recovery pass"
            )
        self.registry.revalidate(tuple(self.completed_deltas))
        final_evidence = self.registry.evidence()
        last_postimage: dict[int, dict[str, Any]] = {}
        completed_delta_set = set(self.completed_deltas)
        for record in current_records:
            if record["action_id"].startswith("b2:") and record["payload"].get(
                "status"
            ) in {"achieved", "achieved-before-crash"}:
                delta = self._delta_from_action(record["action_id"])
                if delta not in completed_delta_set:
                    continue
                postimage = record["payload"].get("postimage")
                if not isinstance(postimage, dict):
                    raise ExecutionError(
                        "journaled registry outcome lacks exact postimage"
                    )
                last_postimage[delta.registry_index] = postimage
        for index, postimage in last_postimage.items():
            actual = final_evidence[index]
            # Later atomic replacements in the same registry legitimately change
            # inode identity even when exact rollback restores this postimage.
            if any(
                postimage.get(field) != actual.get(field)
                for field in ("path", "device", "digest", "document")
            ):
                raise ExecutionError(
                    "actual registry differs from exact journaled postimage"
                )
        authorization = self.rollback_authorization(current_records)
        if authorization is not None and not self.completed_actions:
            grant_digest = authorization[0]["authorization_grant_digest"]
            if grant_digest != verified_rollback_grant_digest:
                raise ExecutionError("rollback completion lacks reverified authorization")
            self._finish_rollback(
                tuple(authorization[0]["authorized_actions"]), grant_digest
            )
            return
        if current_records and not allow_incident_rollback:
            raise ExecutionError(
                "existing attempt was accounted but requires an incident grant"
            )

    def _reconcile_authorized_unit_drift(
        self,
        full_authorized: tuple[str, ...],
        authorization_grant_digest: str,
    ) -> None:
        immutable_fields = (
            "name",
            "source_fragment_path",
            "source_fragment_digest",
            "source_load_state",
            "trigger_edges",
        )
        reconciliation_index = sum(
            1
            for record in self.journal.read_all()
            if record["action_id"].startswith("rollback-reconcile:")
            and record["kind"] == "intent"
        )
        for expected in self.units:
            actual = self.backend.inspect_unit(expected.name)
            baseline = UnitState(
                expected.name,
                expected.fragment_path,
                expected.fragment_digest,
                expected.load_state,
                expected.active_state,
                expected.unit_file_state,
                False,
                expected.trigger_edges,
            )
            if any(
                getattr(actual, field) != getattr(baseline, field)
                for field in immutable_fields
            ):
                raise ExecutionError("unit identity drift cannot be rollback-reconciled")
            needed: list[tuple[str, str]] = []
            if actual.runtime_masked:
                needed.append((f"b1:{expected.name}:mask", "unmask"))
            if actual.unit_file_state != expected.unit_file_state:
                needed.append((f"b1:{expected.name}:disable", "enable"))
            if actual.active_state != expected.active_state:
                needed.append((f"b1:{expected.name}:stop", "start"))
            for original, operation in needed:
                if original not in full_authorized:
                    raise ExecutionError(
                        "unit drift requires an operation absent from rollback authorization"
                    )
                reconciliation_index += 1
                action = f"rollback-reconcile:{reconciliation_index:04d}:{original}"
                payload = {
                    "restores": original,
                    "operation": operation,
                    "authorization_grant_digest": authorization_grant_digest,
                }
                self.journal.append("intent", action, payload)
                self.backend.unit_operation(expected.name, operation)
                if not self._operation_achieved(
                    self.backend.inspect_unit(expected.name), expected, operation
                ):
                    raise ExecutionError("authorized unit reconciliation failed")
                self.journal.append(
                    "outcome", action, {**payload, "status": "restored"}
                )
            if not self._same_preflight(self.backend.inspect_unit(expected.name), expected):
                raise ExecutionError("fenced unit does not match exact signed baseline")

    def _validated_rollback_snapshot(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        unit_states = tuple(
            self.backend.inspect_unit(expected.name) for expected in self.units
        )
        if any(
            not self._same_preflight(actual, expected)
            for actual, expected in zip(unit_states, self.units, strict=True)
        ):
            raise ExecutionError("fenced unit does not match exact signed baseline")
        preserved_states = tuple(
            self.backend.preserved_identity(expected.name)
            for expected in self.preserved
        )
        if preserved_states != self.preserved:
            raise ExecutionError("preserved unit identity drifted during rollback")
        return (
            [self._state_payload(actual) for actual in unit_states],
            [
                {
                    "name": item.name,
                    "healthy": item.healthy,
                    "start_identity": item.start_identity,
                }
                for item in preserved_states
            ],
        )

    def _finish_rollback(
        self,
        full_authorized: tuple[str, ...],
        authorization_grant_digest: str,
    ) -> None:
        if self.completed_actions:
            raise ExecutionError("rollback cannot finish with achieved effects")
        self.registry.revalidate()
        self._reconcile_authorized_unit_drift(
            full_authorized, authorization_grant_digest
        )
        unit_snapshot, preserved_snapshot = self._validated_rollback_snapshot()
        records = self.journal.read_all()
        completed = [
            record
            for record in records
            if record["kind"] == "checkpoint"
            and record["action_id"] == "rollback-complete"
        ]
        invalidations = [
            record
            for record in records
            if record["kind"] == "invalidation"
            and record["action_id"] == "attempt"
            and record["payload"].get("reason")
            == "authorized-pre-f0-restoration"
        ]
        if len(completed) > 1 or len(invalidations) > 1:
            raise ExecutionError("rollback terminal evidence is duplicated")
        if completed and not invalidations:
            raise ExecutionError("rollback completion lacks mandatory invalidation")
        if not completed:
            if not invalidations:
                self.journal.append(
                    "invalidation",
                    "attempt",
                    {"reason": "authorized-pre-f0-restoration"},
                )
            self.journal.append(
                "checkpoint",
                "rollback-complete",
                {
                    "authorization_grant_digest": authorization_grant_digest,
                    "authorized_actions": list(full_authorized),
                    "unit_state_digest": strict_json.digest(unit_snapshot),
                    "preserved_identity_digest": strict_json.digest(
                        preserved_snapshot
                    ),
                },
            )
        self.stage = Stage.ROLLED_BACK

    def rollback_before_f0(
        self,
        authorized_actions: tuple[str, ...],
        authorization_grant_digest: str,
    ) -> None:
        if (
            not isinstance(authorization_grant_digest, str)
            or SHA256.fullmatch(authorization_grant_digest) is None
        ):
            raise ExecutionError("rollback authorization grant digest is invalid")
        if (
            self.stage
            in {Stage.F0_ELIGIBLE, Stage.F0_ESTABLISHED, Stage.OBSERVATION_EXTERNAL}
            or not authorized_actions
        ):
            raise ExecutionError(
                "rollback is forbidden at/after F0 eligibility or without a grant"
            )
        required = tuple(reversed(self.completed_actions))
        authorization = self.rollback_authorization(self.journal.read_all())
        restored = tuple(
            record["action_id"].removeprefix("rollback:")
            for record in self.journal.read_all()
            if record["kind"] in {"outcome", "recovery"}
            and record["action_id"].startswith("rollback:")
            and record["payload"].get("status")
            in {"restored", "restored-before-crash"}
        )
        if (
            authorized_actions != required
            or authorization is None
            or authorization[0]["attempt_id"] != self.attempt_id
            or authorization[0]["authorization_grant_digest"]
            != authorization_grant_digest
            or tuple(authorization[0]["authorized_actions"])
            != (*restored, *authorized_actions)
        ):
            raise ExecutionError(
                "rollback grant is not the exact durable achieved reverse sequence"
            )
        for original in authorized_actions:
            action = "rollback:" + original
            if original.startswith("b2:"):
                delta = self._delta_from_action(original)
                operation = "restore-preimage"
                self.journal.append(
                    "intent",
                    action,
                    {
                        "restores": original,
                        "operation": operation,
                        "authorization_grant_digest": authorization_grant_digest,
                    },
                )
                preimage, postimage = self._b2_images(
                    self.journal.read_all(), original
                )
                self.backend.restore_job_preimage(delta, preimage, postimage)
                self.completed_deltas.remove(delta)
                self.registry.revalidate(
                    tuple(self.completed_deltas), changed_delta=delta
                )
                if self.backend.job_is_paused(delta):
                    raise ExecutionError("exact job preimage restoration failed")
            else:
                _prefix, name, prior_operation = original.split(":", 2)
                operation = ROLLBACK_OPERATIONS[prior_operation]
                self.journal.append(
                    "intent",
                    action,
                    {
                        "restores": original,
                        "operation": operation,
                        "authorization_grant_digest": authorization_grant_digest,
                    },
                )
                self.backend.unit_operation(name, operation)
                expected = self.units[FENCED_UNITS.index(name)]
                if not self._operation_achieved(
                    self.backend.inspect_unit(name), expected, operation
                ):
                    raise ExecutionError("exact unit operation restoration failed")
            self.completed_actions.remove(original)
            self.journal.append(
                "outcome",
                action,
                {
                    "status": "restored",
                    "operation": operation,
                    "authorization_grant_digest": authorization_grant_digest,
                },
            )
        self._finish_rollback(
            tuple(authorization[0]["authorized_actions"]),
            authorization_grant_digest,
        )

    def revalidate_f0_live_state(self) -> tuple[str, ...]:
        if self.backend.effect_capable_processes():
            raise ExecutionError("effect-capable process exists at F0 candidate time")
        actual_preserved = tuple(
            self.backend.preserved_identity(item.name) for item in self.preserved
        )
        if actual_preserved != self.preserved or any(
            not item.healthy for item in actual_preserved
        ):
            raise ExecutionError("preserved generic continuity drifted before F0")
        registry_digests = self.registry.revalidate(tuple(FIXED_DELTAS))
        if any(
            not self._fenced(self.backend.inspect_unit(item.name), item)
            for item in self.units
        ):
            raise ExecutionError("B1 fence drifted before F0")
        if any(not self.backend.job_is_paused(delta) for delta in FIXED_DELTAS):
            raise ExecutionError("B2 target recurred before F0")
        return tuple(registry_digests)

    def establish_f0_candidate(
        self,
        evidence: dict[str, Any],
        capture_id: str,
        f0_at: str,
        validate: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        if (
            self.stage is not Stage.F0_ELIGIBLE
            or set(evidence) != F0_EVIDENCE_FIELDS
            or len(self.custody_reads) != 2
            or capture_id != self.capture_id
            or self.capture_journal_head is None
            or not isinstance(f0_at, str)
            or not f0_at.endswith("Z")
        ):
            raise ExecutionError("F0 requires complete B1/B2/B3 and typed evidence")
        try:
            for value in evidence.values():
                ArtifactRef.parse(value)
        except ArtifactError as exc:
            raise ExecutionError("F0 evidence does not reference actual bytes") from exc
        registry_digests = self.revalidate_f0_live_state()
        artifact = {
            "schema": "phase-b.f0.v3",
            "attempt_id": self.attempt_id,
            "capture_id": capture_id,
            "f0_at": f0_at,
            "evidence": evidence,
            "custody_reads": self.custody_reads,
            "registry_digests": list(registry_digests),
            "journal_head": self.journal.head(),
        }
        validate(artifact)
        if self.revalidate_f0_live_state() != registry_digests:
            raise ExecutionError("F0 registry state drifted during evidence validation")
        self.journal.append(
            "checkpoint",
            "f0-established",
            {"artifact": artifact, "artifact_digest": digest(artifact)},
        )
        self.stage = Stage.F0_ESTABLISHED
        return artifact
