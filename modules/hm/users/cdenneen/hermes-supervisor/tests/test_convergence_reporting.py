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
            }
        ],
        "edges": [],
        "classification_counts": {"Waiting": 1, "Unknown": 0},
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
    assert parse_command("resume unexpected") is None
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
