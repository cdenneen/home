#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

from axis_supervisor.canary import expire_grant, load_grant, write_grant
from axis_supervisor.decomposition import SemanticDecompositionEngine
from axis_supervisor.models import validate_allowed_path, validate_assignment
from axis_supervisor.mutation import MutationGate, OperationClass
from axis_supervisor.schema_registry import read_record, write_record


ROOT = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_ROOT",
        Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor",
    )
)


def assignment_path(assignment_id: str) -> Path:
    return ROOT / "assignments" / f"{assignment_id}.json"


def create(grant_file: Path) -> dict:
    control = read_record(
        ROOT / "control.json", "axis.external-development-supervisor.control"
    )
    if control.get("allow_repository_mutation"):
        raise RuntimeError("global mutation must remain disabled for a canary")
    grant = json.loads(grant_file.read_text(encoding="utf-8"))
    grant["allowed_paths"] = [
        validate_allowed_path(path) for path in grant["allowed_paths"]
    ]
    grant.setdefault("events", []).append(
        {"recorded_at_epoch": int(time.time()), "event": "grant-created"}
    )
    gate = MutationGate(ROOT, source="operator-cli")
    decision = gate.decide(OperationClass.CONTROL)
    gate.require(decision, OperationClass.CONTROL)
    write_grant(ROOT, grant)
    graph = read_record(
        ROOT / "execution-graph.json",
        "axis.external-development-supervisor.execution-graph",
    )
    source_item = {
        "ref": grant["target_ref"],
        "source_kind": "gitlab-issue",
        "kind": "issue",
        "project": grant["repository"],
        "title": grant["target_title"],
        "source_state": "opened",
        "labels": ["canary"],
        "authority_facts": {"canary_grant_id": grant["grant_id"]},
        "blocking_dependency_refs": [],
        "merge_request_facts": [],
        "acceptance_criteria_present": True,
        "acceptance_facts": {"ids": ["CANARY-1"], "open_ids": ["CANARY-1"]},
        "source_evidence": {"description": grant["target_title"]},
        "repository_head": grant["source_sha"],
        "retrieval_errors": [],
        "mutation_allowed": True,
    }
    assignment = {
        "schema": "axis.external-development-supervisor.assignment",
        "schema_version": "1.0.0",
        "assignment_id": grant["assignment_id"],
        "lifecycle_state": "ready-implementation",
        "kind": "implementation",
        "queue_ref": f"canary:{grant['grant_id']}",
        "target_ref": grant["target_ref"],
        "work_item": grant["target_ref"],
        "project": grant["repository"],
        "title": grant["target_title"],
        "authority": {"state": "canary", "grant_id": grant["grant_id"]},
        "governance_state": "Executable",
        "planning_record": {
            "revision": 1,
            "digest": grant["grant_digest"],
            "approval_note": grant["authority_ref"],
        },
        "candidate": {
            "slice_id": grant["grant_id"],
            "category": "implementation",
            "result": "Executable",
            "allowed_paths": grant["allowed_paths"],
            "required_tests": grant["required_tests"],
        },
        "allowed_paths": grant["allowed_paths"],
        "required_tests": grant["required_tests"],
        "source_item": source_item,
        "source_fingerprint": SemanticDecompositionEngine.source_fingerprint(source_item),
        "source_inventory_generation_id": graph["inventory_generation_id"],
        "source_main_sha": grant["source_sha"],
        "canary_branch": grant["branch"],
        "canary_worktree": grant["worktree"],
        "created_by_run": f"canary-{grant['grant_id']}",
        "created_at_epoch": int(time.time()),
        "lease_id": None,
        "lease_uri": None,
        "worker": None,
    }
    assignment = validate_assignment(assignment, ROOT)
    gate.require(decision, OperationClass.CONTROL)
    write_record(
        assignment_path(grant["assignment_id"]),
        assignment,
        "axis.external-development-supervisor.assignment",
    )
    return {"grant": grant, "assignment": assignment}


def bootstrap_issue(glab: str) -> dict:
    grant = load_grant(ROOT)
    assignment = validate_assignment(
        json.loads(
            assignment_path(grant["assignment_id"]).read_text(encoding="utf-8")
        ),
        ROOT,
    )
    gate = MutationGate(ROOT, source="operator-cli")
    decision = gate.decide(
        OperationClass.CANARY_BOOTSTRAP,
        assignment=assignment,
        repository=assignment["project"],
    )
    gate.require(
        decision,
        OperationClass.CANARY_BOOTSTRAP,
        assignment=assignment,
        repository=assignment["project"],
    )
    description = (
        "Parent program: ghostspace/axis-lab#16\n\n"
        "Canary authority: "
        f"{grant['authority_ref']}\n\n"
        "Acceptance:\n"
        "- [ ] tofu proof rejects a terraform-only version payload\n"
        "- [ ] terraform proof rejects a tofu-only version payload\n"
        "- [ ] focused tests pass in the Supervisor bubblewrap boundary\n"
        "- [ ] no apply, credential, migration, deployment, or production effect\n"
    )
    encoded = quote(assignment["project"], safe="")
    output = subprocess.check_output(
        [
            glab,
            "api",
            "--hostname",
            "gitlab.com",
            "--method",
            "POST",
            "--field",
            f"title={grant['target_title']}",
            "--field",
            f"description={description}",
            f"projects/{encoded}/issues",
        ],
        text=True,
        timeout=120,
    )
    issue = json.loads(output)
    target_ref = f"{assignment['project']}#{issue['iid']}"
    grant["target_ref"] = target_ref
    grant["issue_iid"] = int(issue["iid"])
    grant.setdefault("events", []).append(
        {
            "recorded_at_epoch": int(time.time()),
            "event": "governing-issue-created",
            "ref": target_ref,
            "url": issue.get("web_url"),
        }
    )
    write_grant(ROOT, grant)
    assignment["target_ref"] = target_ref
    assignment["work_item"] = target_ref
    assignment["source_item"]["ref"] = target_ref
    assignment["source_item"]["iid"] = int(issue["iid"])
    assignment["source_item"]["web_url"] = issue.get("web_url")
    assignment["source_item"]["source_evidence"]["description"] = description
    assignment["source_fingerprint"] = SemanticDecompositionEngine.source_fingerprint(
        assignment["source_item"]
    )
    control_decision = gate.decide(OperationClass.CONTROL)
    gate.require(control_decision, OperationClass.CONTROL)
    write_record(
        assignment_path(grant["assignment_id"]),
        assignment,
        "axis.external-development-supervisor.assignment",
    )
    return {"grant": grant, "assignment": assignment, "issue": issue}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("grant_file", type=Path)
    bootstrap = sub.add_parser("bootstrap-issue")
    bootstrap.add_argument("--glab", default="glab")
    expire = sub.add_parser("expire")
    expire.add_argument("--status", choices=("consumed", "expired", "failed"), required=True)
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "create":
        result = create(args.grant_file)
    elif args.command == "bootstrap-issue":
        result = bootstrap_issue(args.glab)
    elif args.command == "expire":
        result = expire_grant(ROOT, args.status)
    else:
        result = load_grant(ROOT)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
