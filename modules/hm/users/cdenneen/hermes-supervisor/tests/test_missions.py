import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def write_control(root: Path) -> None:
    value = json.loads((ROOT / "control.defaults.json").read_text(encoding="utf-8"))
    (root / "control.json").write_text(json.dumps(value), encoding="utf-8")


def gate(state: str = "passed", evidence: str = "evidence") -> dict:
    return {"state": state, "evidence": [evidence]}


def capability(
    name: str,
    *,
    missing_gate: str | None = None,
    external: bool = False,
    scheduled_actions: list[dict] | None = None,
    linked_work_items: list[str] | None = None,
) -> dict:
    states = {
        gate_name: gate()
        for gate_name in (
            "implementation",
            "integration",
            "deployment",
            "validation",
            "verification",
            "operator_acceptance",
            "program_risk",
        )
    }
    if missing_gate:
        states[missing_gate] = gate(
            "pending",
            "external approval required" if external else "current evidence missing",
        )
    graduated = missing_gate is None
    states["graduated"] = gate("passed" if graduated else "pending")
    return {
        "capability": name,
        "linked_work_items": linked_work_items or [],
        "graduation_state": states,
        "scheduled_actions": scheduled_actions or [],
        "graduated": graduated,
    }


def graduation(*capabilities: dict) -> dict:
    graduated = sum(value["graduated"] for value in capabilities)
    denominator = len(capabilities)
    return {
        "primary_kpi": {
            "count": graduated,
            "denominator": denominator,
            "percent": round(graduated * 100 / denominator, 1) if denominator else 0.0,
        },
        "effectiveness_fingerprint": "sha256:" + "a" * 64,
        "repository_convergence_digest": "sha256:" + "b" * 64,
        "milestones": [],
        "capabilities": list(capabilities),
    }


def graph(*, nodes: list[dict] | None = None, queue: list[dict] | None = None) -> dict:
    return {"nodes": nodes or [], "executable_queue": queue or []}


def test_cycle_response_is_observation_not_termination(tmp_path: Path):
    from axis_supervisor.missions import ActiveMissionState

    write_control(tmp_path)
    missions = ActiveMissionState(tmp_path)
    current = missions.reconcile(
        {}, graph(), graduation(capability("Service", missing_gate="verification"))
    )
    observed = missions.observe(
        {"result": "blocked", "cannot_truthfully_complete": True},
        source="model-response",
    )

    assert current["current_state"] == "active"
    assert observed["current_state"] == "active"
    assert observed["termination_condition"]["should_terminate"] is False
    assert "cannot_truthfully_complete" in observed["observations"][-1]["summary"]


def test_mission_survives_cron_restart_and_preserves_observations(tmp_path: Path):
    from axis_supervisor.missions import ActiveMissionState

    write_control(tmp_path)
    first_manager = ActiveMissionState(tmp_path)
    first = first_manager.reconcile(
        {}, graph(), graduation(capability("Service", missing_gate="integration"))
    )
    first_manager.observe("pipeline is still running", source="cron-cycle")

    restarted = ActiveMissionState(tmp_path).reconcile(
        {}, graph(), graduation(capability("Service", missing_gate="integration"))
    )

    assert restarted["mission_id"] == first["mission_id"]
    assert restarted["created_at"] == first["created_at"]
    assert restarted["current_state"] == "active"
    assert restarted["observations"][-1]["summary"] == "pipeline is still running"


def test_missing_internal_evidence_generates_bounded_action(tmp_path: Path):
    from axis_supervisor.missions import ActiveMissionState

    write_control(tmp_path)
    current = graduation(
        capability(
            "Service",
            missing_gate="verification",
            linked_work_items=["ghostspace/axis#1"],
        )
    )
    current["milestones"] = [
        {
            "milestone": "AX-M1",
            "debts": [
                {
                    "kind": "capability-gate",
                    "ref": "Service",
                    "gate": "verification",
                    "reason": "current evidence missing",
                },
                {
                    "kind": "capability-gate",
                    "ref": "Unrelated",
                    "gate": "verification",
                    "reason": "unrelated debt",
                },
            ],
        }
    ]
    mission = ActiveMissionState(tmp_path).reconcile(
        {},
        graph(
            nodes=[
                {
                    "ref": "ghostspace/axis#1",
                    "milestone": "AX-M1",
                    "classification": "Completed",
                }
            ]
        ),
        current,
    )

    assert mission["missing_gates"][0]["gate"] == "verification"
    action = mission["generated_actions"][0]
    assert action["kind"] == "reconcile-missing-evidence"
    assert action["engineering_purpose"].startswith("advance verification")
    assert action["gate_owner"] == "technical-verification"
    assert action["expected_gates"] == [
        {
            "capability": "Service",
            "gate": "verification",
            "from_state": "pending",
            "to_state": "passed",
        }
    ]
    assert action["expected_capabilities"] == ["Service"]
    assert action["expected_milestones"] == ["AX-M1"]
    assert action["expected_debt_reduction"] == [
        {
            "milestone": "AX-M1",
            "kind": "capability-gate",
            "ref": "Service",
            "gate": "verification",
            "reason": "current evidence missing",
        }
    ]
    assert action["expected_evidence"] == ["current evidence missing"]
    assert action["convergence_fingerprint"] == "sha256:" + "b" * 64


def test_external_blocked_stream_does_not_stop_compatible_work(tmp_path: Path):
    from axis_supervisor.missions import ActiveMissionState

    write_control(tmp_path)
    queue = [
        {
            "ref": "slice:independent",
            "target_ref": "ghostspace/axis#2",
            "affected_capabilities": ["Independent"],
            "selection_rationale": "independent executable gate",
        }
    ]
    mission = ActiveMissionState(tmp_path).reconcile(
        {},
        graph(queue=queue),
        graduation(
            capability("Blocked", missing_gate="operator_acceptance", external=True),
            capability("Independent", missing_gate="implementation"),
        ),
    )

    assert mission["current_state"] == "active"
    assert mission["external_blockers"][0]["ref"].startswith("capability:Blocked")
    assert any(
        action["kind"] == "dispatch-executable"
        and action["target"] == "ghostspace/axis#2"
        for action in mission["generated_actions"]
    )


def test_desired_state_achievement_is_terminal(tmp_path: Path):
    from axis_supervisor.missions import ActiveMissionState

    write_control(tmp_path)
    mission = ActiveMissionState(tmp_path).reconcile(
        {}, graph(), graduation(capability("Service"))
    )

    assert mission["current_state"] == "completed"
    assert mission["termination_condition"] == {
        "desired_state_achieved": True,
        "every_remaining_path_external_only": False,
        "should_terminate": True,
        "reason": "desired-state-achieved",
    }


def test_every_remaining_path_external_only_is_terminal(tmp_path: Path):
    from axis_supervisor.missions import ActiveMissionState

    write_control(tmp_path)
    mission = ActiveMissionState(tmp_path).reconcile(
        {},
        graph(),
        graduation(
            capability("Service", missing_gate="operator_acceptance", external=True)
        ),
    )

    assert mission["generated_actions"] == []
    assert mission["current_state"] == "blocked-external"
    assert mission["termination_condition"] == {
        "desired_state_achieved": False,
        "every_remaining_path_external_only": True,
        "should_terminate": True,
        "reason": "all-remaining-paths-external-only",
    }


def test_legacy_mission_state_migrates_in_place(tmp_path: Path):
    from axis_supervisor.missions import ActiveMissionState

    write_control(tmp_path)
    (tmp_path / "active-mission.json").write_text(
        json.dumps(
            {
                "mission_id": "legacy-mission",
                "created_at": "2026-01-01T00:00:00+00:00",
                "observations": ["legacy response"],
            }
        ),
        encoding="utf-8",
    )

    migrated = ActiveMissionState(tmp_path).reconcile(
        {}, graph(), graduation(capability("Service", missing_gate="verification"))
    )

    assert migrated["schema_version"] == "2.0.0"
    assert migrated["mission_id"] == "legacy-mission"
    assert migrated["created_at"] == "2026-01-01T00:00:00+00:00"
    assert migrated["observations"][0]["source"] == "migration"
    assert migrated["observations"][0]["summary"] == "legacy response"


def test_zero_effect_action_is_suppressed_and_becomes_state_model_defect(
    tmp_path: Path,
):
    from axis_supervisor.missions import ActiveMissionState

    write_control(tmp_path)
    manager = ActiveMissionState(tmp_path)
    current_graduation = graduation(
        capability("Service", missing_gate="verification")
    )
    first = manager.reconcile({}, graph(), current_graduation)
    action = first["generated_actions"][0]
    contract_fields = {
        key: action[key]
        for key in (
            "engineering_purpose",
            "gate_owner",
            "expected_gates",
            "expected_capabilities",
            "expected_milestones",
            "expected_debt_reduction",
            "expected_evidence",
            "convergence_fingerprint",
            "evidence_model_fingerprint",
            "suppression_fingerprint",
        )
    }
    assignments = tmp_path / "assignments"
    assignments.mkdir()
    (assignments / "completed.json").write_text(
        json.dumps(
            {
                "assignment_id": "completed",
                "project": "ghostspace/axis",
                "work_item": "Service",
                "lifecycle_state": "completed",
                "result_state": "no-op-verification-completed",
                "action_contract": {
                    "action_id": action["action_id"],
                    "source_ref": action["source_ref"],
                    **contract_fields,
                },
            }
        ),
        encoding="utf-8",
    )

    cycle_one = manager.reconcile({}, graph(), current_graduation)
    cycle_two = manager.reconcile({}, graph(), current_graduation)
    cycle_three = manager.reconcile({}, graph(), current_graduation)

    assert cycle_one["generated_actions"] == []
    assert cycle_one["effectiveness_metrics"]["suppressed_fingerprints"] == 1
    assert cycle_two["action_effectiveness"][0]["zero_effect_cycles"] == 2
    assert cycle_three["action_effectiveness"][0]["classification"] == (
        "state-model-defect"
    )
    assert cycle_three["effectiveness_metrics"]["state_model_defects"] == 1
    assert "state-model defect" in cycle_three["observations"][-1]["summary"]

    changed = dict(current_graduation)
    changed["effectiveness_fingerprint"] = "sha256:" + "c" * 64
    resumed = manager.reconcile({}, graph(), changed)
    assert resumed["generated_actions"]
    assert resumed["action_effectiveness"][0]["classification"] == "zero-effect"

    satisfied = graduation(capability("Service"))
    satisfied["effectiveness_fingerprint"] = "sha256:" + "d" * 64
    effective = manager.reconcile({}, graph(), satisfied)
    assert effective["action_effectiveness"][0]["observed_gates"] == [
        {"capability": "Service", "gate": "verification", "state": "passed"}
    ]
    assert effective["action_effectiveness"][0]["classification"] == "effective"


def test_action_48_backfill_records_partial_effect_without_suppression(tmp_path: Path):
    from axis_supervisor.missions import ActiveMissionState

    write_control(tmp_path)
    current = capability("Ghost")
    for gate_name in (
        "validation",
        "verification",
        "operator_acceptance",
        "program_risk",
    ):
        current["graduation_state"][gate_name] = gate("pending")
    current["graduated"] = False
    current["graduation_state"]["graduated"] = gate("pending")
    current_graduation = graduation(current)
    current_graduation["applicability_model_revision"] = "ax-m4-calibration-v1"
    expected_gates = [
        {
            "capability": "Ghost",
            "gate": gate_name,
            "from_state": "pending",
            "to_state": "passed",
        }
        for gate_name in (
            "integration",
            "deployment",
            "validation",
            "verification",
            "operator_acceptance",
            "program_risk",
        )
    ]
    assignments = tmp_path / "assignments"
    assignments.mkdir()
    (assignments / "action-48.json").write_text(
        json.dumps(
            {
                "assignment_id": "ghost-action-48",
                "project": "ghostspace/axis",
                "work_item": "Ghost",
                "lifecycle_state": "completed",
                "result_state": "runtime-converged",
                "action_contract": {
                    "action_id": "48",
                    "source_ref": "runtime:ghost",
                    "expected_gates": expected_gates,
                    "expected_capabilities": ["Ghost"],
                    "evidence_model_fingerprint": "sha256:" + "0" * 64,
                    "applicability_model_revision": "legacy-boolean-v1",
                    "suppression_fingerprint": "sha256:" + "1" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    mission = ActiveMissionState(tmp_path).reconcile(
        {}, graph(), current_graduation
    )
    evaluation = mission["action_effectiveness"][0]

    assert evaluation["assignment_id"] == "ghost-action-48"
    assert evaluation["pre_snapshot"]["confidence"] == 14.3
    assert evaluation["post_snapshot"]["confidence"] == 42.9
    assert evaluation["normalized_delta"]["gates_reduced"] == 2
    assert evaluation["normalized_delta"]["confidence_delta"] == 28.6
    assert evaluation["classification"] == "effective"
    assert mission["effectiveness_metrics"]["suppressed_fingerprints"] == 0
    assert mission["effectiveness_metrics"]["gates_reduced"] == 2
