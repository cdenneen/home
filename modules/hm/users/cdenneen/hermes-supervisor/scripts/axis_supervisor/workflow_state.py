import fcntl
import json
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .lifecycle import is_integrable
from .models import validate_allowed_path, validate_assignment
from .mutation import MutationGate, OperationClass, load_canonical_lease
from .observability import record_event
from .repository_ownership import (
    assignment_ownership,
    ownership_denial,
    ownership_evidence_matches,
    resolve_repository_ownership,
    validate_repository_ownership,
)
from .schema_registry import (
    RecordVersionError,
    read_record,
    validate_record,
    write_record,
)

HANDOFF_SCHEMA = "axis.external-development-supervisor.implementation-handoff"
INTEGRATION_QUEUE_SCHEMA = "axis.external-development-supervisor.integration-queue"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_paths(paths: list[str] | None) -> set[str]:
    return {
        str(PurePosixPath(str(path).lstrip("./"))).rstrip("/")
        for path in paths or []
        if str(path).strip()
    }


def _overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def classify_main_advance(
    source_main_sha: str | None,
    current_main_sha: str | None,
    implementation_paths: list[str] | None = None,
    main_changed_paths: list[str] | None = None,
    *,
    merge_commit_sha: str | None = None,
) -> str:
    if current_main_sha and merge_commit_sha == current_main_sha:
        return "integrated"
    if not source_main_sha or not current_main_sha or source_main_sha == current_main_sha:
        return "unchanged"
    implementation = _normalized_paths(implementation_paths)
    advanced = _normalized_paths(main_changed_paths)
    if not advanced:
        return "advanced-unassessed"
    if implementation and not any(_overlap(a, b) for a in implementation for b in advanced):
        return "compatible"
    return "repair-required"


def recover_integration_binding(
    root: Path,
    *,
    project: str,
    branch: str,
    sha: str,
    mr_iid: int,
    worktree: str,
) -> tuple[dict | None, str]:
    """Recover one exact integration binding from durable supervisor records.

    Inventory is a projection and may lag or omit a valid assignment/lease pair.
    This recovery is deliberately narrower than normal assignment discovery: every
    custody fact must agree across the assignment, its persisted handoff, and its
    canonical writable lease.  The returned assignment remains subject to the
    normal cycle lease heartbeat, mutation gate, and fresh GitLab inspection.
    """
    if not all((project, branch, sha, mr_iid, worktree)):
        return None, "local custody facts are incomplete"

    candidates = []
    assignments = root / "assignments"
    for path in sorted(assignments.glob("*.json")) if assignments.exists() else []:
        try:
            assignment = validate_assignment(
                json.loads(path.read_text(encoding="utf-8")), root
            )
            if not is_integrable(assignment) or assignment.get("project") != project:
                continue
            worker = assignment.get("worker") or {}
            if (
                worker.get("branch") != branch
                or worker.get("commit") != sha
                or worker.get("worktree") != worktree
                or int((worker.get("handoff") or {}).get("mr_iid") or 0) != mr_iid
            ):
                continue
            assignment_ownership_value = assignment_ownership(
                assignment,
                context=f"integration-binding-recovery:{assignment.get('assignment_id')}",
            )
            if not ownership_evidence_matches(
                assignment.get("repository_ownership"), assignment_ownership_value
            ):
                continue
            handoff = read_record(
                root
                / "implementation-handoffs"
                / f"{assignment['assignment_id']}.json",
                HANDOFF_SCHEMA,
            )
            handoff_ownership = validate_repository_ownership(
                handoff.get("responsibility"),
                handoff.get("repository"),
                context=f"integration-binding-recovery-handoff:{assignment.get('assignment_id')}",
            )
            if (
                handoff.get("assignment_id") != assignment["assignment_id"]
                or handoff.get("repository") != project
                or handoff.get("branch") != branch
                or handoff.get("commit") != sha
                or int(handoff.get("mr_iid") or 0) != mr_iid
                or handoff.get("state") != "ready-for-integration"
                or any(
                    handoff_ownership.get(key) != assignment_ownership_value.get(key)
                    for key in ("responsibility", "repository", "canonical_repository")
                )
                or not ownership_evidence_matches(
                    handoff.get("repository_ownership"), handoff_ownership
                )
            ):
                continue
            lease = load_canonical_lease(root, assignment)
            if lease.get("read_only") or int(lease.get("expires_at_epoch") or 0) <= int(
                time.time()
            ):
                continue
            candidates.append(assignment)
        except Exception:  # A malformed durable record can never grant recovery.
            continue

    if len(candidates) == 1:
        return candidates[0], "recovered exact durable integration binding"
    if len(candidates) > 1:
        return None, "multiple exact durable integration bindings are ambiguous"
    return None, "no exact durable integration binding"


def _actionable_supervisor_mr(merge_request: dict) -> bool:
    return bool(
        merge_request.get("state") == "opened"
        and merge_request.get("target_branch") == "main"
        and merge_request.get("approval_facts_available")
        and (
            merge_request.get("approved")
            or merge_request.get("approval_state") == "approved"
        )
        and merge_request.get("pipeline_facts_available")
        and str(merge_request.get("pipeline_status") or "").lower() == "success"
        and str(merge_request.get("merge_status") or "").lower()
        in {"mergeable", "can_be_merged"}
        and not merge_request.get("draft")
    )


def _exact_owned_integration_facts(inventory: dict, merge_request: dict) -> dict | None:
    """Return proven local custody for one current actionable Supervisor MR.

    This deliberately requires more than the ``hermes/`` branch convention.  The
    collector must observe the exact MR head in one Supervisor-owned branch and
    one clean active worktree before an historical implementation can regain its
    durable integration projection.
    """
    project = str(merge_request.get("project") or "")
    branch_name = str(merge_request.get("source_branch") or "")
    sha = str(merge_request.get("sha") or "")
    iid = int(merge_request.get("iid") or 0)
    if not project or not branch_name or not sha or not iid or not _actionable_supervisor_mr(
        merge_request
    ):
        return None
    local = ((inventory.get("repositories") or {}).get(project) or {}).get(
        "local_facts"
    ) or {}
    branches = [
        value
        for value in local.get("remote_branches") or []
        if isinstance(value, dict)
        and value.get("name") == branch_name
        and value.get("head") == sha
        and value.get("owned_by_supervisor")
    ]
    if len(branches) != 1:
        return None
    branch = branches[0]
    local_mr = branch.get("merge_request") or {}
    worktree = str(branch.get("active_worktree") or "")
    if (
        not worktree
        or int(local_mr.get("iid") or 0) != iid
        or local_mr.get("sha") != sha
        or local_mr.get("state") not in {None, "opened"}
    ):
        return None
    worktrees = [
        value
        for value in local.get("worktrees") or []
        if isinstance(value, dict)
        and value.get("path") == worktree
        and value.get("branch") == branch_name
        and value.get("head") == sha
        and not value.get("dirty")
        and not value.get("integrated_into_default")
    ]
    changed_paths = branch.get("changed_paths") or []
    if len(worktrees) != 1 or not changed_paths:
        return None
    try:
        normalized_paths = [validate_allowed_path(str(path)) for path in changed_paths]
    except ValueError:
        return None
    if len(normalized_paths) != len(set(normalized_paths)):
        return None
    return {
        "project": project,
        "branch": branch_name,
        "sha": sha,
        "mr_iid": iid,
        "mr_url": merge_request.get("web_url"),
        "worktree": worktree,
        "changed_paths": normalized_paths,
        "source_main_sha": local.get("default_remote_head"),
    }


def is_mr_driven_integration_projection(assignment: dict) -> bool:
    """Whether an assignment is the exact inherited projection of one MR.

    A projected assignment represents an already-published Supervisor MR, not a
    controlling GitLab issue.  Its ``work_item`` therefore deliberately uses
    GitLab's MR notation (``project!iid``).  Keep this recognition strict so a
    malformed ordinary assignment cannot bypass the normal issue-closure gate.
    """
    try:
        recovery = assignment.get("integration_recovery") or {}
        worker = assignment.get("worker") or {}
        handoff = worker.get("handoff") or {}
        authority = assignment.get("authority") or {}
        source = authority.get("source") or {}
        project = str(assignment.get("project") or "")
        iid = int(recovery.get("mr_iid") or 0)
        return bool(
            project
            and iid > 0
            and assignment.get("work_item") == f"{project}!{iid}"
            and recovery.get("project") == project
            and recovery.get("branch") == worker.get("branch")
            and recovery.get("sha") == worker.get("commit")
            and recovery.get("worktree") == worker.get("worktree")
            and recovery.get("changed_paths") == worker.get("changed_paths")
            and int(handoff.get("mr_iid") or 0) == iid
            and authority.get("state") == "inherited"
            and source.get("kind")
            == "exact-supervisor-owned-awaiting-integration"
            and source.get("branch") == recovery.get("branch")
            and source.get("commit") == recovery.get("sha")
            and int(source.get("mr_iid") or 0) == iid
        )
    except (AttributeError, TypeError, ValueError):
        return False


def post_main_cleanup_disposition(assignment: dict, cleanup: dict) -> str | None:
    """Classify the observed repository cleanup after a merged MR.

    Exact MR-driven projections inherit a pre-existing local checkout.  Once
    its worktree and remote source branch are gone, retaining the local branch
    is not an outstanding external resource.  The retained branch remains
    observable in the raw cleanup facts; this narrow exception is deliberately
    unavailable to ordinary issue-driven assignments.
    """
    if not (
        cleanup.get("worktree_removed") is True
        and cleanup.get("remote_source_branch_absent") is True
    ):
        return None
    if cleanup.get("local_branch_deleted") is True:
        return "all-owned-branches-removed"
    if (
        cleanup.get("local_branch_deleted") is False
        and is_mr_driven_integration_projection(assignment)
    ):
        return "mr-projection-local-branch-retained"
    return None


def post_merge_cleanup_is_complete(assignment: dict, cleanup: dict) -> bool:
    """Require a settled repository plus release of the canonical lease."""
    return bool(
        post_main_cleanup_disposition(assignment, cleanup)
        and cleanup.get("lease_removed") is True
    )


class WorkflowState:
    def __init__(self, root: Path):
        self.root = root
        self.handoffs = root / "implementation-handoffs"
        self.queue_path = root / "integration-queue.json"
        self.queue_lock_path = root / "integration-queue.lock"
        self.gate = MutationGate(root, source="workflow-state")

    def _authorize(self) -> None:
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)

    def load_queue(self) -> dict:
        if self.queue_path.exists():
            try:
                return read_record(self.queue_path, INTEGRATION_QUEUE_SCHEMA)
            except RecordVersionError:
                legacy = json.loads(self.queue_path.read_text(encoding="utf-8"))
                if legacy.get("schema_version") != "1.0.0":
                    raise
                for item in legacy.get("items") or []:
                    ownership = resolve_repository_ownership(
                        [item.get("responsibility")],
                        item.get("repository"),
                        context=f"integration-queue-v1-migration:{item.get('assignment_id')}",
                        allow_repository_inference=True,
                    )
                    item["responsibility"] = ownership["responsibility"]
                    item["repository_ownership"] = ownership
                legacy["schema_version"] = "2.0.0"
                return legacy
        return {
            "schema": INTEGRATION_QUEUE_SCHEMA,
            "schema_version": "2.0.0",
            "updated_at": utc_now(),
            "items": [],
        }

    def write_queue(self, queue: dict) -> None:
        queue["updated_at"] = utc_now()
        self._authorize()
        write_record(self.queue_path, queue, INTEGRATION_QUEUE_SCHEMA)

    def mutate_queue(self, mutation: Callable[[dict], object]) -> object:
        self.queue_lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.queue_lock_path.open("a", encoding="utf-8") as lock:
            os.chmod(self.queue_lock_path, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            queue = self.load_queue()
            result = mutation(queue)
            self.write_queue(queue)
            return result

    def persist_handoff(self, assignment: dict, result: dict) -> dict:
        ownership = assignment_ownership(
            assignment,
            context=f"implementation-handoff:{assignment.get('assignment_id')}",
        )
        worker_handoff = result.get("handoff") or {}
        handoff = {
            "schema": HANDOFF_SCHEMA,
            "schema_version": "2.0.0",
            "assignment_id": assignment["assignment_id"],
            "work_item": assignment["work_item"],
            "repository": assignment["project"],
            "responsibility": ownership["responsibility"],
            "repository_ownership": ownership,
            "branch": str(result.get("branch") or ""),
            "commit": str(result.get("commit") or ""),
            "allowed_paths": list(assignment.get("allowed_paths") or []),
            "changed_paths": list(result.get("changed_paths") or []),
            "tests": list(worker_handoff.get("tests") or []),
            "mr_iid": worker_handoff.get("mr_iid"),
            "mr_url": worker_handoff.get("mr_url"),
            "source_main_sha": (assignment.get("source_item") or {}).get(
                "repository_head"
            ),
            "created_at": utc_now(),
            "state": "ready-for-integration",
        }
        self._authorize()
        write_record(
            self.handoffs / f"{assignment['assignment_id']}.json",
            handoff,
            HANDOFF_SCHEMA,
        )
        return handoff

    def _adopted_assignment(self, facts: dict) -> dict:
        assignment_id = f"integration-mr-{facts['mr_iid']}-{facts['sha'][:12]}"
        assignment = {
            "schema": "axis.external-development-supervisor.assignment",
            "schema_version": "4.0.0",
            "assignment_id": assignment_id,
            "assignment_type": "code-implementation",
            "result_state": "awaiting-integration",
            "work_item_disposition": "requires-integration",
            "lifecycle_state": "awaiting-integration",
            "project": facts["project"],
            "responsibility": "axis-runtime/product",
            "work_item": f"{facts['project']}!{facts['mr_iid']}",
            "planning_record": None,
            "allowed_paths": facts["changed_paths"],
            "required_tests": [],
            "action_contract": None,
            "mutation_grant_id": None,
            "mutation_grant_uri": None,
            "created_by_run": f"integration-recovery-{facts['mr_iid']}-{facts['sha'][:12]}",
            "lease_id": None,
            "lease_uri": None,
            "source_item": {"repository_head": facts["source_main_sha"]},
            # This is custody inherited from an already-published Supervisor
            # branch, not a new implementation grant.  The normal integration
            # mutation gate still performs fresh GitLab and lease checks.
            "authority": {
                "state": "inherited",
                "source": {
                    "kind": "exact-supervisor-owned-awaiting-integration",
                    "branch": facts["branch"],
                    "commit": facts["sha"],
                    "mr_iid": facts["mr_iid"],
                },
                "reason": "exact current supervisor branch, worktree, and MR custody",
            },
            "governance_state": "Executable",
            "integration_recovery": dict(facts),
            "worker": {
                "branch": facts["branch"],
                "commit": facts["sha"],
                "worktree": facts["worktree"],
                "changed_paths": facts["changed_paths"],
                "handoff": {
                    "mr_iid": facts["mr_iid"],
                    "mr_url": facts["mr_url"],
                    "tests": [],
                },
            },
        }
        assignment["repository_ownership"] = assignment_ownership(
            assignment,
            context=f"integration-recovery:{assignment_id}",
        )
        return validate_assignment(assignment, self.root)

    @staticmethod
    def _matches_adopted_facts(assignment: dict, facts: dict) -> bool:
        recovery = assignment.get("integration_recovery") or {}
        worker = assignment.get("worker") or {}
        handoff = worker.get("handoff") or {}
        try:
            ownership = assignment_ownership(
                assignment,
                context=f"integration-recovery-match:{assignment.get('assignment_id')}",
            )
        except Exception:
            return False
        return bool(
            is_integrable(assignment)
            and assignment.get("project") == facts["project"]
            and assignment.get("work_item") == f"{facts['project']}!{facts['mr_iid']}"
            and worker.get("branch") == facts["branch"]
            and worker.get("commit") == facts["sha"]
            and worker.get("worktree") == facts["worktree"]
            and worker.get("changed_paths") == facts["changed_paths"]
            and int(handoff.get("mr_iid") or 0) == facts["mr_iid"]
            and recovery == facts
            and ownership_evidence_matches(
                assignment.get("repository_ownership"), ownership
            )
        )

    def _existing_handoff_matches(self, assignment: dict, facts: dict) -> bool:
        path = self.handoffs / f"{assignment['assignment_id']}.json"
        if not path.exists():
            return True
        try:
            handoff = read_record(path, HANDOFF_SCHEMA)
            ownership = assignment_ownership(
                assignment,
                context=f"integration-recovery-handoff:{assignment['assignment_id']}",
            )
            return bool(
                handoff.get("assignment_id") == assignment["assignment_id"]
                and handoff.get("repository") == facts["project"]
                and handoff.get("branch") == facts["branch"]
                and handoff.get("commit") == facts["sha"]
                and int(handoff.get("mr_iid") or 0) == facts["mr_iid"]
                and handoff.get("state") == "ready-for-integration"
                and handoff.get("allowed_paths") == facts["changed_paths"]
                and handoff.get("changed_paths") == facts["changed_paths"]
                and ownership_evidence_matches(
                    handoff.get("repository_ownership"), ownership
                )
            )
        except Exception:
            return False

    def _record_projection_failure(
        self,
        assignment: dict,
        facts: dict,
        *,
        stage: str,
        error: BaseException | str,
    ) -> None:
        """Persist why exact MR custody could not become a writable binding.

        The projection is deliberately fail-closed: an exact local observation
        alone never authorizes integration.  It must not, however, turn a lease
        controller limit or malformed durable record into an invisible return to
        inspection-only lanes.  Operational events are the durable diagnostic
        surface already consumed by the dashboard and runbook tooling.
        """
        error_text = (
            f"{type(error).__name__}: {error}"
            if isinstance(error, BaseException)
            else error
        )
        record_event(
            self.root,
            "integration_projection_failed",
            assignment=assignment,
            details={
                "disposition": "inspection-only",
                "failure_stage": stage,
                "failure_classification": (
                    type(error).__name__
                    if isinstance(error, BaseException)
                    else "projection-precondition"
                ),
                "error": error_text,
                "project": facts["project"],
                "mr_iid": facts["mr_iid"],
                "branch": facts["branch"],
                "commit": facts["sha"],
                "worktree": facts["worktree"],
                "retryable": True,
            },
            source="workflow-state",
            notify=True,
        )

    def project_owned_awaiting_integrations(
        self,
        inventory: dict,
        *,
        reviewer: str,
        claim_lease: Callable[[dict], dict],
    ) -> list[tuple[dict, dict]]:
        """Persist exact current Supervisor MR custody through the normal handoff path.

        This is intentionally a projection repair, not generic MR adoption.  It
        considers only actionable MRs whose local branch/worktree/MR observations
        are exact, and it refuses to overwrite a stale or conflicting durable
        handoff.  Lease acquisition is delegated to the canonical controller.
        """
        projected = []
        assignments = self.root / "assignments"
        merge_requests = sorted(
            (
                value
                for value in inventory.get("open_merge_requests") or []
                if isinstance(value, dict)
            ),
            key=lambda value: (str(value.get("project") or ""), int(value.get("iid") or 0)),
        )
        for merge_request in merge_requests:
            facts = _exact_owned_integration_facts(inventory, merge_request)
            if facts is None:
                continue
            assignment_id = f"integration-mr-{facts['mr_iid']}-{facts['sha'][:12]}"
            path = assignments / f"{assignment_id}.json"
            assignment = {
                "assignment_id": assignment_id,
                "project": facts["project"],
                "work_item": f"{facts['project']}!{facts['mr_iid']}",
                "lifecycle_state": "awaiting-integration",
            }
            created_assignment = False
            stage = "construct-adopted-assignment"
            try:
                proposed = self._adopted_assignment(facts)
                assignment = proposed
                stage = "validate-existing-assignment"
                if path.exists():
                    assignment = validate_assignment(
                        json.loads(path.read_text(encoding="utf-8")), self.root
                    )
                    if not self._matches_adopted_facts(assignment, facts):
                        self._record_projection_failure(
                            assignment,
                            facts,
                            stage=stage,
                            error="existing assignment does not match current exact custody",
                        )
                        continue
                else:
                    assignment = proposed
                    stage = "persist-assignment"
                    self._authorize()
                    write_record(
                        path,
                        assignment,
                        "axis.external-development-supervisor.assignment",
                    )
                    created_assignment = True
                stage = "validate-existing-handoff"
                if not self._existing_handoff_matches(assignment, facts):
                    self._record_projection_failure(
                        assignment,
                        facts,
                        stage=stage,
                        error="existing handoff does not match current exact custody",
                    )
                    continue
                stage = "load-canonical-lease"
                try:
                    lease = load_canonical_lease(self.root, assignment)
                except Exception as lease_error:
                    if assignment.get("lease_id") or assignment.get("lease_uri"):
                        self._record_projection_failure(
                            assignment,
                            facts,
                            stage=stage,
                            error=lease_error,
                        )
                        continue
                    stage = "claim-canonical-lease"
                    lease = claim_lease(assignment)
                    stage = "validate-claimed-lease"
                    if (
                        lease.get("lease_id") != assignment["assignment_id"]
                        or lease.get("assignment_id") != assignment["assignment_id"]
                        or lease.get("owner_run_id") != assignment.get("created_by_run")
                        or lease.get("read_only")
                        or int(lease.get("expires_at_epoch") or 0) <= int(time.time())
                        or f"repo:{facts['project']}" not in (lease.get("resources") or [])
                    ):
                        self._record_projection_failure(
                            assignment,
                            facts,
                            stage=stage,
                            error="canonical lease claim did not return an exact writable lease",
                        )
                        continue
                    assignment["lease_id"] = lease["lease_id"]
                    assignment["lease_uri"] = (
                        self.root / "leases" / lease["lease_id"] / "lease.json"
                    ).resolve().as_uri()
                    self._authorize()
                    write_record(
                        path,
                        assignment,
                        "axis.external-development-supervisor.assignment",
                    )
                    stage = "reload-canonical-lease"
                    lease = load_canonical_lease(self.root, assignment)
                stage = "validate-canonical-lease"
                if (
                    lease.get("read_only")
                    or lease.get("owner_run_id") != assignment.get("created_by_run")
                    or int(lease.get("expires_at_epoch") or 0) <= int(time.time())
                    or f"repo:{facts['project']}" not in (lease.get("resources") or [])
                ):
                    self._record_projection_failure(
                        assignment,
                        facts,
                        stage=stage,
                        error="canonical lease does not authorize this exact projection",
                    )
                    continue
                result = {
                    "branch": facts["branch"],
                    "commit": facts["sha"],
                    "worktree": facts["worktree"],
                    "changed_paths": facts["changed_paths"],
                    "handoff": {
                        "mr_iid": facts["mr_iid"],
                        "mr_url": facts["mr_url"],
                        "tests": [],
                    },
                }
                stage = "persist-handoff"
                handoff = self.persist_handoff(assignment, result)
                stage = "enqueue-integration"
                self.enqueue(assignment, handoff, reviewer)
                projected.append((assignment, lease))
            except Exception as exc:
                # Do not let a failed claim occupy dispatcher capacity.  The
                # controller requires an assignment record, so a candidate must
                # be written before claiming; when that first claim fails, roll
                # back only the record created by this attempt and retain the
                # durable incident for the next deterministic retry.
                if created_assignment and not assignment.get("lease_id"):
                    path.unlink(missing_ok=True)
                self._record_projection_failure(
                    assignment,
                    facts,
                    stage=stage,
                    error=exc,
                )
                continue
        return projected

    def adapt_handoff(self, handoff: dict) -> dict:
        value = dict(handoff)
        if value.get("schema") != HANDOFF_SCHEMA:
            validate_record(value, HANDOFF_SCHEMA)
        if value.get("schema_version") == "1.0.0":
            ownership = resolve_repository_ownership(
                [value.get("responsibility")],
                value.get("repository"),
                context=f"implementation-handoff-v1-migration:{value.get('assignment_id')}",
                allow_repository_inference=True,
            )
            value["schema_version"] = "2.0.0"
            value["responsibility"] = ownership["responsibility"]
            value["repository_ownership"] = ownership
        validate_record(value, HANDOFF_SCHEMA)
        return value

    def load_handoff(self, assignment_id: str) -> dict:
        path = self.handoffs / f"{assignment_id}.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        migrated = self.adapt_handoff(value)
        if migrated != value:
            self._authorize()
            write_record(path, migrated, HANDOFF_SCHEMA)
        return migrated

    def enqueue(
        self, assignment: dict, handoff: dict, reviewer: str
    ) -> dict:
        original_handoff = handoff
        handoff = self.adapt_handoff(handoff)
        handoff_path = self.handoffs / f"{assignment['assignment_id']}.json"
        if handoff != original_handoff or not handoff_path.exists():
            self._authorize()
            write_record(handoff_path, handoff, HANDOFF_SCHEMA)
        ownership = assignment_ownership(
            assignment,
            context=f"reviewer-handoff:{assignment.get('assignment_id')}",
        )
        handoff_ownership = validate_repository_ownership(
            handoff.get("responsibility"),
            handoff.get("repository"),
            context=f"reviewer-handoff-record:{assignment.get('assignment_id')}",
        )
        if any(
            handoff_ownership.get(key) != ownership.get(key)
            for key in ("responsibility", "repository", "canonical_repository")
        ) or not ownership_evidence_matches(
            handoff.get("repository_ownership"), handoff_ownership
        ):
            raise ownership_denial(
                ownership,
                context=f"reviewer-handoff:{assignment.get('assignment_id')}",
                reason="handoff-does-not-match-assignment-ownership",
                actual={
                    "responsibility": handoff.get("responsibility"),
                    "repository": handoff.get("repository"),
                    "repository_ownership": handoff.get("repository_ownership"),
                },
            )
        now = utc_now()
        item = {
            "assignment_id": assignment["assignment_id"],
            "work_item": assignment["work_item"],
            "repository": assignment["project"],
            "responsibility": ownership["responsibility"],
            "repository_ownership": ownership,
            "handoff_uri": (
                self.handoffs / f"{assignment['assignment_id']}.json"
            ).resolve().as_uri(),
            "mr_iid": handoff.get("mr_iid"),
            "mr_url": handoff.get("mr_url"),
            "reviewer": reviewer,
            "state": "awaiting-review",
            "main_advance": "unchanged",
            "enqueued_at": now,
            "updated_at": now,
            "last_error": None,
        }

        def append(queue: dict) -> dict:
            queue["items"] = [
                existing
                for existing in queue["items"]
                if existing["assignment_id"] != assignment["assignment_id"]
            ] + [item]
            return item

        return self.mutate_queue(append)  # type: ignore[return-value]

    def update_integration(
        self,
        assignment_id: str,
        *,
        state: str,
        main_advance: str | None = None,
        last_error: str | None = None,
    ) -> dict | None:
        def update(queue: dict) -> dict | None:
            item = next(
                (
                    value
                    for value in queue["items"]
                    if value["assignment_id"] == assignment_id
                ),
                None,
            )
            if item is None:
                return None
            item["state"] = state
            item["updated_at"] = utc_now()
            item["last_error"] = last_error
            if main_advance is not None:
                item["main_advance"] = main_advance
            return item

        return self.mutate_queue(update)  # type: ignore[return-value]
