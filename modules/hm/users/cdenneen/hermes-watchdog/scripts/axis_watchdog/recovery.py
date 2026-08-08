import hashlib
import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .diagnostics import DIAGNOSTIC_SCHEMA, validate_diagnostic
from .records import Ledger, atomic_write, load_optional, timestamp

TERMINAL_TRANSACTION_STATES = {"completed", "failed", "waiting-human"}


def unavailable_diagnostic() -> dict[str, Any]:
    return {
        "schema": DIAGNOSTIC_SCHEMA,
        "schema_version": "1.0.0",
        "classification": "unknown",
        "summary": "Bounded diagnostic was unavailable.",
        "recommended_action": "Inspect deterministic watchdog evidence.",
        "confidence": 0.0,
    }


class RecoveryJournal:
    def __init__(
        self,
        root: Path,
        ledger: Ledger,
        *,
        fault: Any | None = None,
    ):
        self.root = root
        self.directory = root / "recovery-transactions"
        self.ledger = ledger
        self.fault = fault

    def _path(self, incident_id: str) -> Path:
        return self.directory / f"{incident_id}.json"

    def begin(self, incident: dict[str, Any], now: int) -> dict[str, Any]:
        incident = dict(incident)
        evidence_fingerprint = str(
            incident.get("evidence_fingerprint")
            or "sha256:"
            + hashlib.sha256(
                json.dumps(
                    [
                        incident.get("anomaly_code"),
                        incident.get("dimension"),
                        incident.get("recovery_level"),
                        incident.get("repair_repository"),
                    ],
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        incident["evidence_fingerprint"] = evidence_fingerprint
        path = self._path(str(incident["incident_id"]))
        existing = load_optional(path)
        if existing and existing.get("opened_at") == incident.get("opened_at"):
            return existing
        identity = json.dumps(
            [incident["incident_id"], incident.get("opened_at")], separators=(",", ":")
        )
        transaction = {
            "schema": "axis.development-watchdog.recovery-transaction",
            "schema_version": "1.0.0",
            "recovery_id": "recovery-" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            "incident_id": incident["incident_id"],
            "opened_at": incident.get("opened_at"),
            "evidence_fingerprint": evidence_fingerprint,
            "incident": incident,
            "status": "pending",
            "last_transition": None,
            "detail": "transaction created",
            "created_at": timestamp(now),
            "updated_at": timestamp(now),
        }
        atomic_write(path, transaction)
        if self.fault:
            self.fault("after-transaction-created", transaction)
        return transaction

    def transition(
        self,
        transaction: dict[str, Any],
        *,
        action: str,
        target: str,
        status: str,
        transition: str,
        detail: str,
        now: int,
    ) -> dict[str, Any]:
        duplicate = next(
            (
                entry
                for entry in self.ledger.entries()
                if entry.get("recovery_id") == transaction["recovery_id"]
                and entry.get("transition") == transition
            ),
            None,
        )
        if duplicate is None:
            self.ledger.append(
                {
                    "recovery_id": transaction["recovery_id"],
                    "incident_id": transaction["incident_id"],
                    "level": int((transaction.get("incident") or {})["recovery_level"]),
                    "action": action,
                    "target": target,
                    "status": status,
                    "transition": transition,
                    "detail": detail[:1200],
                    "occurred_at": timestamp(now),
                }
            )
        if self.fault:
            self.fault("after-transition-ledger", transaction)
        transaction = {
            **transaction,
            "status": status,
            "last_transition": transition,
            "detail": detail[:1200],
            "updated_at": timestamp(now),
        }
        atomic_write(self._path(str(transaction["incident_id"])), transaction)
        if self.fault:
            self.fault("after-transition-journal", transaction)
        return transaction

    def pending(self) -> list[dict[str, Any]]:
        values = []
        for path in sorted(self.directory.glob("*.json")):
            value = load_optional(path)
            if value and value.get("status") not in TERMINAL_TRANSACTION_STATES:
                values.append(value)
        return values

    def for_incident(self, incident_id: str) -> dict[str, Any]:
        return load_optional(self._path(incident_id))

    def completed_for_evidence(
        self, incident_id: str, evidence_fingerprint: str
    ) -> dict[str, Any]:
        transaction = self.for_incident(incident_id)
        if (
            not transaction
            or transaction.get("status") != "completed"
            or transaction.get("evidence_fingerprint") != evidence_fingerprint
        ):
            return {}
        restored = any(
            entry.get("recovery_id") == transaction.get("recovery_id")
            and entry.get("transition") == "health-restored"
            for entry in self.ledger.entries()
        )
        return {} if restored else transaction


class RecoveryExecutor:
    def __init__(
        self,
        root: Path,
        supervisor_root: Path,
        *,
        runner: Any | None = None,
        self_repair_command: str | None = None,
        runtime_repair_command: str | None = None,
    ):
        self.root = root
        self.supervisor_root = supervisor_root
        self.runner = runner or subprocess.run
        self.self_repair_command = self_repair_command or os.environ.get(
            "AXIS_WATCHDOG_SELF_REPAIR_COMMAND",
            "axis-development-watchdog-self-repair",
        )
        self.runtime_repair_command = runtime_repair_command or os.environ.get(
            "AXIS_WATCHDOG_RUNTIME_REPAIR_COMMAND",
            "axis-development-watchdog-runtime-repair",
        )

    def _run(self, command: str, timeout: int) -> str:
        result = self.runner(
            shlex.split(command),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            output = (result.stdout or "") + (result.stderr or "")
            raise RuntimeError(
                f"recovery command exited {result.returncode}: {output[-1000:]}"
            )
        return (result.stdout or "").strip()[-1000:] or "command completed"

    @staticmethod
    def _ownership() -> dict[str, Any]:
        return {
            "schema": "axis.external-development-supervisor.repository-ownership-evidence",
            "schema_version": "1.0.0",
            "status": "validated",
            "context": "watchdog-supervisor-repair",
            "responsibility": "supervisor-orchestration/temporary-slack/cron",
            "repository": "cdenneen/home",
            "canonical_repository": "cdenneen/home",
            "reason": None,
            "responsibility_to_canonical_repository": {
                "supervisor-orchestration/temporary-slack/cron": "cdenneen/home",
                "axis-runtime/product": "ghostspace/axis",
                "contracts/planning-records": "ghostspace/axis-governance",
                "deployment/realistic-validation": "ghostspace/axis-lab",
            },
        }

    def _repair_assignment(
        self,
        recovery_id: str,
        incident: dict[str, Any],
        diagnosis: dict[str, Any],
        now: int,
    ) -> dict[str, Any]:
        assignment_id = "assignment-watchdog-" + recovery_id.removeprefix("recovery-")
        source = json.dumps(
            {"recovery_id": recovery_id, "diagnosis": diagnosis},
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "schema": "axis.external-development-supervisor.assignment",
            "schema_version": "4.0.0",
            "assignment_id": assignment_id,
            "assignment_type": "read-only-analysis",
            "result_state": "pending",
            "work_item_disposition": "not-evaluated",
            "lifecycle_state": "ready-semantic",
            "kind": "watchdog-supervisor-repair",
            "queue_ref": recovery_id,
            "target_ref": recovery_id,
            "work_item": recovery_id,
            "project": "cdenneen/home",
            "responsibility": "supervisor-orchestration/temporary-slack/cron",
            "repository_ownership": self._ownership(),
            "title": "Bounded AXIS watchdog supervisor repair analysis",
            "authority": {
                "state": "preparation-only",
                "reason": "watchdog level-4 diagnostic escalation",
            },
            "planning_record": None,
            "candidate": {
                "responsibility": "supervisor-orchestration/temporary-slack/cron",
                "allowed_paths": [
                    "modules/hm/users/cdenneen/hermes-supervisor",
                    "modules/hm/users/cdenneen/hermes-watchdog",
                ],
                "required_tests": [
                    "nix build .#checks.aarch64-linux.hermes-watchdog",
                    "nix build .#checks.aarch64-linux.hermes-supervisor",
                ],
            },
            "allowed_paths": [
                "modules/hm/users/cdenneen/hermes-supervisor",
                "modules/hm/users/cdenneen/hermes-watchdog",
            ],
            "required_tests": [
                "nix build .#checks.aarch64-linux.hermes-watchdog",
                "nix build .#checks.aarch64-linux.hermes-supervisor",
            ],
            "source_item": {
                "watchdog_recovery_id": recovery_id,
                "diagnostic_evidence": {
                    "encoding": "json",
                    "trust": "untrusted-data",
                    "instruction_authority": False,
                    "value": diagnosis,
                },
                "product_dispatch_allowed": False,
            },
            "source_fingerprint": "sha256:" + hashlib.sha256(source.encode()).hexdigest(),
            "source_inventory_generation_id": None,
            "revalidation_tier": None,
            "ranking_factors": {"watchdog_repair": True},
            "selection_rationale": "durable level-4 watchdog repair analysis",
            "action_contract": None,
            "created_by_run": recovery_id,
            "created_at_epoch": now,
            "lease_id": None,
            "lease_uri": None,
            "worker": None,
            "mutation_grant_id": None,
            "mutation_grant_uri": None,
        }

    def execute(
        self,
        recovery_id: str,
        incident: dict[str, Any],
        diagnosis: dict[str, Any] | None,
        control: dict[str, Any],
        now: int,
    ) -> tuple[str, str]:
        del control
        level = int(incident["recovery_level"])
        if level == 0:
            return "completed", "current watchdog cycle supplied the missed heartbeat catch-up"
        if level == 1:
            return "in-progress", "canonical Slack projection retry is scheduled in this cycle"
        if level == 2:
            return "completed", self._run(self.self_repair_command, timeout=60)
        if level == 3:
            return "completed", self._run(self.runtime_repair_command, timeout=60)
        if level == 4:
            if incident.get("repair_repository") != "cdenneen/home":
                raise ValueError("level-4 repair escalation escaped cdenneen/home")
            validated = validate_diagnostic(diagnosis or unavailable_diagnostic())
            assignment = self._repair_assignment(recovery_id, incident, validated, now)
            path = self.supervisor_root / "assignments" / f"{assignment['assignment_id']}.json"
            existing = load_optional(path)
            if existing and existing.get("source_fingerprint") != assignment["source_fingerprint"]:
                raise RuntimeError("stable watchdog repair assignment conflicts with persisted data")
            if not existing:
                atomic_write(path, assignment)
            return "completed", f"persisted supervisor repair assignment {assignment['assignment_id']}"
        if level == 5:
            return "waiting-human", "Product Owner action is required"
        raise ValueError(f"unsupported recovery level: {level}")
