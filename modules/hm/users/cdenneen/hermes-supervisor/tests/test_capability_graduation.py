import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def write_control(root: Path, maximum: int = 3) -> None:
    value = json.loads((ROOT / "control.defaults.json").read_text(encoding="utf-8"))
    value["max_active_assignments"] = maximum
    (root / "control.json").write_text(json.dumps(value), encoding="utf-8")


def verified_node() -> dict:
    return {
        "ref": "ghostspace/axis#1",
        "milestone": "AX-M4",
        "labels": ["operator::accepted"],
        "classification": "Completed",
        "source_state": "closed",
        "flow_stage": "verified-complete",
        "flow_evidence": ["canonical verification complete"],
        "verification": {"state": "verified-complete"},
        "semantic_record": {
            "candidate_slices": [{"allowed_paths": ["src/service.py"]}]
        },
    }


def convergence() -> dict:
    return {
        "convergence_digest": "sha256:" + "a" * 64,
        "repository_convergence_digest": "sha256:" + "b" * 64,
        "capabilities": [
            {
                "capability": "Service",
                "expected_revision": "main",
                "evidence_fingerprint": "sha256:" + "c" * 64,
                "projected_runtimes": ["ghost"],
            }
        ],
        "runtimes": [
            {
                "runtime": "ghost",
                "status": "converged",
                "health": "healthy",
                "verification_status": "verified",
                "operator_acceptance": "accepted",
                "operator_evidence": "operator://acceptance/service",
                "required_command_available": True,
                "capabilities_behind": [],
            }
        ],
    }


def test_mission_v2_projection_is_durable_and_gate_complete(tmp_path: Path):
    from axis_supervisor.capability_graduation import CapabilityGraduationProjector
    from axis_supervisor.schema_registry import read_record

    write_control(tmp_path)
    (tmp_path / "capability-runtime-matrix.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "capabilities": {
                    "Service": {
                        "paths": ["src/service.py"],
                        "runtimes": ["ghost"],
                        "gate_applicability": {
                            "implementation": True,
                            "integration": True,
                            "deployment": True,
                            "validation": True,
                            "verification": True,
                            "operator_acceptance": True,
                            "program_risk": True,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    assignment_dir = tmp_path / "assignments"
    assignment_dir.mkdir()
    (assignment_dir / "merged.json").write_text(
        json.dumps(
            {
                "assignment_id": "merged",
                "project": "ghostspace/axis",
                "result_state": "repository-converged",
                "worker": {
                    "commit": "merge-sha",
                    "changed_paths": ["src/service.py"],
                },
            }
        ),
        encoding="utf-8",
    )
    inventory = {
        "generation_id": "inventory-1",
        "supervisor_assignments": [],
    }
    graph = {
        "generation_id": "graph-1",
        "nodes": [verified_node()],
        "executable_queue": [
            {
                "ref": "merge-impact:service",
                "target_ref": "ghostspace/axis#1",
                "milestone": "AX-M4",
                "candidate": {
                    "allowed_paths": ["src/service.py"],
                    "required_tests": ["pytest -q"],
                },
            }
        ],
        "scheduler_state": {
            "current_constraint": {"estimated_roadmap_delay_days": 3.0}
        },
    }

    projection = CapabilityGraduationProjector(tmp_path).build(
        inventory, graph, convergence()
    )
    persisted = read_record(
        tmp_path / "capability-graduation.json",
        "axis.external-development-supervisor.capability-graduation",
    )

    assert persisted == projection
    assert projection["schema_version"] == "5.0.0"
    assert projection["primary_kpi"] == {
        "name": "graduated-capabilities",
        "count": 1,
        "denominator": 1,
        "percent": 100.0,
    }
    capability = projection["capabilities"][0]
    assert capability["graduated"] is True
    assert capability["gate_denominator"] == 7
    assert capability["gates_passed"] == 7
    assert capability["first_failing_gate"] is None
    assert capability["production_confidence"] == 100.0
    assert capability["operator_confidence"] == 100.0
    assert set(capability["product_subdimensions"]) == {
        "CLI",
        "Node",
        "Web",
        "Desktop",
        "HUD",
        "Neural",
    }
    assert all(
        gate["applicable"]
        for name, gate in capability["graduation_state"].items()
        if name != "graduated"
    )
    assert set(capability["graduation_state"]) == {
        "implementation",
        "integration",
        "deployment",
        "validation",
        "verification",
        "operator_acceptance",
        "program_risk",
        "graduated",
    }
    assert capability["scheduled_actions"][0]["stages"] == ["verification"]
    assert capability["scheduled_actions"][0]["repository"] == "ghostspace/axis-lab"
    assert projection["denominator"] == {
        "active": 0,
        "archive": 0,
        "historical": 0,
        "future": 0,
        "decision": 0,
        "blocked": 0,
        "graduated": 1,
    }
    assert projection["milestones"][0]["forecast"]["days"] == 3.0
    assert projection["production_confidence"] == 100.0
    assert projection["operator_confidence"] == 100.0
    assert projection["merge_impact_projection"][0]["affected_capabilities"] == [
        "Service"
    ]
    assert projection["action_scores"][0]["capability_context"][0][
        "capability"
    ] == "Service"

    unchanged = CapabilityGraduationProjector(tmp_path).build(
        inventory, graph, convergence()
    )
    assert unchanged["capabilities"][0]["scheduled_actions"] == []

    # A process restart or unrelated main advance cannot erase this capability's
    # still-valid calibrated evidence merely because runtime collection is absent.
    unavailable_after_restart = convergence()
    unavailable_after_restart["runtimes"] = []
    restored = CapabilityGraduationProjector(tmp_path).build(
        inventory | {"generation_id": "inventory-unrelated-main"},
        graph | {"generation_id": "graph-unrelated-main"},
        unavailable_after_restart,
    )
    assert restored["calibration_reconciliation"]["state"] == "complete"
    assert restored["capabilities"][0]["graduated"] is True

    production_only_node = verified_node() | {"labels": []}
    production_only_convergence = convergence()
    production_only_convergence["runtimes"][0].update(
        {"operator_acceptance": None, "operator_evidence": None}
    )
    production_only = CapabilityGraduationProjector(tmp_path).build(
        inventory,
        graph | {"nodes": [production_only_node]},
        production_only_convergence,
    )["capabilities"][0]
    assert production_only["production_confidence"] == 100.0
    assert production_only["operator_confidence"] == 0.0
    assert production_only["graduation_confidence"] < 100.0

    analysis = verified_node()
    analysis.update(
        {
            "classification": "Running",
            "source_state": "opened",
            "flow_stage": "analysis",
            "verification": {"state": "active-technical-revalidation"},
        }
    )
    active_analysis = CapabilityGraduationProjector(tmp_path).build(
        inventory, graph | {"nodes": [analysis]}, convergence()
    )
    assert active_analysis["capabilities"][0]["graduation_state"]["implementation"][
        "state"
    ] == "pending"


def test_v4_projection_migrates_confidence_dimensions_before_validation(
    tmp_path: Path,
):
    from axis_supervisor.capability_graduation import read_capability_graduation

    fixture = ROOT / "tests" / "fixtures" / "capability-graduation-v4.json"
    path = tmp_path / "capability-graduation.json"
    path.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

    migrated = read_capability_graduation(path)
    capability = migrated["capabilities"][0]
    assert migrated["schema_version"] == "5.0.0"
    assert capability["production_confidence"] == 100.0
    assert capability["operator_confidence"] is None
    assert migrated["production_confidence"] == 100.0
    assert migrated["operator_confidence"] is None
    assert capability["program_risk"]["score"] == 50
    assert capability["product_subdimensions"]["CLI"]["applicable"] is True


def test_gate_applicability_has_no_implicit_defaults(tmp_path: Path):
    from axis_supervisor.capability_graduation import CapabilityGraduationProjector

    write_control(tmp_path)
    (tmp_path / "capability-runtime-matrix.json").write_text(
        json.dumps(
            {
                "capabilities": {
                    "Service": {"paths": ["src/service.py"], "runtimes": []}
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "assignments").mkdir()

    try:
        CapabilityGraduationProjector(tmp_path).build(
            {"supervisor_assignments": []},
            {"nodes": [], "executable_queue": []},
            {"capabilities": [], "runtimes": []},
        )
    except ValueError as exc:
        assert "explicitly declare applicability for every gate" in str(exc)
    else:
        raise AssertionError("implicit capability gate defaults were accepted")


def test_authoritative_applicability_has_exactly_18_explicit_rows():
    from axis_supervisor.capability_graduation import (
        GATES,
        _gate,
        _gate_applicability,
    )

    matrix = json.loads(
        (ROOT / "capability-runtime-matrix.json").read_text(encoding="utf-8")
    )

    assert matrix["authoritative"] is True
    assert matrix["applicability_model_revision"] == "ax-m4-calibration-v1"
    assert len(matrix["capabilities"]) == 18
    for name, definition in matrix["capabilities"].items():
        applicability = _gate_applicability(name, definition)
        assert set(applicability) == set(GATES)
        assert all(
            state in {"required", "conditional", "not-applicable"}
            for state in applicability.values()
        )

    documentation = _gate_applicability(
        "Documentation", matrix["capabilities"]["Documentation"]
    )
    assert documentation == {
        "implementation": "required",
        "integration": "required",
        "deployment": "not-applicable",
        "validation": "not-applicable",
        "verification": "not-applicable",
        "operator_acceptance": "not-applicable",
        "program_risk": "not-applicable",
    }
    assert matrix["runtimes"]["mbair"]["participation"] == "optional"
    assert _gate("pending", [], applicability="conditional") == {
        "applicable": False,
        "applicability": "conditional",
        "state": "not-required",
        "evidence": [],
    }
    assert _gate(
        "passed", ["operator proof"], applicability="conditional", condition_met=True
    )["state"] == "passed"


def test_optional_runtime_is_excluded_from_gate_denominator(tmp_path: Path):
    from axis_supervisor.capability_graduation import CapabilityGraduationProjector

    write_control(tmp_path)
    (tmp_path / "assignments").mkdir()
    (tmp_path / "capability-runtime-matrix.json").write_text(
        json.dumps(
            {
                "applicability_model_revision": "test-v1",
                "capabilities": {
                    "Service": {
                        "paths": ["src/service.py"],
                        "runtimes": ["ghost", "mbair"],
                        "gate_applicability": {gate: True for gate in (
                            "implementation",
                            "integration",
                            "deployment",
                            "validation",
                            "verification",
                            "operator_acceptance",
                            "program_risk",
                        )},
                    }
                },
                "runtimes": {
                    "ghost": {"participation": "required"},
                    "mbair": {"participation": "optional"},
                },
            }
        ),
        encoding="utf-8",
    )
    current = convergence()
    current["runtimes"].append(
        {
            "runtime": "mbair",
            "status": "unknown",
            "health": None,
            "verification_status": None,
            "capabilities_behind": ["Service"],
        }
    )

    projection = CapabilityGraduationProjector(tmp_path).build(
        {"generation_id": "inventory", "supervisor_assignments": []},
        {
            "generation_id": "graph",
            "nodes": [verified_node()],
            "executable_queue": [],
        },
        current,
    )

    capability = projection["capabilities"][0]
    assert capability["required_runtimes"] == ["ghost"]
    assert capability["graduated"] is True
    assert projection["primary_kpi"]["denominator"] == 1


def test_validation_streams_promote_evidence_and_consolidate_actions(tmp_path: Path):
    from axis_supervisor.capability_graduation import CapabilityGraduationProjector
    from axis_supervisor.missions import ActiveMissionState

    write_control(tmp_path)
    (tmp_path / "assignments").mkdir()
    gates = (
        "implementation",
        "integration",
        "deployment",
        "validation",
        "verification",
        "operator_acceptance",
        "program_risk",
    )
    streams = {
        name: {
            "title": title,
            "runtimes": ["ghost"],
            "capabilities": ["Service"],
            "gates": ["deployment", "validation", "verification"],
        }
        for name, title in (
            ("ghost-operator", "Ghost operator validation"),
            ("macbookpro-desktop", "macbookpro Desktop validation"),
            ("nyx-node", "Nyx Node validation"),
            ("cross-interface-cli", "cross-interface CLI validation"),
        )
    }
    (tmp_path / "capability-runtime-matrix.json").write_text(
        json.dumps(
            {
                "applicability_model_revision": "test-v1",
                "capabilities": {
                    "Service": {
                        "paths": ["src/service.py"],
                        "runtimes": ["ghost"],
                        "gate_applicability": {gate: True for gate in gates},
                    }
                },
                "runtimes": {"ghost": {"participation": "required"}},
                "validation_streams": streams,
            }
        ),
        encoding="utf-8",
    )
    inventory = {"generation_id": "inventory", "supervisor_assignments": []}
    graph = {
        "generation_id": "graph",
        "nodes": [verified_node()],
        "executable_queue": [],
    }
    projector = CapabilityGraduationProjector(tmp_path)
    promoted = projector.build(inventory, graph, convergence())
    evidence_ids = [
        stream["evidence"]["evidence_id"] for stream in promoted["validation_streams"]
    ]
    promoted_again = projector.build(inventory, graph, convergence())

    assert len(promoted["validation_streams"]) == 4
    assert {stream["status"] for stream in promoted["validation_streams"]} == {
        "evidence-promoted"
    }
    assert evidence_ids == [
        stream["evidence"]["evidence_id"]
        for stream in promoted_again["validation_streams"]
    ]
    assert len(list((tmp_path / "validation-evidence").glob("*.json"))) == 4

    pending_convergence = convergence()
    pending_convergence["runtimes"][0].update(
        {
            "status": "deployment-required",
            "health": None,
            "verification_status": None,
            "operator_acceptance": None,
            "operator_evidence": None,
            "capabilities_behind": ["Service"],
        }
    )
    pending = projector.build(inventory, graph, pending_convergence)
    mission = ActiveMissionState(tmp_path).reconcile(inventory, graph, pending)
    stream_actions = [
        action
        for action in mission["generated_actions"]
        if action["kind"] == "validate-capability-stream"
    ]
    assert len(stream_actions) == 4
    assert {action["target"] for action in stream_actions} == set(streams)
    assert all(action["capability_context"] for action in stream_actions)
    assert all(
        action["merge_impact_projection"]["affected_capabilities"] == ["Service"]
        for action in stream_actions
    )


def test_stale_downstream_assignments_are_satisfied_by_current_evidence():
    from axis_supervisor.capability_graduation import assignment_is_satisfied

    graph = {"nodes": [{"ref": "issue#1", "verification": {"state": "verified-complete"}}]}
    repository = {"status": "green", "branches": [], "orphan_worktrees": []}
    current = convergence()
    current["runtimes"][0]["running_revision"] = "main"
    graduation_state = {
        "capabilities": [
            {
                "capability": "Service",
                "graduation_state": {"verification": {"state": "passed"}},
            }
        ]
    }

    repository_assignment = {
        "assignment_type": "repository-convergence",
        "project": "ghostspace/axis",
        "source_item": {
            "ref": "local-convergence:ghostspace/axis:branch:hermes/old",
            "convergence_facts": {"scope": "branch", "branch": "hermes/old"},
        },
    }
    assert assignment_is_satisfied(
        repository_assignment,
        graph,
        repository,
        current,
        graduation_state,
    )
    assert assignment_is_satisfied(
        {
            "assignment_type": "capability-deployment",
            "source_item": {
                "target_runtime": "ghost",
                "affected_capabilities": ["Service"],
                "expected_revision": "main",
            },
        },
        graph,
        repository,
        current,
        graduation_state,
    )
    assert assignment_is_satisfied(
        {"assignment_type": "no-op-verification", "work_item": "issue#1"},
        graph,
        repository,
        current,
        graduation_state,
    )

    repository["branches"] = [
        {"repository": "ghostspace/axis", "branch": "hermes/old"}
    ]
    assert not assignment_is_satisfied(
        repository_assignment, graph, repository, current, graduation_state
    )
    current["runtimes"][0]["running_revision"] = "newer-main"
    assert not assignment_is_satisfied(
        {
            "assignment_type": "capability-deployment",
            "source_item": {
                "target_runtime": "ghost",
                "affected_capabilities": ["Service"],
                "expected_revision": "main",
            },
        },
        graph,
        repository,
        current,
        graduation_state,
    )


def test_action_score_rewards_evidence_and_unblock_over_cost_and_risk():
    from axis_supervisor.capability_graduation import action_score

    capability = {
        "graduation_state": {"verification": {"state": "passed"}},
        "graduation_confidence": 90,
        "program_risk": {"score": 5},
    }
    high_value = action_score(
        {
            "affected_capabilities": ["Service"],
            "source_fingerprint": "source",
            "semantic_evidence_fingerprint": "evidence",
            "authority": {"source": ["approval"]},
            "ranking_factors": {"dependency_unlock_count": 3},
            "candidate": {
                "allowed_paths": ["src/service.py"],
                "required_tests": ["pytest -q"],
            },
        },
        {"Service": capability},
    )
    expensive = action_score(
        {
            "affected_capabilities": ["Service"],
            "candidate": {
                "allowed_paths": [f"src/{index}.py" for index in range(12)],
                "required_tests": [f"test-{index}" for index in range(8)],
            },
        },
        {
            "Service": capability
            | {"graduation_confidence": 20, "program_risk": {"score": 90}}
        },
    )

    assert high_value["score"] > expensive["score"]
    assert high_value["benefit"]["verified_capability"] == 1
    assert high_value["benefit"]["unblock_value"] == 60
    assert expensive["penalty"]["risk"] == 90


def test_post_merge_fingerprint_only_invalidates_overlapping_paths():
    from axis_supervisor.noop import targeted_post_merge_fingerprint

    entry = {
        "project": "ghostspace/axis",
        "candidate": {"allowed_paths": ["src/service.py"]},
    }
    base = targeted_post_merge_fingerprint(entry, [])
    unrelated = targeted_post_merge_fingerprint(
        entry,
        [
            {
                "assignment_id": "docs",
                "project": "ghostspace/axis",
                "result_state": "repository-converged",
                "worker": {"commit": "a", "changed_paths": ["docs/readme.md"]},
            }
        ],
    )
    related = targeted_post_merge_fingerprint(
        entry,
        [
            {
                "assignment_id": "service",
                "project": "ghostspace/axis",
                "result_state": "repository-converged",
                "worker": {"commit": "b", "changed_paths": ["src/service.py"]},
            }
        ],
    )

    assert unrelated == base
    assert related != base


def test_scheduler_throttles_implementation_for_downstream_wip():
    from axis_supervisor.graph import _scheduler_state

    queue = [
        {
            "ref": "implementation",
            "kind": "implementation",
            "project": "ghostspace/axis",
            "assignment_type": "code-implementation",
            "ranking_score": 500,
            "selection_rationale": "implementation",
        },
        {
            "ref": "verification",
            "kind": "technical-revalidation",
            "project": "ghostspace/axis",
            "assignment_type": "no-op-verification",
            "ranking_score": 100,
            "selection_rationale": "verification",
        },
    ]
    control = {"tier_a_batch_size": 2, "max_active_assignments": 4}
    active = [
        {
            "assignment_id": f"verify-{index}",
            "assignment_type": "no-op-verification",
            "lifecycle_state": "running-semantic",
        }
        for index in range(2)
    ]

    scheduler = _scheduler_state(
        queue,
        control,
        {"available_model_call_budget": 2, "active_assignments": active},
    )

    assert scheduler["capacity_rebalance"]["implementation_throttled"] is True
    assert scheduler["wip_limits"]["implementation"] == 0
    assert scheduler["selected_batch"][0]["ref"] == "verification"
