import hashlib
import json
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from .accounting import AccountingLedger
from .canary import current_main_sha
from .models import validate_allowed_path
from .repository_ownership import (
    RepositoryOwnershipDenied,
    assignment_ownership,
    ownership_denial,
    ownership_evidence_matches,
)
from .schema_registry import read_record, validate_record, write_record

SCHEMA = "axis.external-development-supervisor.mutation-grant"


class AssignmentGrantDenied(PermissionError):
    def __init__(self, message: str, evidence: dict | None = None):
        self.evidence = evidence
        super().__init__(message)


def grant_path(root: Path, assignment_id: str) -> Path:
    return root / "mutation-grants" / assignment_id / "grant.json"


def canonical_grant_path(root: Path, assignment: dict) -> Path:
    grant_id = str(assignment.get("mutation_grant_id") or "")
    grant_uri = str(assignment.get("mutation_grant_uri") or "")
    expected_id = f"grant-{assignment.get('assignment_id')}"
    if grant_id != expected_id or not grant_uri:
        raise AssignmentGrantDenied("assignment has no canonical mutation grant reference")
    parsed = urlparse(grant_uri)
    if parsed.scheme != "file" or parsed.netloc:
        raise AssignmentGrantDenied("mutation_grant_uri must be a local file URI")
    path = Path(unquote(parsed.path)).resolve()
    expected = grant_path(root, assignment["assignment_id"]).resolve()
    if path != expected:
        raise AssignmentGrantDenied("mutation_grant_uri does not identify the canonical grant")
    return path


def immutable_scope(grant: dict) -> dict:
    return {
        key: grant[key]
        for key in (
            "assignment_id",
            "assignment_type",
            "work_item",
            "responsibility",
            "repository",
            "repository_ownership",
            "source_sha",
            "source_fingerprint",
            "branch_prefix",
            "branch",
            "worktree",
            "path_policy",
            "allowed_paths",
            "prohibited_paths",
            "permitted_operation_classes",
            "permitted_git_operations",
            "permitted_gitlab_operations",
            "required_tests",
            "max_model_calls",
            "max_retries",
            "max_prompt_bytes",
            "max_cost_usd",
            "approval_source",
            "required_evidence",
            "integration_conditions",
        )
    }


def scope_digest(grant: dict) -> str:
    payload = json.dumps(immutable_scope(grant), sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def create_grant(root: Path, assignment: dict, control: dict) -> dict:
    assignment_id = assignment["assignment_id"]
    planning = assignment.get("planning_record") or {}
    authority = assignment.get("authority") or {}
    authority_state = authority.get("state")
    if assignment.get("assignment_type") not in {
        "governance-document-mutation",
        "code-implementation",
        "ci-integration-repair",
    }:
        raise AssignmentGrantDenied("assignment type is not grant-eligible")
    try:
        ownership = assignment_ownership(
            assignment, context=f"mutation-grant-create:{assignment_id}"
        )
    except RepositoryOwnershipDenied as exc:
        raise AssignmentGrantDenied(str(exc), exc.evidence) from exc
    if authority_state not in {"direct", "inherited"}:
        raise AssignmentGrantDenied("bounded mutation grant requires direct or inherited authority")
    if not planning.get("digest") or not planning.get("approval_note"):
        raise AssignmentGrantDenied("bounded mutation grant requires an exact approved PlanningRecord")
    allowed_paths = sorted(
        {validate_allowed_path(path) for path in assignment.get("allowed_paths") or []}
    )
    required_tests = list(dict.fromkeys(assignment.get("required_tests") or []))
    if not allowed_paths or not required_tests:
        raise AssignmentGrantDenied("bounded mutation grant requires exact paths and tests")
    authority_facts = (assignment.get("source_item") or {}).get(
        "authority_facts"
    ) or {}
    approved_assignment_type = authority_facts.get("approved_assignment_type")
    assignment_type_matches = approved_assignment_type == assignment[
        "assignment_type"
    ] or (
        approved_assignment_type == "code-implementation"
        and assignment["assignment_type"] == "ci-integration-repair"
    )
    if authority_state == "direct" and (
        not assignment_type_matches
        or sorted(authority_facts.get("approved_allowed_paths") or [])
        != allowed_paths
        or list(authority_facts.get("approved_required_tests") or [])
        != required_tests
    ):
        raise AssignmentGrantDenied(
            "assignment scope does not match the approved PlanningRecord"
        )
    source_sha = str((assignment.get("source_item") or {}).get("repository_head") or "")
    if len(source_sha) != 40:
        raise AssignmentGrantDenied("bounded mutation grant requires an exact source SHA")
    now = int(time.time())
    grant = {
        "schema": SCHEMA,
        "schema_version": "1.0.0",
        "grant_id": f"grant-{assignment_id}",
        "scope_digest": "sha256:" + "0" * 64,
        "status": "active",
        "assignment_id": assignment_id,
        "assignment_type": assignment["assignment_type"],
        "work_item": assignment["work_item"],
        "responsibility": ownership["responsibility"],
        "repository": ownership["canonical_repository"],
        "repository_ownership": ownership,
        "source_sha": source_sha,
        "source_fingerprint": assignment["source_fingerprint"],
        "branch_prefix": "hermes/",
        "branch": f"hermes/{assignment_id}",
        "worktree": str(root / "worktrees" / assignment_id),
        "path_policy": "exact-set",
        "allowed_paths": allowed_paths,
        "prohibited_paths": [".git/**", ".sops.yaml", "secrets/**"],
        "permitted_operation_classes": [
            "model-call",
            "repository-mutation",
            "gitlab-mutation",
        ],
        "permitted_git_operations": [
            "clone",
            "configure-remote",
            "configure-local-identity",
            "create-owned-branch",
            "apply-bounded-patch",
            "stage-allowed-paths",
            "commit",
            "push-owned-branch",
            "fetch",
            "checkout-merged-main",
            "remove-owned-worktree",
            "delete-owned-branch",
            "provision-test-environment",
            "run-required-tests",
        ],
        "permitted_gitlab_operations": [
            "create-owned-mr",
            "update-owned-mr",
            "merge-reviewed-mr",
            "record-evidence",
            "close-controlling-work-item",
        ],
        "required_tests": required_tests,
        "max_model_calls": int(control.get("mutation_grant_max_model_calls", 2)),
        "max_retries": int(control.get("mutation_grant_max_retries", 1)),
        "max_prompt_bytes": int(control.get("mutation_grant_max_prompt_bytes", 200000)),
        "max_cost_usd": float(control.get("mutation_grant_max_cost_usd", 5.0)),
        "issued_at_epoch": now,
        "expires_at_epoch": now
        + int(control.get("mutation_grant_ttl_seconds", 21600)),
        "approval_source": {
            "authority_state": authority_state,
            "source_refs": list(
                dict.fromkeys(
                    [
                        str(planning["approval_note"]),
                        *[str(value) for value in authority.get("source") or []],
                    ]
                )
            ),
            "planning_digest": planning["digest"],
            "planning_revision": int(planning["revision"]),
            "approval_note": planning["approval_note"],
        },
        "required_evidence": [
            "changed-path set equals grant allowed_paths",
            "required tests pass before commit",
            "pipeline succeeds for exact branch head",
            "merge targets protected main with exact reviewed SHA",
            "required tests pass on merged main",
            "GitLab work item records completion evidence",
            "owned branch, worktree, lease, and grant are cleaned",
        ],
        "integration_conditions": {
            "target_branch": "main",
            "pipeline_status": "success",
            "approvals_satisfied": True,
            "discussions_resolved": True,
            "no_conflicts": True,
            "exact_head_sha": True,
            "post_main_tests": True,
            "fresh_cycle_recognition": True,
        },
        "mr_iid": None,
        "mr_sha": None,
        "events": [{"event": "grant-created", "recorded_at_epoch": now}],
    }
    grant["scope_digest"] = scope_digest(grant)
    validate_record(grant, SCHEMA)
    path = grant_path(root, assignment_id)
    write_record(path, grant, SCHEMA)
    assignment["mutation_grant_id"] = grant["grant_id"]
    assignment["mutation_grant_uri"] = path.resolve().as_uri()
    return grant


def load_grant(root: Path, assignment: dict) -> dict:
    return read_record(canonical_grant_path(root, assignment), SCHEMA)


def merged_recovery_matches(grant: dict, assignment: dict, mr: dict | None, main_sha: str) -> bool:
    worker = assignment.get("worker") or {}
    handoff = worker.get("handoff") or {}
    return bool(
        mr
        and grant.get("mr_iid")
        and mr.get("state") == "merged"
        and int(mr.get("iid") or 0) == int(grant["mr_iid"])
        and int(handoff.get("mr_iid") or 0) == int(grant["mr_iid"])
        and mr.get("target_branch") == "main"
        and mr.get("source_branch") == grant["branch"]
        and mr.get("sha") == grant.get("mr_sha") == worker.get("commit")
        and (mr.get("diff_refs") or {}).get("base_sha") == grant["source_sha"]
        and mr.get("merge_commit_sha") == main_sha
    )


def validate_grant(
    root: Path,
    assignment: dict,
    operation: str,
    repository: str | None,
    *,
    effect: str | None = None,
    merged_mr: dict | None = None,
) -> dict:
    grant = load_grant(root, assignment)
    try:
        ownership = assignment_ownership(
            assignment,
            context=f"mutation-grant-validate:{assignment.get('assignment_id')}",
        )
    except RepositoryOwnershipDenied as exc:
        raise AssignmentGrantDenied(str(exc), exc.evidence) from exc
    now = int(time.time())
    if grant["scope_digest"] != scope_digest(grant):
        raise AssignmentGrantDenied("mutation grant scope digest mismatch")
    if grant["status"] != "active" or now >= int(grant["expires_at_epoch"]):
        raise AssignmentGrantDenied("mutation grant is inactive or expired")
    if grant["assignment_id"] != assignment.get("assignment_id"):
        raise AssignmentGrantDenied("mutation grant assignment mismatch")
    if grant["assignment_type"] != assignment.get("assignment_type"):
        raise AssignmentGrantDenied("mutation grant assignment type mismatch")
    if grant["work_item"] != assignment.get("work_item"):
        raise AssignmentGrantDenied("mutation grant work item mismatch")
    if (
        grant["responsibility"] != ownership["responsibility"]
        or not ownership_evidence_matches(grant["repository_ownership"], ownership)
    ):
        denied = ownership_denial(
            ownership,
            context=f"mutation-grant-record:{assignment.get('assignment_id')}",
            reason="mutation-grant-ownership-evidence-mismatch",
            actual={
                "responsibility": grant.get("responsibility"),
                "repository": grant.get("repository"),
                "repository_ownership": grant.get("repository_ownership"),
            },
        )
        raise AssignmentGrantDenied(str(denied), denied.evidence) from denied
    if grant["repository"] != repository or repository != assignment.get("project"):
        raise AssignmentGrantDenied("mutation grant repository mismatch")
    if grant["source_fingerprint"] != assignment.get("source_fingerprint"):
        raise AssignmentGrantDenied("mutation grant source fingerprint mismatch")
    planning = assignment.get("planning_record") or {}
    authority_state = (assignment.get("authority") or {}).get("state")
    approval = grant["approval_source"]
    if authority_state not in {"direct", "inherited"} or approval[
        "authority_state"
    ] != authority_state:
        raise AssignmentGrantDenied("mutation grant authority changed")
    if (
        planning.get("digest") != approval["planning_digest"]
        or int(planning.get("revision") or 0) != int(approval["planning_revision"])
        or planning.get("approval_note") != approval["approval_note"]
    ):
        raise AssignmentGrantDenied("mutation grant PlanningRecord changed")
    current_sha = current_main_sha(grant["repository"])
    if current_sha != grant["source_sha"] and not merged_recovery_matches(
        grant, assignment, merged_mr, current_sha
    ):
        raise AssignmentGrantDenied("mutation grant source SHA is stale")
    if grant["branch"] != f"hermes/{assignment['assignment_id']}":
        raise AssignmentGrantDenied("mutation grant branch mismatch")
    if Path(grant["worktree"]).resolve() != (
        root / "worktrees" / assignment["assignment_id"]
    ).resolve():
        raise AssignmentGrantDenied("mutation grant worktree mismatch")
    if grant["allowed_paths"] != sorted(
        {validate_allowed_path(path) for path in assignment.get("allowed_paths") or []}
    ):
        raise AssignmentGrantDenied("mutation grant path scope mismatch")
    if grant["required_tests"] != list(
        dict.fromkeys(assignment.get("required_tests") or [])
    ):
        raise AssignmentGrantDenied("mutation grant test scope mismatch")
    if operation not in grant["permitted_operation_classes"]:
        raise AssignmentGrantDenied("operation class is outside mutation grant")
    if operation == "model-call":
        used = AccountingLedger(root).model_attempts_for_assignment(
            assignment["assignment_id"]
        )
        if used >= int(grant["max_model_calls"]):
            raise AssignmentGrantDenied("mutation grant model-call budget is exhausted")
    if operation == "repository-mutation":
        if not effect or effect not in grant["permitted_git_operations"]:
            raise AssignmentGrantDenied("Git operation is outside mutation grant")
    if operation == "gitlab-mutation":
        if not effect or effect not in grant["permitted_gitlab_operations"]:
            raise AssignmentGrantDenied("GitLab operation is outside mutation grant")
    return grant


def append_event(root: Path, assignment: dict, event: dict) -> dict:
    grant = load_grant(root, assignment)
    grant["events"].append({"recorded_at_epoch": int(time.time()), **event})
    write_record(canonical_grant_path(root, assignment), grant, SCHEMA)
    return grant


def bind_mr(root: Path, assignment: dict, mr: dict) -> dict:
    grant = load_grant(root, assignment)
    grant["mr_iid"] = int(mr["iid"])
    grant["mr_sha"] = str(mr["sha"])
    grant["events"].append(
        {
            "event": "merge-request-bound",
            "recorded_at_epoch": int(time.time()),
            "iid": int(mr["iid"]),
            "sha": str(mr["sha"]),
            "url": mr.get("web_url"),
        }
    )
    write_record(canonical_grant_path(root, assignment), grant, SCHEMA)
    return grant


def finish_grant(root: Path, assignment: dict, status: str) -> dict:
    if status not in {"consumed", "failed", "expired", "revoked"}:
        raise ValueError("invalid mutation grant terminal status")
    grant = load_grant(root, assignment)
    grant["status"] = status
    grant["expires_at_epoch"] = min(int(grant["expires_at_epoch"]), int(time.time()))
    grant["events"].append(
        {"event": f"grant-{status}", "recorded_at_epoch": int(time.time())}
    )
    write_record(canonical_grant_path(root, assignment), grant, SCHEMA)
    return grant
