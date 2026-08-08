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
    value = json.loads((ROOT / "control.defaults.json").read_text(encoding="utf-8"))
    value.update(
        {
            "mode": "enabled",
            "allow_repository_mutation": False,
            "repository_allowlist": ["ghostspace/axis"],
        }
    )
    value.update(overrides)
    return value


def write_claim_assignment(root: Path, assignment_id: str, run_id: str) -> None:
    assignments = root / "assignments"
    assignments.mkdir(parents=True, exist_ok=True)
    (assignments / f"{assignment_id}.json").write_text(
        json.dumps(
            {
                "schema": "axis.external-development-supervisor.assignment",
                "schema_version": "1.0.0",
                "assignment_id": assignment_id,
                "lifecycle_state": "ready-implementation",
                "kind": "implementation",
                "project": "ghostspace/axis",
                "work_item": "ghostspace/axis#1",
                "planning_record": None,
                "allowed_paths": [],
                "required_tests": [],
                "authority": {"state": "direct"},
                "governance_state": "Executable",
                "created_by_run": run_id,
            }
        ),
        encoding="utf-8",
    )


def test_authority_requires_exact_approval_digest():
    reconcile = load_module(
        "reconcile", ROOT / "scripts" / "axis_supervisor" / "collector.py"
    )
    record = "Immutable PlanningRecord\nDigest: `sha256:" + "a" * 64 + "`"
    matching = ["Product Owner approval — Approve exact digest sha256:" + "a" * 64]
    mismatch = ["Product Owner approval — Approve exact digest sha256:" + "b" * 64]
    assert reconcile.extract_authority_facts("", [record, *matching], matching)[
        "approval_matches_record"
    ]
    assert reconcile.extract_authority_facts("", [record, *mismatch], mismatch)[
        "approval_mismatch"
    ]
    assert not reconcile.extract_authority_facts("", matching, matching)[
        "approval_matches_record"
    ]


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
    result = reconcile.extract_authority_facts("", newest_record_first, approval)
    assert result["record_digest"] == new_digest
    assert result["approval_matches_record"] is False
    assert result["approval_mismatch"] is True


def test_approval_note_url_binds_exact_digest_not_first_product_owner_note():
    reconcile = load_module(
        "reconcile_approval_url", ROOT / "scripts" / "axis_supervisor" / "collector.py"
    )
    digest = "sha256:" + "a" * 64
    notes = [
        {"id": 3, "author": {"username": "cdenneen"}, "body": "ordinary note"},
        {
            "id": 2,
            "author": {"username": "cdenneen"},
            "body": "Product Owner approval — Approve exact digest sha256:" + "b" * 64,
        },
        {
            "id": 1,
            "author": {"username": "cdenneen"},
            "body": f"Product Owner approval — Approve exact digest {digest}",
        },
    ]
    assert (
        reconcile.approval_note_url(
            notes, {"cdenneen"}, digest, "https://example.test/issue/1"
        )
        == "https://example.test/issue/1#note_1"
    )


def test_ready_label_does_not_bypass_authority():
    from axis_supervisor.classifier import classify_source_item

    item = classify_source_item(
        {
            "ref": "ghostspace/axis#1",
            "source_kind": "gitlab-issue",
            "kind": "issue",
            "project": "ghostspace/axis",
            "title": "Ungoverned",
            "source_state": "opened",
            "labels": ["ready"],
            "authority_facts": {},
            "blocking_dependency_refs": [],
            "merge_request_facts": [],
            "acceptance_facts": {"ids": [], "open_ids": []},
            "source_evidence": {},
            "retrieval_errors": [],
            "mutation_allowed": True,
        }
    )
    assert item["classification"] == "Waiting"
    assert item["blocker_type"] == "governance"


def test_waiting_decomposition_is_recorded():
    from axis_supervisor.classifier import classify_source_item

    reconcile = load_module(
        "reconcile_decomp", ROOT / "scripts" / "axis_supervisor" / "collector.py"
    )
    acceptance = reconcile.extract_acceptance_facts(
        "acceptance_id: AC-1\nstate: open\nstatement: bounded slice"
    )
    value = classify_source_item(
        {
            "ref": "ghostspace/axis#1",
            "source_kind": "gitlab-issue",
            "kind": "issue",
            "project": "ghostspace/axis",
            "title": "Waiting",
            "source_state": "opened",
            "labels": [],
            "authority_facts": {},
            "blocking_dependency_refs": ["ghostspace/axis#2"],
            "merge_request_facts": [],
            "acceptance_facts": acceptance,
            "source_evidence": {},
            "retrieval_errors": [],
            "mutation_allowed": True,
        }
    )["decomposition"]
    assert value["evaluated"] is True
    assert value["open_acceptance_ids"] == ["AC-1"]


def test_paginated_gitlab_arrays_are_fully_decoded():
    reconcile = load_module(
        "reconcile_pages", ROOT / "scripts" / "axis_supervisor" / "collector.py"
    )
    assert reconcile.decode_json_stream('[{"id":1}]\n[{"id":2}]\n') == [
        {"id": 1},
        {"id": 2},
    ]


def test_issue_note_collection_paginates_retries_and_preserves_provenance():
    from axis_supervisor.collector import NOTES_OK, collect_issue_notes

    def note(note_id: int, *, system: bool = False) -> dict:
        return {
            "id": note_id,
            "author": {"id": 42, "username": "cdenneen"},
            "created_at": f"2026-08-08T10:17:{note_id % 60:02d}.000Z",
            "updated_at": f"2026-08-08T10:18:{note_id % 60:02d}.000Z",
            "body": f"note {note_id}",
            "system": system,
        }

    calls = []

    def request(path: str):
        calls.append(path)
        if "&page=1" in path:
            return [note(value) for value in range(1, 101)]
        if calls.count(path) == 1:
            raise RuntimeError("transient GitLab failure")
        return [note(101, system=True)]

    snapshot = collect_issue_notes(
        request,
        "123",
        29,
        fetched_at="2026-08-08T12:00:00+00:00",
    )
    assert snapshot["state"] == NOTES_OK
    assert len(snapshot["notes"]) == 101
    assert len(calls) == 3
    collected = next(value for value in snapshot["notes"] if value["id"] == 101)
    assert collected["author_identity"] == "gitlab-user:42"
    assert collected["created_at"] == "2026-08-08T10:17:41.000Z"
    assert collected["updated_at"] == "2026-08-08T10:18:41.000Z"
    assert collected["body"] == "note 101"
    assert collected["body_digest"] == (
        "sha256:1ff1b53adb1214ca5f2a5908c01fa97158c4153838f126c90e55228d5b3e3e9d"
    )
    assert collected["system"] is True
    assert collected["fetched_at"] == "2026-08-08T12:00:00+00:00"
    assert collected["collector_revision"] == "gitlab-issue-notes-v1"


def test_system_note_cannot_establish_a_canonical_finding():
    from axis_supervisor.finding_ingestion import normalize_gitlab_findings

    digest = "sha256:" + "a" * 64
    finding = f"""Current-main regression finding - system trace
Affected tests:
- test_x
Expected: pass.
Actual: fail.
Classification: PRODUCT_DEFECT
Capability: MCP
Affected gates: verification
Approved slice_id: repair
Authority: `{digest}`
Replay: pytest -q tests/test_x.py
"""
    values = normalize_gitlab_findings(
        [
            {
                "id": 1,
                "author": {"username": "cdenneen"},
                "body": finding,
                "system": True,
            }
        ],
        "ghostspace/axis#29",
        "a" * 40,
        {"cdenneen"},
    )
    assert values[0]["invalid_reason"] == "system-finding-note"


def test_issue_note_collection_fails_closed_for_partial_duplicate_and_malformed_pages():
    from axis_supervisor.collector import NOTES_ERROR, collect_issue_notes

    valid = {
        "id": 1,
        "author": {"id": 42, "username": "cdenneen"},
        "created_at": "2026-08-08T10:17:07.576Z",
        "updated_at": "2026-08-08T10:17:07.576Z",
        "body": "canonical note",
    }
    page = [dict(valid, id=value) for value in range(1, 101)]
    partial = collect_issue_notes(
        lambda path: page
        if "&page=1" in path
        else (_ for _ in ()).throw(RuntimeError()),
        "123",
        29,
        retries=0,
    )
    duplicate = collect_issue_notes(
        lambda path: page if "&page=1" in path else [valid],
        "123",
        29,
        retries=0,
    )
    malformed = collect_issue_notes(
        lambda _path: [{"id": 1, "author": {}, "body": "bad"}],
        "123",
        29,
        retries=0,
    )
    for snapshot in (partial, duplicate, malformed):
        assert snapshot["state"] == NOTES_ERROR
        assert snapshot["notes"] == []


def test_note_collection_cache_freshness_tracks_issue_note_and_finding_state():
    from axis_supervisor.decomposition import SemanticDecompositionEngine

    base = {
        "ref": "ghostspace/axis#29",
        "source_kind": "gitlab-issue",
        "updated_at": "2026-08-08T10:00:00Z",
        "source_evidence": {
            "notes_state": "NOTES_EMPTY",
            "notes_fetched_at": "2026-08-08T12:00:00Z",
            "canonical_finding_state": "absent",
            "notes": [],
        },
        "findings": [],
        "retrieval_errors": [],
    }
    fingerprint = SemanticDecompositionEngine.source_fingerprint
    refreshed = dict(base) | {
        "source_evidence": base["source_evidence"]
        | {"notes_fetched_at": "2026-08-08T12:05:00Z"}
    }
    note_added = dict(base) | {
        "source_evidence": base["source_evidence"]
        | {
            "notes_state": "NOTES_OK",
            "canonical_finding_state": "present",
            "notes": [{"id": 1, "body_digest": "sha256:changed"}],
        }
    }
    issue_edited = dict(base) | {"updated_at": "2026-08-08T10:01:00Z"}
    assert fingerprint(base) == fingerprint(refreshed)
    assert fingerprint(base) != fingerprint(note_added)
    assert fingerprint(base) != fingerprint(issue_edited)


def semantic_record(
    target_ref: str,
    candidates: list[dict],
    authority_state: str = "inherited",
    source_fingerprint: str = "fixture-fingerprint",
    evidence_fingerprint: str = "fixture-evidence",
):
    canonical_responsibilities = {
        "cdenneen/home": "supervisor-orchestration/temporary-slack/cron",
        "ghostspace/axis": "axis-runtime/product",
        "ghostspace/axis-governance": "contracts/planning-records",
        "ghostspace/axis-lab": "deployment/realistic-validation",
    }
    for candidate in candidates:
        if candidate.get("result") == "Executable":
            candidate.setdefault(
                "responsibility", canonical_responsibilities[candidate["project"]]
            )
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
            {
                "kind": "implementation-wwwhh",
                "ref": "https://example.test/implementation",
            },
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
        json.dumps(control(semantic_priority_refs=[], allow_repository_mutation=True)),
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
        json.dumps(control(semantic_priority_refs=[], allow_repository_mutation=True)),
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
            source_item,
        ],
        "executable_queue": [],
        "execution_graph": {"edges": []},
        "idle_proof": {},
    }
    graph = ExecutionGraphBuilder(tmp_path).build(inventory)
    assert graph["semantic_decomposition_pending"] == 0
    implementation = next(
        item for item in graph["executable_queue"] if item["kind"] == "implementation"
    )
    assert implementation["authority"]["state"] == "inherited"


def test_exact_immutable_decision_releases_implementation_to_frontier(tmp_path: Path):
    from axis_supervisor.decisions import DECISION_DIGEST, DECISION_ID, DecisionStore
    from axis_supervisor.decomposition import SemanticDecompositionEngine
    from axis_supervisor.graph import ExecutionGraphBuilder

    (tmp_path / "control.json").write_text(
        json.dumps(control(semantic_priority_refs=[], allow_repository_mutation=True)),
        encoding="utf-8",
    )
    source_item = {
        "ref": DECISION_ID,
        "kind": "issue",
        "project": "ghostspace/axis",
        "title": "MCP tranche v2",
        "classification": "Waiting",
        "authority": {},
        "dependencies": [],
        "source_evidence": {"description": "Exact decision required"},
    }
    source_fingerprint = SemanticDecompositionEngine.source_fingerprint(source_item)
    engine = SemanticDecompositionEngine(tmp_path)
    evidence_fingerprint = engine.save_evidence(DECISION_ID, {"fixture": True})
    record = semantic_record(
        DECISION_ID,
        [
            {
                "slice_id": "mcp-v2",
                "title": "Implement MCP tranche v2",
                "category": "implementation",
                "result": "Executable",
                "rationale": "exact Product Owner decision unlocks this slice",
                "project": "ghostspace/axis",
                "allowed_paths": ["src/mcp"],
                "required_tests": ["pytest -q tests/test_mcp.py"],
            }
        ],
        authority_state="needs-product-owner",
        source_fingerprint=source_fingerprint,
        evidence_fingerprint=evidence_fingerprint,
    )
    record["decision_packet"] = {
        "decision_id": DECISION_ID,
        "current_record": "MCP tranche v2",
        "current_digest": DECISION_DIGEST,
        "decision_requested": "Approve?",
        "recommendation": "Approve",
        "consequences": "Scheduling remains blocked.",
        "downstream_effects": ["frontier rebuild"],
        "unresolved_assumptions": [],
        "response_syntax": f"Approve exact digest {DECISION_DIGEST}",
    }
    engine.save(record)
    DecisionStore(tmp_path).persist(
        {
            "schema": "axis.external-development-supervisor.decision",
            "schema_version": "1.0.0",
            "decision_id": DECISION_ID,
            "digest": DECISION_DIGEST,
            "outcome": "approved-with-conditions",
            "conditions": "Keep rollout bounded.",
            "verification": "Run the MCP conformance suite.",
            "decided_by": "U1",
            "workspace_id": "T1",
            "channel": "D1",
            "message_ts": "1.1",
            "action_id": "axis_decision_conditions_submit",
            "action_ts": "1.2",
            "decided_at": "2026-08-06T00:00:00+00:00",
            "frontier_rebuild_requested_at": "2026-08-06T00:00:00+00:00",
        }
    )
    graph = ExecutionGraphBuilder(tmp_path).build(
        {
            "generation_id": "g1",
            "work_items": [source_item],
            "executable_queue": [],
            "execution_graph": {"edges": []},
            "idle_proof": {},
        }
    )
    implementation = next(
        item for item in graph["executable_queue"] if item["kind"] == "implementation"
    )
    assert implementation["authority"]["state"] == "direct"
    assert (
        implementation["authority"]["decision_record"]["conditions"]
        == "Keep rollout bounded."
    )


def test_confirmed_axis29_mcp_timeout_finding_promotes_to_frontier_after_authority_v2(
    tmp_path: Path,
):
    from axis_supervisor.graph import _semantic_candidates

    finding = {
        "finding_id": "axis29-mcp-timeout-regression",
        "state": "confirmed",
        "repair_candidate": {
            "slice_id": "axis29-mcp-timeout-repair",
            "title": "Repair MCP timeout cancellation regression",
            "category": "implementation",
            "result": "Executable",
            "project": "ghostspace/axis",
            "responsibility": "axis-runtime/product",
            "allowed_paths": ["src/axis_runtime/mcp.py", "tests/test_mcp_adapter.py"],
            "required_tests": ["pytest -q tests/test_mcp_adapter.py"],
            "rationale": "The canonical axis#29 finding is bounded and repairable.",
        },
    }
    candidates = _semantic_candidates({"candidate_slices": [], "findings": [finding]})
    assert candidates[0]["finding_id"] == "axis29-mcp-timeout-regression"
    assert candidates[0]["slice_id"] == "axis29-mcp-timeout-repair"


def test_collected_axis29_finding_promotes_once_to_dispatchable_frontier(
    tmp_path: Path,
):
    from axis_supervisor.collector import extract_findings
    from axis_supervisor.dispatcher import Dispatcher
    from axis_supervisor.graph import ExecutionGraphBuilder

    digest = "sha256:" + "2" * 64
    source_sha = "a" * 40
    planning = """Immutable PlanningRecord v2 - MCP Parallel Tranche

Digest: `sha256:2222222222222222222222222222222222222222222222222222222222222222`
Assignment type: code-implementation
Repository: ghostspace/axis
Authorized slices:
- axis29-task-handles: src/axis_runtime/mcp_tasks.py, tests/test_mcp_task_handles.py
Required tests:
- pytest -q tests/test_mcp_task_handles.py
"""
    finding = """Current-main regression finding - MCP task-handle acceptance failure

Affected tests:
- test_live_schema_drift_blocks_task_startup
- test_polling_budget_is_bounded_and_ambiguous_start_is_not_replayed
- test_notification_is_only_a_hint_and_polling_establishes_completion

Expected: bounded task-handle protocol tests terminate.
Actual: current-main tests timeout after merged work.
Classification: PRODUCT_DEFECT
Capability: MCP / Service Plane
Affected gates: current-main verification
Shared dependents: ghostspace/axis#31
Approved slice_id: axis29-task-handles
Authority: use existing axis#29 PlanningRecord v2 digest `sha256:2222222222222222222222222222222222222222222222222222222222222222` only for bounded same-owner repair scope.
Replay: exact three tests plus combined MCP suite after repair.
"""
    findings = extract_findings(
        [
            {
                "id": 3661401209,
                "author": {"username": "cdenneen"},
                "created_at": "2026-08-08T10:17:07.576Z",
                "body": finding,
            },
            {"id": 3654285470, "author": {"username": "cdenneen"}, "body": planning},
        ],
        "ghostspace/axis#29",
        source_sha,
        {"cdenneen"},
    )
    assert findings[0]["owner_ref"] == "ghostspace/axis#29"
    assert findings[0]["provenance"]["note_author"] == "cdenneen"
    assert findings[0]["provenance"]["source_sha"] == source_sha

    (tmp_path / "control.json").write_text(
        json.dumps(control(allow_repository_mutation=True)), encoding="utf-8"
    )
    source = {
        "ref": "ghostspace/axis#29",
        "source_kind": "gitlab-issue",
        "kind": "issue",
        "project": "ghostspace/axis",
        "title": "MCP timeout regression",
        "source_state": "opened",
        "labels": ["p0"],
        "authority_facts": {
            "approval_matches_record": True,
            "record_digest": digest,
            "record_revision": 2,
            "approval_note": "https://gitlab.com/ghostspace/axis/-/issues/29#note_3661401209",
            "approved_assignment_type": "code-implementation",
            "approved_allowed_paths": [
                "src/axis_runtime/mcp_tasks.py",
                "tests/test_mcp_task_handles.py",
            ],
            "approved_required_tests": ["pytest -q tests/test_mcp_task_handles.py"],
        },
        "blocking_dependency_refs": [],
        "merge_request_facts": [],
        "acceptance_facts": {"ids": [], "open_ids": []},
        "source_evidence": {"notes": []},
        "retrieval_errors": [],
        "mutation_allowed": True,
        "findings": findings,
        "repository_head": source_sha,
    }
    inventory = {"generation_id": "finding-fixture", "work_items": [source]}
    graph = ExecutionGraphBuilder(tmp_path).build(inventory)
    entry = graph["executable_queue"][0]
    assert entry["ref"] == f"finding:{findings[0]['identity']}"
    assert entry["finding_identity"] == findings[0]["identity"]
    assert entry["expected_gates"][0]["gate"] == "current-main verification"
    assert entry["wake_refs"] == ["ghostspace/axis#31"]
    assert graph["edges"][-1]["relationship"] == "finding-shared-dependent"

    from axis_supervisor.missions import ActiveMissionState

    ActiveMissionState(tmp_path).reconcile(
        inventory,
        graph,
        {
            "primary_kpi": {"count": 0, "denominator": 1, "percent": 0.0},
            "capabilities": [],
            "milestones": [],
            "effectiveness_fingerprint": "sha256:" + "e" * 64,
            "repository_convergence_digest": "sha256:" + "d" * 64,
        },
    )

    mission_path = tmp_path / "active-mission.json"
    stale_mission = json.loads(mission_path.read_text())
    stale_mission["generated_actions"][0]["lifecycle"] = "STALE"
    mission_path.write_text(json.dumps(stale_mission), encoding="utf-8")
    assert Dispatcher(tmp_path).dispatch(graph, "finding-fixture", entry) is None

    ActiveMissionState(tmp_path).reconcile(
        inventory,
        graph,
        {
            "primary_kpi": {"count": 0, "denominator": 1, "percent": 0.0},
            "capabilities": [],
            "milestones": [],
            "effectiveness_fingerprint": "sha256:" + "e" * 64,
            "repository_convergence_digest": "sha256:" + "d" * 64,
        },
    )
    nonmatching_mission = json.loads(mission_path.read_text())
    nonmatching_mission["generated_actions"][0]["source_ref"] = "finding:other"
    mission_path.write_text(json.dumps(nonmatching_mission), encoding="utf-8")
    assert Dispatcher(tmp_path).dispatch(graph, "finding-fixture", entry) is None

    ActiveMissionState(tmp_path).reconcile(
        inventory,
        graph,
        {
            "primary_kpi": {"count": 0, "denominator": 1, "percent": 0.0},
            "capabilities": [],
            "milestones": [],
            "effectiveness_fingerprint": "sha256:" + "e" * 64,
            "repository_convergence_digest": "sha256:" + "d" * 64,
        },
    )

    dispatched = Dispatcher(tmp_path).dispatch(graph, "finding-fixture", entry)
    assert dispatched is not None
    assert dispatched["finding_identity"] == findings[0]["identity"]
    assert dispatched["planning_record"]["digest"] == digest
    blocked_reasons = {
        json.loads(line)["details"].get("reason")
        for line in (tmp_path / "operational-events.jsonl").read_text().splitlines()
        if json.loads(line).get("event_type") == "finding_dispatch_blocked"
    }
    assert {"mission-action-stale", "mission-action-nonmatching"}.issubset(
        blocked_reasons
    )
    assert Dispatcher(tmp_path).dispatch(graph, "finding-fixture", entry) is None


def test_confirmed_finding_without_owner_authority_stays_decision_only(tmp_path: Path):
    from axis_supervisor.graph import ExecutionGraphBuilder

    (tmp_path / "control.json").write_text(json.dumps(control()), encoding="utf-8")
    source = {
        "ref": "ghostspace/axis#29",
        "source_kind": "gitlab-issue",
        "kind": "issue",
        "project": "ghostspace/axis",
        "title": "MCP timeout regression",
        "source_state": "opened",
        "labels": [],
        "authority_facts": {},
        "blocking_dependency_refs": [],
        "merge_request_facts": [],
        "acceptance_facts": {"ids": [], "open_ids": []},
        "source_evidence": {},
        "retrieval_errors": [],
        "mutation_allowed": True,
        "repository_head": "a" * 40,
        "findings": [
            {
                "finding_id": "missing-authority",
                "state": "confirmed",
                "owner_ref": "ghostspace/axis#29",
                "identity": "sha256:" + "f" * 64,
                "shared_dependents": [],
                "authority_digest": "sha256:" + "a" * 64,
                "provenance": {
                    "source_sha": "a" * 40,
                    "parser_revision": "gitlab-finding-note-v1",
                },
                "repair_candidate": {
                    "slice_id": "repair",
                    "title": "Repair",
                    "category": "implementation",
                    "result": "Executable",
                    "project": "ghostspace/axis",
                    "responsibility": "axis-runtime/product",
                    "allowed_paths": ["src/axis_runtime/mcp.py"],
                    "required_tests": ["pytest -q tests/test_mcp_adapter.py"],
                    "rationale": "Bounded repair.",
                },
            }
        ],
    }
    graph = ExecutionGraphBuilder(tmp_path).build(
        {"generation_id": "missing-authority", "work_items": [source]}
    )
    assert graph["executable_queue"] == []
    assert graph["nodes"][0]["flow_stage"] == "decision"


def test_canonical_finding_edits_are_idempotent_and_duplicate_notes_supersede():
    from axis_supervisor.finding_ingestion import normalize_gitlab_findings

    digest = "sha256:" + "b" * 64
    planning = f"""Immutable PlanningRecord v2
Digest: `{digest}`
Assignment type: code-implementation
Repository: ghostspace/axis
Authorized slices:
- axis29-task-handles: src/axis_runtime/mcp_tasks.py
Required tests:
- pytest -q tests/test_mcp_task_handles.py
"""

    def body(actual: str) -> str:
        return f"""Current-main regression finding - MCP task-handle acceptance failure
Affected tests:
- test_task_handle
Expected: task protocol terminates.
Actual: {actual}
Classification: PRODUCT_DEFECT
Capability: MCP
Affected gates: verification
Approved slice_id: axis29-task-handles
Authority: bounded repair `{digest}`.
Replay: run the task suite.
"""

    notes = [
        {"id": 10, "author": {"username": "cdenneen"}, "body": body("timeout")},
        {"id": 1, "author": {"username": "cdenneen"}, "body": planning},
    ]
    first = normalize_gitlab_findings(
        notes, "ghostspace/axis#29", "a" * 40, {"cdenneen"}
    )[0]
    edited = normalize_gitlab_findings(
        [
            {
                "id": 10,
                "author": {"username": "cdenneen"},
                "body": body("late timeout"),
            },
            {"id": 1, "author": {"username": "cdenneen"}, "body": planning},
        ],
        "ghostspace/axis#29",
        "a" * 40,
        {"cdenneen"},
    )[0]
    assert edited["identity"] == first["identity"]
    assert edited["revision_identity"] != first["revision_identity"]

    duplicate = normalize_gitlab_findings(
        [
            {"id": 10, "author": {"username": "cdenneen"}, "body": body("timeout")},
            {
                "id": 11,
                "author": {"username": "cdenneen"},
                "body": body("later timeout"),
            },
            {"id": 1, "author": {"username": "cdenneen"}, "body": planning},
        ],
        "ghostspace/axis#29",
        "a" * 40,
        {"cdenneen"},
    )
    states = {finding["note_id"]: finding["state"] for finding in duplicate}
    assert states == {10: "superseded", 11: "confirmed"}


def test_finding_amendment_v2_merges_the_production_lineage_and_fails_closed(
    tmp_path: Path,
):
    from axis_supervisor.collector import NOTES_OK, collect_issue_notes
    from axis_supervisor.finding_ingestion import normalize_gitlab_findings
    from axis_supervisor.graph import ExecutionGraphBuilder

    digest = "sha256:5ac201b880ffcfc6ca4642a7b9beb525d5e1dd0a3f784a01564139ed85c3dd3d"
    source_sha = "a" * 40
    original = f"""Current-main regression finding — MCP task-handle acceptance failure

Affected tests:
- test_live_schema_drift_blocks_task_startup
- test_polling_budget_is_bounded_and_ambiguous_start_is_not_replayed
- test_notification_is_only_a_hint_and_polling_establishes_completion

Expected: bounded task-handle protocol tests terminate with governed drift/polling semantics.
Actual: current-main tests timeout after merged !150–!153.
Classification: PRODUCT_DEFECT
Capability: MCP / Service Plane
Affected gates: current-main verification, axis-lab conformance
Authority: use existing axis#29 PlanningRecord v2 digest `{digest}` only for bounded same-owner repair scope; no new capability expansion.
Replay: exact three tests plus combined MCP suite after repair. Do not close axis#29 until replay passes.
"""
    amendment = f"""Finding amendment v2 — supersedes finding note 3661401209 for structured Supervisor ingestion

Finding ID: axis29-mcp-task-regression
Finding class: PRODUCT_DEFECT
Owner work item: ghostspace/axis#29
Approved slice_id: axis29-task-handles
PlanningRecord revision: 2
PlanningRecord digest: `{digest}`
Repository: ghostspace/axis
Affected gate: current-main verification, axis-lab conformance
Affected tests:
- test_live_schema_drift_blocks_task_startup
- test_polling_budget_is_bounded_and_ambiguous_start_is_not_replayed
- test_notification_is_only_a_hint_and_polling_establishes_completion
Expected behavior: bounded task-handle protocol terminates with governed drift/polling semantics.
Observed behavior: current-main tests timeout after merged !150–!153.
Source evidence: note 3661401209; current main task-handle implementation.
Affected downstream: !155, !157, !158
Replay: exact three tests, combined MCP suite, axis-lab MCP conformance.
Scope: only src/axis_runtime/mcp_tasks.py, tests/test_mcp_task_handles.py, tests/mcp_fixture_server_tasks.py; no credential custody, ProviderRuntime/plugin-host, or live Ghost authority.
Supersession: this metadata amendment preserves original finding provenance and supplies the exact approved slice required for fail-closed promotion.
"""
    planning = f"""Immutable PlanningRecord v2
Digest: `{digest}`
Assignment type: code-implementation
Repository: ghostspace/axis
Authorized slices:
- axis29-task-handles: src/axis_runtime/mcp_tasks.py, tests/test_mcp_task_handles.py, tests/mcp_fixture_server_tasks.py
Required tests:
- pytest -q tests/test_mcp_task_handles.py
"""

    def note(note_id: int, body: str, *, edited: bool = False) -> dict:
        return {
            "id": note_id,
            "author": {"username": "cdenneen"},
            "body": body,
            "created_at": "2026-08-08T10:17:07.576Z",
            "updated_at": "2026-08-08T10:18:07.576Z"
            if edited
            else "2026-08-08T10:17:07.576Z",
        }

    original_note = note(3661401209, original)
    amendment_note = note(3661825323, amendment)
    planning_note = note(3654285470, planning)
    fixture_notes = [amendment_note, planning_note, original_note]
    snapshot = collect_issue_notes(
        lambda path: fixture_notes if "&page=1" in path else [],
        "123",
        29,
        fetched_at="2026-08-08T12:00:00+00:00",
    )
    assert snapshot["state"] == NOTES_OK
    assert {value["id"] for value in snapshot["notes"]} == {
        3654285470,
        3661401209,
        3661825323,
    }
    findings = normalize_gitlab_findings(
        snapshot["notes"],
        "ghostspace/axis#29",
        source_sha,
        {"cdenneen"},
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding["state"] == "confirmed"
    assert (
        finding["identity"]
        == normalize_gitlab_findings(
            [original_note, planning_note],
            "ghostspace/axis#29",
            source_sha,
            {"cdenneen"},
        )[0]["identity"]
    )
    assert finding["provenance"]["parser_revision"] == "gitlab-finding-note-v2"
    assert [value["note_id"] for value in finding["provenance"]["version_chain"]] == [
        3661401209,
        3661825323,
    ]
    assert finding["shared_dependents"] == [
        "ghostspace/axis!155",
        "ghostspace/axis!157",
        "ghostspace/axis!158",
    ]

    (tmp_path / "control.json").write_text(
        json.dumps(control(allow_repository_mutation=True)), encoding="utf-8"
    )
    graph = ExecutionGraphBuilder(tmp_path).build(
        {
            "generation_id": "amendment-v2",
            "work_items": [
                {
                    "ref": "ghostspace/axis#29",
                    "source_kind": "gitlab-issue",
                    "kind": "issue",
                    "project": "ghostspace/axis",
                    "title": "MCP timeout regression",
                    "source_state": "opened",
                    "labels": ["p0"],
                    "authority_facts": {
                        "approval_matches_record": True,
                        "record_digest": digest,
                        "record_revision": 2,
                    },
                    "blocking_dependency_refs": [],
                    "merge_request_facts": [],
                    "acceptance_facts": {"ids": [], "open_ids": []},
                    "source_evidence": {"notes": []},
                    "retrieval_errors": [],
                    "mutation_allowed": True,
                    "repository_head": source_sha,
                    "findings": findings,
                }
            ],
        }
    )
    entry = graph["executable_queue"][0]
    assert entry["ref"] == f"finding:{finding['identity']}"
    assert json.loads((tmp_path / "executable-frontier.json").read_text())[
        "schema"
    ] == ("axis.external-development-supervisor.executable-frontier")

    cases = {
        "wrong-owner": (
            amendment.replace("ghostspace/axis#29", "ghostspace/axis#30"),
            "amendment-owner-mismatch",
        ),
        "wrong-repository": (
            amendment.replace(
                "Repository: ghostspace/axis", "Repository: ghostspace/axis-lab"
            ),
            "amendment-repository-mismatch",
        ),
        "wrong-digest": (
            amendment.replace(digest, "sha256:" + "0" * 64),
            "amendment-digest-mismatch",
        ),
        "unknown-slice": (
            amendment.replace("axis29-task-handles", "unknown-slice"),
            "amendment-unknown-approved-slice",
        ),
        "wrong-slice-repository": (
            planning.replace(
                "Repository: ghostspace/axis", "Repository: ghostspace/axis-lab"
            ),
            "amendment-slice-repository-mismatch",
        ),
        "malformed": (
            amendment.replace("Finding ID:", "Finding identifier:"),
            "malformed-finding-amendment",
        ),
        "v3": (
            amendment.replace("Finding amendment v2", "Finding amendment v3"),
            "unsupported-finding-amendment-version",
        ),
        "bad-supersession": (
            amendment.replace("this metadata amendment", "a replacement"),
            "invalid-amendment-supersession",
        ),
    }
    for label, (candidate, reason) in cases.items():
        notes = [note(3661825323, candidate), original_note, planning_note]
        if label == "wrong-slice-repository":
            notes[0] = amendment_note
            notes[-1] = note(3654285470, candidate)
        assert (
            normalize_gitlab_findings(
                notes, "ghostspace/axis#29", source_sha, {"cdenneen"}
            )[0]["invalid_reason"]
            == reason
        )

    assert (
        normalize_gitlab_findings(
            [amendment_note, note(3661825324, amendment), original_note, planning_note],
            "ghostspace/axis#29",
            source_sha,
            {"cdenneen"},
        )[0]["invalid_reason"]
        == "competing-finding-amendments"
    )
    assert (
        normalize_gitlab_findings(
            [note(3661825323, amendment, edited=True), original_note, planning_note],
            "ghostspace/axis#29",
            source_sha,
            {"cdenneen"},
        )[0]["invalid_reason"]
        == "finding-amendment-edited"
    )


def test_canonical_finding_missing_authority_is_invalid_and_never_executable():
    from axis_supervisor.finding_ingestion import normalize_gitlab_findings

    findings = normalize_gitlab_findings(
        [
            {
                "id": 10,
                "author": {"username": "cdenneen"},
                "body": """Current-main regression finding - malformed
Affected tests:
- test_x
Expected: pass.
Actual: fail.
Classification: PRODUCT_DEFECT
Capability: MCP
Affected gates: verification
Replay: run tests.
""",
            }
        ],
        "ghostspace/axis#29",
        "a" * 40,
        {"cdenneen"},
    )
    assert findings[0]["state"] == "invalid"
    assert findings[0]["invalid_reason"] == "missing-or-invalid-canonical-field"


def test_untrusted_structured_finding_is_ingestion_invalid():
    from axis_supervisor.finding_ingestion import normalize_gitlab_findings

    findings = normalize_gitlab_findings(
        [
            {
                "id": 10,
                "author": {"username": "untrusted"},
                "body": "Current-main regression finding - spoofed",
            }
        ],
        "ghostspace/axis#29",
        "a" * 40,
        {"cdenneen"},
    )
    assert findings[0]["state"] == "invalid"
    assert findings[0]["invalid_reason"] == "untrusted-finding-author"


def test_untrusted_planning_record_scope_is_ingestion_invalid():
    from axis_supervisor.finding_ingestion import normalize_gitlab_findings

    digest = "sha256:" + "a" * 64
    finding = f"""Current-main regression finding - MCP task-handle failure
Affected tests:
- test_task_handle
Expected: task protocol terminates.
Actual: task protocol times out.
Classification: PRODUCT_DEFECT
Capability: MCP
Affected gates: verification
Approved slice_id: axis29-task-handles
Authority: bounded repair `{digest}`.
Replay: run the task suite.
"""
    planning = f"""Immutable PlanningRecord v2
Digest: `{digest}`
Assignment type: code-implementation
Repository: ghostspace/axis
Authorized slices:
- axis29-task-handles: src/axis_runtime/mcp_tasks.py
Required tests:
- pytest -q tests/test_mcp_task_handles.py
"""
    findings = normalize_gitlab_findings(
        [
            {"id": 10, "author": {"username": "cdenneen"}, "body": finding},
            {"id": 9, "author": {"username": "untrusted"}, "body": planning},
        ],
        "ghostspace/axis#29",
        "a" * 40,
        {"cdenneen"},
    )
    assert findings[0]["state"] == "invalid"
    assert findings[0]["invalid_reason"] == "untrusted-planning-record"


def test_finding_slice_id_must_match_exactly_one_authorized_slice():
    from axis_supervisor.finding_ingestion import normalize_gitlab_findings

    digest = "sha256:" + "b" * 64

    def finding(slice_id: str) -> str:
        return f"""Current-main regression finding - MCP task-handle failure
Affected tests:
- test_task_handle
Expected: task protocol terminates.
Actual: task protocol times out.
Classification: PRODUCT_DEFECT
Capability: MCP
Affected gates: verification
Approved slice_id: {slice_id}
Authority: bounded repair `{digest}`.
Replay: run the task suite.
"""

    def planning(slices: list[str]) -> str:
        return f"""Immutable PlanningRecord v2
Digest: `{digest}`
Assignment type: code-implementation
Repository: ghostspace/axis
Authorized slices:
{chr(10).join(f"- {slice}" for slice in slices)}
Required tests:
- pytest -q tests/test_mcp_task_handles.py
"""

    for slices in (
        ["axis29-other: src/axis_runtime/other.py"],
        [
            "axis29-task-handles: src/axis_runtime/mcp_tasks.py",
            "axis29-task-handles: tests/test_mcp_task_handles.py",
        ],
    ):
        value = normalize_gitlab_findings(
            [
                {
                    "id": 10,
                    "author": {"username": "cdenneen"},
                    "body": finding("axis29-task-handles"),
                },
                {"id": 9, "author": {"username": "cdenneen"}, "body": planning(slices)},
            ],
            "ghostspace/axis#29",
            "a" * 40,
            {"cdenneen"},
        )
        assert value[0]["state"] == "invalid"
        assert value[0]["invalid_reason"] == "unresolved-authorized-scope"

    missing = normalize_gitlab_findings(
        [
            {
                "id": 10,
                "author": {"username": "cdenneen"},
                "body": finding("").replace("Approved slice_id: \n", ""),
            },
            {
                "id": 9,
                "author": {"username": "cdenneen"},
                "body": planning(
                    ["axis29-task-handles: src/axis_runtime/mcp_tasks.py"]
                ),
            },
        ],
        "ghostspace/axis#29",
        "a" * 40,
        {"cdenneen"},
    )
    assert missing[0]["state"] == "invalid"
    assert missing[0]["invalid_reason"] == "missing-or-invalid-canonical-field"


def test_finding_dispatch_blocks_missing_corrupt_and_stale_frontiers(tmp_path: Path):
    from axis_supervisor.dispatcher import Dispatcher
    from axis_supervisor.frontier import ExecutableFrontier

    (tmp_path / "control.json").write_text(json.dumps(control()), encoding="utf-8")
    item = {
        "ref": "finding:sha256:frontier",
        "target_ref": "ghostspace/axis#29",
        "finding_identity": "sha256:frontier",
        "project": "ghostspace/axis",
        "assignment_type": "code-implementation",
    }
    graph = {"generation_id": "graph-current", "executable_queue": [item]}
    dispatcher = Dispatcher(tmp_path)
    assert dispatcher.dispatch(graph, "run-1", item) is None

    (tmp_path / "executable-frontier.json").write_text("{", encoding="utf-8")
    assert dispatcher.dispatch(graph, "run-2", item) is None

    ExecutableFrontier(tmp_path).build([item], [], "graph-before-crash")
    assert dispatcher.dispatch(graph, "run-3", item) is None

    ExecutableFrontier(tmp_path).build([item], [], "graph-current")
    assert dispatcher.dispatch(graph, "run-4", item) is None
    (tmp_path / "active-mission.json").write_text("{", encoding="utf-8")
    assert dispatcher.dispatch(graph, "run-5", item) is None

    events = [
        json.loads(line)
        for line in (tmp_path / "operational-events.jsonl").read_text().splitlines()
    ]
    assert [event["details"]["reason"] for event in events] == [
        "frontier-unavailable:CorruptRecordError",
        "frontier-unavailable:CorruptRecordError",
        "frontier-generation-mismatch",
        "mission-unavailable:missing",
        "mission-unavailable:JSONDecodeError",
    ]
    assert events[2]["details"]["frontier_source_generation_id"] == "graph-before-crash"
    assert events[2]["details"]["graph_generation_id"] == "graph-current"


def test_watchdog_ready_semantic_assignment_is_retired_as_invalid_contract():
    from axis_supervisor.collector import retire_unsupported_watchdog_assignment

    assignment = {
        "assignment_id": "assignment-watchdog-recovery-1",
        "assignment_type": "read-only-analysis",
        "lifecycle_state": "ready-semantic",
        "result_state": "pending",
        "work_item_disposition": "not-evaluated",
        "action_contract": None,
    }
    assert retire_unsupported_watchdog_assignment(assignment) is True
    assert assignment["lifecycle_state"] == "cancelled"
    assert assignment["retirement"]["classification"] == "INVALID"
    assert assignment["retirement"]["invalid_contract"]["review_path"] is None
    assert assignment["provenance"]["invalid_contract"]["worker_path"] is None
    assert retire_unsupported_watchdog_assignment(assignment) is False


def test_product_heartbeat_is_compact_and_rate_limited(tmp_path: Path):
    from axis_supervisor.observability import record_product_heartbeat
    from axis_supervisor.slack_projection import SlackProjection

    (tmp_path / "control.json").write_text(json.dumps(control()), encoding="utf-8")
    graduation = {
        "primary_kpi": {"count": 2, "denominator": 18},
        "production_confidence": 40.0,
        "capabilities": [{"first_failing_gate": "validation"}],
    }
    first = record_product_heartbeat(tmp_path, graduation)
    assert first is not None
    assert first["details"] == {
        "product_outcome": {
            "graduated_capabilities": 2,
            "capability_denominator": 18,
            "product_confidence": 40.0,
            "first_failing_gate": "validation",
        }
    }
    assert (
        record_product_heartbeat(
            tmp_path, graduation, now=first["created_at_epoch"] + 29 * 60
        )
        is None
    )
    rendered = SlackProjection.render_event(first)
    assert "Product heartbeat" in rendered
    assert "Assignment" not in rendered


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
        entry
        for entry in graph["executable_queue"]
        if entry.get("target_ref") == item["ref"]
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
        {
            "generation_id": "g1",
            "work_items": [item],
            "executable_queue": [],
            "execution_graph": {"edges": []},
            "idle_proof": {},
        },
        {"available_model_call_budget": 10},
    )
    assert graph["executable_queue"] == []
    assert graph["semantic_authority_unresolved"] == 1
    assert graph["governed_queue_zero_proven"] is False


def test_global_mutation_disabled_keeps_grant_eligible_implementation_slice(
    tmp_path: Path,
):
    from axis_supervisor.decomposition import SemanticDecompositionEngine
    from axis_supervisor.graph import ExecutionGraphBuilder

    (tmp_path / "control.json").write_text(
        json.dumps(control(semantic_priority_refs=[], allow_repository_mutation=False)),
        encoding="utf-8",
    )
    item = {
        "ref": "ghostspace/axis#4",
        "kind": "issue",
        "project": "ghostspace/axis",
        "title": "Inherited implementation",
        "classification": "Waiting",
        "authority": {
            "approval_matches_record": True,
            "record_digest": "sha256:" + "a" * 64,
            "record_revision": 1,
            "approval_note": "https://example.test/approval",
        },
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
    engine = SemanticDecompositionEngine(tmp_path)
    record["evidence_fingerprint"] = engine.save_evidence(
        item["ref"], {"fixture": True}
    )
    engine.save(record)
    graph = ExecutionGraphBuilder(tmp_path).build(
        {
            "generation_id": "g1",
            "work_items": [item],
            "executable_queue": [],
            "execution_graph": {"edges": []},
            "idle_proof": {},
        },
        {"available_model_call_budget": 10},
    )
    implementation = next(
        entry
        for entry in graph["executable_queue"]
        if entry.get("kind") == "implementation"
    )
    assert implementation["assignment_type"] == "code-implementation"
    assert graph["scheduler_state"]["selected_batch"][0]["assignment_type"] == (
        "code-implementation"
    )
    assert graph["scheduler_state"]["limiting_constraint"] == "implementation-ready"


def test_dispatcher_reuses_capacity_only_across_independent_repositories(
    tmp_path: Path,
):
    from axis_supervisor.dispatcher import Dispatcher

    (tmp_path / "control.json").write_text(
        json.dumps(control(max_active_assignments=2)), encoding="utf-8"
    )
    assignments = tmp_path / "assignments"
    assignments.mkdir()
    active = {
        "schema": "axis.external-development-supervisor.assignment",
        "schema_version": "1.0.0",
        "assignment_id": "active-axis",
        "assignment_type": "code-implementation",
        "result_state": "awaiting-integration",
        "work_item_disposition": "requires-integration",
        "lifecycle_state": "awaiting-integration",
        "kind": "implementation",
        "project": "ghostspace/axis",
        "work_item": "ghostspace/axis#1",
        "planning_record": None,
        "allowed_paths": [],
        "required_tests": [],
        "created_by_run": "run-active",
        "lease_id": None,
        "lease_uri": None,
        "mutation_grant_id": None,
        "mutation_grant_uri": None,
    }
    (assignments / "active-axis.json").write_text(json.dumps(active), encoding="utf-8")
    dispatcher = Dispatcher(tmp_path)
    same_project = {
        "ref": "semantic-decomposition:ghostspace/axis#2",
        "target_ref": "ghostspace/axis#2",
        "kind": "semantic-decomposition",
        "assignment_type": "read-only-analysis",
        "project": "ghostspace/axis",
        "title": "same repository",
        "classification": "Executable",
        "authority": {"state": "preparation-only"},
        "source_item": {},
        "source_fingerprint": "same",
        "ranking_score": 10,
    }
    graph = {"inventory_generation_id": "g1", "executable_queue": [same_project]}
    assert dispatcher.dispatch(graph, "run-next", same_project) is None

    independent = same_project | {
        "ref": "semantic-decomposition:ghostspace/axis-governance#2",
        "target_ref": "ghostspace/axis-governance#2",
        "kind": "capability-evidence-analysis",
        "project": "ghostspace/axis-governance",
        "title": "independent repository",
        "source_fingerprint": "independent",
    }
    created = dispatcher.dispatch(graph, "run-next", independent)
    assert created is not None
    assert created["project"] == "ghostspace/axis-governance"
    assert created["assignment_type"] == "read-only-analysis"
    assert created["lifecycle_state"] == "ready-semantic"


def test_semantic_test_commands_reject_shell_control():
    from axis_supervisor.models import test_command_argv

    assert test_command_argv("uv run --extra dev pytest -q tests/test_x.py")[:2] == [
        "uv",
        "run",
    ]
    assert test_command_argv("uv run python -m compileall -q src tests")[:5] == [
        "uv",
        "run",
        "python",
        "-m",
        "compileall",
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
        json.dumps(control(max_semantic_prompt_bytes=200_000)), encoding="utf-8"
    )
    manager = workers.HermesWorkerManager(tmp_path, "/bin/hermes", "/bin/supervisorctl")
    manager.hermes_python = lambda: sys.executable
    manager.gate = type("Gate", (), {"require": lambda *_args, **_kwargs: None})()
    manager.accounting = type(
        "Accounting",
        (),
        {
            "start": lambda *_args, **_kwargs: object(),
            "finish": lambda *_args, **_kwargs: None,
        },
    )()
    monkeypatch.setattr(
        workers,
        "load_canonical_lease",
        lambda *_args, **_kwargs: {"fencing_token": "token"},
    )
    prompt = "evidence" * 10_000
    assignment = {
        "assignment_id": "assignment-large-prompt",
        "project": "ghostspace/axis",
        "created_by_run": "run-1",
    }
    output = manager.run_model(
        "gpt-5.4",
        prompt,
        900,
        assignment,
        "semantic",
        object(),
        toolsets="",
    )

    assert output == '{"result":"ok"}'
    assert prompt not in captured["command"]
    assert captured["input"] == prompt
    assert captured["kwargs"]["stdin"] is workers.subprocess.PIPE
    assert captured["command"][1].endswith("oneshot_stdin.py")
    assert captured["command"][-2:] == ["--toolsets", ""]


def test_axis119_proof_is_verified_complete():
    from axis_supervisor.verification import verification_for

    item, assignment = verified_item_and_assignment()
    verification = verification_for(item, [assignment])
    assert verification["state"] == "verified-complete"
    assert verification["failed_checks"] == []
    assert verification["completion_assignment_id"] == "axis119-hermes-proof-1"
    assert verification["source"] == "historical-adapter"


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
    from axis_supervisor.verification import verification_for

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
        "nodes": [
            {
                **item,
                "source_state": item.get("state"),
                "semantic_record": None,
                "verification": verification_for(item, [assignment]),
            }
            for item in items
        ],
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
        for title in (
            "AX-M14 End",
            "AX-M10 Deploy",
            "AX-M9.4 RC",
            "AX-M5 Execute",
            "AX-M4 Memory",
        )
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
            "milestone": "AX-M5",
        },
        {
            "ref": "semantic-decomposition:ghostspace/axis#4",
            "target_ref": "ghostspace/axis#4",
            "kind": "semantic-decomposition",
            "project": "ghostspace/axis",
            "milestone": "AX-M4",
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
        "scheduler_state": {
            "configured_batch_ceiling": 2,
            "available_model_call_budget": 2,
            "selected_batch": [queue[0]],
            "deferred_items": [queue[1]],
            "next_eligible_work": queue[0],
            "limiting_constraint": "single-item-dispatch",
        },
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
    assert (
        revalidation_tier(
            base,
            None,
            {"verification_result": {"disposition": "verified-complete"}},
        )
        is None
    )


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
        assert "verification record" in str(exc)
    else:
        raise AssertionError("non-source-linked verification evidence was accepted")
    del record["verification_result"]["checks"][CHECK_NAMES[0]]
    try:
        validate_semantic_record(record)
    except ValueError as exc:
        assert "required property" in str(exc)
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
    write_claim_assignment(root, "a1", "r1")
    write_claim_assignment(root, "a2", "r2")
    first = subprocess.run(
        [
            sys.executable,
            script,
            "claim",
            "a1",
            "--run-id",
            "r1",
            "--resource",
            "path:ghostspace/axis:src",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    lease = json.loads(first.stdout)
    conflict = subprocess.run(
        [
            sys.executable,
            script,
            "claim",
            "a2",
            "--run-id",
            "r2",
            "--resource",
            "path:ghostspace/axis:src",
        ],
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
    assert not lease_dir.exists()
    assert len(list((root / "leases").glob("stale-*-expired/lease.json"))) == 1
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
    write_claim_assignment(root, "a1", "r1")
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
    write_claim_assignment(root, "a1", "a1")
    write_claim_assignment(root, "a2", "a2")
    commands = [
        [
            sys.executable,
            script,
            "claim",
            assignment,
            "--run-id",
            assignment,
            "--resource",
            "path:ghostspace/axis:src",
        ]
        for assignment in ("a1", "a2")
    ]
    processes = [
        subprocess.Popen(
            command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        for command in commands
    ]
    returncodes = [process.wait(timeout=10) for process in processes]
    assert sorted(returncodes) == [0, 1]


def test_slack_projection_updates_persistent_overview(tmp_path: Path):
    import pytest

    from axis_supervisor.observability import record_event
    from axis_supervisor.slack_projection import SlackProjection
    from axis_supervisor.verification import verification_for

    projection = SlackProjection(tmp_path)
    projection.env_file = lambda: {"SLACK_BOT_TOKEN": "redacted"}
    calls = []
    messages = {}
    post_count = 0

    def api(_token, method, payload):
        nonlocal post_count
        calls.append((method, payload))
        if method == "auth.test":
            return {
                "ok": True,
                "team": "Test",
                "team_id": "T1",
                "user_id": "UBOT",
            }
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": "D1"}}
        if method in {"chat.postMessage", "chat.update"}:
            if method == "chat.postMessage":
                post_count += 1
            ts = str(payload.get("ts") or f"123.{455 + post_count}")
            messages[ts] = {"ts": ts, "text": payload["text"]}
            return {"ok": True, "channel": "D1", "ts": ts}
        if method == "conversations.history":
            return {"ok": True, "messages": list(messages.values())}
        raise AssertionError(method)

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
        "inventory_generation_id": "g1",
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
            {
                **verified,
                "source_state": verified["state"],
                "semantic_record": None,
                "verification": verification_for(verified, [assignment]),
            },
            {
                **waiting,
                "source_state": waiting["state"],
                "semantic_record": None,
                "verification": verification_for(waiting, [assignment]),
            },
        ],
        "classification_counts": {"Integrated": 1, "Waiting": 1, "Unknown": 0},
        "flow_counts": {
            "backlog": 1,
            "verified-complete": 1,
        },
        "scheduler_state": {
            "configured_batch_ceiling": 2,
            "available_model_call_budget": 1,
            "selected_batch": [],
            "deferred_items": [],
            "next_eligible_work": None,
            "limiting_constraint": "queue-depth",
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
                "name": "implementation-ready-supply",
                "evidence": ["one analysis item"],
                "engineering_impact": "verified roadmap progress is paced by this stage",
                "estimated_roadmap_delay_days": None,
                "forecast_confidence": "insufficient-history",
                "recommended_action": "analyze the next critical-path item",
            },
        },
    }
    control_value = control(slack_user_id="U1")
    (tmp_path / "control.json").write_text(json.dumps(control_value), encoding="utf-8")
    first = projection.update(inventory, graph, control_value)
    assert first["updated"] is True
    assert first["delivery_stage"] == "Slack_message_verified"
    assert [method for method, _ in calls] == [
        "auth.test",
        "conversations.open",
        "chat.postMessage",
        "conversations.history",
    ]
    fallback, blocks, _ = projection.render(inventory, graph, control_value)
    assert fallback.startswith(
        "AXIS | Capabilities 0/0 graduated (0%) | Roadmap 1/2 verified"
    )
    assert blocks[0]["type"] == "header"
    assert [block["text"]["text"] for block in blocks if block["type"] == "header"] == [
        "AXIS",
        "ROADMAP",
        "CAPABILITIES",
        "ACTIVE PRODUCT WORK",
        "DEPLOYMENT RING",
        "VALIDATION",
        "DECISIONS",
        "RECENT PRODUCT PROGRESS",
    ]
    assert len([block for block in blocks if block["type"] == "section"]) == 8
    assert any("█" in block.get("text", {}).get("text", "") for block in blocks)
    assert not any(
        forbidden in json.dumps(blocks).lower()
        for forbidden in (
            "issue",
            "assignment",
            "worktree",
            "lease",
            "grant",
            "enum",
            "ci-poll",
            "model",
            "lifecycle",
        )
    )
    record = json.loads((tmp_path / "slack-overview-record.json").read_text())
    state = json.loads((tmp_path / "slack-overview-state.json").read_text())
    assert state["schema_version"] == "1.1.0"
    assert state["delivery_stage"] == "Slack_message_verified"
    assert state["projection_timestamps"]["dashboard"]["overview"] == first["ts"]
    assert state["dashboard_fallback"]["blocks"][0]["type"] == "header"
    assert record["composition"]["verified_complete"]["count"] == 1
    assert sum(value["count"] for value in record["composition"].values()) == 2
    calls.clear()
    second = projection.update(inventory, graph, control_value)
    assert second["updated"] is False
    assert second["ts"] == first["ts"]
    assert [method for method, _ in calls] == [
        "auth.test",
        "conversations.open",
        "conversations.history",
    ]
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
    assert third["updated"] is False
    assert third["ts"] == first["ts"]
    assert not any(method == "chat.update" for method, _ in calls)

    live = {
        "schema": "axis.external-development-supervisor.assignment",
        "schema_version": "1.0.0",
        "assignment_id": "assignment-live",
        "lifecycle_state": "running-semantic",
        "kind": "semantic-decomposition",
        "project": "ghostspace/axis",
        "work_item": "ghostspace/axis#2",
        "target_ref": "ghostspace/axis#2",
        "planning_record": None,
        "allowed_paths": [],
        "required_tests": [],
        "authority": {"state": "preparation-only"},
        "created_by_run": "run-live",
        "worker": None,
    }
    assignments = tmp_path / "assignments"
    assignments.mkdir()
    live_path = assignments / "assignment-live.json"
    live_path.write_text(json.dumps(live), encoding="utf-8")
    calls.clear()
    worker_change = projection.update(inventory, graph, control_value)
    assert worker_change["updated"] is False
    assert worker_change["ts"] == first["ts"]
    assert not any("running-semantic" in json.dumps(call) for call in calls)

    record_event(
        tmp_path,
        "assignment_retry",
        assignment=live,
        details={
            "retry": 2,
            "failed_gate": "response-contract",
            "failure_classification": "invalid-json",
            "corrective_action": "bounded repair",
            "unsafe_branch_published": False,
        },
        source="worker",
    )
    fail_events = True
    successful_api = projection.api

    def outage_api(token, method, payload):
        if (
            fail_events
            and method == "chat.postMessage"
            and "Retry / recovery" in payload.get("text", "")
        ):
            raise RuntimeError("simulated Slack outage")
        return successful_api(token, method, payload)

    projection.api = outage_api
    with pytest.raises(RuntimeError, match="simulated Slack outage"):
        projection.update(inventory, graph, control_value)
    failed_state = json.loads((tmp_path / "slack-overview-state.json").read_text())
    failed_outbox = json.loads((tmp_path / "slack-outbox.json").read_text())
    assert failed_state["delivery_stage"] == "delivery_failed"
    assert failed_outbox["notifications"][0]["current_stage"] == "delivery_failed"

    fail_events = False
    failed_outbox["notifications"][0]["next_attempt_epoch"] = 0
    (tmp_path / "slack-outbox.json").write_text(
        json.dumps(failed_outbox), encoding="utf-8"
    )
    recovered = projection.update(inventory, graph, control_value)
    recovered_outbox = json.loads((tmp_path / "slack-outbox.json").read_text())
    assert recovered["delivery_stage"] == "Slack_message_verified"
    assert (
        recovered_outbox["notifications"][0]["current_stage"]
        == "Slack_message_verified"
    )
    assert recovered_outbox["notifications"][0]["recovery_summary"] is True
    assert any(
        "Recovered missed activity" in message["text"] for message in messages.values()
    )


def load_supervisor_slack_plugin():
    return load_module(
        "axis_supervisor_commands_test",
        ROOT / "plugin" / "axis-supervisor-commands" / "__init__.py",
    )


def test_supervisor_slack_plugin_authorizes_only_exact_product_owner_dm(tmp_path: Path):
    from axis_supervisor.command_registry import parse_command

    plugin = load_supervisor_slack_plugin()
    plugin.ROOT = tmp_path
    (tmp_path / "control.json").write_text(
        json.dumps(control(slack_user_id="U1")), encoding="utf-8"
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
    assert parse_command("roadmap")[0]["command"] == "roadmap"
    assert parse_command("resume normal work") is None
    assert parse_command("inspect ghostspace/axis#119")[1] == "ghostspace/axis#119"


def test_supervisor_slack_plugin_executes_typed_command_without_shell(
    monkeypatch, tmp_path: Path
):
    plugin = load_supervisor_slack_plugin()
    plugin.ROOT = tmp_path
    plugin.COMMAND_SCRIPT = tmp_path / "command.py"
    (tmp_path / "control.json").write_text(
        json.dumps(control(slack_user_id="U1")), encoding="utf-8"
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
    assert "AXIS Product Status" in response
    assert captured["command"] == [
        plugin.sys.executable,
        str(plugin.COMMAND_SCRIPT),
        "status",
    ]
    assert "shell" not in captured["kwargs"]


def test_supervisor_slack_inspect_never_renders_privileged_internals():
    plugin = load_supervisor_slack_plugin()
    response = plugin._render(
        {
            "command": "inspect",
            "view": "evidence",
            "summary": {
                "ref": "ghostspace/axis#119",
                "title": "CLI product proof",
                "milestone": "AX-M4",
                "product_state": "Waiting",
                "evidence_state": "pending",
                "capabilities": ["CLI"],
                "active_product_actions": 1,
                "projected_merge_impacts": 1,
            },
            "evidence": {
                "assignment_id": "secret-assignment",
                "worktree": "/internal/path",
                "lease": "secret-lease",
                "mutation_grant_id": "secret-grant",
                "ci_poll": {"status": "running"},
            },
        }
    )
    lowered = response.lower()
    assert "cli product proof" in lowered
    assert "intentionally omitted from slack" in lowered
    for forbidden in ("assignment", "worktree", "lease", "grant", "ci_poll"):
        assert forbidden not in lowered


def test_supervisor_slack_uses_na_and_gray_optional_runtime_semantics():
    plugin = load_supervisor_slack_plugin()
    capabilities = plugin._render(
        {
            "command": "capabilities",
            "production_confidence": 100.0,
            "operator_confidence": None,
            "items": [],
        }
    )
    deployments = plugin._render(
        {
            "command": "deployments",
            "verified": 4,
            "total": 4,
            "optional": 1,
            "items": [
                {
                    "ring": "mbair",
                    "status": "offline",
                    "display_state": "gray",
                    "required": False,
                    "capability_gaps": [],
                }
            ],
        }
    )
    assert "Operator confidence N/A" in capabilities
    assert "⚪ offline (optional)" in deployments
    assert "4/4 required verified | optional 1" in deployments


def test_supervisor_slack_plugin_registers_stable_decision_actions():
    from axis_supervisor.decisions import (
        APPROVE_ACTION_ID,
        APPROVE_CONDITIONS_ACTION_ID,
        CONDITIONS_SUBMIT_ACTION_ID,
        REJECT_ACTION_ID,
    )

    plugin = load_supervisor_slack_plugin()

    class Context:
        def __init__(self):
            self.actions = []

        def register_hook(self, *_args, **_kwargs):
            pass

        def register_command(self, *_args, **_kwargs):
            pass

        def register_slack_action_handler(self, action_id, _handler):
            self.actions.append(action_id)

    context = Context()
    plugin.register(context)
    assert context.actions == [
        APPROVE_ACTION_ID,
        APPROVE_CONDITIONS_ACTION_ID,
        REJECT_ACTION_ID,
        CONDITIONS_SUBMIT_ACTION_ID,
    ]
