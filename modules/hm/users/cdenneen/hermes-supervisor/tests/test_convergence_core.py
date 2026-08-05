import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def control(**overrides) -> dict:
    value = {
        "schema": "axis.external-development-supervisor.control",
        "schema_version": "1.0.0",
        "mode": "enabled",
        "kill_switch": False,
        "allow_repository_mutation": False,
        "allow_technical_revalidation": True,
        "repository_allowlist": ["ghostspace/axis"],
        "max_active_assignments": 1,
        "lease_seconds": 120,
        "minimum_free_disk_gib": 0,
        "daily_worker_cycle_limit": 24,
        "daily_model_call_limit": 24,
        "tier_a_batch_size": 2,
        "max_semantic_prompt_bytes": 200_000,
        "mutation_grant_ttl_seconds": 21600,
        "mutation_grant_max_model_calls": 2,
        "mutation_grant_max_retries": 1,
        "mutation_grant_max_prompt_bytes": 200_000,
        "mutation_grant_max_cost_usd": 5.0,
    }
    value.update(overrides)
    return value


def assignment(root: Path, **overrides) -> dict:
    lease_path = root / "leases" / "assignment-1" / "lease.json"
    value = {
        "schema": "axis.external-development-supervisor.assignment",
        "schema_version": "1.0.0",
        "assignment_id": "assignment-1",
        "assignment_type": "code-implementation",
        "result_state": "pending",
        "work_item_disposition": "not-evaluated",
        "lifecycle_state": "running-implementation",
        "project": "ghostspace/axis",
        "work_item": "ghostspace/axis#119",
        "planning_record": None,
        "allowed_paths": ["src/example.py"],
        "required_tests": ["pytest tests/test_example.py"],
        "kind": "implementation",
        "authority": {"state": "direct"},
        "governance_state": "Executable",
        "created_by_run": "run-1",
        "lease_id": "assignment-1",
        "lease_uri": lease_path.resolve().as_uri(),
        "mutation_grant_id": None,
        "mutation_grant_uri": None,
    }
    value.update(overrides)
    return value


def lease(**overrides) -> dict:
    now = int(time.time())
    value = {
        "schema": "axis.external-development-supervisor.lease",
        "schema_version": "1.0.0",
        "lease_id": "assignment-1",
        "assignment_id": "assignment-1",
        "owner_run_id": "run-1",
        "fencing_token": "a" * 32,
        "resources": ["repo:ghostspace/axis"],
        "read_only": False,
        "acquired_at_epoch": now,
        "heartbeat_at_epoch": now,
        "expires_at_epoch": now + 120,
    }
    value.update(overrides)
    return value


def item() -> dict:
    return {
        "ref": "ghostspace/axis#119",
        "state": "closed",
        "classification": "Integrated",
        "acceptance_criteria_present": True,
        "repository_head": "main-sha",
        "merge_requests": [{"state": "merged"}],
    }


def historical_assignment(**overrides) -> dict:
    value = {
        "assignment_id": "axis119-hermes-proof-1",
        "work_item": "ghostspace/axis#119",
        "state": "completed",
        "phase": "integrated",
        "planning_record": {
            "digest": "sha256:" + "a" * 64,
            "approval_note": "https://example.test/approval",
        },
        "acceptance": ["bounded custody"],
        "required_tests": ["pytest tests/test_public_release_bundle.py"],
        "merge_request": {
            "state": "merged",
            "merge_commit_sha": "merge-sha",
            "url": "https://example.test/mr/146",
        },
        "pipeline": {
            "status": "success",
            "url": "https://example.test/pipeline/1",
        },
        "evidence": [
            {"kind": "implementation-wwwhh", "ref": "https://example.test/impl"},
            {"kind": "integration-wwwhh", "ref": "https://example.test/integration"},
            {"kind": "post-merge-verification", "ref": "origin/main@merge-sha"},
        ],
        "cleanup": {
            "worktree_removed": True,
            "local_branch_deleted": True,
            "remote_source_branch_absent": True,
            "lease_removed": True,
        },
    }
    value.update(overrides)
    return value


def all_true_verification() -> dict:
    from axis_supervisor.verification import CHECK_NAMES, verification_result

    return verification_result(
        {name: True for name in CHECK_NAMES},
        ["https://example.test/evidence"],
        tier="A",
    )


def test_lifecycle_migrates_historical_completed_and_converges_writes():
    from axis_supervisor.lifecycle import adapt_assignment, is_completed, set_lifecycle

    historical = {"state": "completed", "phase": "integrated"}
    assert adapt_assignment(historical)["lifecycle_state"] == "completed"
    assert is_completed(historical)
    set_lifecycle(historical, "waiting")
    assert historical == {"lifecycle_state": "waiting"}


def test_analysis_assignment_completion_does_not_claim_implementation_completion():
    from axis_supervisor.lifecycle import adapt_assignment

    analyzed = adapt_assignment(
        {
            "kind": "semantic-decomposition",
            "state": "completed",
            "phase": "semantic-complete",
            "worker": {
                "record": {
                    "verification_result": {
                        "disposition": "corrective-implementation-required"
                    }
                }
            },
        }
    )
    assert analyzed["assignment_type"] == "read-only-analysis"
    assert analyzed["result_state"] == "analysis-completed"
    assert analyzed["work_item_disposition"] == "requires-implementation"

    no_op = adapt_assignment(
        {
            "kind": "technical-revalidation",
            "state": "completed",
            "phase": "semantic-complete",
            "worker": {
                "record": {
                    "verification_result": {"disposition": "verified-complete"}
                }
            },
        }
    )
    assert no_op["assignment_type"] == "no-op-verification"
    assert no_op["result_state"] == "no-op-verification-completed"
    assert no_op["work_item_disposition"] == "no-op-verified"


def test_current_and_historical_completion_use_one_verification_shape(tmp_path: Path):
    from axis_supervisor.verification import (
        completion_receipt,
        verification_for,
    )

    historical = verification_for(item(), [historical_assignment()])
    assert historical["state"] == "verified-complete"
    assert historical["source"] == "historical-adapter"

    current = assignment(
        tmp_path,
        lifecycle_state="completed",
        planning_record={
            "revision": 1,
            "digest": "sha256:" + "b" * 64,
            "approval_note": "https://example.test/approval",
        },
        source_item=item(),
        worker={"handoff": {"wwwhh": {"what": "done"}, "mr_url": "https://mr"}},
        source_inventory_generation_id="inventory-before",
        source_fingerprint="source-before",
    )
    receipt = completion_receipt(
        current,
        {
            "mr": {
                "state": "merged",
                "merge_commit_sha": "merge-sha",
                "web_url": "https://example.test/mr/1",
            },
            "pipeline": {"status": "success", "web_url": "https://pipeline"},
        },
        [{"command": current["required_tests"][0], "returncode": 0}],
        {
            "worktree_removed": True,
            "local_branch_deleted": True,
            "remote_source_branch_absent": True,
            "lease_removed": True,
        },
        fresh_cycle_recognition=False,
    )
    current["completion_receipt"] = receipt
    fresh_item = item()
    fresh_item["classification"] = "Revalidation"
    result = verification_for(
        fresh_item,
        [current],
        current_inventory_generation_id="inventory-after",
        current_source_fingerprint="source-after",
    )
    assert result["state"] == "verified-complete"
    assert result["verification_result"].keys() == historical["verification_result"].keys()


def test_missing_pipeline_cleanup_and_fresh_recognition_do_not_verify():
    from axis_supervisor.verification import recognize_fresh_cycle, verification_for

    missing = historical_assignment(pipeline={}, cleanup={})
    result = verification_for(item(), [missing])
    assert result["state"] == "pending-current-revalidation"
    assert {"pipeline_rechecked", "cleanup_rechecked"}.issubset(result["failed_checks"])

    receipt = all_true_verification()
    receipt["checks"]["fresh_cycle_recognition"] = False
    receipt["failed_checks"] = ["fresh_cycle_recognition"]
    receipt["disposition"] = "active-technical-revalidation"
    receipt["failure_disposition"] = "fresh cycle pending"
    current = historical_assignment(completion_receipt=receipt)
    result = verification_for(item(), [current])
    assert result["state"] == "pending-current-revalidation"
    current["completion_receipt"] = recognize_fresh_cycle(receipt)
    assert verification_for(item(), [current])["state"] == "verified-complete"


def test_semantic_verification_is_authoritative_for_positive_and_negative_results():
    from axis_supervisor.verification import CHECK_NAMES, verification_for, verification_result

    pending = verification_result(
        {name: name != "pipeline_rechecked" for name in CHECK_NAMES},
        ["https://example.test/evidence"],
        tier="A",
        failure_disposition="pipeline is missing",
    )
    semantic = {
        "verification_result": pending,
        "source_inventory_generation_id": "inventory-before",
        "source_fingerprint": "source-current",
    }
    assert verification_for(
        item(),
        [historical_assignment()],
        semantic,
        current_inventory_generation_id="inventory-after",
        current_source_fingerprint="source-current",
    )["state"] == (
        "pending-current-revalidation"
    )

    incomplete_assignment = historical_assignment(pipeline={})
    semantic["verification_result"] = all_true_verification()
    assert verification_for(
        item(),
        [incomplete_assignment],
        semantic,
        current_inventory_generation_id="inventory-after",
        current_source_fingerprint="source-current",
    )["state"] == "pending-current-revalidation"

    no_op_assignment = historical_assignment(
        assignment_type="no-op-verification",
        result_state="no-op-verification-completed",
        technical_results={"all_passed": True, "main_sha": "a" * 40},
    )
    assert verification_for(
        item(),
        [no_op_assignment],
        semantic,
        current_inventory_generation_id="inventory-after",
        current_source_fingerprint="source-current",
    )["state"] == "verified-complete"


def test_schema_registry_validates_fixtures_and_fails_closed(tmp_path: Path):
    from axis_supervisor.schema_registry import (
        CorruptRecordError,
        PartialRecordError,
        RecordVersionError,
        read_record,
        validate_record,
    )

    fixtures = [
        (control(), "axis.external-development-supervisor.control"),
        (assignment(tmp_path), "axis.external-development-supervisor.assignment"),
        (lease(), "axis.external-development-supervisor.lease"),
        (all_true_verification(), "axis.external-development-supervisor.verification"),
        (
            {
                "schema": "axis.external-development-supervisor.run",
                "schema_version": "1.0.0",
                "run_id": "run-1",
                "status": "started",
                "host": "test",
                "started_at_epoch": int(time.time()),
                "mode": "enabled",
                "allow_repository_mutation": False,
                "inventory_generation_id": None,
                "model_calls_remaining": 2,
            },
            "axis.external-development-supervisor.run",
        ),
    ]
    for value, schema in fixtures:
        assert validate_record(value, schema) is value

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    with pytest.raises(CorruptRecordError):
        read_record(corrupt, "axis.external-development-supervisor.control")
    with pytest.raises(PartialRecordError):
        validate_record(
            {
                "schema": "axis.external-development-supervisor.control",
                "schema_version": "1.0.0",
            },
            "axis.external-development-supervisor.control",
        )
    bad_version = control(schema_version="2.0.0")
    with pytest.raises(RecordVersionError):
        validate_record(bad_version, "axis.external-development-supervisor.control")


def test_accounting_ledger_is_the_canonical_counter(tmp_path: Path):
    from axis_supervisor.accounting import AccountingLedger

    ledger = AccountingLedger(tmp_path)
    attempt = ledger.start(
        role="semantic",
        model="gpt-5.4",
        provider="openai-api",
        run="run-1",
        assignment="assignment-1",
        limit=1,
    )
    ledger.finish(attempt, "succeeded", usage={"input_tokens": 10})
    assert ledger.model_attempts_today() == 1
    records = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    assert [record["result"] for record in records] == ["started", "succeeded"]
    assert records[-1]["usage"] == {"input_tokens": 10}
    with pytest.raises(RuntimeError, match="daily model call limit"):
        ledger.start(
            role="semantic",
            model="gpt-5.4",
            provider="openai-api",
            run="run-1",
            assignment="assignment-1",
            limit=1,
        )


def test_mutation_is_default_denied_and_lower_helper_cannot_bypass(tmp_path: Path):
    from axis_supervisor.accounting import AccountingLedger
    from axis_supervisor.mutation import MutationDenied, MutationGate, OperationClass
    from axis_supervisor.schema_registry import write_record
    from axis_supervisor.workers import HermesWorkerManager

    write_record(
        tmp_path / "control.json",
        control(),
        "axis.external-development-supervisor.control",
    )
    lease_path = tmp_path / "leases" / "assignment-1" / "lease.json"
    write_record(
        lease_path,
        lease(),
        "axis.external-development-supervisor.lease",
    )
    value = assignment(tmp_path)
    gate = MutationGate(tmp_path)
    assert gate.decide(OperationClass.RECONCILIATION).operation is OperationClass.RECONCILIATION
    for operation in (OperationClass.REPOSITORY, OperationClass.GITLAB):
        with pytest.raises(MutationDenied, match="repository mutation is disabled"):
            gate.decide(
                operation,
                assignment=value,
                repository=value["project"],
                fencing_token="a" * 32,
            )

    write_record(
        tmp_path / "control.json",
        control(allow_repository_mutation=True),
        "axis.external-development-supervisor.control",
    )
    AccountingLedger(tmp_path).start(
        role="implementation",
        model="gpt-5.3-codex",
        provider="openai-api",
        run="run-1",
        assignment="assignment-1",
        limit=1,
    )
    with pytest.raises(MutationDenied, match="source is not trusted"):
        MutationGate(tmp_path, source="slack").decide(
            OperationClass.REPOSITORY,
            assignment=value,
            repository=value["project"],
            fencing_token="a" * 32,
        )
    decision = MutationGate(tmp_path).decide(
        OperationClass.REPOSITORY,
        assignment=value,
        repository=value["project"],
        fencing_token="a" * 32,
    )
    assert decision.operation is OperationClass.REPOSITORY

    gate = MutationGate(tmp_path)
    decision = gate.decide(
        OperationClass.REPOSITORY,
        assignment=value,
        repository=value["project"],
        fencing_token="a" * 32,
    )
    write_record(
        tmp_path / "control.json",
        control(allow_repository_mutation=False),
        "axis.external-development-supervisor.control",
    )
    with pytest.raises(MutationDenied, match="disabled after decision"):
        gate.require(
            decision,
            OperationClass.REPOSITORY,
            assignment=value,
            repository=value["project"],
        )

    write_record(
        tmp_path / "control.json",
        control(allow_repository_mutation=True),
        "axis.external-development-supervisor.control",
    )
    decision = gate.decide(
        OperationClass.REPOSITORY,
        assignment=value,
        repository=value["project"],
        fencing_token="a" * 32,
    )
    expired = lease(expires_at_epoch=1)
    write_record(
        lease_path,
        expired,
        "axis.external-development-supervisor.lease",
    )
    with pytest.raises(MutationDenied, match="expired"):
        gate.require(
            decision,
            OperationClass.REPOSITORY,
            assignment=value,
            repository=value["project"],
        )

    manager = HermesWorkerManager(tmp_path, "/bin/false", "/bin/false", gate)
    with pytest.raises(MutationDenied, match="missing or invalid"):
        manager.implementation(value, tmp_path)


def test_bounded_assignment_grant_allows_only_exact_effect(monkeypatch, tmp_path: Path):
    from axis_supervisor import assignment_grants
    from axis_supervisor.assignment_grants import create_grant, grant_path
    from axis_supervisor.mutation import MutationDenied, MutationGate, OperationClass
    from axis_supervisor.schema_registry import write_record

    control_value = control()
    write_record(
        tmp_path / "control.json",
        control_value,
        "axis.external-development-supervisor.control",
    )
    value = assignment(
        tmp_path,
        assignment_type="code-implementation",
        result_state="pending",
        work_item_disposition="not-evaluated",
        planning_record={
            "revision": 1,
            "digest": "sha256:" + "b" * 64,
            "approval_note": "https://example.test/approval",
        },
        source_item={
            "repository_head": "c" * 40,
            "authority_facts": {
                "approved_assignment_type": "code-implementation",
                "approved_allowed_paths": ["src/example.py"],
                "approved_required_tests": ["pytest tests/test_example.py"],
            },
        },
        source_fingerprint="source-fingerprint",
        mutation_grant_id=None,
        mutation_grant_uri=None,
    )
    create_grant(tmp_path, value, control_value)
    write_record(
        tmp_path / "assignments" / "assignment-1.json",
        value,
        "axis.external-development-supervisor.assignment",
    )
    write_record(
        tmp_path / "leases" / "assignment-1" / "lease.json",
        lease(),
        "axis.external-development-supervisor.lease",
    )
    monkeypatch.setattr(assignment_grants, "current_main_sha", lambda _repo: "c" * 40)
    gate = MutationGate(tmp_path, source="cycle")
    decision = gate.decide(
        OperationClass.REPOSITORY,
        assignment=value,
        repository=value["project"],
        fencing_token="a" * 32,
        effect="clone",
    )
    gate.require(
        decision,
        OperationClass.REPOSITORY,
        assignment=value,
        repository=value["project"],
        effect="clone",
    )
    with pytest.raises(MutationDenied, match="outside mutation grant"):
        gate.decide(
            OperationClass.REPOSITORY,
            assignment=value,
            repository=value["project"],
            fencing_token="a" * 32,
            effect="force-push",
        )
    path = grant_path(tmp_path, value["assignment_id"])
    grant = json.loads(path.read_text(encoding="utf-8"))
    grant["allowed_paths"] = ["src/other.py"]
    write_record(path, grant, "axis.external-development-supervisor.mutation-grant")
    with pytest.raises(MutationDenied, match="scope digest mismatch"):
        gate.decide(
            OperationClass.REPOSITORY,
            assignment=value,
            repository=value["project"],
            fencing_token="a" * 32,
            effect="clone",
        )


def test_implementation_worker_prompt_is_a_no_tool_patch_plan():
    from axis_supervisor.prompt_factory import PromptFactory

    prompts = PromptFactory()
    implementation = prompts.implementation_prompt(
        {"assignment_id": "assignment-1"}, {"src/example.py": "value = 1\n"}
    )
    assert "no-tool patch planner" in implementation
    assert '"patch"' in implementation
    assert "Do not invoke tools" in implementation


def test_large_implementation_context_requires_and_uses_source_ranges():
    from axis_supervisor.workers import bounded_source_context

    content = "".join(f"line {index}\n" for index in range(1, 20_001))
    excerpt = bounded_source_context(
        "src/example.py",
        content,
        "Inspect src/example.py#L10000-L10010",
        maximum_bytes=2_000,
    )
    assert "exact excerpt src/example.py lines 9960-10050" in excerpt
    assert "line 10000" in excerpt
    assert "line 1\n" not in excerpt
    with pytest.raises(RuntimeError, match="lacks candidate line-range evidence"):
        bounded_source_context(
            "src/example.py", content, "no source range", maximum_bytes=2_000
        )


def test_operational_metrics_measure_verified_throughput(tmp_path: Path):
    from axis_supervisor.observability import OperationalEventLog
    from axis_supervisor.schema_registry import write_record

    write_record(
        tmp_path / "control.json",
        control(),
        "axis.external-development-supervisor.control",
    )
    log = OperationalEventLog(tmp_path, "cycle")
    analysis = {
        "assignment_id": "analysis-1",
        "work_item": "ghostspace/axis#1",
        "project": "ghostspace/axis",
        "lifecycle_state": "completed",
    }
    implementation = analysis | {
        "assignment_id": "implementation-1",
        "lifecycle_state": "completed",
    }
    log.emit(
        "assignment_selected",
        assignment=analysis,
        details={"assignment_type": "read-only-analysis"},
        notify=False,
    )
    log.emit(
        "assignment_disposition",
        assignment=analysis,
        details={
            "assignment_type": "read-only-analysis",
            "disposition": "analysis-completed",
            "work_item_disposition": "requires-implementation",
        },
        notify=False,
    )
    log.emit(
        "assignment_selected",
        assignment=implementation,
        details={"assignment_type": "code-implementation"},
        notify=False,
    )
    for event_type in (
        "implementation_completed",
        "mr_merged",
        "post_main_verified",
        "grant_consumed",
    ):
        log.emit(event_type, assignment=implementation, notify=False)
    metrics = log.throughput_metrics(int(time.time()) - 60, int(time.time()) + 60)
    assert metrics["analysis_to_implementation_percent"] == 100
    assert metrics["implementation_to_merge_percent"] == 100
    assert metrics["merge_to_verified_percent"] == 100
    assert metrics["post_main_verified"] == 1


@pytest.mark.parametrize(
    "path",
    ["/home/cdenneen/.ssh/id_ed25519", "../secret", ".git/config", "src/../secret"],
)
def test_allowed_paths_reject_absolute_traversal_and_git_metadata(path: str):
    from axis_supervisor.models import validate_allowed_path

    with pytest.raises(ValueError, match="allowed path"):
        validate_allowed_path(path)


def test_allowlisted_source_rejects_symlink_escape(tmp_path: Path):
    from axis_supervisor.workers import resolve_allowed_source

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    outside = tmp_path / "secret"
    outside.write_text("secret", encoding="utf-8")
    (worktree / "linked").symlink_to(outside)
    with pytest.raises(RuntimeError, match="escapes repository custody"):
        resolve_allowed_source(worktree, "linked")


def test_corrupt_and_expired_leases_are_quarantined_for_stale_recovery(tmp_path: Path):
    from axis_supervisor.schema_registry import write_record

    root = tmp_path / "runtime"
    write_record(
        root / "control.json",
        control(),
        "axis.external-development-supervisor.control",
    )
    corrupt = root / "leases" / "corrupt" / "lease.json"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_text("{", encoding="utf-8")
    expired = lease(
        lease_id="expired",
        assignment_id="expired",
        expires_at_epoch=1,
    )
    write_record(
        root / "leases" / "expired" / "lease.json",
        expired,
        "axis.external-development-supervisor.lease",
    )
    expired_assignment = assignment(
        root,
        assignment_id="expired",
        lifecycle_state="running-implementation",
        lease_id="expired",
        lease_uri=(root / "leases" / "expired" / "lease.json").resolve().as_uri(),
    )
    write_record(
        root / "assignments" / "expired.json",
        expired_assignment,
        "axis.external-development-supervisor.assignment",
    )
    env = os.environ | {
        "AXIS_SUPERVISOR_ROOT": str(root),
        "PYTHONPATH": str(SCRIPTS),
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "supervisorctl.py"), "recover"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    recovered = json.loads(result.stdout)["recovered"]
    assert len(recovered) == 2
    assert not corrupt.parent.exists()
    assert not (root / "leases" / "expired").exists()
    assert len(list((root / "leases").glob("stale-*-corrupt"))) == 1
    assert len(list((root / "leases").glob("stale-*-expired"))) == 1
    recovered_assignment = json.loads(
        (root / "assignments" / "expired.json").read_text(encoding="utf-8")
    )
    assert recovered_assignment["lifecycle_state"] == "recovery-required"
    assert recovered_assignment["lease_id"] is None


def test_preflight_has_no_baseline_or_proof_advisories(tmp_path: Path):
    from axis_supervisor.schema_registry import write_record

    root = tmp_path / "runtime"
    write_record(
        root / "control.json",
        control(kill_switch=True),
        "axis.external-development-supervisor.control",
    )
    env = os.environ | {
        "AXIS_SUPERVISOR_ROOT": str(root),
        "PYTHONPATH": str(SCRIPTS),
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "preflight.py")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert not any("baseline" in key or "proof" in key for key in payload)


def test_preflight_quarantines_only_dead_stale_inventory_lock(tmp_path: Path):
    from axis_supervisor.schema_registry import write_record

    root = tmp_path / "runtime"
    (root / "runs").mkdir(parents=True)
    (root / "assignments").mkdir()
    write_record(
        root / "control.json",
        control(minimum_free_disk_gib=0),
        "axis.external-development-supervisor.control",
    )
    lock = root / "inventory.lock"
    lock.mkdir()
    old = int(time.time()) - 600
    os.utime(lock, (old, old))
    failing = tmp_path / "fail.py"
    failing.write_text("raise SystemExit(2)\n", encoding="utf-8")
    env = os.environ | {
        "AXIS_SUPERVISOR_ROOT": str(root),
        "AXIS_SUPERVISOR_RECONCILE": str(failing),
        "AXIS_SUPERVISOR_CTL": str(SCRIPTS / "supervisorctl.py"),
        "AXIS_SUPERVISOR_CYCLE": str(SCRIPTS / "axis_supervisor" / "cycle.py"),
        "PYTHONPATH": str(SCRIPTS),
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "preflight.py")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert "live reconciliation failed closed" in payload["reason"]
    assert len(list(root.glob("inventory.lock.stale.*"))) == 1
    assert not lock.exists()
