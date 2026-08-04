import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def control(**overrides):
    value = {
        "schema": "axis.external-development-supervisor.control",
        "schema_version": "1.0.0",
        "mode": "enabled",
        "kill_switch": False,
        "allow_repository_mutation": False,
        "repository_allowlist": ["ghostspace/axis"],
    }
    value.update(overrides)
    return value


def test_authority_requires_exact_approval_digest():
    reconcile = load_module("reconcile", ROOT / "scripts" / "axis_supervisor" / "collector.py")
    record = "Immutable PlanningRecord\nDigest: `sha256:" + "a" * 64 + "`"
    matching = ["Product Owner approval — Approve exact digest sha256:" + "a" * 64]
    mismatch = ["Product Owner approval — Approve exact digest sha256:" + "b" * 64]
    assert reconcile.authority_from_text("", [record, *matching], matching)[
        "approval_matches_record"
    ]
    assert reconcile.authority_from_text("", [record, *mismatch], mismatch)[
        "approval_mismatch"
    ]
    assert not reconcile.authority_from_text("", matching, matching)["approval_matches_record"]


def test_ready_label_does_not_bypass_authority():
    reconcile = load_module("reconcile_ready", ROOT / "scripts" / "axis_supervisor" / "collector.py")
    issue = {"state": "opened", "labels": ["ready"], "title": "Ungoverned"}
    classification, blocker, _ = reconcile.classify_issue(issue, "", [], [], [], [])
    assert classification == "Waiting"
    assert blocker == "governance"


def test_waiting_decomposition_is_recorded():
    reconcile = load_module("reconcile_decomp", ROOT / "scripts" / "axis_supervisor" / "collector.py")
    value = reconcile.decomposition_for(
        "acceptance_id: AC-1\nstate: open\nstatement: bounded slice",
        "Waiting",
        "Dependency",
    )
    assert value["evaluated"] is True
    assert value["open_acceptance_ids"] == ["AC-1"]


def test_paginated_gitlab_arrays_are_fully_decoded():
    reconcile = load_module("reconcile_pages", ROOT / "scripts" / "axis_supervisor" / "collector.py")
    assert reconcile.decode_json_stream('[{"id":1}]\n[{"id":2}]\n') == [
        {"id": 1},
        {"id": 2},
    ]


def semantic_record(
    target_ref: str,
    candidates: list[dict],
    authority_state: str = "inherited",
    source_fingerprint: str = "fixture-fingerprint",
    evidence_fingerprint: str = "fixture-evidence",
):
    return {
        "schema": "axis.external-development-supervisor.semantic-record",
        "schema_version": "1.0.0",
        "target_ref": target_ref,
        "source_fingerprint": source_fingerprint,
        "evidence_fingerprint": evidence_fingerprint,
        "candidate_slices": candidates,
        "evidence_inspected": [{"ref": "test", "claim": "fixture"}],
        "permitted_actions": ["tests"],
        "prohibited_actions": ["activation"],
        "direct_blocker": None,
        "transitive_blocker_chain": [],
        "authority_source": ["parent#1"],
        "authority_resolution": {
            "state": authority_state,
            "source_refs": ["parent#1"],
            "controlling_parent": "parent#1",
            "parent_digest": "sha256:" + "a" * 64,
            "rationale": "approved parent permits bounded child",
            "permitted_effects": ["tests"],
            "prohibited_effects": ["activation"],
        },
        "next_state_changing_event": "test completion",
        "revalidated_at": "2026-01-01T00:00:00Z",
    }


def test_missing_semantic_record_creates_decomposition_assignment(tmp_path: Path):
    from axis_supervisor.graph import ExecutionGraphBuilder

    (tmp_path / "control.json").write_text(
        json.dumps(
            control(semantic_priority_refs=[], allow_repository_mutation=True)
        ),
        encoding="utf-8",
    )
    inventory = {
        "generation_id": "g1",
        "work_items": [
            {
                "ref": "ghostspace/axis#1",
                "kind": "issue",
                "project": "ghostspace/axis",
                "title": "Waiting work",
                "classification": "Waiting",
                "authority": {},
                "dependencies": [],
            }
        ],
        "executable_queue": [],
        "execution_graph": {"edges": []},
        "idle_proof": {},
    }
    graph = ExecutionGraphBuilder(tmp_path).build(inventory)
    assert graph["semantic_decomposition_pending"] == 1
    assert graph["executable_queue"][0]["kind"] == "semantic-decomposition"
    assert graph["governed_queue_zero_proven"] is False


def test_inherited_authority_semantic_slice_becomes_executable(tmp_path: Path):
    from axis_supervisor.decomposition import SemanticDecompositionEngine
    from axis_supervisor.graph import ExecutionGraphBuilder

    (tmp_path / "control.json").write_text(
        json.dumps(
            control(semantic_priority_refs=[], allow_repository_mutation=True)
        ),
        encoding="utf-8",
    )
    ref = "ghostspace/axis#2"
    source_item = {
        "ref": ref,
        "kind": "issue",
        "project": "ghostspace/axis",
        "title": "Parent-governed child",
        "classification": "Waiting",
        "authority": {},
        "dependencies": [],
        "source_evidence": {
            "description": "Controlled by parent#1",
            "parent_refs": ["parent#1"],
        },
    }
    source_fingerprint = SemanticDecompositionEngine.source_fingerprint(source_item)
    engine = SemanticDecompositionEngine(tmp_path)
    evidence_fingerprint = engine.save_evidence(ref, {"fixture": True})
    engine.save(
        semantic_record(
            ref,
            [
                {
                    "slice_id": "tests",
                    "title": "Add bounded negative tests",
                    "category": "negative-test",
                    "result": "Executable",
                    "rationale": "parent authority permits non-activating tests",
                    "project": "ghostspace/axis",
                    "allowed_paths": ["tests/test_example.py"],
                    "required_tests": ["pytest -q tests/test_example.py"],
                }
            ],
            source_fingerprint=source_fingerprint,
            evidence_fingerprint=evidence_fingerprint,
        )
    )
    inventory = {
        "generation_id": "g1",
        "work_items": [
            {
                "ref": "parent#1",
                "kind": "issue",
                "classification": "Integrated",
                "authority": {
                    "approval_matches_record": True,
                    "record_digest": "sha256:" + "a" * 64,
                },
                "dependencies": [],
            },
            source_item
        ],
        "executable_queue": [],
        "execution_graph": {"edges": []},
        "idle_proof": {},
    }
    graph = ExecutionGraphBuilder(tmp_path).build(inventory)
    assert graph["semantic_decomposition_pending"] == 1
    implementation = next(
        item for item in graph["executable_queue"] if item["kind"] == "implementation"
    )
    assert implementation["authority"]["state"] == "inherited"


def test_semantic_worker_cannot_self_grant_inherited_authority(tmp_path: Path):
    from axis_supervisor.decomposition import SemanticDecompositionEngine
    from axis_supervisor.graph import ExecutionGraphBuilder

    (tmp_path / "control.json").write_text(
        json.dumps(control(semantic_priority_refs=[], allow_repository_mutation=True)),
        encoding="utf-8",
    )
    item = {
        "ref": "ghostspace/axis#3",
        "kind": "issue",
        "project": "ghostspace/axis",
        "title": "Unverified inheritance",
        "classification": "Waiting",
        "authority": {},
        "dependencies": [],
    }
    fingerprint = SemanticDecompositionEngine.source_fingerprint(item)
    engine = SemanticDecompositionEngine(tmp_path)
    evidence_fingerprint = engine.save_evidence(item["ref"], {"fixture": True})
    engine.save(
        semantic_record(
            item["ref"],
            [
                {
                    "slice_id": "code",
                    "title": "Mutating code",
                    "category": "implementation",
                    "result": "Executable",
                    "rationale": "claimed inheritance",
                    "project": "ghostspace/axis",
                    "allowed_paths": ["src/example.py"],
                    "required_tests": ["pytest -q"],
                }
            ],
            source_fingerprint=fingerprint,
            evidence_fingerprint=evidence_fingerprint,
        )
    )
    graph = ExecutionGraphBuilder(tmp_path).build(
        {"generation_id": "g1", "work_items": [item], "executable_queue": [], "execution_graph": {"edges": []}, "idle_proof": {}}
    )
    assert graph["executable_queue"] == []
    assert graph["semantic_authority_unresolved"] == 1
    assert graph["governed_queue_zero_proven"] is False


def test_mutation_disabled_suppresses_implementation_slice(tmp_path: Path):
    from axis_supervisor.decomposition import SemanticDecompositionEngine
    from axis_supervisor.graph import ExecutionGraphBuilder

    (tmp_path / "control.json").write_text(
        json.dumps(control(semantic_priority_refs=[], allow_repository_mutation=False)),
        encoding="utf-8",
    )
    parent = {
        "ref": "ghostspace/axis#10",
        "classification": "Integrated",
        "authority": {"approval_matches_record": True},
        "dependencies": [],
    }
    item = {
        "ref": "ghostspace/axis#4",
        "kind": "issue",
        "project": "ghostspace/axis",
        "title": "Inherited implementation",
        "classification": "Waiting",
        "authority": {},
        "dependencies": [],
    }
    fingerprint = SemanticDecompositionEngine.source_fingerprint(item)
    record = semantic_record(
        item["ref"],
        [
            {
                "slice_id": "code",
                "title": "Code slice",
                "category": "implementation",
                "result": "Executable",
                "rationale": "bounded",
                "project": "ghostspace/axis",
                "allowed_paths": ["src/example.py"],
                "required_tests": ["pytest -q"],
            }
        ],
        source_fingerprint=fingerprint,
    )
    record["authority_resolution"]["controlling_parent"] = parent["ref"]
    engine = SemanticDecompositionEngine(tmp_path)
    record["evidence_fingerprint"] = engine.save_evidence(item["ref"], {"fixture": True})
    engine.save(record)
    graph = ExecutionGraphBuilder(tmp_path).build(
        {"generation_id": "g1", "work_items": [parent, item], "executable_queue": [], "execution_graph": {"edges": []}, "idle_proof": {}}
    )
    assert not any(entry.get("kind") == "implementation" for entry in graph["executable_queue"])


def test_semantic_test_commands_reject_shell_control():
    from axis_supervisor.models import test_command_argv

    assert test_command_argv("uv run --extra dev pytest -q tests/test_x.py")[:2] == [
        "uv",
        "run",
    ]
    try:
        test_command_argv("pytest -q; curl https://example.invalid")
    except ValueError:
        pass
    else:
        raise AssertionError("shell control syntax was accepted")


def test_inherited_authority_rejects_substring_parent_match():
    from axis_supervisor.authority import AuthorityResolver

    item = {
        "ref": "ghostspace/axis#2",
        "authority": {},
        "dependencies": [],
        "source_evidence": {"description": "Related discussion mentions axis#10"},
    }
    parent = {
        "ref": "axis#1",
        "authority": {
            "approval_matches_record": True,
            "record_digest": "sha256:" + "a" * 64,
        },
    }
    record = semantic_record(item["ref"], [], source_fingerprint="x")
    record["authority_resolution"].update(
        {
            "controlling_parent": "axis#1",
            "source_refs": ["axis#1"],
            "parent_digest": "sha256:" + "a" * 64,
        }
    )
    result = AuthorityResolver().resolve(item, record, parent)
    assert result["state"] == "unresolved"


def test_kill_switch_suppresses_before_reconciliation(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "control.json").write_text(
        json.dumps(control(kill_switch=True)), encoding="utf-8"
    )
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "preflight.py")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["wakeAgent"] is False
    assert not (root / "inventory.lock").exists()


def test_daily_budget_suppresses_before_reconciliation(tmp_path: Path):
    root = tmp_path / "runtime"
    (root / "runs").mkdir(parents=True)
    (root / "assignments").mkdir()
    (root / "control.json").write_text(
        json.dumps(control(minimum_free_disk_gib=0, daily_worker_cycle_limit=0)),
        encoding="utf-8",
    )
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "preflight.py")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["wakeAgent"] is False
    assert "daily model call limit" in payload["reason"]


def test_reconciliation_failure_suppresses_agent(tmp_path: Path):
    root = tmp_path / "runtime"
    (root / "runs").mkdir(parents=True)
    (root / "assignments").mkdir()
    (root / "control.json").write_text(
        json.dumps(control(minimum_free_disk_gib=0, daily_worker_cycle_limit=99)),
        encoding="utf-8",
    )
    failing = tmp_path / "fail.py"
    failing.write_text("raise SystemExit(2)\n", encoding="utf-8")
    env = os.environ | {
        "AXIS_SUPERVISOR_ROOT": str(root),
        "AXIS_SUPERVISOR_RECONCILE": str(failing),
        "AXIS_SUPERVISOR_CTL": str(ROOT / "scripts" / "supervisorctl.py"),
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "preflight.py")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["wakeAgent"] is False
    assert "live reconciliation failed closed" in payload["reason"]


def test_fenced_lease_conflict_and_release(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "control.json").write_text(
        json.dumps(
            control(
                allow_repository_mutation=True,
                max_active_assignments=1,
                lease_seconds=120,
            )
        ),
        encoding="utf-8",
    )
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    script = str(ROOT / "scripts" / "supervisorctl.py")
    first = subprocess.run(
        [sys.executable, script, "claim", "a1", "--run-id", "r1", "--resource", "path:ghostspace/axis:src"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    lease = json.loads(first.stdout)
    conflict = subprocess.run(
        [sys.executable, script, "claim", "a2", "--run-id", "r2", "--resource", "path:ghostspace/axis:src"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert conflict.returncode != 0
    subprocess.run(
        [sys.executable, script, "release", "a1", "--token", lease["fencing_token"]],
        env=env,
        check=True,
    )
    assert not (root / "leases" / "a1").exists()


def test_expired_lease_recovery(tmp_path: Path):
    root = tmp_path / "runtime"
    lease_dir = root / "leases" / "expired"
    lease_dir.mkdir(parents=True)
    (root / "control.json").write_text(json.dumps(control()), encoding="utf-8")
    (lease_dir / "lease.json").write_text(
        json.dumps(
            {
                "assignment_id": "expired",
                "fencing_token": "token-token-token-token",
                "expires_at_epoch": 1,
            }
        ),
        encoding="utf-8",
    )
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "supervisorctl.py"), "recover"],
        env=env,
        check=True,
    )
    assert lease_dir.exists()
    recovered = json.loads((lease_dir / "lease.json").read_text(encoding="utf-8"))
    assert recovered["recovery_required"] is True
    heartbeat = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "supervisorctl.py"),
            "heartbeat",
            "expired",
            "--token",
            "token-token-token-token",
        ],
        env=env,
        check=False,
    )
    assert heartbeat.returncode != 0


def test_resource_allowlist_requires_exact_project(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "control.json").write_text(
        json.dumps(control(allow_repository_mutation=True, max_active_assignments=1)),
        encoding="utf-8",
    )
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "supervisorctl.py"),
            "claim",
            "bad",
            "--run-id",
            "r1",
            "--resource",
            "path:evil/ghostspace/axis:src",
        ],
        env=env,
        check=False,
    )
    assert result.returncode != 0


def test_concurrent_claims_are_serialized(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "control.json").write_text(
        json.dumps(
            control(
                allow_repository_mutation=True,
                max_active_assignments=1,
                lease_seconds=120,
            )
        ),
        encoding="utf-8",
    )
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    script = str(ROOT / "scripts" / "supervisorctl.py")
    commands = [
        [sys.executable, script, "claim", assignment, "--run-id", assignment, "--resource", "path:ghostspace/axis:src"]
        for assignment in ("a1", "a2")
    ]
    processes = [subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for command in commands]
    returncodes = [process.wait(timeout=10) for process in processes]
    assert sorted(returncodes) == [0, 1]


def test_reporter_rejects_inconsistent_queue(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "control.json").write_text(json.dumps(control()), encoding="utf-8")
    (root / "inventory.json").write_text(
        json.dumps(
            {
                "schema": "axis.external-development-supervisor.inventory",
                "schema_version": "1.0.0",
                "classification_counts": {"Executable": 0, "Unknown": 0},
                "waiting_reason_counts": {"Unknown": 0},
                "queue_depth": 0,
                "executable_queue": [{"ref": "bad"}],
                "invariant": {"unknown_count": 0},
            }
        ),
        encoding="utf-8",
    )
    (root / "execution-graph.json").write_text(
        json.dumps(
            {
                "queue_depth": 0,
                "executable_queue": [{"ref": "bad"}],
                "governed_queue_zero_proven": False,
                "semantic_decomposition_pending": 1,
            }
        ),
        encoding="utf-8",
    )
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": []}), encoding="utf-8")
    env = os.environ | {
        "AXIS_SUPERVISOR_ROOT": str(root),
        "AXIS_SUPERVISOR_CRON_JOBS": str(jobs),
    }
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "report.py")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "queue_depth" in result.stdout


def test_reporter_suppresses_during_inventory_generation(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "inventory.lock").mkdir()
    env = os.environ | {"AXIS_SUPERVISOR_ROOT": str(root)}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "report.py")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "[SILENT]"


def test_report_delivery_is_acknowledged_on_next_successful_run(tmp_path: Path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "control.json").write_text(
        json.dumps(control(report_cron_job_id="report", report_heartbeat_minutes=90)),
        encoding="utf-8",
    )
    counts = {
        name: 0
        for name in (
            "Executable",
            "Running",
            "Blocked",
            "Waiting",
            "Integrated",
            "Superseded",
            "Completed",
            "Invalid",
            "Unknown",
        )
    }
    inventory = {
        "schema": "axis.external-development-supervisor.inventory",
        "schema_version": "1.0.0",
        "generation_id": "g1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "classification_counts": counts,
        "waiting_reason_counts": {"Unknown": 0},
        "queue_depth": 0,
        "executable_queue": [],
        "invariant": {"unknown_count": 0},
        "idle_proof": {"queue_zero_proven": True},
        "work_items": [],
        "repositories": {},
    }
    (root / "inventory.json").write_text(json.dumps(inventory), encoding="utf-8")
    (root / "execution-graph.json").write_text(
        json.dumps(
            {
                "generation_id": "graph-1",
                "queue_depth": 0,
                "executable_queue": [],
                "governed_queue_zero_proven": True,
                "classifier_queue_empty": True,
                "semantic_decomposition_pending": 0,
            }
        ),
        encoding="utf-8",
    )
    jobs = tmp_path / "jobs.json"
    jobs.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "report",
                        "repeat": {"completed": 0},
                        "last_status": None,
                        "last_delivery_error": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    env = os.environ | {
        "AXIS_SUPERVISOR_ROOT": str(root),
        "AXIS_SUPERVISOR_CRON_JOBS": str(jobs),
    }
    script = str(ROOT / "scripts" / "report.py")
    first = subprocess.run(
        [sys.executable, script], env=env, text=True, capture_output=True, check=True
    )
    assert "AXIS Development Supervisor" in first.stdout
    assert (root / "report-delivery-pending.json").exists()
    jobs.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "report",
                        "repeat": {"completed": 1},
                        "last_status": "ok",
                        "last_delivery_error": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    second = subprocess.run(
        [sys.executable, script], env=env, text=True, capture_output=True, check=True
    )
    assert second.stdout.strip() == "[SILENT]"
    assert (root / "report-delivery-state.json").exists()
    assert not (root / "report-delivery-pending.json").exists()


def test_slack_projection_updates_persistent_overview(tmp_path: Path):
    from axis_supervisor.slack_projection import SlackProjection

    projection = SlackProjection(tmp_path)
    projection.env_file = lambda: {"SLACK_BOT_TOKEN": "redacted"}
    calls = []

    def api(_token, method, payload):
        calls.append((method, payload))
        if method == "conversations.open":
            return {"channel": {"id": "D1"}}
        return {"ts": "123.456"}

    projection.api = api
    inventory = {
        "generation_id": "g1",
        "generated_at": "2026-01-01T00:00:00Z",
        "classification_counts": {
            "Integrated": 1,
            "Completed": 1,
            "Running": 0,
            "Waiting": 1,
            "Revalidation": 1,
            "Blocked": 1,
            "Invalid": 0,
            "Superseded": 0,
        },
        "roadmap_confidence": {"percent": 80},
        "supervisor_assignments": [],
        "active_leases": [],
        "milestones": [],
        "work_items": [],
    }
    graph = {
        "queue_depth": 1,
        "nodes": [],
    }
    control_value = {"mode": "enabled", "max_active_assignments": 1, "slack_user_id": "U1"}
    first = projection.update(inventory, graph, control_value)
    assert first["updated"] is True
    assert [method for method, _ in calls] == ["conversations.open", "chat.postMessage"]
    fallback, blocks, _ = projection.render(inventory, graph, control_value)
    assert "queue=1" in fallback
    assert blocks[0]["type"] == "header"
    assert any("█" in block.get("text", {}).get("text", "") for block in blocks)
    calls.clear()
    second = projection.update(inventory, graph, control_value)
    assert second["updated"] is False
    assert calls == []
    graph["queue_depth"] = 2
    third = projection.update(inventory, graph, control_value)
    assert third["updated"] is True
    assert calls[0][0] == "chat.update"
