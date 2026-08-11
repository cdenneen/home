import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from axis_supervisor.collector import write_inventory  # noqa: E402
from axis_supervisor.classifier import classify_source_item  # noqa: E402
from axis_supervisor.graph import ExecutionGraphBuilder  # noqa: E402
from axis_supervisor.schema_registry import validate_record  # noqa: E402


def source_item(
    ref: str,
    *,
    project: str = "ghostspace/axis",
    state: str = "opened",
    labels: list[str] | None = None,
    authority: dict | None = None,
    dependencies: list[str] | None = None,
    merge_requests: list[dict] | None = None,
) -> dict:
    return {
        "ref": ref,
        "source_kind": "gitlab-issue",
        "kind": "issue",
        "project": project,
        "iid": int(ref.rsplit("#", 1)[1]),
        "title": ref,
        "source_state": state,
        "labels": labels or [],
        "milestone": None,
        "priority": None,
        "authority_facts": authority or {},
        "blocking_dependency_refs": dependencies or [],
        "merge_request_facts": merge_requests or [],
        "acceptance_criteria_present": False,
        "acceptance_facts": {"ids": [], "open_ids": []},
        "updated_at": "2026-08-04T00:00:00Z",
        "web_url": f"https://example.test/{ref}",
        "source_evidence": {
            "description": "",
            "notes": [],
            "parent_refs": [],
            "related_mr_urls": [],
        },
        "repository_head": "head",
        "retrieval_errors": [],
        "mutation_allowed": True,
    }


def inventory(items: list[dict], edges: list[dict] | None = None) -> dict:
    return {
        "schema": "axis.external-development-supervisor.inventory",
        "schema_version": "1.0.0",
        "generation_id": "inventory-1",
        "generated_at": "2026-08-04T00:00:00Z",
        "duration_seconds": 1.0,
        "mode": "enabled",
        "allow_repository_mutation": True,
        "repositories": {},
        "repository_allowlist": ["ghostspace/axis"],
        "repositories_inspected": 1,
        "work_items_discovered": len(items),
        "work_items": items,
        "dependency_edges": edges or [],
        "milestones": [],
        "open_merge_requests": [],
        "supervisor_assignments": [],
        "active_leases": [],
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


def configure(root: Path, **overrides) -> None:
    control = json.loads((ROOT / "control.defaults.json").read_text(encoding="utf-8"))
    control.update(
        {
            "mode": "enabled",
            "allow_repository_mutation": True,
            "semantic_priority_refs": [],
        }
    )
    control.update(overrides)
    (root / "control.json").write_text(json.dumps(control), encoding="utf-8")


def test_inventory_is_source_facts_only_and_schema_valid(tmp_path: Path):
    configure(tmp_path)
    value = inventory([source_item("ghostspace/axis#1")])
    write_inventory(tmp_path / "inventory.json", value)

    persisted = json.loads((tmp_path / "inventory.json").read_text(encoding="utf-8"))
    validate_record(persisted, "axis.external-development-supervisor.inventory")
    assert not {
        "classification_counts",
        "executable_queue",
        "queue_depth",
        "execution_graph",
        "classifier_queue_empty",
        "governed_queue_zero_proven",
    }.intersection(persisted)
    assert "classification" not in persisted["work_items"][0]
    assert persisted["work_items"][0]["blocking_dependency_refs"] == []


def test_graph_has_one_deterministic_node_per_source_and_excludes_blocked(
    tmp_path: Path,
):
    configure(tmp_path)
    blocked = source_item(
        "ghostspace/axis#2", labels=["blocked"], dependencies=[]
    )
    waiting = source_item(
        "ghostspace/axis#1", dependencies=["ghostspace/axis#2"]
    )
    edge = {
        "from_ref": waiting["ref"],
        "to_ref": blocked["ref"],
        "relationship": "is_blocked_by",
    }

    graph = ExecutionGraphBuilder(tmp_path).build(inventory([blocked, waiting], [edge]))

    assert [node["ref"] for node in graph["nodes"]] == [
        "ghostspace/axis#1",
        "ghostspace/axis#2",
    ]
    assert graph["edges"] == [edge]
    assert not any(
        entry.get("target_ref") == blocked["ref"]
        or entry["ref"] == blocked["ref"]
        for entry in graph["executable_queue"]
    )
    assert graph["scheduler_state"]["available_model_call_budget"] is None
    assert graph["scheduler_state"]["selected_batch"] == []
    assert (
        graph["scheduler_state"]["limiting_constraint"]
        == "available-model-call-budget-unknown"
    )


def test_scheduler_excludes_closed_historical_work_without_verification_action(
    tmp_path: Path,
):
    configure(tmp_path, tier_a_batch_size=2)
    items = [
        source_item(
            "ghostspace/axis#2",
            project="ghostspace/axis-governance",
            state="closed",
            merge_requests=[{"state": "merged"}],
        ),
        source_item(
            "ghostspace/axis#1",
            state="closed",
            merge_requests=[{"state": "merged"}],
        ),
    ]

    graph = ExecutionGraphBuilder(tmp_path).build(
        inventory(items), {"model_calls_remaining": 1}
    )
    scheduler = graph["scheduler_state"]

    assert scheduler["configured_batch_ceiling"] == 2
    assert scheduler["available_model_call_budget"] == 1
    assert scheduler["selected_batch"] == []
    assert scheduler["deferred_items"] == []
    assert scheduler["next_eligible_work"] is None
    assert scheduler["limiting_constraint"] == "no-executable-work"
    assert graph["flow_counts"]["historical"] == 2


def test_safe_repository_convergence_can_be_executable(tmp_path: Path):
    configure(tmp_path)
    item = source_item("ghostspace/axis#9")
    item.update(
        {
            "ref": "local-convergence:ghostspace/axis:branch:hermes/done",
            "source_kind": "repository-branch",
            "kind": "repository-convergence",
            "iid": 9,
            "convergence_facts": {
                "scope": "branch",
                "branch": "hermes/done",
                "dirty": False,
                "related_open_merge_request": False,
                "integrated_into_default": True,
                "supervisor_owned": True,
                "under_owned_worktree_root": False,
                "remote_fresh": True,
            },
        }
    )

    graph = ExecutionGraphBuilder(tmp_path).build(
        inventory([item]), {"available_model_call_budget": 1}
    )

    assert graph["nodes"][0]["classification"] == "Executable"
    assert graph["nodes"][0]["authority"]["state"] == "direct"
    assert graph["executable_queue"][0]["kind"] == "repository-convergence"


def test_collector_emits_actionable_root_convergence_for_behind_main(
    tmp_path: Path, monkeypatch
):
    from axis_supervisor import collector

    local_facts = {
        "path": "/workspace/axis",
        "present": True,
        "branch": "main",
        "head": "a" * 40,
        "default_remote_head": "b" * 40,
        "dirty": False,
        "remote_fresh": True,
        "root_is_default_branch": True,
        "root_fast_forward_safe": True,
        "root_needs_fast_forward": True,
        "worktrees": [],
        "local_branches": [{"name": "main", "head": "a" * 40}],
        "remote_branches": [],
    }
    captured = {}

    def fake_glab(path: str, **_kwargs):
        if path.startswith("groups/"):
            return [
                {
                    "id": 1,
                    "path": "axis",
                    "path_with_namespace": "ghostspace/axis",
                    "default_branch": "main",
                    "web_url": "https://example.test/ghostspace/axis",
                }
            ]
        return []

    monkeypatch.setattr(
        collector,
        "load_control",
        lambda: {
            "mode": "enabled",
            "allow_repository_mutation": True,
            "repository_allowlist": ["ghostspace/axis"],
            "owned_branch_prefixes": ["hermes/"],
            "owned_worktree_root": str(tmp_path / "worktrees"),
        },
    )
    monkeypatch.setattr(collector, "active_mission_issue_refs", lambda: set())
    monkeypatch.setattr(collector, "glab", fake_glab)
    monkeypatch.setattr(collector, "local_repository_state", lambda *_args: local_facts)
    monkeypatch.setattr(
        collector, "write_inventory", lambda _path, value: captured.setdefault("value", value)
    )

    assert collector.main() == 0
    root = next(
        item
        for item in captured["value"]["work_items"]
        if item["ref"] == "local-convergence:ghostspace/axis:root"
    )
    assert root["convergence_facts"]["root_needs_fast_forward"] is True
    assert classify_source_item(root)["classification"] == "Executable"


def test_collector_bounds_and_reports_dependency_link_timeouts(
    tmp_path: Path, monkeypatch
):
    from axis_supervisor import collector

    captured = {}
    calls = []

    def fake_glab(path: str, **kwargs):
        calls.append((path, kwargs))
        if path.startswith("groups/"):
            return [
                {
                    "id": 1,
                    "path": "axis",
                    "path_with_namespace": "ghostspace/axis",
                    "default_branch": "main",
                    "web_url": "https://example.test/ghostspace/axis",
                }
            ]
        if "/issues?" in path:
            return [
                {
                    "iid": 79,
                    "title": "Bounded dependency retrieval",
                    "state": "opened",
                    "labels": [],
                    "description": "",
                    "web_url": "https://example.test/ghostspace/axis/-/issues/79",
                }
            ]
        if path.endswith("/links"):
            raise subprocess.TimeoutExpired(["glab", "api"], kwargs["timeout"])
        return []

    monkeypatch.setattr(
        collector,
        "load_control",
        lambda: {
            "mode": "enabled",
            "allow_repository_mutation": True,
            "repository_allowlist": ["ghostspace/axis"],
            "owned_branch_prefixes": ["hermes/"],
            "owned_worktree_root": str(tmp_path / "worktrees"),
        },
    )
    monkeypatch.setattr(collector, "active_mission_issue_refs", lambda: set())
    monkeypatch.setattr(collector, "glab", fake_glab)
    monkeypatch.setattr(
        collector,
        "local_repository_state",
        lambda *_args: {"present": False, "remote_fresh": True},
    )
    monkeypatch.setattr(
        collector,
        "write_inventory",
        lambda _path, value: captured.setdefault("value", value),
    )

    assert collector.main() == 0

    inventory = captured["value"]
    assert inventory["work_items"][0]["retrieval_errors"] == [
        "links: TimeoutExpired"
    ]
    assert inventory["collection_status"]["dependency_link_timeouts"] == [
        {
            "ref": "ghostspace/axis#79",
            "error": "links: TimeoutExpired",
            "timeout_seconds": collector.DEPENDENCY_LINK_TIMEOUT_SECONDS,
        }
    ]
    link_call = next(kwargs for path, kwargs in calls if path.endswith("/links"))
    assert link_call["timeout"] == collector.DEPENDENCY_LINK_TIMEOUT_SECONDS


def test_global_queue_zero_proof_is_graph_owned(tmp_path: Path):
    configure(tmp_path)
    blocked = source_item("ghostspace/axis#1", labels=["blocked"])

    graph = ExecutionGraphBuilder(tmp_path).build(inventory([blocked]))

    assert graph["executable_queue"] == []
    assert graph["queue_depth"] == 0
    assert graph["governed_queue_zero_proven"] is True
    assert all(graph["queue_zero_proof"].values())
    assert "classifier_queue_empty" not in graph


def test_queue_zero_is_not_proven_when_policy_hides_executable_work(
    tmp_path: Path,
):
    configure(tmp_path, allow_repository_mutation=False)
    executable = source_item(
        "ghostspace/axis#1", authority={"approval_matches_record": True}
    )

    graph = ExecutionGraphBuilder(tmp_path).build(inventory([executable]))

    assert [item["kind"] for item in graph["executable_queue"]] == [
        "semantic-decomposition"
    ]
    assert graph["governed_queue_zero_proven"] is False


def test_source_fingerprint_covers_canonical_authority_and_dependency_facts():
    from axis_supervisor.decomposition import SemanticDecompositionEngine

    base = source_item("ghostspace/axis#1")
    authority_changed = dict(base) | {
        "authority_facts": {"approval_matches_record": True}
    }
    dependency_changed = dict(base) | {
        "blocking_dependency_refs": ["ghostspace/axis#2"]
    }
    retrieval_changed = dict(base) | {"retrieval_errors": ["notes unavailable"]}
    fingerprint = SemanticDecompositionEngine.source_fingerprint
    assert fingerprint(base) != fingerprint(authority_changed)
    assert fingerprint(base) != fingerprint(dependency_changed)
    assert fingerprint(base) != fingerprint(retrieval_changed)


def test_repository_convergence_has_a_deterministic_branch_executor(tmp_path: Path):
    from axis_supervisor import cycle

    configure(tmp_path)
    cycle.ROOT = tmp_path

    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True
    )
    (repo / "README").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "hermes/done"], cwd=repo, check=True)

    class Gate:
        def require(self, *_args, **_kwargs):
            return None

    result = cycle.converge_repository(
        {
            "assignment_id": "assignment-1",
            "project": "ghostspace/axis",
            "source_item": {
                "convergence_facts": {
                    "scope": "branch",
                    "branch": "hermes/done",
                }
            },
        },
        repo,
        Gate(),
        object(),
    )
    assert result["branch_removed"] is True
    branches = subprocess.check_output(
        ["git", "branch", "--format=%(refname:short)"], cwd=repo, text=True
    ).splitlines()
    assert "hermes/done" not in branches


def test_root_convergence_fast_forwards_and_prunes_stale_worktree_metadata(
    tmp_path: Path, monkeypatch
):
    from axis_supervisor import collector, cycle
    from axis_supervisor.repository_convergence import RepositoryConvergenceProjector

    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    repo = tmp_path / "axis"
    peer = tmp_path / "peer"
    stale_worktree = tmp_path / "stale-worktree"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-b", "main", str(seed)], check=True)
    for checkout in (seed,):
        subprocess.run(["git", "config", "user.name", "Test"], cwd=checkout, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=checkout,
            check=True,
        )
    (seed / "README").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=seed, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=seed, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=seed, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=seed, check=True)
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=remote, check=True
    )
    subprocess.run(["git", "clone", str(remote), str(repo)], check=True)
    subprocess.run(["git", "clone", str(remote), str(peer)], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=peer, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=peer, check=True
    )
    (peer / "README").write_text("updated\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-am", "advance main"], cwd=peer, check=True)
    subprocess.run(["git", "push"], cwd=peer, check=True)
    subprocess.run(
        ["git", "worktree", "add", "-b", "hermes/stale", str(stale_worktree)],
        cwd=repo,
        check=True,
    )
    shutil.rmtree(stale_worktree)

    monkeypatch.setattr(collector, "WORKSPACE", tmp_path)
    project = {"path": "axis", "default_branch": "main"}
    before = collector.local_repository_state(project)
    assert before["root_needs_fast_forward"] is True
    assert any(worktree["prunable"] for worktree in before["worktrees"])

    configure(tmp_path)
    projection = RepositoryConvergenceProjector(tmp_path).build(
        {
            "generation_id": "before",
            "repositories": {
                "ghostspace/axis": {"default_branch": "main", "local_facts": before}
            },
            "supervisor_assignments": [],
            "active_leases": [],
        }
    )
    assert projection["status"] == "amber"
    assert projection["counts"]["orphan_worktrees"] == 1

    class Gate:
        def require(self, *_args, **_kwargs):
            return None

    result = cycle.converge_repository(
        {
            "assignment_id": "root-convergence",
            "project": "ghostspace/axis",
            "source_item": {
                "convergence_facts": {
                    "scope": "root",
                    "path": str(repo),
                    "branch": "main",
                    "default_branch": "main",
                }
            },
        },
        repo,
        Gate(),
        object(),
    )
    assert result["root_fast_forwarded"] is True
    assert result["worktree_metadata_pruned"] is True
    assert "prunable" not in subprocess.check_output(
        ["git", "worktree", "list", "--porcelain"], cwd=repo, text=True
    )

    after = collector.local_repository_state(project)
    assert after["head"] == after["default_remote_head"]
    assert not any(worktree["prunable"] for worktree in after["worktrees"])
    projection = RepositoryConvergenceProjector(tmp_path).build(
        {
            "generation_id": "after",
            "repositories": {
                "ghostspace/axis": {"default_branch": "main", "local_facts": after}
            },
            "supervisor_assignments": [],
            "active_leases": [],
        }
    )
    assert projection["status"] == "green"
