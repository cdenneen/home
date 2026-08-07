import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from axis_supervisor.delivery_lanes import (  # noqa: E402
    LANES,
    LANE_CAPACITY,
    WORKER_POOL_CAPACITY,
    DeliveryLaneProjector,
    classify_ci,
    lane_from_assignment,
    lane_from_finding,
    lane_from_graph_node,
    lane_from_mr,
)
from axis_supervisor.frontier import build_executable_frontier  # noqa: E402
from axis_supervisor.schema_registry import write_record  # noqa: E402
from axis_supervisor.validation_findings import (  # noqa: E402
    ExternalImplementationAdoptions,
)


def write_control(root: Path, **overrides) -> dict:
    value = json.loads((ROOT / "control.defaults.json").read_text(encoding="utf-8"))
    value.update({"mode": "enabled", **overrides})
    (root / "control.json").write_text(json.dumps(value), encoding="utf-8")
    return value


def graph_node(
    ref: str = "ghostspace/axis#1",
    *,
    flow_stage: str = "implementation-ready",
    labels: list[str] | None = None,
) -> dict:
    return {
        "ref": ref,
        "source_kind": "gitlab-issue",
        "kind": "issue",
        "project": "ghostspace/axis",
        "title": "Bounded delivery",
        "labels": labels or [],
        "milestone": "AX-M4",
        "classification": "Executable",
        "authority": {"state": "direct"},
        "flow_stage": flow_stage,
        "flow_evidence": [f"fixture {flow_stage}"],
        "ranking_score": 100,
    }


def source_item(
    ref: str = "ghostspace/axis#1",
    *,
    labels: list[str] | None = None,
    notes: list[str] | None = None,
    merge_requests: list[dict] | None = None,
    updated_at: str = "2026-08-07T00:00:00+00:00",
) -> dict:
    return {
        "ref": ref,
        "project": "ghostspace/axis",
        "title": "Bounded delivery",
        "labels": labels or [],
        "updated_at": updated_at,
        "source_evidence": {
            "notes": [
                {"id": index, "body": body}
                for index, body in enumerate(notes or [], 1)
            ]
        },
        "merge_request_facts": merge_requests or [],
    }


def graph(nodes: list[dict], queue: list[dict] | None = None) -> dict:
    return {
        "generation_id": "graph-one",
        "nodes": nodes,
        "executable_queue": queue or [],
        "scheduler_state": {"selected_batch": queue or []},
    }


def inventory(
    items: list[dict], assignments: list[dict] | None = None, **extra
) -> dict:
    return {
        "generation_id": "inventory-one",
        "work_items": items,
        "supervisor_assignments": assignments or [],
        "open_merge_requests": [],
        "external_implementation_merge_requests": [],
        **extra,
    }


def assignment(
    assignment_id: str = "assignment-one",
    *,
    lifecycle: str = "running-implementation",
    work_item: str = "ghostspace/axis#1",
    paths: list[str] | None = None,
) -> dict:
    return {
        "schema": "axis.external-development-supervisor.assignment",
        "schema_version": "1.0.0",
        "assignment_id": assignment_id,
        "assignment_type": "code-implementation",
        "result_state": "pending",
        "work_item_disposition": "not-evaluated",
        "lifecycle_state": lifecycle,
        "project": "ghostspace/axis",
        "work_item": work_item,
        "planning_record": None,
        "allowed_paths": paths or ["src/example.py"],
        "required_tests": [],
        "created_at_epoch": 1,
    }


def test_lane_and_worker_pool_contract_is_complete():
    assert LANES == (
        "BACKLOG",
        "READY",
        "IMPLEMENTATION",
        "HANDOFF",
        "REVIEW",
        "REPAIR",
        "INTEGRATION",
        "MERGE_READY",
        "POST_MAIN_VERIFICATION",
        "DEPLOYMENT",
        "VALIDATION",
        "DECISION",
        "GRADUATED",
        "BLOCKED",
    )
    assert WORKER_POOL_CAPACITY == {
        "semantic": 2,
        "implementation": 4,
        "review-integration": 2,
        "repair": 2,
        "deployment": 2,
        "validation": 2,
    }
    assert LANE_CAPACITY["IMPLEMENTATION"] == 4
    assert LANE_CAPACITY["GRADUATED"] is None


@pytest.mark.parametrize(
    ("mr", "lane", "ci"),
    [
        (
            {
                "state": "opened",
                "draft": True,
                "pipeline_status": "success",
            },
            "REVIEW",
            "passed",
        ),
        (
            {"state": "opened", "pipeline_status": "failed"},
            "REPAIR",
            "failed",
        ),
        (
            {"state": "opened", "pipeline_status": "running"},
            "INTEGRATION",
            "pending",
        ),
        (
            {
                "state": "opened",
                "pipeline_status": "success",
                "detailed_merge_status": "mergeable",
            },
            "MERGE_READY",
            "passed",
        ),
        (
            {"state": "merged", "pipeline_status": "success"},
            "POST_MAIN_VERIFICATION",
            "passed",
        ),
        (
            {"state": "opened", "pipeline_status": "manual"},
            "BLOCKED",
            "manual",
        ),
    ],
)
def test_gitlab_mr_state_and_ci_classification(mr: dict, lane: str, ci: str):
    assert classify_ci(mr) == ci
    assert lane_from_mr(mr)[0] == lane


def test_gitlab_lane_label_and_note_override_derived_graph_state():
    label_node = graph_node(flow_stage="backlog", labels=["delivery::merge-ready"])
    assert lane_from_graph_node(label_node)[0] == "MERGE_READY"
    note_node = graph_node(flow_stage="backlog") | {
        "source_evidence": {
            "notes": [{"id": 1, "body": "delivery_lane: VALIDATION"}]
        }
    }
    assert lane_from_graph_node(note_node)[0] == "VALIDATION"


def test_assignment_handoff_review_repair_and_downstream_transitions():
    assert lane_from_assignment(assignment(lifecycle="implementation-complete"))[0] == (
        "HANDOFF"
    )
    waiting = assignment(lifecycle="awaiting-integration")
    assert lane_from_assignment(waiting, {"state": "awaiting-review"})[0] == "REVIEW"
    assert lane_from_assignment(
        waiting,
        {"state": "waiting-ci", "main_advance": "repair-required"},
    )[0] == "REPAIR"
    assert lane_from_assignment(
        assignment(lifecycle="integrated-post-main-verified")
    )[0] == "POST_MAIN_VERIFICATION"
    deployment = assignment(lifecycle="ready-implementation") | {
        "assignment_type": "capability-deployment"
    }
    assert lane_from_assignment(deployment)[0] == "DEPLOYMENT"
    assert lane_from_assignment(
        deployment | {"lifecycle_state": "canonical-complete"}
    )[0] == "GRADUATED"


def test_validation_verification_closes_and_reopens_delivery_lane():
    assert lane_from_finding({"status": "REPLAY_PENDING"})[0] == "VALIDATION"
    assert lane_from_finding({"status": "CLOSED"})[0] == "GRADUATED"
    assert lane_from_finding({"status": "REOPENED"})[0] == "REPAIR"


def test_persistent_board_reconciles_source_and_detects_stalls_and_transitions(
    tmp_path: Path,
):
    write_control(tmp_path)
    item = source_item(updated_at="2026-08-01T00:00:00+00:00")
    projector = DeliveryLaneProjector(tmp_path)
    first = projector.build(
        inventory([item]),
        graph([graph_node()]),
        {"generated_at": "2026-08-07T00:00:00+00:00", "capabilities": []},
        now=1_786_089_600,
    )
    ready = next(lane for lane in first["lanes"] if lane["lane"] == "READY")
    assert ready["wip"] == 1
    assert ready["items"][0]["stalled"] is True
    assert first["source_reconciliation"]["gitlab_source_items"] == 1
    assert first["flow_metrics"]["milestone_lane_wip"]["AX-M4"] == {"READY": 1}

    second = projector.build(
        inventory([item]),
        graph([graph_node(flow_stage="implementation")]),
        {"generated_at": "2026-08-07T01:00:00+00:00", "capabilities": []},
        now=1_786_093_200,
    )
    transition = second["transitions"][-1]
    assert second["generation_number"] == 2
    assert transition["from_lane"] == "READY"
    assert transition["to_lane"] == "IMPLEMENTATION"
    assert next(
        lane for lane in second["lanes"] if lane["lane"] == "IMPLEMENTATION"
    )["items"][0]["entered_at"] == second["generated_at"]


def test_main_advance_repair_overrides_pending_mr_lane(tmp_path: Path):
    write_control(tmp_path)
    mr = {"iid": 7, "state": "opened", "pipeline_status": "running"}
    value = assignment(lifecycle="awaiting-integration")
    workflow_queue = {
        "schema": "axis.external-development-supervisor.integration-queue",
        "schema_version": "4.0.0",
        "updated_at": "2026-08-07T00:00:00+00:00",
        "items": [
            {
                "assignment_id": "assignment-one",
                "work_item": "ghostspace/axis#1",
                "repository": "ghostspace/axis",
                "responsibility": "axis-runtime/product",
                "repository_ownership": {},
                "handoff_uri": "file:///tmp/handoff.json",
                "mr_iid": 7,
                "mr_url": "https://example.test/mr/7",
                "reviewer": "reviewer",
                "state": "waiting-ci",
                "main_advance": "repair-required",
                "enqueued_at": "2026-08-07T00:00:00+00:00",
                "updated_at": "2026-08-07T00:00:00+00:00",
                "last_error": None,
                "origin_finding": None,
                "targeted_replay": None,
                "worktree_context": None,
            }
        ],
    }
    write_record(
        tmp_path / "integration-queue.json",
        workflow_queue,
        "axis.external-development-supervisor.integration-queue",
    )
    board = DeliveryLaneProjector(tmp_path).build(
        inventory([source_item(merge_requests=[mr])], [value]),
        graph([graph_node()]),
        {"generated_at": "2026-08-07T00:00:00+00:00", "capabilities": []},
        now=1_786_089_600,
    )
    source = next(
        item
        for lane in board["lanes"]
        for item in lane["items"]
        if item["delivery_id"] == "source:ghostspace/axis#1"
    )
    assert source["lane"] == "REPAIR"
    assert source["main_advance"] == "repair-required"


def test_external_mrs_are_adopted_at_live_gitlab_lanes(tmp_path: Path):
    write_control(tmp_path)
    external = [
        {
            "project": "ghostspace/axis",
            "iid": 155,
            "state": "opened",
            "pipeline_status": "success",
            "detailed_merge_status": "mergeable",
            "web_url": "https://gitlab.com/ghostspace/axis/-/merge_requests/155",
        },
        {
            "project": "ghostspace/axis",
            "iid": 156,
            "state": "merged",
            "pipeline_status": "success",
            "web_url": "https://gitlab.com/ghostspace/axis/-/merge_requests/156",
        },
    ]
    inventory_value = inventory(
        [], external_implementation_merge_requests=external
    )
    ExternalImplementationAdoptions(tmp_path).reconcile(inventory_value)
    board = DeliveryLaneProjector(tmp_path).build(
        inventory_value,
        graph([]),
        {"generated_at": "2026-08-07T00:00:00+00:00", "capabilities": []},
        now=1_786_089_600,
    )
    external_items = {
        item["ref"]: item
        for lane in board["lanes"]
        for item in lane["items"]
        if item["source_kind"] == "external-implementation"
    }
    assert external_items["ghostspace/axis!155"]["lane"] == "MERGE_READY"
    assert external_items["ghostspace/axis!156"]["lane"] == (
        "POST_MAIN_VERIFICATION"
    )
    assert set(external_items) == {
        "ghostspace/axis!155",
        "ghostspace/axis!156",
        "ghostspace/axis!157",
        "ghostspace/axis!158",
    }


def test_generation_b_refills_compatible_work_while_integration_is_active(
    tmp_path: Path,
):
    write_control(tmp_path, max_active_assignments=2)
    active = assignment(
        lifecycle="integrated-post-main-verified", paths=["src/integrated.py"]
    )
    queue = [
        {
            "ref": "slice:ghostspace/axis#2:implementation",
            "target_ref": "ghostspace/axis#2",
            "project": "ghostspace/axis",
            "assignment_type": "code-implementation",
            "ranking_score": 200,
            "candidate": {"allowed_paths": ["src/independent.py"]},
        },
        {
            "ref": "slice:ghostspace/axis#3:implementation",
            "target_ref": "ghostspace/axis#3",
            "project": "ghostspace/axis",
            "assignment_type": "code-implementation",
            "ranking_score": 190,
            "candidate": {"allowed_paths": ["src/other.py"]},
        },
    ]
    frontier = build_executable_frontier(
        tmp_path, queue, [active], "graph-one", now=1_786_089_600
    )
    write_record(
        tmp_path / "executable-frontier.json",
        frontier,
        "axis.external-development-supervisor.executable-frontier",
    )
    board = DeliveryLaneProjector(tmp_path).build(
        inventory([], [active]),
        graph([], queue),
        {"generated_at": "2026-08-07T00:00:00+00:00", "capabilities": []},
        now=1_786_089_600,
    )
    generation_b = board["dispatch_generations"]["B"]
    assert generation_b["eligible"] is True
    assert generation_b["available_capacity"] == 1
    assert generation_b["selected_refs"] == [queue[0]["ref"]]
    assert board["flow_metrics"]["generation_b_refill_count"] == 1


def test_assignment_migration_backfills_lane_generation_fields():
    from axis_supervisor.lifecycle import adapt_assignment

    migrated = adapt_assignment(assignment())
    assert migrated["delivery_lane"] is None
    assert migrated["dispatch_generation"] is None
    assert migrated["lane_entered_at"] is None
