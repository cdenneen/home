import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def write_control(root: Path) -> None:
    (root / "control.json").write_text(
        (ROOT / "control.defaults.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def queue_item(
    ref: str,
    path: str,
    *,
    assignment_type: str = "code-implementation",
    score: int = 100,
) -> dict:
    return {
        "ref": ref,
        "target_ref": ref,
        "project": "ghostspace/axis",
        "assignment_type": assignment_type,
        "ranking_score": score,
        "candidate": {"allowed_paths": [path]},
    }


def test_frontier_allows_compatible_same_repository_paths_and_stage_capacity(
    tmp_path: Path,
):
    from axis_supervisor.frontier import (
        STAGE_CAPACITIES,
        build_executable_frontier,
        compatible,
    )

    queue = [
        queue_item(f"axis#{index}", f"src/feature-{index}.py", score=200 - index)
        for index in range(1, 6)
    ]
    frontier = build_executable_frontier(tmp_path, queue, [], "graph-1", now=100)

    assert STAGE_CAPACITIES == {
        "semantic": 2,
        "implementation": 4,
        "integration": 2,
        "repair": 2,
        "deployment": 2,
    }
    assert compatible(queue[0], queue[1]) is True
    assert frontier["selected"] == [f"axis#{index}" for index in range(1, 5)]
    assert frontier["deferred"] == [
        {
            "entry_id": "axis#5",
            "reason": "stage-capacity",
            "conflicts_with": None,
        }
    ]


def test_frontier_skips_quarantine_and_ci_waiting_does_not_consume_implementation(
    tmp_path: Path,
):
    from axis_supervisor.frontier import build_executable_frontier

    (tmp_path / "quarantines.json").write_text(
        json.dumps(
            {
                "items": [
                    {"work_item": "axis#1", "expires_at_epoch": 200}
                ]
            }
        ),
        encoding="utf-8",
    )
    waiting_ci = {
        "assignment_id": "assignment-ci",
        "assignment_type": "code-implementation",
        "lifecycle_state": "awaiting-integration",
        "project": "ghostspace/axis",
        "allowed_paths": ["src/already.py"],
    }
    queue = [
        queue_item("axis#1", "src/quarantined.py"),
        queue_item("axis#2", "src/alternate.py"),
    ]

    frontier = build_executable_frontier(
        tmp_path, queue, [waiting_ci], "graph-1", now=100
    )

    assert frontier["in_use"]["integration"] == 1
    assert frontier["in_use"]["implementation"] == 0
    assert frontier["selected"] == ["axis#2"]
    assert frontier["deferred"][0]["reason"] == "quarantined"


def test_handoff_and_integration_queue_persist_reviewer(tmp_path: Path):
    from axis_supervisor.schema_registry import read_record
    from axis_supervisor.workflow_state import WorkflowState

    write_control(tmp_path)
    assignment = {
        "assignment_id": "assignment-1",
        "work_item": "ghostspace/axis#1",
        "project": "ghostspace/axis",
        "allowed_paths": ["src/example.py"],
        "source_item": {"repository_head": "a" * 40},
    }
    result = {
        "branch": "hermes/assignment-1",
        "commit": "b" * 40,
        "changed_paths": ["src/example.py"],
        "handoff": {
            "tests": [{"command": "pytest -q", "returncode": 0}],
            "mr_iid": 7,
            "mr_url": "https://gitlab.com/ghostspace/axis/-/merge_requests/7",
        },
    }
    workflow = WorkflowState(tmp_path)

    handoff = workflow.persist_handoff(assignment, result)
    queued = workflow.enqueue(assignment, handoff, "reviewer-one")

    persisted_handoff = read_record(
        tmp_path / "implementation-handoffs" / "assignment-1.json",
        "axis.external-development-supervisor.implementation-handoff",
    )
    persisted_queue = read_record(
        tmp_path / "integration-queue.json",
        "axis.external-development-supervisor.integration-queue",
    )
    assert persisted_handoff["commit"] == "b" * 40
    assert queued["reviewer"] == "reviewer-one"
    assert persisted_queue["items"][0]["state"] == "awaiting-review"
    assert persisted_queue["items"][0]["handoff_uri"].endswith(
        "/implementation-handoffs/assignment-1.json"
    )


def test_main_advance_classification():
    from axis_supervisor.workflow_state import classify_main_advance

    assert classify_main_advance("a", "a", ["src/a.py"], []) == "unchanged"
    assert (
        classify_main_advance("a", "b", ["src/a.py"], ["docs/readme.md"])
        == "compatible"
    )
    assert (
        classify_main_advance("a", "b", ["src/a.py"], ["src/a.py"])
        == "repair-required"
    )
    assert classify_main_advance("a", "b", ["src/a.py"], []) == "advanced-unassessed"
    assert (
        classify_main_advance("a", "b", merge_commit_sha="b") == "integrated"
    )


def _legacy_slack_state(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.9.0",
                "semantic_revision": "legacy",
                "record_schema": "axis.external-development-supervisor.roadmap-semantics",
            }
        ),
        encoding="utf-8",
    )


def test_slack_cards_persist_timestamp_update_and_aggregate_duplicates(tmp_path: Path):
    from axis_supervisor.observability import record_event
    from axis_supervisor.slack_projection import SlackProjection

    write_control(tmp_path)
    projection = SlackProjection(tmp_path)
    _legacy_slack_state(projection.state_path)
    state = projection.load_state()
    messages: dict[str, dict] = {}
    calls: list[str] = []

    def api(_token: str, method: str, payload: dict) -> dict:
        calls.append(method)
        if method in {"chat.postMessage", "chat.update"}:
            ts = str(payload.get("ts") or f"1.{len(messages) + 1}")
            messages[ts] = {"ts": ts, "text": payload["text"]}
            return {"ok": True, "channel": "D1", "ts": ts}
        if method == "conversations.history":
            return {"ok": True, "messages": list(messages.values())}
        raise AssertionError(method)

    projection.api = api
    assignment = {
        "assignment_id": "assignment-1",
        "work_item": "ghostspace/axis#1",
        "project": "ghostspace/axis",
        "lifecycle_state": "ready-semantic",
    }
    details = {"assignment_type": "read-only-analysis"}
    record_event(
        tmp_path,
        "assignment_selected",
        assignment=assignment,
        details=details,
        source="worker",
    )
    record_event(
        tmp_path,
        "worker_started",
        assignment=assignment,
        details=details,
        source="worker",
    )

    projection.process_outbox("token", "D1", state)
    persisted = projection.load_state()
    ts = persisted["projection_timestamps"]["assignment"]["assignment-1"]
    assert calls.count("chat.postMessage") == 1
    assert "Collapsed 2 read-only/no-op events" in messages[ts]["text"]

    calls.clear()
    assignment["lifecycle_state"] = "completed"
    record_event(
        tmp_path,
        "assignment_disposition",
        assignment=assignment,
        details=details | {"disposition": "analysis-completed"},
        source="worker",
    )
    projection.process_outbox("token", "D1", persisted)
    updated = projection.load_state()
    assert updated["projection_timestamps"]["assignment"]["assignment-1"] == ts
    assert calls.count("chat.update") == 1


def test_incident_card_posts_once_then_updates(tmp_path: Path):
    from axis_supervisor.observability import record_event
    from axis_supervisor.slack_projection import SlackProjection

    write_control(tmp_path)
    projection = SlackProjection(tmp_path)
    _legacy_slack_state(projection.state_path)
    state = projection.load_state()
    messages: dict[str, dict] = {}
    calls: list[str] = []

    def api(_token: str, method: str, payload: dict) -> dict:
        calls.append(method)
        if method in {"chat.postMessage", "chat.update"}:
            ts = str(payload.get("ts") or "2.1")
            messages[ts] = {"ts": ts, "text": payload["text"]}
            return {"ok": True, "channel": "D1", "ts": ts}
        if method == "conversations.history":
            return {"ok": True, "messages": list(messages.values())}
        raise AssertionError(method)

    projection.api = api
    assignment = {
        "assignment_id": "assignment-incident",
        "work_item": "ghostspace/axis#9",
        "project": "ghostspace/axis",
        "lifecycle_state": "running-implementation",
    }
    for retry in (1, 2):
        record_event(
            tmp_path,
            "assignment_retry",
            assignment=assignment,
            details={"retry": retry, "incident_id": "incident-9"},
            source="worker",
        )
        projection.process_outbox("token", "D1", state)
        state = projection.load_state()

    assert calls.count("chat.postMessage") == 1
    assert calls.count("chat.update") == 1
    assert state["projection_timestamps"]["incident"]["incident-9"] == "2.1"
