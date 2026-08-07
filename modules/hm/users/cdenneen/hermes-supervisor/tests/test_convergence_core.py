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
    from axis_supervisor.repository_ownership import validate_repository_ownership

    lease_path = root / "leases" / "assignment-1" / "lease.json"
    ownership = validate_repository_ownership(
        "axis-runtime/product", "ghostspace/axis", context="test-assignment"
    )
    value = {
        "schema": "axis.external-development-supervisor.assignment",
        "schema_version": "4.0.0",
        "assignment_id": "assignment-1",
        "assignment_type": "code-implementation",
        "result_state": "pending",
        "work_item_disposition": "not-evaluated",
        "lifecycle_state": "running-implementation",
        "project": "ghostspace/axis",
        "responsibility": "axis-runtime/product",
        "repository_ownership": ownership,
        "work_item": "ghostspace/axis#119",
        "planning_record": None,
        "allowed_paths": ["src/example.py"],
        "required_tests": ["pytest tests/test_example.py"],
        "action_contract": None,
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
    from axis_supervisor.models import validate_assignment
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

    legacy_assignment = assignment(tmp_path)
    legacy_assignment["schema_version"] = "1.0.0"
    legacy_assignment.pop("action_contract")
    migrated_assignment = validate_assignment(legacy_assignment, tmp_path)
    assert migrated_assignment["schema_version"] == "4.0.0"
    assert migrated_assignment["action_contract"] is None

    persisted_v3 = json.loads(
        (ROOT / "tests" / "fixtures" / "assignment-v3.json").read_text(
            encoding="utf-8"
        )
    )
    migrated_v3 = validate_assignment(persisted_v3, tmp_path)
    assert migrated_v3["schema_version"] == "4.0.0"
    assert migrated_v3["action_contract"]["capability_context"] == [
        {"capability": "CLI"}
    ]
    assert migrated_v3["action_contract"]["merge_impact_projection"] == {
        "affected_capabilities": ["CLI"],
        "product_subdimensions": [],
        "milestones": ["AX-M4"],
        "gates": [],
        "production_confidence_before": 40.0,
    }

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
    from axis_supervisor.assignment_grants import (
        AssignmentGrantDenied,
        create_grant,
        grant_path,
        load_grant,
        validate_grant,
    )
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
    grant = create_grant(tmp_path, value, control_value)
    assert grant["responsibility"] == "axis-runtime/product"
    assert grant["repository_ownership"]["status"] == "validated"
    legacy_grant = dict(grant)
    legacy_grant["schema_version"] = "1.0.0"
    legacy_grant.pop("responsibility")
    legacy_grant.pop("repository_ownership")
    grant_path(tmp_path, value["assignment_id"]).write_text(
        json.dumps(legacy_grant), encoding="utf-8"
    )
    migrated_grant = load_grant(tmp_path, value)
    assert migrated_grant["schema_version"] == "2.0.0"
    assert migrated_grant["responsibility"] == "axis-runtime/product"
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
    mismatched = value | {"project": "ghostspace/axis-governance"}
    with pytest.raises(AssignmentGrantDenied, match="repository-ownership-denied"):
        validate_grant(
            tmp_path,
            mismatched,
            "repository-mutation",
            "ghostspace/axis-governance",
            effect="clone",
        )
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
        {
            "assignment_id": "assignment-1",
            "project": "ghostspace/axis",
            "responsibility": "axis-runtime/product",
        },
        {"src/example.py": "value = 1\n"},
    )
    assert "no-tool patch planner" in implementation
    assert '"patch"' in implementation
    assert "Do not invoke tools" in implementation
    assert "Canonical repository ownership boundary" in implementation


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
    from axis_supervisor.observability import (
        OperationalEventLog,
        record_engineering_retrospective,
    )
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
        "created_at_epoch": int(time.time()) - 10,
        "assignment_type": "read-only-analysis",
        "result_state": "analysis-completed",
        "work_item_disposition": "requires-implementation",
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
    first_retrospective = record_engineering_retrospective(
        tmp_path, analysis, source="cycle"
    )
    second_retrospective = record_engineering_retrospective(
        tmp_path, analysis, source="cycle"
    )
    assert first_retrospective["details"]["retrospective_revision"] == 1
    assert second_retrospective["details"]["retrospective_revision"] == 2
    assert (
        second_retrospective["details"]["supersedes_event_id"]
        == first_retrospective["event_id"]
    )
    assert 0 <= first_retrospective["details"]["duration_seconds"] <= 20


def test_roadmap_quality_projection_is_advisory_and_provenance_bound(
    tmp_path: Path,
):
    from axis_supervisor.roadmap_quality import RoadmapQualityProjector
    from axis_supervisor.schema_registry import write_record

    write_record(
        tmp_path / "control.json",
        control(),
        "axis.external-development-supervisor.control",
    )
    historical = {
        "ref": "ghostspace/axis#1",
        "source_kind": "gitlab-issue",
        "kind": "issue",
        "project": "ghostspace/axis",
        "title": "Historical",
        "source_state": "closed",
        "labels": [],
        "milestone": None,
        "authority_facts": {},
        "blocking_dependency_refs": [],
        "merge_request_facts": [
            {
                "iid": 1,
                "state": "merged",
                "web_url": "https://example.test/mr/1",
            }
        ],
        "acceptance_criteria_present": True,
        "acceptance_facts": {"ids": ["AC-1"], "open_ids": []},
        "source_evidence": {"description": ""},
        "retrieval_errors": [],
        "mutation_allowed": True,
    }
    decomposition = {
        **historical,
        "ref": "ghostspace/axis#2",
        "title": "Needs decomposition",
        "source_state": "opened",
        "merge_request_facts": [],
        "authority_facts": {
            "record_digest": "sha256:" + "a" * 64,
            "approval_matches_record": True,
        },
        "source_evidence": {
            "description": """relationship_ledger:
    - edge_id: E-1
      type: prerequisite-for
      source: ghostspace/axis#2
      target: ghostspace/axis#3
      state: active
"""
        },
    }
    decision = {
        **historical,
        "ref": "ghostspace/axis#3",
        "title": "Needs PO",
        "source_state": "opened",
        "merge_request_facts": [],
        "authority_facts": {"approval_mismatch": True},
    }
    nodes = [
        {
            "ref": historical["ref"],
            "flow_stage": "historical",
            "verification": {"state": "pending-current-revalidation"},
            "semantic_record": None,
        },
        {
            "ref": decomposition["ref"],
            "flow_stage": "decomposition-needed",
            "verification": {"state": "pending-current-revalidation"},
            "semantic_record": None,
        },
        {
            "ref": decision["ref"],
            "flow_stage": "decision",
            "verification": {"state": "pending-current-revalidation"},
            "semantic_record": None,
        },
    ]
    projection = RoadmapQualityProjector(tmp_path).build(
        {
            "generation_id": "inventory-1",
            "work_items": [historical, decomposition, decision],
            "dependency_edges": [],
        },
        {"generation_id": "graph-1", "nodes": nodes},
    )
    assert projection["cohort_counts"] == {
        "decomposition-needed": 1,
        "historical": 1,
        "product-owner-decision": 1,
    }
    assert projection["metrics"]["historical_archive_coverage"] == 100
    assert projection["metrics"]["decision_queue_accuracy"] == 100
    assert projection["metrics"]["typed_dependency_coverage"] == 100
    assert projection["critical_path_status"]["computable"] is True
    assert any(
        edge["relationship"] == "prerequisite"
        and edge["provenance"]["edge_id"] == "E-1"
        for edge in projection["typed_edges"]
    )
    assert all(
        proposal["requires_product_owner_authority"]
        for proposal in projection["proposals"]
    )


def test_repository_convergence_requires_disposition_for_human_remote_branch(
    tmp_path: Path,
):
    from axis_supervisor.repository_convergence import RepositoryConvergenceProjector
    from axis_supervisor.schema_registry import write_record

    write_record(
        tmp_path / "control.json",
        control(),
        "axis.external-development-supervisor.control",
    )
    inventory_value = {
        "generation_id": "inventory-1",
        "repositories": {
            "ghostspace/axis": {
                "default_branch": "main",
                "local_facts": {
                    "path": "/tmp/axis",
                    "dirty": False,
                    "branch": "main",
                    "head": "a" * 40,
                    "default_remote_head": "a" * 40,
                    "remote_fresh": True,
                    "worktrees": [
                        {
                            "path": "/tmp/axis",
                            "branch": "main",
                            "head": "a" * 40,
                            "is_root": True,
                        }
                    ],
                    "local_branches": [
                        {"name": "main", "head": "a" * 40}
                    ],
                    "remote_branches": [
                        {
                            "name": "human/topic",
                            "head": "b" * 40,
                            "merge_base": "a" * 40,
                            "ahead": 1,
                            "behind": 0,
                            "integrated_into_default": False,
                            "changed_paths": ["src/example.py"],
                            "owned_by_supervisor": False,
                            "active_worktree": None,
                            "merge_request": None,
                        }
                    ],
                },
            }
        },
        "supervisor_assignments": [],
        "active_leases": [],
    }
    (tmp_path / "repository-convergence.json").write_text(
        json.dumps(
            {
                "schema": "axis.external-development-supervisor.repository-convergence",
                "schema_version": "1.0.0",
                "convergence_digest": "sha256:" + "f" * 64,
            }
        ),
        encoding="utf-8",
    )
    projector = RepositoryConvergenceProjector(tmp_path)
    ambiguous = projector.build(inventory_value)
    assert ambiguous["schema_version"] == "2.0.0"
    assert ambiguous["fingerprint_lifecycle"] == {
        "current": ambiguous["convergence_digest"],
        "previous": "sha256:" + "f" * 64,
        "changed": True,
        "stable_cycles": 0,
    }
    assert ambiguous["status"] == "red"
    assert ambiguous["counts"]["ambiguous_branches"] == 1
    (tmp_path / "branch-dispositions.json").write_text(
        json.dumps(
            {
                "branches": [
                    {
                        "repository": "ghostspace/axis",
                        "branch": "human/topic",
                        "status": "retained",
                        "disposition": "intentionally-retained",
                        "owner": "owner",
                        "blocker": "active review",
                        "expiry": "2026-09-01",
                        "next_action": "complete review",
                        "evidence": ["https://example.test/issue/1"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    retained = projector.build(inventory_value)
    assert retained["status"] == "green"
    assert retained["counts"]["retained_branches"] == 1
    stable = projector.build(inventory_value)
    assert stable["fingerprint_lifecycle"]["changed"] is False
    assert stable["fingerprint_lifecycle"]["stable_cycles"] == 1


def test_capability_convergence_deploys_only_affected_runtime(tmp_path: Path):
    from axis_supervisor.capability_convergence import CapabilityConvergenceProjector
    from axis_supervisor.schema_registry import write_record

    write_record(
        tmp_path / "control.json",
        control(),
        "axis.external-development-supervisor.control",
    )
    repository = tmp_path / "axis"
    repository.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.test"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=repository, check=True
    )
    (repository / "src").mkdir()
    (repository / "src/service.py").write_text("v1\n", encoding="utf-8")
    (repository / "docs").mkdir()
    (repository / "docs/readme.md").write_text("docs\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repository, check=True)
    first = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    (repository / "src/service.py").write_text("v2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-m", "service"], cwd=repository, check=True)
    current = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", current],
        cwd=repository,
        check=True,
    )
    identity = tmp_path / "identity.json"
    identity.write_text(
        json.dumps({"runtime_revision": first, "health": "healthy"}),
        encoding="utf-8",
    )
    matrix = {
        "schema_version": "1.0.0",
        "repository": "ghostspace/axis",
        "repository_path": str(repository),
        "capabilities": {
            "Service": {"paths": ["src/service.py"], "runtimes": ["ghost"]},
            "Documentation": {"paths": ["docs"], "runtimes": []},
            "Optional Desktop": {
                "paths": ["src/service.py"],
                "runtimes": ["mbair"],
            },
        },
        "runtimes": {
            "ghost": {
                "ring": 0,
                "display_name": "Ghost",
                "host": "local",
                "identity_path": str(identity),
                "deployment_target": "nixosConfigurations.ghost",
            },
            "mbair": {
                "ring": 1,
                "display_name": "mbair",
                "participation": "optional",
                "host": "local",
                "identity_path": str(tmp_path / "offline-identity.json"),
                "deployment_target": "darwinConfigurations.mbair",
            },
        },
    }
    projector = CapabilityConvergenceProjector(tmp_path)
    projector.matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    repository_state = {
        "status": "green",
        "convergence_digest": "sha256:" + "a" * 64,
    }
    lagging = projector.build(repository_state)
    assert lagging["deployment_assignments"][0]["affected_capabilities"] == [
        "Service"
    ]
    assert all(
        value["target_runtime"] != "mbair"
        for value in lagging["deployment_assignments"]
    )
    assert lagging["promotion_status"]["blocked"] is False
    identity.write_text(
        json.dumps(
            {
                    "runtime_revision": current,
                    "health": "healthy",
                    "verification_status": "verified",
                    "capability_revisions": {"Service": current},
            }
        ),
        encoding="utf-8",
    )
    converged = projector.build(repository_state)
    assert converged["deployment_assignments"] == []
    assert converged["runtimes"][0]["status"] == "converged"


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
