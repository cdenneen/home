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
        "responsibility": "axis-runtime/product",
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


def test_routine_analysis_events_do_not_create_standalone_slack_messages(tmp_path: Path):
    from axis_supervisor.observability import record_event
    from axis_supervisor.slack_projection import SlackProjection

    write_control(tmp_path)
    projection = SlackProjection(tmp_path)
    _legacy_slack_state(projection.state_path)
    state = projection.load_state()
    calls: list[str] = []

    def api(_token: str, method: str, payload: dict) -> dict:
        calls.append(method)
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

    assert not (tmp_path / "slack-outbox.json").exists()
    assignment["lifecycle_state"] = "completed"
    record_event(
        tmp_path,
        "assignment_disposition",
        assignment=assignment,
        details=details | {"disposition": "analysis-completed"},
        source="worker",
    )
    projection.process_outbox("token", "D1", state)
    assert calls == []
    assert not (tmp_path / "slack-outbox.json").exists()


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


def decision_packet() -> dict:
    from axis_supervisor.decisions import DECISION_DIGEST, DECISION_ID

    return {
        "decision_id": DECISION_ID,
        "current_record": "MCP tranche v2",
        "current_digest": DECISION_DIGEST,
        "decision_requested": "Approve the bounded MCP tranche v2 plan?",
        "recommendation": "Approve",
        "consequences": "Scheduling remains blocked until decided.",
        "downstream_effects": ["rebuild frontier"],
        "unresolved_assumptions": [],
        "response_syntax": f"Approve exact digest {DECISION_DIGEST}",
    }


def test_exact_decision_card_is_interactive_immutable_and_replay_safe(tmp_path: Path):
    from axis_supervisor.decisions import (
        APPROVE_ACTION_ID,
        APPROVE_CONDITIONS_ACTION_ID,
        DECISION_ID,
        REJECT_ACTION_ID,
        SlackDecisionController,
    )

    write_control(tmp_path)
    calls = []
    rebuilds = []

    def api(_token: str, method: str, payload: dict) -> dict:
        calls.append((method, payload))
        return {"ok": True, "channel": "D1", "ts": str(payload.get("ts") or "3.1")}

    controller = SlackDecisionController(tmp_path, api, lambda: rebuilds.append(True))
    ts, _fingerprint = controller.project(
        "token",
        workspace_id="T1",
        authorized_user_id="U1",
        channel="D1",
        decision_id=DECISION_ID,
        packet=decision_packet(),
        ts=None,
    )
    blocks = calls[0][1]["blocks"]
    assert [block["type"] for block in blocks[:3]] == ["header", "section", "section"]
    assert {
        element["action_id"]
        for block in blocks
        for element in block.get("elements", [])
    } == {APPROVE_ACTION_ID, APPROVE_CONDITIONS_ACTION_ID, REJECT_ACTION_ID}

    body = {
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "channel": {"id": "D1"},
        "message": {"ts": ts},
    }
    action = {
        "action_id": APPROVE_ACTION_ID,
        "action_ts": "4.1",
        "value": controller.action_value(),
    }
    result = controller.handle_action("token", body, action)
    assert result["status"] == "scheduling"
    assert rebuilds == [True]
    assert [payload["ts"] for method, payload in calls if method == "chat.update"] == [ts, ts]
    record = controller.store.load(DECISION_ID)
    assert record["outcome"] == "approved"

    replay = controller.handle_action("token", body, action)
    assert replay["replayed"] is True
    assert rebuilds == [True]

    try:
        controller.handle_action(
            "token",
            body,
            action | {"action_id": REJECT_ACTION_ID},
        )
    except ValueError as exc:
        assert "conflicts with immutable outcome" in str(exc)
    else:
        raise AssertionError("conflicting replay was accepted")

    bad_body = body | {"user": {"id": "U2"}}
    try:
        controller.handle_action("token", bad_body, action)
    except PermissionError as exc:
        assert "authorized_user_id mismatch" in str(exc)
    else:
        raise AssertionError("wrong Slack identity was accepted")


def test_approve_with_conditions_modal_is_bounded_and_persists_fields(tmp_path: Path):
    from axis_supervisor.decisions import (
        APPROVE_CONDITIONS_ACTION_ID,
        CONDITIONS_BLOCK_ID,
        CONDITIONS_INPUT_ID,
        CONDITIONS_SUBMIT_ACTION_ID,
        DECISION_ID,
        MAX_CONDITIONS_LENGTH,
        MAX_VERIFICATION_LENGTH,
        SlackDecisionController,
        VERIFICATION_BLOCK_ID,
        VERIFICATION_INPUT_ID,
    )

    write_control(tmp_path)
    calls = []
    rebuilds = []

    def api(_token: str, method: str, payload: dict) -> dict:
        calls.append((method, payload))
        return {"ok": True, "channel": "D1", "ts": str(payload.get("ts") or "5.1")}

    controller = SlackDecisionController(tmp_path, api, lambda: rebuilds.append(True))
    ts, _fingerprint = controller.project(
        "token",
        workspace_id="T1",
        authorized_user_id="U1",
        channel="D1",
        decision_id=DECISION_ID,
        packet=decision_packet(),
        ts=None,
    )
    body = {
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "channel": {"id": "D1"},
        "message": {"ts": ts},
        "trigger_id": "trigger-1",
    }
    controller.handle_action(
        "token",
        body,
        {
            "action_id": APPROVE_CONDITIONS_ACTION_ID,
            "value": controller.action_value(),
        },
    )
    modal = next(payload["view"] for method, payload in calls if method == "views.open")
    inputs = [block["element"] for block in modal["blocks"] if block["type"] == "input"]
    assert [value["max_length"] for value in inputs] == [
        MAX_CONDITIONS_LENGTH,
        MAX_VERIFICATION_LENGTH,
    ]

    submit_body = {
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "view": {
            "private_metadata": modal["private_metadata"],
            "state": {
                "values": {
                    CONDITIONS_BLOCK_ID: {
                        CONDITIONS_INPUT_ID: {"value": "Keep the adapter behind the tranche flag."}
                    },
                    VERIFICATION_BLOCK_ID: {
                        VERIFICATION_INPUT_ID: {"value": "Run the MCP conformance suite."}
                    },
                }
            },
        },
    }
    result = controller.handle_action(
        "token",
        submit_body,
        {
            "action_id": CONDITIONS_SUBMIT_ACTION_ID,
            "action_ts": "6.1",
            "value": controller.action_value(),
        },
    )
    assert result["record"]["outcome"] == "approved-with-conditions"
    assert result["record"]["conditions"].startswith("Keep the adapter")
    assert rebuilds == [True]


def test_decision_replay_resumes_an_interrupted_frontier_rebuild(tmp_path: Path):
    from axis_supervisor.decisions import APPROVE_ACTION_ID, DECISION_ID, SlackDecisionController

    write_control(tmp_path)

    def api(_token: str, _method: str, payload: dict) -> dict:
        return {"ok": True, "channel": "D1", "ts": str(payload.get("ts") or "7.1")}

    def fail_rebuild() -> None:
        raise RuntimeError("interrupted")

    controller = SlackDecisionController(tmp_path, api, fail_rebuild)
    ts, _fingerprint = controller.project(
        "token",
        workspace_id="T1",
        authorized_user_id="U1",
        channel="D1",
        decision_id=DECISION_ID,
        packet=decision_packet(),
        ts=None,
    )
    body = {
        "team": {"id": "T1"},
        "user": {"id": "U1"},
        "channel": {"id": "D1"},
        "message": {"ts": ts},
    }
    action = {
        "action_id": APPROVE_ACTION_ID,
        "action_ts": "7.2",
        "value": controller.action_value(),
    }
    try:
        controller.handle_action("token", body, action)
    except RuntimeError as exc:
        assert str(exc) == "interrupted"
    else:
        raise AssertionError("failed frontier rebuild was hidden")
    assert controller.store.load_frontier_request(DECISION_ID)["status"] == "pending"

    rebuilds = []
    recovered = SlackDecisionController(tmp_path, api, lambda: rebuilds.append(True))
    result = recovered.handle_action("token", body, action)
    assert result["replayed"] is True
    assert result["status"] == "scheduling"
    assert rebuilds == [True]
    request = recovered.store.load_frontier_request(DECISION_ID)
    assert request["status"] == "completed"
    assert request["attempts"] == 2


def test_no_op_fingerprint_blocks_redispatch_until_evidence_changes(tmp_path: Path):
    from axis_supervisor.dispatcher import Dispatcher
    from axis_supervisor.noop import no_op_fingerprint

    write_control(tmp_path)
    item = {
        "ref": "technical-revalidation:ghostspace/axis#5:tests",
        "target_ref": "ghostspace/axis#5",
        "kind": "technical-revalidation",
        "assignment_type": "no-op-verification",
        "project": "ghostspace/axis",
        "title": "Re-run focused tests",
        "authority": {"state": "preparation-only"},
        "candidate": {
            "slice_id": "tests",
            "allowed_paths": [],
            "required_tests": ["pytest -q tests/test_mcp.py"],
        },
        "source_item": {
            "repository_head": "abc",
            "state": "closed",
            "updated_at": "2026-08-01T00:00:00Z",
            "source_evidence": {"notes": [{"id": 1, "body": "proof"}]},
        },
        "semantic_evidence_fingerprint": "evidence-1",
    }
    item["no_op_fingerprint"] = no_op_fingerprint(item)
    assignments = tmp_path / "assignments"
    assignments.mkdir()
    completed = {
        "schema": "axis.external-development-supervisor.assignment",
        "schema_version": "1.0.0",
        "assignment_id": "noop-complete",
        "assignment_type": "no-op-verification",
        "result_state": "no-op-verification-completed",
        "work_item_disposition": "no-op-verified",
        "lifecycle_state": "completed",
        "kind": "technical-revalidation",
        "project": "ghostspace/axis",
        "work_item": "ghostspace/axis#5",
        "planning_record": None,
        "allowed_paths": [],
        "required_tests": ["pytest -q tests/test_mcp.py"],
        "created_by_run": "run-old",
        "no_op_fingerprint": item["no_op_fingerprint"],
    }
    (assignments / "noop-complete.json").write_text(json.dumps(completed), encoding="utf-8")
    graph = {"inventory_generation_id": "g1", "executable_queue": [item]}
    dispatcher = Dispatcher(tmp_path)
    assert dispatcher.dispatch(graph, "run-new", item) is None

    refreshed = item | {
        "source_item": item["source_item"]
        | {"updated_at": "2026-08-06T00:00:00Z"}
    }
    refreshed["no_op_fingerprint"] = no_op_fingerprint(refreshed)
    assert refreshed["no_op_fingerprint"] == item["no_op_fingerprint"]
    assert dispatcher.dispatch(graph, "run-new", refreshed) is None

    changed = item | {
        "source_item": item["source_item"]
        | {"source_evidence": {"notes": [{"id": 2, "body": "new proof"}]}}
    }
    changed["no_op_fingerprint"] = no_op_fingerprint(changed)
    assert changed["no_op_fingerprint"] != item["no_op_fingerprint"]
    assert dispatcher.dispatch(graph, "run-new", changed) is not None


def test_no_op_activity_is_dashboard_only():
    from axis_supervisor.observability import is_routine_analysis_event

    assert is_routine_analysis_event(
        {
            "event_type": "assignment_disposition",
            "details": {
                "assignment_type": "no-op-verification",
                "disposition": "no-op-verification-completed",
            },
        }
    )
    assert not is_routine_analysis_event(
        {
            "event_type": "assignment_retry",
            "details": {"assignment_type": "no-op-verification"},
        }
    )
