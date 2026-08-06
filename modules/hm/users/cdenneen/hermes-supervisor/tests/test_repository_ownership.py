import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def write_control(root: Path) -> None:
    control = json.loads((ROOT / "control.defaults.json").read_text(encoding="utf-8"))
    control.update({"mode": "enabled", "allow_repository_mutation": True})
    (root / "control.json").write_text(json.dumps(control), encoding="utf-8")


def test_canonical_repository_ownership_table_accepts_only_exact_pairs():
    from axis_supervisor.repository_ownership import (
        RESPONSIBILITY_TO_CANONICAL_REPOSITORY,
        RepositoryOwnershipDenied,
        validate_repository_ownership,
    )

    assert RESPONSIBILITY_TO_CANONICAL_REPOSITORY == {
        "supervisor-orchestration/temporary-slack/cron": "cdenneen/home",
        "axis-runtime/product": "ghostspace/axis",
        "contracts/planning-records": "ghostspace/axis-governance",
        "deployment/realistic-validation": "ghostspace/axis-lab",
    }
    for responsibility, repository in RESPONSIBILITY_TO_CANONICAL_REPOSITORY.items():
        evidence = validate_repository_ownership(
            responsibility, repository, context="test-valid"
        )
        assert evidence["status"] == "validated"
        assert evidence["canonical_repository"] == repository

    with pytest.raises(RepositoryOwnershipDenied) as rejected:
        validate_repository_ownership(
            "contracts/planning-records", "ghostspace/axis", context="test-invalid"
        )
    assert rejected.value.evidence["status"] == "denied"
    assert rejected.value.evidence["reason"] == (
        "repository-does-not-match-responsibility"
    )
    assert rejected.value.evidence["canonical_repository"] == (
        "ghostspace/axis-governance"
    )


def test_prompt_factory_includes_boundary_and_rejects_mismatch():
    from axis_supervisor.prompt_factory import PromptFactory
    from axis_supervisor.repository_ownership import RepositoryOwnershipDenied

    assignment = {
        "assignment_id": "assignment-1",
        "target_ref": "ghostspace/axis#1",
        "project": "ghostspace/axis",
        "responsibility": "axis-runtime/product",
    }
    prompts = PromptFactory()
    rendered = [
        prompts.semantic_prompt(assignment),
        prompts.implementation_prompt(assignment, {}),
        prompts.patch_repair_prompt(assignment, {}, "diff", "invalid"),
    ]
    assert all("responsibility_to_canonical_repository" in prompt for prompt in rendered)
    assert all("cdenneen/home" in prompt for prompt in rendered)

    with pytest.raises(RepositoryOwnershipDenied) as rejected:
        prompts.implementation_prompt(
            assignment | {"project": "ghostspace/axis-governance"}, {}
        )
    assert rejected.value.evidence["context"] == "implementation-worker-prompt"


def test_dispatcher_denies_ambiguous_repository_fallback(tmp_path: Path):
    from axis_supervisor.dispatcher import Dispatcher
    from axis_supervisor.repository_ownership import RepositoryOwnershipDenied

    write_control(tmp_path)
    item = {
        "ref": "slice:ghostspace/axis#1:implementation",
        "target_ref": "ghostspace/axis#1",
        "kind": "implementation",
        "assignment_type": "code-implementation",
        "project": "ghostspace/axis",
        "title": "Missing responsibility",
        "classification": "Executable",
        "authority": {"state": "direct"},
        "candidate": {
            "allowed_paths": ["src/example.py"],
            "required_tests": ["pytest -q tests/test_example.py"],
        },
        "source_item": {},
        "source_fingerprint": "fingerprint",
    }
    with pytest.raises(RepositoryOwnershipDenied) as rejected:
        Dispatcher(tmp_path).dispatch(
            {"inventory_generation_id": "g1", "executable_queue": [item]},
            "run-1",
            item,
        )
    assert rejected.value.evidence["reason"] == "ambiguous-fallback-denied"
    assert rejected.value.evidence["repository"] == "ghostspace/axis"


def test_reviewer_rejects_cross_repository_handoff(tmp_path: Path):
    from axis_supervisor.repository_ownership import RepositoryOwnershipDenied
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
        "handoff": {"tests": [], "mr_iid": 7, "mr_url": None},
    }
    workflow = WorkflowState(tmp_path)
    handoff = workflow.persist_handoff(assignment, result)
    mismatched = handoff | {
        "responsibility": "contracts/planning-records",
        "repository": "ghostspace/axis-governance",
    }
    with pytest.raises(RepositoryOwnershipDenied) as rejected:
        workflow.enqueue(assignment, mismatched, "reviewer-one")
    assert rejected.value.evidence["reason"] == (
        "handoff-does-not-match-assignment-ownership"
    )
    assert not (tmp_path / "integration-queue.json").exists()

    tampered_evidence = handoff | {
        "repository_ownership": handoff["repository_ownership"]
        | {"canonical_repository": "ghostspace/axis-governance"}
    }
    with pytest.raises(RepositoryOwnershipDenied) as rejected_evidence:
        workflow.enqueue(assignment, tampered_evidence, "reviewer-one")
    assert rejected_evidence.value.evidence["reason"] == (
        "handoff-does-not-match-assignment-ownership"
    )
