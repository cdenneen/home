import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import unquote, urlparse

from .accounting import AccountingLedger
from .lifecycle import is_terminal
from .schema_registry import read_record


class OperationClass(str, Enum):
    RECONCILIATION = "reconciliation-state-write"
    MODEL_CALL = "model-call"
    REPOSITORY = "repository-mutation"
    GITLAB = "gitlab-mutation"
    CONTROL = "control-mutation"
    SCHEDULER = "scheduler-mutation"
    RECONCILE = "reconciliation-trigger"


class MutationDenied(PermissionError):
    pass


@dataclass(frozen=True)
class GateDecision:
    operation: OperationClass
    repository: str | None
    assignment_id: str | None
    lease_id: str | None
    fencing_token: str | None
    lease_expires_at_epoch: int | None
    authority_state: str | None
    governance_state: str | None
    _issuer: str


def canonical_lease_path(root: Path, assignment: dict) -> Path:
    lease_id = str(assignment.get("lease_id") or "")
    lease_uri = str(assignment.get("lease_uri") or "")
    if not lease_id or not lease_uri:
        raise MutationDenied("assignment has no canonical lease reference")
    parsed = urlparse(lease_uri)
    if parsed.scheme != "file" or parsed.netloc:
        raise MutationDenied("lease_uri must be a local file URI")
    path = Path(unquote(parsed.path)).resolve()
    expected = (root / "leases" / lease_id / "lease.json").resolve()
    if path != expected:
        raise MutationDenied("lease_uri does not identify the canonical lease")
    return path


def load_canonical_lease(root: Path, assignment: dict) -> dict:
    lease = read_record(
        canonical_lease_path(root, assignment),
        "axis.external-development-supervisor.lease",
    )
    if lease["lease_id"] != assignment["lease_id"]:
        raise MutationDenied("canonical lease_id mismatch")
    if lease["assignment_id"] != assignment.get("assignment_id"):
        raise MutationDenied("canonical lease assignment mismatch")
    return lease


class MutationGate:
    def __init__(self, root: Path, source: str = "cycle"):
        self.root = root
        self.source = source
        self._issuer = uuid.uuid4().hex
        self.accounting = AccountingLedger(root)

    def decide(
        self,
        operation: OperationClass,
        *,
        assignment: dict | None = None,
        repository: str | None = None,
        fencing_token: str | None = None,
    ) -> GateDecision:
        if not isinstance(operation, OperationClass):
            raise MutationDenied(f"unknown operation class: {operation}")
        control = read_record(
            self.root / "control.json",
            "axis.external-development-supervisor.control",
        )
        if operation == OperationClass.RECONCILIATION:
            if self.source not in {
                "collector",
                "cycle",
                "dispatcher",
                "graph",
                "lease-controller",
                "preflight",
                "reporter",
                "worker",
            }:
                raise MutationDenied(
                    f"reconciliation write source is not trusted: {self.source}"
                )
            return self._decision(operation, repository, assignment)
        if operation == OperationClass.CONTROL:
            if self.source not in {"operator-cli", "product-owner-slack"}:
                raise MutationDenied(f"control mutation source is not trusted: {self.source}")
            return self._decision(operation, repository, assignment)
        if operation == OperationClass.SCHEDULER:
            if self.source not in {"home-manager", "operator-cli"}:
                raise MutationDenied(
                    f"scheduler mutation source is not trusted: {self.source}"
                )
            return self._decision(operation, repository, assignment)
        if operation == OperationClass.RECONCILE:
            if self.source not in {"operator-cli", "product-owner-slack"}:
                raise MutationDenied(
                    f"reconciliation trigger source is not trusted: {self.source}"
                )
            return self._decision(operation, repository, assignment)
        if control.get("kill_switch") or control.get("mode") != "enabled":
            raise MutationDenied("supervisor governance state does not permit this operation")
        if operation == OperationClass.MODEL_CALL:
            limit = int(control.get("daily_model_call_limit", 0))
            if self.accounting.model_attempts_today() >= limit:
                raise MutationDenied("model-call budget is exhausted")
            if assignment is None:
                raise MutationDenied("model calls require assignment context")
            lease = self._validate_lease(
                assignment, repository, fencing_token, allow_read_only=True
            )
            return self._decision(operation, repository, assignment, lease)
        if self.source not in {"cycle", "worker"}:
            raise MutationDenied(f"mutation source is not trusted: {self.source}")
        if not control.get("allow_repository_mutation"):
            raise MutationDenied("repository mutation is disabled")
        if assignment is None or not repository:
            raise MutationDenied("mutation requires assignment and repository context")
        if is_terminal(assignment):
            raise MutationDenied("terminal assignment cannot authorize mutation")
        if repository != assignment.get("project"):
            raise MutationDenied("mutation repository does not match assignment project")
        if repository not in set(control.get("repository_allowlist") or []):
            raise MutationDenied("repository is not allowlisted")
        authority = assignment.get("authority") or {}
        authority_state = authority.get("state")
        if authority_state is None and authority.get("approval_matches_record"):
            authority_state = "direct"
        if authority_state not in {"direct", "inherited"}:
            raise MutationDenied("direct or validated inherited authority is required")
        governance_state = assignment.get("governance_state") or (
            assignment.get("candidate") or {}
        ).get("result")
        if governance_state not in {"Executable", "Running"}:
            raise MutationDenied("assignment governance state is not executable")
        lease = self._validate_lease(
            assignment, repository, fencing_token, allow_read_only=False
        )
        return self._decision(operation, repository, assignment, lease)

    def _validate_lease(
        self,
        assignment: dict,
        repository: str | None,
        fencing_token: str | None,
        *,
        allow_read_only: bool,
    ) -> dict:
        lease = load_canonical_lease(self.root, assignment)
        if lease["owner_run_id"] != assignment.get("created_by_run"):
            raise MutationDenied("canonical lease owner does not match assignment run")
        if lease["fencing_token"] != fencing_token:
            raise MutationDenied("canonical lease fencing token mismatch")
        if lease["expires_at_epoch"] <= int(time.time()):
            raise MutationDenied("canonical lease is expired or requires recovery")
        if lease["read_only"] and not allow_read_only:
            raise MutationDenied("read-only lease cannot authorize mutation")
        if repository and not any(
            resource.split(":", 2)[1] == repository
            for resource in lease["resources"]
            if ":" in resource
        ):
            raise MutationDenied("canonical lease does not cover repository")
        return lease

    def _decision(
        self,
        operation: OperationClass,
        repository: str | None,
        assignment: dict | None,
        lease: dict | None = None,
    ) -> GateDecision:
        return GateDecision(
            operation=operation,
            repository=repository,
            assignment_id=(assignment or {}).get("assignment_id"),
            lease_id=(lease or {}).get("lease_id"),
            fencing_token=(lease or {}).get("fencing_token"),
            lease_expires_at_epoch=(lease or {}).get("expires_at_epoch"),
            authority_state=((assignment or {}).get("authority") or {}).get("state"),
            governance_state=(assignment or {}).get("governance_state")
            or ((assignment or {}).get("candidate") or {}).get("result"),
            _issuer=self._issuer,
        )

    def require(
        self,
        decision: GateDecision | None,
        operation: OperationClass,
        *,
        assignment: dict | None = None,
        repository: str | None = None,
    ) -> None:
        if (
            decision is None
            or decision._issuer != self._issuer
            or decision.operation != operation
            or decision.repository != repository
            or decision.assignment_id != (assignment or {}).get("assignment_id")
        ):
            raise MutationDenied("missing or invalid mutation gate decision")
        if operation in {
            OperationClass.MODEL_CALL,
            OperationClass.REPOSITORY,
            OperationClass.GITLAB,
        }:
            if assignment is None:
                raise MutationDenied("effect decision lost assignment context")
            if is_terminal(assignment):
                raise MutationDenied("assignment became terminal after decision")
            if repository and repository != assignment.get("project"):
                raise MutationDenied("assignment project changed after decision")
            control = read_record(
                self.root / "control.json",
                "axis.external-development-supervisor.control",
            )
            if control.get("kill_switch") or control.get("mode") != "enabled":
                raise MutationDenied("supervisor governance changed after decision")
            if operation in {OperationClass.REPOSITORY, OperationClass.GITLAB} and not control.get(
                "allow_repository_mutation"
            ):
                raise MutationDenied("repository mutation was disabled after decision")
            if repository and repository not in set(control.get("repository_allowlist") or []):
                raise MutationDenied("repository allowlist changed after decision")
            if ((assignment.get("authority") or {}).get("state")) != decision.authority_state:
                raise MutationDenied("assignment authority changed after decision")
            governance_state = assignment.get("governance_state") or (
                assignment.get("candidate") or {}
            ).get("result")
            if governance_state != decision.governance_state:
                raise MutationDenied("assignment governance changed after decision")
            lease = self._validate_lease(
                assignment,
                repository,
                decision.fencing_token,
                allow_read_only=operation == OperationClass.MODEL_CALL,
            )
            if (
                lease.get("lease_id") != decision.lease_id
                or lease.get("fencing_token") != decision.fencing_token
                or int(lease.get("expires_at_epoch", 0))
                < int(decision.lease_expires_at_epoch or 0)
                or int(lease.get("expires_at_epoch", 0)) <= int(time.time())
            ):
                raise MutationDenied("canonical lease changed after decision")
            if operation in {OperationClass.REPOSITORY, OperationClass.GITLAB} and lease.get(
                "read_only"
            ):
                raise MutationDenied("canonical lease became read-only")
