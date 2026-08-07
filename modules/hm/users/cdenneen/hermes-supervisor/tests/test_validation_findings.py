import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from axis_supervisor.dispatcher import Dispatcher  # noqa: E402
from axis_supervisor.lifecycle import adapt_assignment  # noqa: E402
from axis_supervisor.validation_findings import (  # noqa: E402
    EXTERNAL_IMPLEMENTATION_SEEDS,
    FINDING_CLASSIFICATIONS,
    ExternalImplementationAdoptions,
    ValidationFindingStore,
    classify_validation_finding,
)
from axis_supervisor.workflow_state import WorkflowState  # noqa: E402

def write_control(root: Path) -> None:
    value = json.loads((ROOT / "control.defaults.json").read_text(encoding="utf-8"))
    value.update({"mode": "enabled", "daily_model_call_limit": 10})
    (root / "control.json").write_text(json.dumps(value), encoding="utf-8")


def evidence(root: Path, suffix: str = "one") -> dict:
    path = root / f"evidence-{suffix}.json"
    path.write_text("{}\n", encoding="utf-8")
    return {"evidence_id": f"evidence-{suffix}", "uri": path.resolve().as_uri()}


def owner(*, approved: bool) -> dict:
    digest = "sha256:" + "a" * 64
    return {
        "ref": "ghostspace/axis#900",
        "project": "ghostspace/axis",
        "repository_head": "a" * 40,
        "authority_facts": {
            "approval_matches_record": approved,
            "record_digest": digest if approved else None,
            "record_revision": 2,
            "approval_note": "https://gitlab.com/ghostspace/axis/-/issues/900#note_1"
            if approved
            else None,
        },
    }


def inventory(*, approved: bool) -> dict:
    return {
        "generation_id": "inventory-one",
        "work_items": [owner(approved=approved)],
        "repositories": {
            "ghostspace/axis": {"local_facts": {"default_remote_head": "a" * 40}}
        },
    }


def product_defect() -> dict:
    return {
        "summary": "Scheduler returns an incorrect bounded result",
        "classification": "PRODUCT_DEFECT",
        "capability": "Scheduler",
        "gate": "validation",
        "repository": "ghostspace/axis",
        "responsibility": "axis-runtime/product",
        "owner_ref": "ghostspace/axis#900",
        "allowed_paths": ["src/axis_kernel/workflow.py"],
        "required_tests": ["pytest -q tests/test_workflow.py"],
        "replay": {"stream": "ghost-operator"},
    }


def test_canonical_classification_contract():
    assert FINDING_CLASSIFICATIONS == {
        "EVIDENCE_ONLY",
        "CONFIGURATION",
        "DEPLOYMENT",
        "PRODUCT_DEFECT",
        "ROADMAP_GAP",
        "AUTHORITY_BLOCKED",
        "EXTERNAL_BLOCKED",
    }
    for classification in FINDING_CLASSIFICATIONS:
        assert (
            classify_validation_finding(
                {"summary": classification, "classification": classification}
            )
            == classification
        )


def test_existing_owner_and_approval_resolve_before_executable_child(tmp_path: Path):
    write_control(tmp_path)
    store = ValidationFindingStore(tmp_path)
    unresolved = store.promote(
        "ghost-operator", evidence(tmp_path), product_defect(), inventory(approved=False)
    )
    assert unresolved["owner_resolution"]["status"] == "existing-owner"
    assert unresolved["status"] == "DECISION_REQUIRED"
    assert unresolved["decision_packet"]["response_syntax"].startswith(
        "Approve exact digest sha256:"
    )

    approved = store.promote(
        "ghost-operator", evidence(tmp_path), product_defect(), inventory(approved=True)
    )
    assert approved["finding_id"] == unresolved["finding_id"]
    assert approved["status"] == "EXECUTABLE"
    assert approved["planning_record"]["revision"] == 2
    assert approved["decision_packet"] is None
    assert len(store.all()) == 1


def test_restart_duplicate_and_interrupted_temp_write_are_idempotent(tmp_path: Path):
    write_control(tmp_path)
    first = ValidationFindingStore(tmp_path).promote(
        "ghost-operator", evidence(tmp_path), product_defect(), inventory(approved=True)
    )
    temporary = tmp_path / "validation-findings" / ".interrupted.json.tmp"
    temporary.write_text("{", encoding="utf-8")

    restarted = ValidationFindingStore(tmp_path)
    second = restarted.promote(
        "ghost-operator", evidence(tmp_path), product_defect(), inventory(approved=True)
    )
    assert second == first
    assert [record["finding_id"] for record in restarted.all()] == [first["finding_id"]]


def test_finding_uses_same_frontier_and_provenance_reaches_grant(tmp_path: Path):
    write_control(tmp_path)
    store = ValidationFindingStore(tmp_path)
    finding = store.promote(
        "ghost-operator", evidence(tmp_path), product_defect(), inventory(approved=True)
    )
    queue = store.executable_entries(inventory(approved=True), [])
    assert len(queue) == 1
    assert queue[0]["origin_finding"]["finding_id"] == finding["finding_id"]
    assert queue[0]["affected_capabilities"] == ["Scheduler"]
    assert queue[0]["expected_gate"] == "validation"
    assert queue[0]["targeted_replay"]["stream"] == "ghost-operator"

    assignment = Dispatcher(tmp_path).dispatch(
        {"executable_queue": queue, "inventory_generation_id": "inventory-one"},
        "run-one",
        queue[0],
    )
    assert assignment is not None
    assert assignment["schema_version"] == "5.0.0"
    assert assignment["origin_finding"]["finding_id"] == finding["finding_id"]
    assert assignment["worktree_context"]["targeted_replay"] == assignment[
        "targeted_replay"
    ]
    grant = json.loads(
        (tmp_path / "mutation-grants" / assignment["assignment_id"] / "grant.json").read_text(
            encoding="utf-8"
        )
    )
    assert grant["schema_version"] == "3.0.0"
    assert grant["origin_finding"] == assignment["origin_finding"]
    assert grant["targeted_replay"] == assignment["targeted_replay"]
    workflow = WorkflowState(tmp_path)
    handoff = workflow.persist_handoff(
        assignment,
        {
            "branch": f"hermes/{assignment['assignment_id']}",
            "commit": "b" * 40,
            "changed_paths": assignment["allowed_paths"],
            "handoff": {
                "tests": [],
                "mr_iid": 1,
                "mr_url": "https://example.test/mr/1",
            },
        },
    )
    integration = workflow.enqueue(assignment, handoff, "reviewer")
    for value in (handoff, integration):
        assert value["origin_finding"] == assignment["origin_finding"]
        assert value["targeted_replay"] == assignment["targeted_replay"]
        assert value["worktree_context"] == assignment["worktree_context"]


def test_only_authorized_roadmap_gap_reaches_executable_frontier(tmp_path: Path):
    write_control(tmp_path)
    store = ValidationFindingStore(tmp_path)
    gap = product_defect() | {
        "summary": "Missing scheduler roadmap capability",
        "classification": "ROADMAP_GAP",
    }
    blocked = store.promote(
        "ghost-operator", evidence(tmp_path), gap, inventory(approved=True)
    )
    assert blocked["status"] == "DECISION_REQUIRED"
    assert store.executable_entries(inventory(approved=True), []) == []

    authorized = store.promote(
        "ghost-operator",
        evidence(tmp_path),
        gap | {"roadmap_authorized": True},
        inventory(approved=True),
    )
    assert authorized["finding_id"] == blocked["finding_id"]
    assert authorized["status"] == "EXECUTABLE"
    assert len(store.executable_entries(inventory(approved=True), [])) == 1


def test_targeted_replay_closes_and_reopens_finding_gate(tmp_path: Path):
    write_control(tmp_path)
    store = ValidationFindingStore(tmp_path)
    finding = store.promote(
        "ghost-operator", evidence(tmp_path), product_defect(), inventory(approved=True)
    )
    assignment = {
        "assignment_id": "assignment-one",
        "origin_finding": {
            "finding_id": finding["finding_id"],
            "fingerprint": finding["fingerprint"],
        },
    }
    scheduled = store.schedule_replay(assignment, ["https://example.test/mr/1"])
    assert scheduled["status"] == "REPLAY_PENDING"
    assert scheduled["targeted_replay"]["attempt"] == 1
    closed = store.complete_replay(
        finding["finding_id"], passed=True, evidence=["pytest=0"]
    )
    assert closed["status"] == "CLOSED"
    assert closed["gate_resolution"] == "passed"
    assert closed["targeted_replay"]["state"] == "passed"

    reopened = store.promote(
        "ghost-operator",
        evidence(tmp_path, "regression"),
        product_defect(),
        inventory(approved=True),
    )
    assert reopened["status"] == "REOPENED"
    scheduled_again = store.schedule_replay(assignment, ["https://example.test/mr/2"])
    failed = store.complete_replay(
        finding["finding_id"], passed=False, evidence=["pytest=1"]
    )
    assert scheduled_again["targeted_replay"]["attempt"] == 2
    assert failed["status"] == "REOPENED"
    assert failed["gate_resolution"] == "failed"
    assert failed["targeted_replay"]["state"] == "failed"


def test_failed_assignment_is_reopened_after_restart(tmp_path: Path):
    write_control(tmp_path)
    store = ValidationFindingStore(tmp_path)
    finding = store.promote(
        "ghost-operator", evidence(tmp_path), product_defect(), inventory(approved=True)
    )
    store.mark_assigned(finding["finding_id"], "assignment-crashed")
    failed_assignment = {
        "assignment_id": "assignment-crashed",
        "created_at_epoch": 1,
        "lifecycle_state": "recovery-required",
        "origin_finding": {"finding_id": finding["finding_id"]},
    }
    queue = ValidationFindingStore(tmp_path).executable_entries(
        inventory(approved=True), [failed_assignment]
    )
    assert len(queue) == 1
    reopened = store.load(finding["finding_id"])
    assert reopened["status"] == "REOPENED"
    assert reopened["gate_resolution"] == "failed"


def test_direct_dispatch_guardrail_and_auditable_bootstrap_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import axis_supervisor.dispatcher as dispatcher_module

    write_control(tmp_path)
    (tmp_path / "active-mission.json").write_text("{}", encoding="utf-8")
    mission = {
        "mission_id": "healthy-mission",
        "current_state": "active",
        "termination_condition": {"should_terminate": False},
        "generated_actions": [{"kind": "dispatch-executable", "source_ref": "other"}],
    }
    monkeypatch.setattr(dispatcher_module, "read_mission_record", lambda _path: mission)
    dispatcher = Dispatcher(tmp_path)
    item = {"ref": "direct", "project": "ghostspace/axis"}
    with pytest.raises(PermissionError, match="direct AXIS"):
        dispatcher._enforce_direct_dispatch_guardrail(
            item, "code-implementation", None
        )
    item["bootstrap_override"] = {
        "authorized_by": "bootstrap-operator",
        "reason": "seed canonical mission recovery",
    }
    audit = dispatcher._enforce_direct_dispatch_guardrail(
        item, "code-implementation", None
    )
    assert audit == {
        "authorized_by": "bootstrap-operator",
        "reason": "seed canonical mission recovery",
        "mission_id": "healthy-mission",
        "item_ref": "direct",
    }


def test_assignment_handoff_and_queue_migrations_add_explicit_provenance(tmp_path: Path):
    assignment = adapt_assignment(
        {
            "schema": "axis.external-development-supervisor.assignment",
            "schema_version": "4.0.0",
            "assignment_id": "legacy",
            "assignment_type": "read-only-analysis",
            "result_state": "pending",
            "work_item_disposition": "not-evaluated",
            "lifecycle_state": "ready-semantic",
            "project": "ghostspace/axis",
            "responsibility": "axis-runtime/product",
            "repository_ownership": {},
            "work_item": "ghostspace/axis#1",
            "planning_record": None,
            "allowed_paths": [],
            "required_tests": [],
            "action_contract": None,
            "mutation_grant_id": None,
            "mutation_grant_uri": None,
        },
        tmp_path,
    )
    assert assignment["schema_version"] == "5.0.0"
    assert assignment["origin_finding"] is None
    assert assignment["targeted_replay"] is None
    assert assignment["worktree_context"] is None

    write_control(tmp_path)
    handoff = {
        "schema": "axis.external-development-supervisor.implementation-handoff",
        "schema_version": "2.0.0",
        "assignment_id": "legacy",
        "work_item": "ghostspace/axis#1",
        "repository": "ghostspace/axis",
        "responsibility": "axis-runtime/product",
        "repository_ownership": {},
        "branch": "hermes/legacy",
        "commit": "a" * 40,
        "allowed_paths": [],
        "changed_paths": [],
        "tests": [],
        "mr_iid": 1,
        "mr_url": "https://example.test/mr/1",
        "created_at": "2026-08-07T00:00:00+00:00",
        "state": "ready-for-integration",
    }
    migrated = WorkflowState(tmp_path).adapt_handoff(handoff)
    assert migrated["schema_version"] == "3.0.0"
    assert migrated["origin_finding"] is None


def test_external_adoptions_cover_mrs_155_through_158_and_report_metrics(tmp_path: Path):
    write_control(tmp_path)
    inventory_value = {
        "open_merge_requests": [
            {
                "project": "ghostspace/axis",
                "iid": 155,
                "state": "opened",
                "source_branch": "hermes/axm4-desktop-ui",
                "sha": "b" * 40,
                "pipeline_status": "success",
                "web_url": EXTERNAL_IMPLEMENTATION_SEEDS[0]["mr_url"],
            }
        ],
        "work_items": [
            {
                "project": "ghostspace/axis",
                "merge_request_facts": [
                    {
                        "iid": 156,
                        "state": "merged",
                        "sha": "c" * 40,
                        "web_url": EXTERNAL_IMPLEMENTATION_SEEDS[1]["mr_url"],
                    }
                ],
            }
        ],
    }
    adoptions = ExternalImplementationAdoptions(tmp_path).reconcile(inventory_value)
    assert [record["mr_ref"] for record in adoptions["records"]] == [
        f"ghostspace/axis!{iid}" for iid in range(155, 159)
    ]
    assert adoptions["records"][0]["state"] == "awaiting-integration"
    assert adoptions["records"][1]["state"] == "merged-awaiting-replay"
    from axis_supervisor.dashboard import render_executive_dashboard

    _fallback, blocks, _fingerprint = render_executive_dashboard(
        tmp_path, {}, {"nodes": []}, {}, []
    )
    dashboard_text = json.dumps(blocks)
    assert "ghostspace/axis!155" in dashboard_text
    assert "Merged Awaiting Replay" in dashboard_text

    adopted = ValidationFindingStore(tmp_path).promote(
        "cross-interface-cli",
        evidence(tmp_path),
        product_defect()
        | {
            "summary": "CLI product defect",
            "capability": "CLI",
        },
        inventory(approved=True),
    )
    assert adopted["status"] == "EXTERNAL_IMPLEMENTATION_ADOPTED"
    metrics = ValidationFindingStore(tmp_path).metrics()
    assert metrics["external_implementation_adopted"] == 1
    assert metrics["by_classification"]["PRODUCT_DEFECT"] == 1
