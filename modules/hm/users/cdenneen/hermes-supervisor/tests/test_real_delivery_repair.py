import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from axis_supervisor.dashboard import _recent_lines  # noqa: E402
from axis_supervisor.collector import normalize_open_merge_request  # noqa: E402
from axis_supervisor.merge_lanes import (  # noqa: E402
    GATED_INTEGRATION,
    INTEGRATION,
    INSPECTION_ONLY,
    consume_next,
    reconcile,
)
from axis_supervisor.semantic_escalation import (  # noqa: E402
    exclude_pending,
    pending,
    quarantine_failed_assignment,
)
from axis_supervisor.repository_ownership import assignment_ownership  # noqa: E402
from axis_supervisor.schema_registry import write_record  # noqa: E402


def _semantic_assignment(repository_head: str = "a" * 40) -> dict:
    return {
        "assignment_id": "semantic-250",
        "assignment_type": "read-only-analysis",
        "target_ref": "ghostspace/axis-governance#250",
        "source_fingerprint": "source-250",
        "source_item": {
            "repository_head": repository_head,
            "authority_facts": {
                "approval_required": True,
                "decision_escalate": True,
                "record_revision": 7,
                "record_digest": "sha256:" + "a" * 64,
            },
        },
        "error": "ValueError: semantic response contract failed",
    }


def test_unchanged_escalation_failure_is_durably_quarantined(tmp_path: Path):
    failed = _semantic_assignment()
    quarantine = quarantine_failed_assignment(tmp_path, failed)

    assert quarantine is not None
    assert quarantine["state"] == "pending-human-escalation"
    assert pending(tmp_path, _semantic_assignment()) == quarantine
    assert pending(tmp_path, _semantic_assignment("b" * 40)) is None
    queue = [
        _semantic_assignment() | {"ref": "semantic-decomposition:governance-250"},
        _semantic_assignment("b" * 40)
        | {"ref": "semantic-decomposition:axis-29", "target_ref": "ghostspace/axis#29"},
    ]
    assert [entry["target_ref"] for entry in exclude_pending(tmp_path, queue)] == [
        "ghostspace/axis#29"
    ]


def test_internal_retry_never_appears_as_product_progress():
    assert _recent_lines(
        [{"event_type": "assignment_retry", "work_item": "ghostspace/axis#29"}]
    ) == ["• No material product change in the current activity window"]


class FakeIntegrator:
    def __init__(self):
        self.calls = []

    def inspect_mr(self, repository: str, iid: int, *, responsibility: str) -> dict:
        self.calls.append((repository, iid, responsibility))
        return {
            "merge_ready": True,
            "pipeline": {"status": "success"},
            "review_pending": False,
        }


def _actionable_mr(iid: int) -> dict:
    return {
        "project": "ghostspace/axis",
        "iid": iid,
        "state": "opened",
        "target_branch": "main",
        "merge_status": "mergeable",
        "pipeline_status": "success",
        "pipeline_facts_available": True,
        "approved": True,
        "approval_facts_available": True,
        "draft": False,
        "sha": f"sha-{iid}",
        "web_url": f"https://gitlab.example/ghostspace/axis/-/merge_requests/{iid}",
    }


def _supervisor_assignment(iid: int, branch: str, sha: str) -> dict:
    return {
        "assignment_id": f"assignment-{iid}",
        "project": "ghostspace/axis",
        "lifecycle_state": "awaiting-integration",
        "lease_id": f"lease-{iid}",
        "worker": {
            "branch": branch,
            "commit": sha,
            "worktree": f"/supervisor/worktrees/assignment-{iid}",
            "handoff": {"mr_iid": iid},
        },
    }


def _custody_inventory(mrs: list[dict], owned_iids: set[int]) -> dict:
    assignments = []
    leases = []
    branches = []
    for mr in mrs:
        iid = int(mr["iid"])
        branch = str(mr.get("source_branch") or f"macos-{iid}")
        if iid in owned_iids:
            assignment = _supervisor_assignment(iid, branch, str(mr["sha"]))
            assignments.append(assignment)
            leases.append(
                {
                    "assignment_id": assignment["assignment_id"],
                    "lease_id": assignment["lease_id"],
                    "read_only": False,
                }
            )
            worktree = assignment["worker"]["worktree"]
        else:
            worktree = None
        branches.append(
            {
                "name": branch,
                "head": mr["sha"],
                "owned_by_supervisor": iid in owned_iids,
                "active_worktree": worktree,
                "merge_request": {"iid": iid, "sha": mr["sha"]},
            }
        )
    return {
        "open_merge_requests": mrs,
        "repositories": {
            "ghostspace/axis": {
                "local_facts": {
                    "remote_branches": branches,
                    "worktrees": [
                        {
                            "path": branch["active_worktree"],
                            "branch": branch["name"],
                            "head": branch["head"],
                        }
                        for branch in branches
                        if branch["active_worktree"]
                    ],
                }
            }
        },
        "supervisor_assignments": assignments,
        "active_leases": leases,
    }


def _write_durable_integration_binding(
    root: Path,
    *,
    assignment_id: str,
    branch: str,
    sha: str,
    iid: int,
    worktree: str,
):
    assignment = {
        "schema": "axis.external-development-supervisor.assignment",
        "schema_version": "4.0.0",
        "assignment_id": assignment_id,
        "assignment_type": "code-implementation",
        "result_state": "awaiting-integration",
        "work_item_disposition": "requires-integration",
        "lifecycle_state": "awaiting-integration",
        "project": "ghostspace/axis",
        "responsibility": "axis-runtime/product",
        "work_item": "ghostspace/axis#155",
        "planning_record": None,
        "allowed_paths": ["src/axis/cli.py"],
        "required_tests": ["pytest -q"],
        "action_contract": None,
        "mutation_grant_id": None,
        "mutation_grant_uri": None,
        "created_by_run": "run-155",
        "lease_id": f"lease-{assignment_id}",
        "lease_uri": (
            root / "leases" / f"lease-{assignment_id}" / "lease.json"
        ).resolve().as_uri(),
        "worker": {
            "branch": branch,
            "commit": sha,
            "worktree": worktree,
            "handoff": {"mr_iid": iid},
        },
    }
    assignment["repository_ownership"] = assignment_ownership(
        assignment, context=f"test-binding:{assignment_id}"
    )
    handoff = {
        "schema": "axis.external-development-supervisor.implementation-handoff",
        "schema_version": "2.0.0",
        "assignment_id": assignment_id,
        "work_item": assignment["work_item"],
        "repository": assignment["project"],
        "responsibility": assignment["responsibility"],
        "repository_ownership": assignment["repository_ownership"],
        "branch": branch,
        "commit": sha,
        "allowed_paths": assignment["allowed_paths"],
        "changed_paths": assignment["allowed_paths"],
        "tests": [{"command": command} for command in assignment["required_tests"]],
        "mr_iid": iid,
        "mr_url": f"https://gitlab.example/ghostspace/axis/-/merge_requests/{iid}",
        "source_main_sha": "main-sha",
        "created_at": "2026-08-10T00:00:00+00:00",
        "state": "ready-for-integration",
    }
    lease = {
        "schema": "axis.external-development-supervisor.lease",
        "schema_version": "1.0.0",
        "lease_id": assignment["lease_id"],
        "assignment_id": assignment_id,
        "owner_run_id": assignment["created_by_run"],
        "fencing_token": "f" * 32,
        "resources": ["repo:ghostspace/axis"],
        "read_only": False,
        "acquired_at_epoch": 1_700_000_000,
        "heartbeat_at_epoch": 1_700_000_000,
        "expires_at_epoch": 4_000_000_000,
    }
    write_record(
        root / "assignments" / f"{assignment_id}.json",
        assignment,
        "axis.external-development-supervisor.assignment",
    )
    write_record(
        root / "implementation-handoffs" / f"{assignment_id}.json",
        handoff,
        "axis.external-development-supervisor.implementation-handoff",
    )
    write_record(
        root / "leases" / assignment["lease_id"] / "lease.json",
        lease,
        "axis.external-development-supervisor.lease",
    )
    return assignment


def test_actionable_external_mrs_are_adopted_and_consumed_by_integrator(
    tmp_path: Path,
):
    inventory = {"open_merge_requests": [_actionable_mr(iid) for iid in (154, 155, 157, 158)]}

    adopted = reconcile(tmp_path, inventory)
    assert [value["lane"] for value in adopted["items"]] == [INTEGRATION] * 4
    assert {value["owner"] for value in adopted["items"]} == {"supervisor-integration"}

    integrator = FakeIntegrator()
    for _ in range(4):
        consumed = consume_next(tmp_path, inventory, integrator)
        assert consumed is not None
        assert consumed["lane"] == INTEGRATION
    assert {iid for _, iid, _ in integrator.calls} == {154, 155, 157, 158}
    persisted = json.loads((tmp_path / "merge-lanes.json").read_text(encoding="utf-8"))
    assert all(value["last_consumed_at_epoch"] for value in persisted["items"])


def test_custody_bound_merge_lane_preempts_an_external_actionable_mr(
    tmp_path: Path,
):
    external = _actionable_mr(154) | {"source_branch": "macos-11-compat"}
    owned = _actionable_mr(155) | {"source_branch": "hermes/axm4-desktop-ui"}
    inventory = _custody_inventory([external, owned], {155})

    adopted = reconcile(tmp_path, inventory)
    by_iid = {item["mr_iid"]: item for item in adopted["items"]}
    assert by_iid[154]["mutation_disposition"] == INSPECTION_ONLY
    assert by_iid[154]["custody"]["reason"] == "source branch is not supervisor-owned"
    assert by_iid[155]["mutation_disposition"] == GATED_INTEGRATION
    assert by_iid[155]["custody"]["assignment_id"] == "assignment-155"

    integrator = FakeIntegrator()
    consumed = consume_next(tmp_path, inventory, integrator)

    assert consumed is not None
    assert consumed["mr_iid"] == 155
    assert consumed["reason"] == (
        "integration inspection passed; lane is bound to governed assignment "
        "assignment-155"
    )
    assert integrator.calls == [("ghostspace/axis", 155, "axis-runtime/product")]


def test_bound_lane_selects_its_existing_gated_integration_assignment():
    from axis_supervisor.cycle import select_integrable_assignment

    external_assignment = {"assignment_id": "external-like"}
    bound_assignment = {"assignment_id": "assignment-155"}
    selected = select_integrable_assignment(
        [external_assignment, bound_assignment],
        {
            "mutation_disposition": GATED_INTEGRATION,
            "custody": {"assignment_id": "assignment-155"},
        },
    )

    assert selected == bound_assignment


def test_durable_binding_recovers_a_missing_inventory_projection(tmp_path: Path):
    from axis_supervisor.cycle import select_integrable_assignment

    external = _actionable_mr(154) | {"source_branch": "macos-11-compat"}
    owned = _actionable_mr(155) | {"source_branch": "hermes/axm4-desktop-ui"}
    inventory = _custody_inventory([external, owned], {155})
    inventory["supervisor_assignments"] = []
    inventory["active_leases"] = []
    durable = _write_durable_integration_binding(
        tmp_path,
        assignment_id="assignment-155",
        branch=owned["source_branch"],
        sha=owned["sha"],
        iid=155,
        worktree="/supervisor/worktrees/assignment-155",
    )

    consumed = consume_next(tmp_path, inventory, FakeIntegrator())

    assert consumed is not None
    assert consumed["mr_iid"] == 155
    assert consumed["mutation_disposition"] == GATED_INTEGRATION
    assert consumed["custody"]["assignment_id"] == "assignment-155"
    assert consumed["custody"]["binding_source"] == "durable-recovery"
    assert select_integrable_assignment([durable], consumed) == durable


def test_durable_binding_recovery_rejects_wrong_branch_sha_and_ambiguity(
    tmp_path: Path,
):
    owned = _actionable_mr(155) | {"source_branch": "hermes/axm4-desktop-ui"}
    inventory = _custody_inventory([owned], {155})
    inventory["supervisor_assignments"] = []
    inventory["active_leases"] = []
    worktree = "/supervisor/worktrees/assignment-155"

    _write_durable_integration_binding(
        tmp_path / "wrong-branch",
        assignment_id="assignment-wrong-branch",
        branch="hermes/other",
        sha=owned["sha"],
        iid=155,
        worktree=worktree,
    )
    wrong_branch = reconcile(tmp_path / "wrong-branch", inventory)["items"][0]
    assert wrong_branch["mutation_disposition"] == INSPECTION_ONLY
    assert wrong_branch["custody"]["assignment_id"] is None

    _write_durable_integration_binding(
        tmp_path / "wrong-sha",
        assignment_id="assignment-wrong-sha",
        branch=owned["source_branch"],
        sha="other-sha",
        iid=155,
        worktree=worktree,
    )
    wrong_sha = reconcile(tmp_path / "wrong-sha", inventory)["items"][0]
    assert wrong_sha["mutation_disposition"] == INSPECTION_ONLY
    assert wrong_sha["custody"]["assignment_id"] is None

    ambiguous_root = tmp_path / "ambiguous"
    for assignment_id in ("assignment-155-a", "assignment-155-b"):
        _write_durable_integration_binding(
            ambiguous_root,
            assignment_id=assignment_id,
            branch=owned["source_branch"],
            sha=owned["sha"],
            iid=155,
            worktree=worktree,
        )
    ambiguous = reconcile(ambiguous_root, inventory)["items"][0]
    assert ambiguous["mutation_disposition"] == INSPECTION_ONLY
    assert ambiguous["custody"]["reason"] == (
        "multiple exact durable integration bindings are ambiguous"
    )


def test_external_lane_stays_non_destructive_without_custody(tmp_path: Path):
    external = _actionable_mr(154) | {"source_branch": "macos-11-compat"}
    inventory = _custody_inventory([external], set())

    consumed = consume_next(tmp_path, inventory, FakeIntegrator())

    assert consumed is not None
    assert consumed["mutation_disposition"] == INSPECTION_ONLY
    assert consumed["custody"]["assignment_id"] is None
    assert consumed["reason"] == (
        "integration inspection passed; merge remains non-destructive because "
        "source branch is not supervisor-owned"
    )


def test_gitlab_approval_and_detail_pipeline_facts_adopt_an_owned_lane(
    tmp_path: Path,
):
    listed_mr = {
        "iid": 154,
        "title": "Production-shaped actionable MR",
        "state": "opened",
        "target_branch": "main",
        "detailed_merge_status": "mergeable",
        "head_pipeline": None,
        "approved_by": [],
        "draft": False,
    }
    approvals = {
        "approved": True,
        "approvals_required": 0,
        "approvals_left": 0,
        "approved_by": [],
    }
    detail = {"head_pipeline": {"status": "success"}}

    merge_request = normalize_open_merge_request(
        "ghostspace/axis", listed_mr, approvals, detail
    )

    assert merge_request["approved"] is True
    assert merge_request["approved_by"] == []
    assert merge_request["approval_state"] == "approved"
    assert merge_request["pipeline_status"] == "success"
    assert merge_request["approval_facts_available"] is True
    assert merge_request["pipeline_facts_available"] is True
    adopted = reconcile(tmp_path, {"open_merge_requests": [merge_request]})
    assert adopted["items"][0]["lane"] == INTEGRATION
    assert adopted["items"][0]["owner"] == "supervisor-integration"


def test_stale_list_approvers_do_not_override_an_unapproved_endpoint_decision(
    tmp_path: Path,
):
    listed_mr = {
        "iid": 154,
        "title": "Stale list approval",
        "state": "opened",
        "target_branch": "main",
        "detailed_merge_status": "mergeable",
        "head_pipeline": {"status": "success"},
        "approved_by": [{"user": {"username": "stale-reviewer"}}],
        "draft": False,
    }
    merge_request = normalize_open_merge_request(
        "ghostspace/axis", listed_mr, {"approved": False}, {}
    )

    assert merge_request["approved"] is False
    assert merge_request["approved_by"] == listed_mr["approved_by"]
    adopted = reconcile(tmp_path, {"open_merge_requests": [merge_request]})
    assert adopted["items"][0]["lane"] != INTEGRATION
    assert adopted["items"][0]["reason"] == "required approval is not observed"


def test_empty_approvals_endpoint_approvers_override_stale_list_approvers():
    listed_mr = {
        "iid": 154,
        "title": "Stale list approver",
        "state": "opened",
        "target_branch": "main",
        "approved_by": [{"user": {"username": "stale-reviewer"}}],
    }
    merge_request = normalize_open_merge_request(
        "ghostspace/axis",
        listed_mr,
        {"approved": False, "approved_by": []},
        {},
    )

    assert merge_request["approved_by"] == []


def test_unavailable_gitlab_facts_fail_closed_with_an_explicit_reason(tmp_path: Path):
    listed_mr = _actionable_mr(154)
    listed_mr.pop("approval_facts_available")
    listed_mr.pop("pipeline_facts_available")

    adopted = reconcile(tmp_path, {"open_merge_requests": [listed_mr]})
    assert adopted["items"][0]["lane"] != INTEGRATION
    assert adopted["items"][0]["reason"] == "GitLab approval facts are unavailable"
