import asyncio
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


def test_latest_immutable_record_supersedes_older_approval():
    reconcile = load_module(
        "reconcile_latest_record", ROOT / "scripts" / "axis_supervisor" / "collector.py"
    )
    old_digest = "sha256:" + "a" * 64
    new_digest = "sha256:" + "b" * 64
    newest_record_first = [
        f"Immutable PlanningRecord\nDigest: `{new_digest}`",
        f"Product Owner approval — Approve exact digest {old_digest}",
        f"Immutable PlanningRecord\nDigest: `{old_digest}`",
    ]
    approval = [newest_record_first[1]]
    result = reconcile.authority_from_text("", newest_record_first, approval)
    assert result["record_digest"] == new_digest
    assert result["approval_matches_record"] is False
    assert result["approval_mismatch"] is True


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


def verified_item_and_assignment():
    item = {
        "ref": "ghostspace/axis#119",
        "kind": "issue",
        "project": "ghostspace/axis",
        "state": "closed",
        "classification": "Integrated",
        "acceptance_criteria_present": True,
        "repository_head": "current-main",
        "merge_requests": [
            {"iid": 146, "state": "merged", "web_url": "https://example.test/mr/146"}
        ],
        "dependencies": [],
        "retrieval_errors": [],
        "milestone": None,
    }
    assignment = {
        "assignment_id": "axis119-hermes-proof-1",
        "work_item": item["ref"],
        "state": "complete",
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
        "pipeline": {"status": "success", "url": "https://example.test/pipeline/1"},
        "evidence": [
            {"kind": "implementation-wwwhh", "ref": "https://example.test/implementation"},
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
    return item, assignment


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


def test_tier_b_test_candidate_is_not_duplicated_as_implementation(tmp_path: Path):
    from axis_supervisor.decomposition import SemanticDecompositionEngine
    from axis_supervisor.graph import ExecutionGraphBuilder
    from axis_supervisor.verification import CHECK_NAMES, VERIFICATION_STANDARD

    (tmp_path / "control.json").write_text(
        json.dumps(
            control(
                semantic_priority_refs=[],
                allow_repository_mutation=True,
                allow_technical_revalidation=True,
            )
        ),
        encoding="utf-8",
    )
    parent = {
        "ref": "parent#1",
        "kind": "issue",
        "classification": "Integrated",
        "authority": {
            "approval_matches_record": True,
            "record_digest": "sha256:" + "a" * 64,
        },
        "dependencies": [],
    }
    item = {
        "ref": "ghostspace/axis#2",
        "kind": "issue",
        "project": "ghostspace/axis",
        "title": "Technical revalidation",
        "state": "closed",
        "classification": "Integrated",
        "authority": {},
        "dependencies": [],
        "source_evidence": {"description": "Controlled by parent#1"},
    }
    engine = SemanticDecompositionEngine(tmp_path)
    fingerprint = engine.source_fingerprint(item)
    evidence = engine.save_evidence(item["ref"], {"fixture": True})
    checks = {name: True for name in CHECK_NAMES}
    checks["required_tests_rechecked"] = False
    record = semantic_record(
        item["ref"],
        [
            {
                "slice_id": "rerun-tests",
                "title": "Rerun focused tests",
                "category": "tests",
                "result": "Executable",
                "rationale": "current test evidence is missing",
                "project": "ghostspace/axis",
                "allowed_paths": [],
                "required_tests": ["pytest -q tests/test_example.py"],
            },
            {
                "slice_id": "rerun-more-tests",
                "title": "Rerun another focused test",
                "category": "negative-test",
                "result": "Executable",
                "rationale": "additional current test evidence is missing",
                "project": "ghostspace/axis",
                "allowed_paths": [],
                "required_tests": ["pytest -q tests/test_other.py"],
            },
        ],
        source_fingerprint=fingerprint,
        evidence_fingerprint=evidence,
    )
    record["verification_result"] = {
        "standard": VERIFICATION_STANDARD,
        "tier": "A",
        "disposition": "active-technical-revalidation",
        "checks": checks,
        "evidence": ["https://example.test/mr"],
        "failed_checks": ["required_tests_rechecked"],
        "failure_disposition": "focused tests must be rerun",
    }
    engine.save(record)
    graph = ExecutionGraphBuilder(tmp_path).build(
        {
            "generation_id": "g1",
            "work_items": [parent, item],
            "supervisor_assignments": [],
            "executable_queue": [],
            "execution_graph": {"edges": []},
            "idle_proof": {},
        }
    )
    target_entries = [
        entry for entry in graph["executable_queue"] if entry.get("target_ref") == item["ref"]
    ]
    assert [entry["kind"] for entry in target_entries] == ["technical-revalidation"]


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


def test_model_prompt_is_sent_over_stdin(monkeypatch, tmp_path: Path):
    from axis_supervisor import workers

    captured = {}

    class Process:
        pid = 123
        returncode = 0

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs

        def communicate(self, input=None, timeout=None):
            captured["input"] = input
            captured["timeout"] = timeout
            return ('{"result":"ok"}', None)

    monkeypatch.setattr(workers.subprocess, "Popen", Process)
    (tmp_path / "control.json").write_text(
        json.dumps({"max_semantic_prompt_bytes": 200_000}), encoding="utf-8"
    )
    assignments = tmp_path / "assignments"
    assignments.mkdir()
    (assignments / "assignment-large-prompt.json").write_text("{}", encoding="utf-8")
    manager = workers.HermesWorkerManager(tmp_path, "/bin/hermes", "/bin/supervisorctl")
    manager.hermes_python = lambda: sys.executable
    prompt = "evidence" * 10_000
    output = manager.run_model(
        "gpt-5.4",
        prompt,
        900,
        {
            "assignment_id": "assignment-large-prompt",
            "lease": {"fencing_token": "token"},
        },
        toolsets="",
    )

    assert output == '{"result":"ok"}'
    assert prompt not in captured["command"]
    assert captured["input"] == prompt
    assert captured["kwargs"]["stdin"] is workers.subprocess.PIPE
    assert captured["command"][1].endswith("oneshot_stdin.py")
    assert captured["command"][-2:] == ["--toolsets", ""]
    persisted = json.loads(
        (assignments / "assignment-large-prompt.json").read_text(encoding="utf-8")
    )
    assert persisted["model_attempts"] == 1
    assert len(persisted["model_attempt_log"]) == 1


def test_axis119_proof_is_verified_complete():
    from axis_supervisor.verification import verification_for

    item, assignment = verified_item_and_assignment()
    verification = verification_for(item, [assignment])
    assert verification["state"] == "verified-complete"
    assert verification["failed_checks"] == []
    assert verification["proof_assignment_id"] == "axis119-hermes-proof-1"


def test_verified_item_is_removed_from_supervisor_queue(tmp_path: Path):
    from axis_supervisor.graph import ExecutionGraphBuilder

    item, assignment = verified_item_and_assignment()
    (tmp_path / "control.json").write_text(
        json.dumps(control(semantic_priority_refs=[])), encoding="utf-8"
    )
    graph = ExecutionGraphBuilder(tmp_path).build(
        {
            "generation_id": "g1",
            "work_items": [item],
            "supervisor_assignments": [assignment],
            "executable_queue": [],
            "execution_graph": {"edges": []},
            "idle_proof": {},
        }
    )
    assert graph["queue_depth"] == 0
    assert graph["semantic_decomposition_pending"] == 0
    assert graph["nodes"][0]["verification"]["state"] == "verified-complete"


def test_roadmap_composition_is_exclusive_and_queue_is_separate():
    from axis_supervisor.reporting import build_roadmap_semantics

    verified, assignment = verified_item_and_assignment()
    items = [
        verified,
        {
            "ref": "ghostspace/axis#1",
            "kind": "issue",
            "project": "ghostspace/axis",
            "state": "closed",
            "classification": "Revalidation",
            "dependencies": [],
            "retrieval_errors": [],
            "milestone": None,
        },
        {
            "ref": "ghostspace/axis#2",
            "kind": "issue",
            "project": "ghostspace/axis",
            "state": "opened",
            "classification": "Waiting",
            "dependencies": [],
            "retrieval_errors": [],
            "milestone": "AX-M14",
        },
        {
            "ref": "ghostspace/axis#3",
            "kind": "issue",
            "project": "ghostspace/axis",
            "state": "opened",
            "classification": "Blocked",
            "dependencies": [],
            "retrieval_errors": [],
            "milestone": None,
        },
        {
            "ref": "ghostspace/axis#4",
            "kind": "issue",
            "project": "ghostspace/axis",
            "state": "opened",
            "classification": "Integrated",
            "dependencies": [],
            "retrieval_errors": [],
            "milestone": None,
        },
    ]
    inventory = {
        "generation_id": "inventory-1",
        "generated_at": "2026-01-01T00:00:00Z",
        "work_items": items,
        "supervisor_assignments": [assignment],
        "milestones": [{"title": "AX-M14", "state": "active"}],
    }
    graph = {
        "generation_id": "graph-1",
        "nodes": [{"ref": item["ref"], "semantic_record": None} for item in items],
        "executable_queue": [
            {
                "ref": "semantic-decomposition:ghostspace/axis#1",
                "target_ref": "ghostspace/axis#1",
                "kind": "semantic-decomposition",
                "project": "ghostspace/axis",
            }
        ],
    }
    semantics = build_roadmap_semantics(inventory, graph)
    assert sum(value["count"] for value in semantics["composition"].values()) == 5
    assert semantics["composition"]["verified_complete"]["count"] == 1
    assert semantics["composition"]["closed_pending_revalidation"]["count"] == 1
    assert semantics["composition"]["executable"]["count"] == 0
    assert semantics["ready_queue"]["count"] == 1
    assert "None are lifecycle-executable" in semantics["ready_queue"]["explanation"]
    assert semantics["complete_roadmap"][0]["zero_executable_reason"]


def test_complete_roadmap_is_numeric_and_execution_relevance_is_separate():
    from axis_supervisor.reporting import build_roadmap_semantics

    milestones = [
        {"title": title, "state": "active"}
        for title in ("AX-M14 End", "AX-M10 Deploy", "AX-M9.4 RC", "AX-M5 Execute", "AX-M4 Memory")
    ]
    items = []
    for number in (14, 10, "9.4", 5, 4):
        items.append(
            {
                "ref": f"ghostspace/axis#{str(number).replace('.', '')}",
                "kind": "issue",
                "project": "ghostspace/axis",
                "state": "opened",
                "classification": "Waiting",
                "dependencies": [],
                "retrieval_errors": [],
                "labels": [f"roadmap::AX-M{number}"],
                "confidence": "high",
            }
        )
    queue = [
        {
            "ref": "semantic-decomposition:ghostspace/axis#5",
            "target_ref": "ghostspace/axis#5",
            "kind": "semantic-decomposition",
            "project": "ghostspace/axis",
        },
        {
            "ref": "semantic-decomposition:ghostspace/axis#4",
            "target_ref": "ghostspace/axis#4",
            "kind": "semantic-decomposition",
            "project": "ghostspace/axis",
        },
    ]
    inventory = {
        "generation_id": "inventory",
        "generated_at": "2026-01-01T00:00:00Z",
        "work_items": items,
        "supervisor_assignments": [],
        "milestones": milestones,
    }
    graph = {
        "generation_id": "graph",
        "nodes": [{"ref": item["ref"], "semantic_record": None} for item in items],
        "executable_queue": queue,
    }
    semantics = build_roadmap_semantics(inventory, graph)
    assert [item["key"] for item in semantics["complete_roadmap"]] == [
        "AX-M4",
        "AX-M5",
        "AX-M9.4",
        "AX-M10",
        "AX-M14",
    ]
    assert semantics["current_execution_frontier"] == "AX-M4"
    assert semantics["current_supervisor_focus"]["milestone"] == "AX-M5"
    assert [item["key"] for item in semantics["active_execution"][:2]] == [
        "AX-M5",
        "AX-M4",
    ]
    assert semantics["complete_roadmap"][-1]["status"] == "future"


def test_revalidation_tiers_are_exclusive():
    from axis_supervisor.revalidation import revalidation_tier

    verification = {"state": "pending-current-revalidation"}
    base = {
        "state": "closed",
        "classification": "Integrated",
        "authority": {},
        "merge_requests": [{"state": "merged"}],
    }
    assert revalidation_tier(base, None, verification) == "A"
    assert revalidation_tier(base | {"merge_requests": []}, None, verification) == "B"
    corrective = {
        "verification_result": {"disposition": "corrective-implementation-required"}
    }
    assert revalidation_tier(base, corrective, verification) == "C"
    technical = {
        "verification_result": {"disposition": "active-technical-revalidation"}
    }
    assert revalidation_tier(base, technical, verification) == "B"
    authority = base | {"authority": {"approval_required": True}}
    assert revalidation_tier(authority, None, verification) == "D"
    assert revalidation_tier(authority, corrective, verification) == "D"
    assert revalidation_tier(base, None, {"state": "verified-complete"}) is None


def test_semantic_verification_requires_all_nine_checks():
    from axis_supervisor.models import validate_semantic_record
    from axis_supervisor.verification import CHECK_NAMES, VERIFICATION_STANDARD

    record = semantic_record("ghostspace/axis#1", [])
    record["verification_result"] = {
        "standard": VERIFICATION_STANDARD,
        "tier": "A",
        "disposition": "verified-complete",
        "checks": {name: True for name in CHECK_NAMES},
        "evidence": ["current-main"],
        "failed_checks": [],
        "failure_disposition": "",
    }
    assert validate_semantic_record(record) is record
    invalid_evidence = json.loads(json.dumps(record))
    invalid_evidence["verification_result"]["evidence"] = [None]
    try:
        validate_semantic_record(invalid_evidence)
    except ValueError as exc:
        assert "source-linked references" in str(exc)
    else:
        raise AssertionError("non-source-linked verification evidence was accepted")
    del record["verification_result"]["checks"][CHECK_NAMES[0]]
    try:
        validate_semantic_record(record)
    except ValueError as exc:
        assert "all nine checks" in str(exc)
    else:
        raise AssertionError("incomplete nine-check verification was accepted")


def test_tier_a_batch_is_bounded_and_repository_independent():
    from axis_supervisor.revalidation import select_tier_a_batch

    queue = [
        {"ref": "a1", "project": "ghostspace/axis", "revalidation_tier": "A"},
        {"ref": "a2", "project": "ghostspace/axis", "revalidation_tier": "A"},
        {
            "ref": "g1",
            "project": "ghostspace/axis-governance",
            "revalidation_tier": "A",
        },
        {"ref": "b1", "project": "ghostspace/axis-lab", "revalidation_tier": "B"},
    ]
    selected = select_tier_a_batch(queue, batch_size=2, model_calls_remaining=2)
    assert [item["ref"] for item in selected] == ["a1", "g1"]
    assert select_tier_a_batch(queue, batch_size=2, model_calls_remaining=0) == []


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
    assert "daily worker cycle limit" in payload["reason"]


def test_daily_model_budget_suppresses_before_reconciliation(tmp_path: Path):
    import time

    root = tmp_path / "runtime"
    (root / "runs").mkdir(parents=True)
    assignments = root / "assignments"
    assignments.mkdir()
    (root / "control.json").write_text(
        json.dumps(
            control(
                minimum_free_disk_gib=0,
                daily_worker_cycle_limit=10,
                daily_model_call_limit=1,
            )
        ),
        encoding="utf-8",
    )
    (assignments / "used.json").write_text(
        json.dumps(
            {
                "created_at_epoch": 0,
                "model_attempts": 1,
                "model_attempt_log": [
                    {"started_at_epoch": int(time.time()), "model": "gpt-5.4"}
                ],
            }
        ),
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
    verified, assignment = verified_item_and_assignment()
    waiting = {
        "ref": "ghostspace/axis#2",
        "kind": "issue",
        "project": "ghostspace/axis",
        "state": "opened",
        "classification": "Waiting",
        "dependencies": [],
        "retrieval_errors": [],
        "milestone": None,
    }
    inventory = {
        "generation_id": "g1",
        "generated_at": "2026-01-01T00:00:00Z",
        "roadmap_confidence": {"percent": 80},
        "supervisor_assignments": [assignment],
        "active_leases": [],
        "milestones": [],
        "work_items": [verified, waiting],
        "invariant": {"unknown_count": 0},
    }
    graph = {
        "generation_id": "graph-1",
        "queue_depth": 1,
        "executable_queue": [
            {
                "ref": "semantic-decomposition:ghostspace/axis#2",
                "target_ref": "ghostspace/axis#2",
                "kind": "semantic-decomposition",
                "project": "ghostspace/axis",
            }
        ],
        "nodes": [
            {"ref": verified["ref"], "semantic_record": None},
            {"ref": waiting["ref"], "semantic_record": None},
        ],
    }
    control_value = {"mode": "enabled", "max_active_assignments": 1, "slack_user_id": "U1"}
    first = projection.update(inventory, graph, control_value)
    assert first["updated"] is True
    assert [method for method, _ in calls] == ["conversations.open", "chat.postMessage"]
    fallback, blocks, _ = projection.render(inventory, graph, control_value)
    assert "queue=1" in fallback
    assert blocks[0]["type"] == "header"
    assert any("█" in block.get("text", {}).get("text", "") for block in blocks)
    record = json.loads((tmp_path / "slack-overview-record.json").read_text())
    assert record["composition"]["verified_complete"]["count"] == 1
    assert sum(value["count"] for value in record["composition"].values()) == 2
    calls.clear()
    second = projection.update(inventory, graph, control_value)
    assert second["updated"] is False
    assert calls == []
    graph["queue_depth"] = 2
    graph["executable_queue"].append(
        {
            "ref": "semantic-decomposition:ghostspace/axis#3",
            "target_ref": "ghostspace/axis#3",
            "kind": "semantic-decomposition",
            "project": "ghostspace/axis",
        }
    )
    third = projection.update(inventory, graph, control_value)
    assert third["updated"] is True
    assert calls[0][0] == "chat.update"


def load_supervisor_slack_plugin():
    return load_module(
        "axis_supervisor_commands_test",
        ROOT / "plugin" / "axis-supervisor-commands" / "__init__.py",
    )


def test_supervisor_slack_plugin_authorizes_only_exact_product_owner_dm(tmp_path: Path):
    plugin = load_supervisor_slack_plugin()
    plugin.ROOT = tmp_path
    (tmp_path / "control.json").write_text(
        json.dumps({"slack_user_id": "U1"}), encoding="utf-8"
    )
    (tmp_path / "slack-overview-state.json").write_text(
        json.dumps({"channel": "D1"}), encoding="utf-8"
    )
    class Platform:
        value = "slack"

    class Source:
        platform = Platform()
        chat_type = "dm"
        user_id = "U1"
        chat_id = "D1"

    class Event:
        source = Source()
        text = "/axis roadmap"

    assert plugin._pre_gateway_dispatch(event=Event()) == {"action": "allow"}
    assert plugin._AUTHORIZED.get() is True
    Event.source.chat_id = "D2"
    plugin._pre_gateway_dispatch(event=Event())
    assert plugin._AUTHORIZED.get() is False
    Event.source.chat_id = "D1"
    Event.source.chat_type = "mpim"
    plugin._pre_gateway_dispatch(event=Event())
    assert plugin._AUTHORIZED.get() is False
    Event.text = "/axisfoo roadmap"
    assert plugin._pre_gateway_dispatch(event=Event()) is None
    assert plugin._parse_command("roadmap") == ("roadmap", "")
    assert plugin._parse_command("resume normal work") is None
    assert plugin._parse_command("inspect ghostspace/axis#119") == (
        "inspect",
        "ghostspace/axis#119",
    )


def test_supervisor_slack_plugin_executes_typed_command_without_shell(
    monkeypatch, tmp_path: Path
):
    plugin = load_supervisor_slack_plugin()
    plugin.ROOT = tmp_path
    plugin.COMMAND_SCRIPT = tmp_path / "command.py"
    (tmp_path / "control.json").write_text(
        json.dumps({"slack_user_id": "U1"}), encoding="utf-8"
    )
    captured = {}

    (tmp_path / "slack-overview-state.json").write_text(
        json.dumps({"channel": "D1"}), encoding="utf-8"
    )
    class Platform:
        value = "slack"

    class Source:
        platform = Platform()
        chat_type = "dm"
        user_id = "U1"
        chat_id = "D1"

    class Event:
        source = Source()
        text = "/axis status"

    class Completed:
        returncode = 0
        stderr = ""
        stdout = json.dumps(
            {
                "command": "status",
                "mode": "enabled",
                "allow_repository_mutation": True,
                "composition": {
                    "verified_complete": {
                        "count": 3,
                        "denominator": 421,
                        "percent": 0.7,
                    }
                },
                "supervisor_work": {
                    "supervisor_work_remaining": 418,
                    "ready_work_total": 417,
                },
                "current_execution_frontier": "AX-M4",
                "current_supervisor_focus": {
                    "kind": "tier-a-batch",
                    "work_items": ["axis#75", "axis-governance#246"],
                },
            }
        )

    def run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(plugin.subprocess, "run", run)
    plugin._pre_gateway_dispatch(event=Event())
    response = asyncio.run(plugin._handle_axis("status"))
    assert "AXIS Supervisor Status" in response
    assert captured["command"] == [
        plugin.sys.executable,
        str(plugin.COMMAND_SCRIPT),
        "status",
    ]
    assert "shell" not in captured["kwargs"]
