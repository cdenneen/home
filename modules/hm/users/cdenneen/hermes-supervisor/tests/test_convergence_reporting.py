import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def current_sources() -> tuple[dict, dict, dict]:
    source_item = {
        "ref": "ghostspace/axis#1",
        "source_kind": "gitlab-issue",
        "kind": "issue",
        "project": "ghostspace/axis",
        "iid": 1,
        "title": "Current work",
        "source_state": "opened",
        "labels": ["roadmap::AX-M4"],
        "milestone": "AX-M4",
        "priority": None,
        "authority_facts": {},
        "blocking_dependency_refs": [],
        "merge_request_facts": [],
        "acceptance_criteria_present": False,
        "acceptance_facts": {"ids": [], "open_ids": []},
        "updated_at": "2026-08-04T12:00:00+00:00",
        "web_url": "https://example.test/axis/1",
        "source_evidence": {},
        "repository_head": "head",
        "retrieval_errors": [],
        "mutation_allowed": True,
    }
    inventory = {
        "schema": "axis.external-development-supervisor.inventory",
        "schema_version": "1.0.0",
        "generation_id": "inventory-current",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": 1.0,
        "mode": "enabled",
        "allow_repository_mutation": False,
        "repositories": {},
        "repository_allowlist": ["ghostspace/axis"],
        "repositories_inspected": 1,
        "work_items_discovered": 1,
        "work_items": [source_item],
        "dependency_edges": [],
        "supervisor_assignments": [],
        "active_leases": [],
        "milestones": [{"title": "AX-M4", "state": "active"}],
        "open_merge_requests": [],
        "collection_status": {
            "configured_repository_count": 1,
            "all_configured_repositories_inspected": True,
            "dependency_queries": 0,
            "dependency_query_failures": 0,
            "retrieval_error_count": 0,
            "stale_repository_count": 0,
            "state_record_errors": [],
            "active_assignment_count": 0,
            "active_lease_count": 0,
        },
    }
    scheduler_state = {
        "configured_batch_ceiling": 2,
        "available_model_call_budget": 1,
        "selected_batch": [],
        "deferred_items": [],
        "next_eligible_work": None,
        "limiting_constraint": "available-model-call-budget",
        "wip_limits": {
            "analysis": 2,
            "implementation": 2,
            "integration": 1,
            "verification": 2,
        },
        "wip_counts": {
            "analysis": 0,
            "implementation": 0,
            "integration": 0,
            "verification": 0,
        },
        "available_capacity": 2,
        "current_constraint": {
            "name": "governance",
            "evidence": ["one unresolved authority item"],
            "engineering_impact": "verified roadmap progress is paced by this stage",
            "estimated_roadmap_delay_days": None,
            "forecast_confidence": "insufficient-history",
            "recommended_action": "produce an exact approval packet",
        },
    }
    graph = {
        "schema": "axis.external-development-supervisor.execution-graph",
        "schema_version": "1.0.0",
        "generation_id": "graph-current",
        "generated_at": "2026-08-04T12:00:01+00:00",
        "inventory_generation_id": inventory["generation_id"],
        "nodes": [
            {
                "ref": "ghostspace/axis#1",
                "source_kind": "gitlab-issue",
                "kind": "issue",
                "project": "ghostspace/axis",
                "title": "Current work",
                "source_state": "opened",
                "classification": "Waiting",
                "blocker_type": "governance",
                "waiting_reason": "Governance approval",
                "classification_rationale": "no authority",
                "authority": {"state": "unresolved"},
                "dependencies": [],
                "ranking_score": 60,
                "ranking_factors": {},
                "semantic_record": None,
                "source_fingerprint": "fingerprint",
                "verification": {
                    "state": "pending-current-revalidation",
                    "checks": {
                        "current_main_and_merge_rechecked": False,
                        "acceptance_evidence_rechecked": False,
                        "required_tests_rechecked": False,
                        "pipeline_rechecked": False,
                        "governance_linkage_rechecked": False,
                        "closure_rechecked": False,
                        "integration_rechecked": False,
                        "cleanup_rechecked": False,
                        "fresh_cycle_recognition": False,
                    },
                    "failed_checks": [
                        "current_main_and_merge_rechecked",
                        "acceptance_evidence_rechecked",
                        "required_tests_rechecked",
                        "pipeline_rechecked",
                        "governance_linkage_rechecked",
                        "closure_rechecked",
                        "integration_rechecked",
                        "cleanup_rechecked",
                        "fresh_cycle_recognition",
                    ],
                    "evidence": [],
                    "verification_result": {
                        "schema": "axis.external-development-supervisor.verification",
                        "schema_version": "1.0.0",
                        "standard": "Supervisor 1.1 audit standard",
                        "tier": "A",
                        "disposition": "active-technical-revalidation",
                        "checks": {
                            "current_main_and_merge_rechecked": False,
                            "acceptance_evidence_rechecked": False,
                            "required_tests_rechecked": False,
                            "pipeline_rechecked": False,
                            "governance_linkage_rechecked": False,
                            "closure_rechecked": False,
                            "integration_rechecked": False,
                            "cleanup_rechecked": False,
                            "fresh_cycle_recognition": False,
                        },
                        "evidence": [],
                        "failed_checks": [
                            "current_main_and_merge_rechecked",
                            "acceptance_evidence_rechecked",
                            "required_tests_rechecked",
                            "pipeline_rechecked",
                            "governance_linkage_rechecked",
                            "closure_rechecked",
                            "integration_rechecked",
                            "cleanup_rechecked",
                            "fresh_cycle_recognition",
                        ],
                        "failure_disposition": "not verified",
                    },
                },
                "revalidation_tier": None,
                "flow_stage": "backlog",
                "flow_evidence": ["authority constraint: unresolved"],
            }
        ],
        "edges": [],
        "classification_counts": {"Waiting": 1, "Unknown": 0},
        "flow_counts": {"backlog": 1},
        "waiting_reason_counts": {"Governance approval": 1},
        "executable_queue": [],
        "queue_depth": 0,
        "semantic_decomposition_pending": 0,
        "semantic_authority_unresolved": 0,
        "policy_suppressed_executable_count": 0,
        "scheduler_state": scheduler_state,
        "queue_zero_proof": {"executable_queue_empty": True},
        "governed_queue_zero_proven": False,
    }
    control = json.loads((ROOT / "control.defaults.json").read_text(encoding="utf-8"))
    control["mode"] = "enabled"
    return inventory, graph, control


def test_reporting_projects_observed_scheduler_state_without_prediction():
    from axis_supervisor.reporting import build_roadmap_semantics

    inventory, graph, control = current_sources()
    semantics = build_roadmap_semantics(inventory, graph, control)

    assert semantics["scheduler_state"] == graph["scheduler_state"]
    assert semantics["current_supervisor_focus"] == {}
    assert "rate" not in semantics["revalidation_plan"]
    assert semantics["source"]["inventory_revision"] == inventory["generation_id"]
    assert semantics["staleness"]["state"] == "current"
    assert len(semantics["semantic_revision"]) == 64
    assert (
        build_roadmap_semantics(inventory, graph, control)["semantic_revision"]
        == semantics["semantic_revision"]
    )

    schema = json.loads(
        (ROOT / "schemas" / "roadmap-semantics.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.Draft202012Validator(schema).validate(semantics)


def test_current_source_contract_rejects_generation_mismatch():
    from axis_supervisor.reporting import require_current_sources

    inventory, graph, _control = current_sources()
    graph["inventory_generation_id"] = "inventory-stale"

    with pytest.raises(ValueError, match="generation mismatch"):
        require_current_sources(inventory, graph)


def test_current_source_contract_rejects_old_matching_generations():
    from axis_supervisor.reporting import require_current_sources

    inventory, graph, _control = current_sources()
    inventory["generated_at"] = "2020-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="older than"):
        require_current_sources(inventory, graph)


def test_current_source_contract_rejects_future_timestamp():
    from axis_supervisor.reporting import require_current_sources

    inventory, graph, _control = current_sources()
    inventory["generated_at"] = "2099-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="future"):
        require_current_sources(inventory, graph)


def test_live_command_ignores_persisted_overview(monkeypatch, tmp_path, capsys):
    inventory, graph, control = current_sources()
    for name, value in (
        ("inventory.json", inventory),
        ("execution-graph.json", graph),
        ("control.json", control),
        ("slack-overview-record.json", {"total_governed_items": 999}),
    ):
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")

    spec = importlib.util.spec_from_file_location("convergence_commands", SCRIPTS / "commands.py")
    assert spec and spec.loader
    commands = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(commands)
    commands.ROOT = tmp_path
    monkeypatch.setattr(sys, "argv", ["commands.py", "status"])

    assert commands.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["governed_items"] == 1
    assert result["source_inventory_revision"] == inventory["generation_id"]
    assert result["staleness"]["state"] == "current"
    assert result["semantic_revision"]
    assert result["generated_at"]

    graph["inventory_generation_id"] = "inventory-stale"
    (tmp_path / "execution-graph.json").write_text(
        json.dumps(graph), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="generation mismatch"):
        commands.main()


def test_focused_read_only_commands_execute_from_current_projections(
    monkeypatch, tmp_path, capsys
):
    inventory, graph, control = current_sources()
    for name, value in (
        ("inventory.json", inventory),
        ("execution-graph.json", graph),
        ("control.json", control),
    ):
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    (tmp_path / "capability-convergence.json").write_text(
        json.dumps(
            {
                "schema": "axis.external-development-supervisor.capability-convergence",
                "schema_version": "1.0.0",
                "generated_at": "2026-08-06T00:00:00+00:00",
                "convergence_digest": "sha256:" + "a" * 64,
                "repository_convergence_digest": "sha256:" + "b" * 64,
                "expected_repository_revision": "main",
                "expected_runtime_revision": "runtime",
                "capabilities": [],
                "runtimes": [],
                "deployment_assignments": [],
                "promotion_status": {},
            }
        ),
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location(
        "focused_convergence_commands", SCRIPTS / "commands.py"
    )
    assert spec and spec.loader
    commands = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(commands)
    commands.ROOT = tmp_path

    expected = {
        "status": "status",
        "roadmap": "roadmap",
        "milestones": "milestones",
        "milestone AX-M4": "milestone",
        "running": "running",
        "recent": "recent",
        "decisions": "decisions",
        "deployments": "deployments",
        "validation": "validation",
        "capabilities": "capabilities",
        "inspect ghostspace/axis#1": "inspect",
    }
    for command, result_command in expected.items():
        monkeypatch.setattr(sys, "argv", ["commands.py", *command.split()])
        assert commands.main() == 0
        result = json.loads(capsys.readouterr().out)
        assert result["command"] == result_command


def test_command_registry_is_the_single_parse_contract():
    from axis_supervisor.command_registry import command_specs, parse_command

    required = {
        "command",
        "aliases",
        "description",
        "params",
        "authority",
        "confirmation",
        "handler_key",
    }
    assert all(required == set(spec) for spec in command_specs())
    assert parse_command("commands")[0]["command"] == "help"
    assert parse_command("inspect ghostspace/axis#119")[1] == "ghostspace/axis#119"
    assert parse_command("milestone AX-M4")[1] == "AX-M4"
    assert parse_command("resume unexpected") is None
    assert {
        "status",
        "roadmap",
        "milestones",
        "milestone",
        "running",
        "recent",
        "decisions",
        "deployments",
        "validation",
        "capabilities",
        "inspect",
    }.issubset({spec["command"] for spec in command_specs()})
    assert {spec["authority"] for spec in command_specs()} == {
        "product-owner-slack-dm"
    }


def test_slack_resume_cannot_enable_repository_mutation(monkeypatch, tmp_path, capsys):
    inventory, graph, control = current_sources()
    for name, value in (
        ("inventory.json", inventory),
        ("execution-graph.json", graph),
        ("control.json", control),
    ):
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "convergence_commands_resume", SCRIPTS / "commands.py"
    )
    assert spec and spec.loader
    commands = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(commands)
    commands.ROOT = tmp_path
    monkeypatch.setenv("AXIS_SUPERVISOR_COMMAND_SOURCE", "product-owner-slack")
    monkeypatch.setattr(sys, "argv", ["commands.py", "resume"])

    assert commands.main() == 0
    capsys.readouterr()
    persisted = json.loads((tmp_path / "control.json").read_text(encoding="utf-8"))
    assert persisted["mode"] == "enabled"
    assert persisted["allow_repository_mutation"] is False


def test_executive_dashboard_has_mission_v2_proof_sections_and_no_internal_text(
    tmp_path: Path,
):
    from axis_supervisor.dashboard import (
        DASHBOARD_PROOF_SECTIONS,
        render_executive_dashboard,
    )
    from axis_supervisor.reporting import build_roadmap_semantics

    inventory, graph, control = current_sources()
    graph["scheduler_state"]["wip_counts"] = {
        "analysis": 1,
        "implementation": 1,
        "integration": 0,
        "verification": 1,
    }
    capability = {
        "schema": "axis.external-development-supervisor.capability-convergence",
        "schema_version": "1.0.0",
        "generated_at": "2026-08-06T00:00:00+00:00",
        "convergence_digest": "sha256:" + "a" * 64,
        "repository_convergence_digest": "sha256:" + "b" * 64,
        "expected_repository_revision": "main",
        "expected_runtime_revision": "runtime",
        "capabilities": [
            {
                "capability": "Web Presentation",
                "projected_runtimes": ["ghost"],
            },
            {
                "capability": "Node Runtime",
                "projected_runtimes": ["nyx"],
            },
            {
                "capability": "Desktop Presentation",
                "projected_runtimes": ["macbookpro", "mbair"],
            },
        ],
        "runtimes": [
            {
                "runtime": "ghost",
                "status": "converged",
                "verification_status": "verified",
                "health": "healthy",
                "capabilities_behind": [],
                "required_command_available": True,
            },
            {
                "runtime": "nyx",
                "status": "deployment-required",
                "verification_status": "pending",
                "health": "healthy",
                "capabilities_behind": ["Node Runtime"],
                "required_command_available": True,
            },
            {
                "runtime": "macbookpro",
                "status": "converged",
                "verification_status": "verified",
                "health": "healthy",
                "capabilities_behind": [],
                "required_command_available": True,
            },
            {
                "runtime": "mbair",
                "status": "unknown",
                "verification_status": None,
                "health": None,
                "capabilities_behind": ["Desktop Presentation"],
                "required_command_available": False,
            },
        ],
        "deployment_assignments": [{"target_runtime": "nyx"}],
        "promotion_status": {
            "blocked": False,
            "reason": "promotion follows capability impact and ring order",
        },
    }
    (tmp_path / "capability-convergence.json").write_text(
        json.dumps(capability), encoding="utf-8"
    )
    events = [
        {
            "event_type": "assignment_disposition",
            "assignment_id": "noop-axis5",
            "work_item": "ghostspace/axis#5",
            "details": {"assignment_type": "no-op-verification"},
        },
        {
            "event_type": "implementation_completed",
            "assignment_id": "implementation-1",
            "work_item": "ghostspace/axis#29",
            "details": {"worktree": "/internal", "lease": "secret"},
        },
    ]
    semantics = build_roadmap_semantics(inventory, graph, control)
    fallback, blocks, fingerprint = render_executive_dashboard(
        tmp_path, inventory, graph, semantics, events
    )
    sections = [block for block in blocks if block["type"] == "section"]
    assert len(sections) == 16
    assert tuple(
        block["text"]["text"].splitlines()[0].strip("*") for block in sections
    ) == DASHBOARD_PROOF_SECTIONS
    rendered = json.dumps(blocks).lower()
    for forbidden in (
        "worktree",
        "lease",
        "grant",
        "model",
        "accounting",
        "lifecycle",
        "timestamp",
        "next: completed",
    ):
        assert forbidden not in rendered
    visible_blocks = json.dumps(blocks, ensure_ascii=False)
    assert "Ghost Runtime" in visible_blocks
    assert "Ghost Web" in visible_blocks
    assert "Nyx axis-node" in visible_blocks
    assert "macbookpro axis-desktop" in visible_blocks
    assert "mbair axis-desktop" in visible_blocks
    assert "⚪ *Offline*" in visible_blocks
    assert "Routine unchanged evidence checks: *1*" in visible_blocks
    assert "█" in visible_blocks and "░" in visible_blocks
    assert "\n" not in fallback
    assert "*" not in fallback
    assert fallback.startswith("AXIS Executive Dashboard | Graduated capabilities")
    assert len(fingerprint) == 64


def test_progress_bar_is_deterministic_and_bounded():
    from axis_supervisor.dashboard import progress_bar

    assert progress_bar(5, 10, width=10) == "█████░░░░░"
    assert progress_bar(20, 10, width=10) == "██████████"
    assert progress_bar(-1, 10, width=10) == "░░░░░░░░░░"
    assert progress_bar(0, 0, width=10) == "░░░░░░░░░░"
