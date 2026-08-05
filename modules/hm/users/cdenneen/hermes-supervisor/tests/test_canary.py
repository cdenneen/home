import json
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def setup_canary(root: Path) -> tuple[dict, dict]:
    from axis_supervisor.canary import write_grant
    from axis_supervisor.schema_registry import write_record

    control = json.loads((ROOT / "control.defaults.json").read_text(encoding="utf-8"))
    control["mode"] = "enabled"
    write_record(
        root / "control.json",
        control,
        "axis.external-development-supervisor.control",
    )
    now = int(time.time())
    assignment_id = "canary-axis-lab-iac-kind"
    branch = f"hermes/{assignment_id}"
    paths = ["scripts/iac_binary_proof.py", "tests/test_iac_binary_proof.py"]
    tests = ["uv run pytest -q tests/test_iac_binary_proof.py"]
    grant = {
        "schema": "axis.external-development-supervisor.canary-grant",
        "schema_version": "1.0.0",
        "grant_id": "axis-lab-iac-kind",
        "grant_digest": "sha256:" + "a" * 64,
        "status": "active",
        "authority_ref": "urn:axis-supervisor:canary:test",
        "assignment_id": assignment_id,
        "target_ref": "pending:axis-lab-iac-kind",
        "target_title": "Fail closed on IaC proof binary-kind mismatch",
        "repository": "ghostspace/axis-lab",
        "source_sha": "b" * 40,
        "branch": branch,
        "worktree": str(root / "worktrees" / assignment_id),
        "allowed_paths": paths,
        "required_tests": tests,
        "operation_sequence": [
            "issue-create",
            "model-call",
            "repository-mutation",
            "gitlab-mutation",
            "integration",
            "verification",
            "cleanup",
        ],
        "issued_at_epoch": now,
        "expires_at_epoch": now + 900,
        "mr_iid": None,
        "issue_iid": None,
        "events": [],
    }
    write_grant(root, grant)
    assignment = {
        "assignment_id": assignment_id,
        "project": grant["repository"],
        "work_item": grant["target_ref"],
        "source_main_sha": grant["source_sha"],
        "canary_branch": branch,
        "allowed_paths": paths,
        "required_tests": tests,
        "authority": {"state": "canary"},
        "governance_state": "Executable",
        "created_by_run": "canary-run",
        "lease_id": assignment_id,
        "lease_uri": (root / "leases" / assignment_id / "lease.json").resolve().as_uri(),
        "lifecycle_state": "running-implementation",
    }
    lease = {
        "schema": "axis.external-development-supervisor.lease",
        "schema_version": "1.0.0",
        "lease_id": assignment_id,
        "assignment_id": assignment_id,
        "owner_run_id": "canary-run",
        "fencing_token": "c" * 32,
        "resources": ["repo:ghostspace/axis-lab"],
        "read_only": False,
        "acquired_at_epoch": now,
        "heartbeat_at_epoch": now,
        "expires_at_epoch": now + 600,
    }
    write_record(
        root / "leases" / assignment_id / "lease.json",
        lease,
        "axis.external-development-supervisor.lease",
    )
    return grant, assignment


def test_canary_gate_allows_only_exact_scope(monkeypatch, tmp_path: Path):
    from axis_supervisor import canary
    from axis_supervisor.mutation import MutationDenied, MutationGate, OperationClass

    _grant, assignment = setup_canary(tmp_path)
    monkeypatch.setattr(canary, "current_main_sha", lambda _repo: "b" * 40)
    gate = MutationGate(tmp_path, source="cycle")
    decision = gate.decide(
        OperationClass.REPOSITORY,
        assignment=assignment,
        repository=assignment["project"],
        fencing_token="c" * 32,
    )
    gate.require(
        decision,
        OperationClass.REPOSITORY,
        assignment=assignment,
        repository=assignment["project"],
    )

    wrong = dict(assignment) | {"project": "ghostspace/axis"}
    with pytest.raises(MutationDenied, match="repository mismatch"):
        gate.decide(
            OperationClass.REPOSITORY,
            assignment=wrong,
            repository="ghostspace/axis",
            fencing_token="c" * 32,
        )
    wrong = dict(assignment) | {"allowed_paths": ["scripts/other.py"]}
    with pytest.raises(MutationDenied, match="path scope mismatch"):
        gate.decide(
            OperationClass.REPOSITORY,
            assignment=wrong,
            repository=assignment["project"],
            fencing_token="c" * 32,
        )
    wrong = dict(assignment) | {"canary_branch": "main"}
    with pytest.raises(MutationDenied, match="branch mismatch"):
        gate.decide(
            OperationClass.REPOSITORY,
            assignment=wrong,
            repository=assignment["project"],
            fencing_token="c" * 32,
        )


def test_canary_gate_denies_expired_stale_and_second_assignment(
    monkeypatch, tmp_path: Path
):
    from axis_supervisor import canary
    from axis_supervisor.canary import load_grant, write_grant
    from axis_supervisor.mutation import MutationDenied, MutationGate, OperationClass

    grant, assignment = setup_canary(tmp_path)
    gate = MutationGate(tmp_path, source="cycle")
    monkeypatch.setattr(canary, "current_main_sha", lambda _repo: "d" * 40)
    with pytest.raises(MutationDenied, match="stale"):
        gate.decide(
            OperationClass.REPOSITORY,
            assignment=assignment,
            repository=assignment["project"],
            fencing_token="c" * 32,
        )
    monkeypatch.setattr(canary, "current_main_sha", lambda _repo: "b" * 40)
    second = dict(assignment) | {"assignment_id": "second"}
    with pytest.raises(MutationDenied, match="assignment mismatch"):
        gate.decide(
            OperationClass.REPOSITORY,
            assignment=second,
            repository=assignment["project"],
            fencing_token="c" * 32,
        )
    grant = load_grant(tmp_path)
    grant["expires_at_epoch"] = int(time.time()) - 1
    write_grant(tmp_path, grant)
    with pytest.raises(MutationDenied, match="expired"):
        gate.decide(
            OperationClass.REPOSITORY,
            assignment=assignment,
            repository=assignment["project"],
            fencing_token="c" * 32,
        )


def test_canary_gate_allows_exact_merged_recovery(monkeypatch, tmp_path: Path):
    from axis_supervisor import canary
    from axis_supervisor.canary import write_grant
    from axis_supervisor.mutation import MutationDenied, MutationGate, OperationClass

    grant, assignment = setup_canary(tmp_path)
    grant["mr_iid"] = 17
    grant["events"].append(
        {
            "event": "merge-request-bound",
            "iid": 17,
            "sha": "e" * 40,
            "recorded_at_epoch": int(time.time()),
        }
    )
    write_grant(tmp_path, grant)
    assignment["worker"] = {
        "commit": "e" * 40,
        "handoff": {"mr_iid": 17},
    }
    merged_mr = {
        "iid": 17,
        "state": "merged",
        "target_branch": "main",
        "source_branch": grant["branch"],
        "sha": "e" * 40,
        "merge_commit_sha": "d" * 40,
        "diff_refs": {"base_sha": grant["source_sha"]},
    }
    monkeypatch.setattr(canary, "current_main_sha", lambda _repo: "d" * 40)
    gate = MutationGate(tmp_path, source="cycle")
    decision = gate.decide(
        OperationClass.REPOSITORY,
        assignment=assignment,
        repository=assignment["project"],
        fencing_token="c" * 32,
        merged_mr=merged_mr,
    )
    gate.require(
        decision,
        OperationClass.REPOSITORY,
        assignment=assignment,
        repository=assignment["project"],
    )

    with pytest.raises(MutationDenied, match="stale"):
        gate.decide(
            OperationClass.REPOSITORY,
            assignment=assignment,
            repository=assignment["project"],
            fencing_token="c" * 32,
            merged_mr=merged_mr | {"sha": "f" * 40},
        )


def test_merge_readiness_fails_closed_before_successful_pipeline():
    from axis_supervisor.integrator import Integrator

    class FakeIntegrator(Integrator):
        def __init__(self):
            pass

        def api(self, path: str):
            if path.endswith("/discussions"):
                return []
            if path.endswith("/approvals"):
                return {"approvals_left": 0}
            return {
                "state": "opened",
                "draft": False,
                "has_conflicts": False,
                "target_branch": "main",
                "source_branch": "hermes/canary-axis-lab-iac-kind",
                "sha": "a" * 40,
                "head_pipeline": {"status": "running"},
            }

    inspection = FakeIntegrator().inspect_mr(
        "ghostspace/axis-lab",
        1,
        expected_source_branch="hermes/canary-axis-lab-iac-kind",
        expected_sha="a" * 40,
    )
    assert inspection["merge_ready"] is False
