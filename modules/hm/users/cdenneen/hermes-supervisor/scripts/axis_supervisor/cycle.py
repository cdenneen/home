import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axis_supervisor.accounting import AccountingLedger
from axis_supervisor.canary import bind_mr, expire_grant
from axis_supervisor.dispatcher import Dispatcher
from axis_supervisor.graph import ExecutionGraphBuilder
from axis_supervisor.integrator import Integrator
from axis_supervisor.lifecycle import is_completed, is_integrable, set_lifecycle
from axis_supervisor.models import validate_assignment
from axis_supervisor.mutation import (
    GateDecision,
    MutationGate,
    OperationClass,
    load_canonical_lease,
)
from axis_supervisor.schema_registry import read_record, validate_record, write_record
from axis_supervisor.verification import completion_receipt
from axis_supervisor.workers import HermesWorkerManager, run_isolated_test

ROOT = Path(os.environ.get("AXIS_SUPERVISOR_ROOT", Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor"))


def save(path: Path, value: dict, gate: MutationGate) -> None:
    normalized = validate_assignment(value)
    value.clear()
    value.update(normalized)
    decision = gate.decide(OperationClass.RECONCILIATION)
    gate.require(decision, OperationClass.RECONCILIATION)
    write_record(path, value, "axis.external-development-supervisor.assignment")


def publish_implementation(
    assignment: dict,
    result: dict,
    glab: str,
    gate: MutationGate,
    repository_decision: GateDecision | None,
    gitlab_decision: GateDecision | None,
) -> dict:
    worktree = Path(result["worktree"])
    branch = result["branch"]
    gate.require(
        repository_decision,
        OperationClass.REPOSITORY,
        assignment=assignment,
        repository=assignment["project"],
    )
    subprocess.run(
        ["git", "push", "--no-verify", "-u", "origin", branch],
        cwd=worktree,
        check=True,
        timeout=180,
    )
    project = quote(assignment["project"], safe="")
    title = f"{assignment.get('target_ref')}: {assignment.get('title')}"
    description = (
        f"Hermes Development Supervisor assignment `{assignment['assignment_id']}`.\n\n"
        f"Controlling work: {assignment.get('target_ref')}\n\n"
        f"WWWHH: `{json.dumps(result['handoff'].get('wwwhh') or {})}`"
    )
    gate.require(
        gitlab_decision,
        OperationClass.GITLAB,
        assignment=assignment,
        repository=assignment["project"],
    )
    output = subprocess.check_output(
        [
            glab,
            "api",
            "--hostname",
            "gitlab.com",
            "--method",
            "POST",
            "--field",
            f"source_branch={branch}",
            "--field",
            "target_branch=main",
            "--field",
            f"title={title}",
            "--field",
            f"description={description}",
            "--field",
            "remove_source_branch=true",
            f"projects/{project}/merge_requests",
        ],
        text=True,
        timeout=120,
    )
    mr = json.loads(output)
    bind_mr(ROOT, assignment, mr)
    result["handoff"].update(
        {
            "mr_iid": mr.get("iid"),
            "mr_url": mr.get("web_url"),
            "pipeline_id": None,
            "pipeline_url": None,
        }
    )
    return result


def converge_repository(
    assignment: dict,
    repo: Path,
    gate: MutationGate,
    decision: GateDecision,
) -> dict:
    gate.require(
        decision,
        OperationClass.REPOSITORY,
        assignment=assignment,
        repository=assignment["project"],
    )
    facts = (assignment.get("source_item") or {}).get("convergence_facts") or {}
    scope = facts.get("scope")
    branch = str(facts.get("branch") or "")
    removed_worktree = False
    removed_branch = False
    if scope == "worktree":
        worktree = Path(str(facts.get("path") or "")).resolve()
        if not worktree.is_relative_to((ROOT / "worktrees").resolve()):
            raise RuntimeError("convergence worktree is outside supervisor custody")
        gate.require(
            decision,
            OperationClass.REPOSITORY,
            assignment=assignment,
            repository=assignment["project"],
        )
        completed = subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=repo,
            check=False,
        )
        removed_worktree = completed.returncode == 0 or not worktree.exists()
    if branch and branch != "detached":
        control = read_record(
            ROOT / "control.json", "axis.external-development-supervisor.control"
        )
        prefixes = tuple(control.get("owned_branch_prefixes") or [])
        if not prefixes or not branch.startswith(prefixes):
            raise RuntimeError("convergence branch is outside supervisor custody")
        gate.require(
            decision,
            OperationClass.REPOSITORY,
            assignment=assignment,
            repository=assignment["project"],
        )
        completed = subprocess.run(
            ["git", "branch", "-D", branch], cwd=repo, check=False
        )
        removed_branch = completed.returncode == 0
    if scope == "branch" and not removed_branch:
        raise RuntimeError("repository convergence did not remove the branch")
    if scope == "worktree" and not removed_worktree:
        raise RuntimeError("repository convergence did not remove the worktree")
    return {
        "scope": scope,
        "branch": branch or None,
        "worktree_removed": removed_worktree,
        "branch_removed": removed_branch,
    }


def close_work_item(
    assignment: dict,
    glab: str,
    gate: MutationGate,
    decision: GateDecision,
    evidence: list[str],
) -> None:
    target = str(assignment.get("work_item") or "")
    if "#" not in target:
        raise RuntimeError("integrated assignment has no GitLab work item ref")
    project, iid = target.rsplit("#", 1)
    encoded = quote(project, safe="")
    gate.require(
        decision,
        OperationClass.GITLAB,
        assignment=assignment,
        repository=project,
    )
    subprocess.check_output(
        [
            glab,
            "api",
            "--hostname",
            "gitlab.com",
            "--method",
            "PUT",
            "--field",
            "state_event=close",
            f"projects/{encoded}/issues/{int(iid)}",
        ],
        text=True,
        timeout=120,
    )
    gate.require(
        decision,
        OperationClass.GITLAB,
        assignment=assignment,
        repository=project,
    )
    note = (
        f"Supervisor assignment `{assignment['assignment_id']}` completed through gated merge and post-merge verification.\n\n"
        + "\n".join(f"- {ref}" for ref in evidence if ref)
    )
    subprocess.check_output(
        [
            glab,
            "api",
            "--hostname",
            "gitlab.com",
            "--method",
            "POST",
            "--field",
            f"body={note}",
            f"projects/{encoded}/issues/{int(iid)}/notes",
        ],
        text=True,
        timeout=120,
    )


def release_failed_assignment(
    assignment: dict,
    path: Path,
    supervisorctl: str,
    gate: MutationGate,
) -> None:
    reconciliation = gate.decide(
        OperationClass.RECONCILIATION,
        assignment=assignment,
        repository=assignment.get("project"),
    )
    worker = assignment.get("worker") or {}
    worktree_value = worker.get("worktree") or assignment.get("worktree")
    if worktree_value:
        worktree = Path(worktree_value)
        if worktree.exists():
            gate.require(
                reconciliation,
                OperationClass.RECONCILIATION,
                assignment=assignment,
                repository=assignment.get("project"),
            )
            recovery_dir = ROOT / "recovery"
            recovery_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            committed = subprocess.run(
                ["git", "format-patch", "--stdout", "origin/main..HEAD"],
                cwd=worktree,
                text=True,
                capture_output=True,
            ).stdout
            unstaged = subprocess.run(
                ["git", "diff", "HEAD"], cwd=worktree, text=True, capture_output=True
            ).stdout
            patch = committed + unstaged
            (recovery_dir / f"{assignment['assignment_id']}.patch").write_text(
                patch, encoding="utf-8"
            )
            if worker.get("custody") == "isolated-clone":
                shutil.rmtree(worktree, ignore_errors=True)
            else:
                recovery_worktree = recovery_dir / "worktrees" / assignment["assignment_id"]
                recovery_worktree.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if recovery_worktree.exists():
                    shutil.rmtree(recovery_worktree)
                worktree.rename(recovery_worktree)
                assignment["recovery_worktree"] = str(recovery_worktree)
    try:
        lease = load_canonical_lease(ROOT, assignment)
    except Exception:
        lease = None
    if lease is not None:
        subprocess.run(
            [
                sys.executable,
                supervisorctl,
                "release",
                assignment["assignment_id"],
                "--token",
                lease["fencing_token"],
            ],
            check=False,
        )
        assignment["lease_id"] = None
        assignment["lease_uri"] = None
    set_lifecycle(assignment, "failed")
    save(path, assignment, gate)


def rebuild() -> dict:
    inventory = read_record(
        ROOT / "inventory.json", "axis.external-development-supervisor.inventory"
    )
    control = read_record(
        ROOT / "control.json", "axis.external-development-supervisor.control"
    )
    remaining = max(
        0,
        int(control.get("daily_model_call_limit", 0))
        - AccountingLedger(ROOT).model_attempts_today(),
    )
    return ExecutionGraphBuilder(ROOT).build(
        inventory, {"available_model_call_budget": remaining}
    )


def execute_new_assignment(
    assignment: dict,
    manager: HermesWorkerManager,
    supervisorctl: str,
    gate: MutationGate,
) -> dict:
    path = ROOT / "assignments" / f"{assignment['assignment_id']}.json"
    resource = f"repo:{assignment.get('project') or 'ghostspace/axis'}"
    claim_command = [
        sys.executable,
        supervisorctl,
        "claim",
        assignment["assignment_id"],
        "--run-id",
        assignment["created_by_run"],
        "--resource",
        resource,
        "--ttl",
        "1200" if assignment["kind"] == "semantic-decomposition" else "3600",
    ]
    if assignment["kind"] in {"semantic-decomposition", "technical-revalidation"}:
        claim_command.append("--read-only")
    try:
        lease_output = subprocess.check_output(claim_command, text=True, timeout=30)
        lease = validate_record(
            json.loads(lease_output), "axis.external-development-supervisor.lease"
        )
    except Exception as exc:
        set_lifecycle(assignment, "failed")
        assignment["error"] = f"lease claim failed: {type(exc).__name__}: {exc}"
        save(path, assignment, gate)
        if (assignment.get("authority") or {}).get("state") == "canary":
            expire_grant(ROOT, "failed")
        raise
    assignment["lease_id"] = lease["lease_id"]
    assignment["lease_uri"] = (
        ROOT / "leases" / lease["lease_id"] / "lease.json"
    ).resolve().as_uri()
    set_lifecycle(
        assignment,
        "running-semantic"
        if assignment["kind"] in {"semantic-decomposition", "technical-revalidation"}
        else "running-implementation",
    )
    save(path, assignment, gate)
    try:
        token = load_canonical_lease(ROOT, assignment)["fencing_token"]
        if assignment["kind"] == "semantic-decomposition":
            model_decision = gate.decide(
                OperationClass.MODEL_CALL,
                assignment=assignment,
                fencing_token=token,
            )
            result = manager.semantic(assignment, model_decision)
            set_lifecycle(assignment, "completed")
        elif assignment["kind"] == "technical-revalidation":
            model_decision = gate.decide(
                OperationClass.MODEL_CALL,
                assignment=assignment,
                fencing_token=token,
            )
            result = manager.technical_revalidation(assignment, model_decision)
            set_lifecycle(assignment, "completed")
        elif assignment["kind"] == "repository-convergence":
            repo = Path("/home/cdenneen/src/workspace/personal/work") / assignment[
                "project"
            ].split("/")[-1]
            repository_decision = gate.decide(
                OperationClass.REPOSITORY,
                assignment=assignment,
                repository=assignment["project"],
                fencing_token=token,
            )
            result = converge_repository(
                assignment, repo, gate, repository_decision
            )
            set_lifecycle(assignment, "completed")
        else:
            repo = Path("/home/cdenneen/src/workspace/personal/work") / assignment[
                "project"
            ].split("/")[-1]
            repository_decision = gate.decide(
                OperationClass.REPOSITORY,
                assignment=assignment,
                repository=assignment["project"],
                fencing_token=token,
            )
            model_decision = gate.decide(
                OperationClass.MODEL_CALL,
                assignment=assignment,
                fencing_token=token,
            )
            result = manager.implementation(
                assignment, repo, repository_decision, model_decision
            )
            gitlab_decision = gate.decide(
                OperationClass.GITLAB,
                assignment=assignment,
                repository=assignment["project"],
                fencing_token=token,
            )
            result = publish_implementation(
                assignment,
                result,
                "/etc/profiles/per-user/cdenneen/bin/glab",
                gate,
                repository_decision,
                gitlab_decision,
            )
            set_lifecycle(assignment, "awaiting-integration")
        assignment["worker"] = result
        save(path, assignment, gate)
        if is_completed(assignment):
            subprocess.run(
                [
                    sys.executable,
                    supervisorctl,
                    "release",
                    assignment["assignment_id"],
                    "--token",
                    token,
                ],
                check=True,
            )
        rebuild()
        return {
            "result": assignment["lifecycle_state"],
            "assignment": assignment["assignment_id"],
        }
    except Exception as exc:
        assignment["error"] = f"{type(exc).__name__}: {exc}"
        release_failed_assignment(assignment, path, supervisorctl, gate)
        if (assignment.get("authority") or {}).get("state") == "canary":
            expire_grant(ROOT, "failed")
        raise


def run_next(run_id: str, hermes: str, supervisorctl: str) -> dict:
    graph = rebuild()
    dispatcher = Dispatcher(ROOT)
    active = dispatcher.active()
    gate = MutationGate(ROOT, source="cycle")
    manager = HermesWorkerManager(ROOT, hermes, supervisorctl, gate)
    if active:
        assignment = validate_assignment(active[0])
        path = ROOT / "assignments" / f"{assignment['assignment_id']}.json"
        if not is_integrable(assignment):
            return {
                "result": "active-assignment-not-integrable",
                "assignment": assignment["assignment_id"],
                "lifecycle_state": assignment.get("lifecycle_state"),
            }
        lease = load_canonical_lease(ROOT, assignment)
        subprocess.run(
            [
                sys.executable,
                supervisorctl,
                "heartbeat",
                assignment["assignment_id"],
                "--token",
                lease["fencing_token"],
                "--ttl",
                "3600",
            ],
            check=True,
        )
        handoff = (assignment.get("worker") or {}).get("handoff") or {}
        iid = int(handoff.get("mr_iid") or 0)
        integrator = Integrator("/etc/profiles/per-user/cdenneen/bin/glab")
        inspection = integrator.inspect_mr(
            assignment["project"],
            iid,
            expected_source_branch=(assignment.get("worker") or {}).get("branch"),
            expected_sha=(assignment.get("worker") or {}).get("commit"),
        )
        pipeline_status = str((inspection.get("pipeline") or {}).get("status") or "")
        integration_result = (
            "integrated-existing"
            if inspection["mr"].get("state") == "merged"
            else
            "integrate"
            if inspection.get("merge_ready")
            else "waiting"
            if pipeline_status
            in {"created", "pending", "preparing", "running", "scheduled", "waiting_for_resource"}
            or inspection.get("review_pending")
            else "blocked"
        )
        integration: dict = {
            "result": {
                "result": integration_result,
                "evidence": [inspection["mr"].get("web_url")],
                "next": "merge when ready" if integration_result == "waiting" else "",
            }
        }
        assignment["integration"] = integration
        result = integration["result"].get("result")
        if result in {"integrate", "integrated-existing"}:
            gitlab_decision = gate.decide(
                OperationClass.GITLAB,
                assignment=assignment,
                repository=assignment["project"],
                fencing_token=lease["fencing_token"],
            )
            if result == "integrate":
                integrator.merge_mr(
                    assignment["project"], iid, assignment, gate, gitlab_decision
                )
            inspection = integrator.inspect_mr(assignment["project"], iid)
            if inspection["mr"].get("state") != "merged":
                raise RuntimeError("gated integration did not produce a merged MR")
            worker_record = assignment.get("worker") or {}
            worktree_value = worker_record.get("worktree")
            branch = worker_record.get("branch")
            if not worktree_value or not branch:
                raise RuntimeError("integration assignment has no worktree/branch custody")
            worktree = Path(str(worktree_value))
            repo = Path("/home/cdenneen/src/workspace/personal/work") / assignment[
                "project"
            ].split("/")[-1]
            repository_decision = gate.decide(
                OperationClass.REPOSITORY,
                assignment=assignment,
                repository=assignment["project"],
                fencing_token=lease["fencing_token"],
            )
            gate.require(
                repository_decision,
                OperationClass.REPOSITORY,
                assignment=assignment,
                repository=assignment["project"],
            )
            subprocess.run(["git", "fetch", "--prune", "origin"], cwd=worktree, check=True)
            gate.require(
                repository_decision,
                OperationClass.REPOSITORY,
                assignment=assignment,
                repository=assignment["project"],
            )
            subprocess.run(
                ["git", "switch", "--detach", "origin/main"], cwd=worktree, check=True
            )
            test_results = []
            verification_error = None
            cleanup = {
                "worktree_removed": False,
                "local_branch_deleted": False,
                "remote_source_branch_absent": False,
                "lease_removed": False,
            }
            try:
                for command in assignment.get("required_tests") or []:
                    completed = run_isolated_test(worktree, command)
                    test_results.append(
                        {"command": command, "returncode": completed.returncode}
                    )
                    if completed.returncode != 0:
                        raise RuntimeError(f"post-merge test failed: {command}")
            except Exception as exc:
                verification_error = f"{type(exc).__name__}: {exc}"
            finally:
                reconciliation = gate.decide(
                    OperationClass.RECONCILIATION,
                    assignment=assignment,
                    repository=assignment["project"],
                )
                gate.require(
                    reconciliation,
                    OperationClass.RECONCILIATION,
                    assignment=assignment,
                    repository=assignment["project"],
                )
                shutil.rmtree(worktree, ignore_errors=True)
                cleanup["worktree_removed"] = not worktree.exists()
                cleanup["local_branch_deleted"] = True
                cleanup["remote_source_branch_absent"] = (
                    subprocess.run(
                        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
                        cwd=repo,
                        text=True,
                        capture_output=True,
                        timeout=60,
                    ).returncode
                    != 0
                )
                released = subprocess.run(
                    [
                        sys.executable,
                        supervisorctl,
                        "release",
                        assignment["assignment_id"],
                        "--token",
                        lease["fencing_token"],
                    ],
                    check=False,
                )
                cleanup["lease_removed"] = released.returncode == 0
            integration["verified_mr"] = inspection["mr"].get("web_url")
            integration["post_merge_tests"] = test_results
            if verification_error:
                set_lifecycle(assignment, "blocked")
                assignment["error"] = verification_error
                result = "post-merge-verification-failed"
                if (assignment.get("authority") or {}).get("state") == "canary":
                    expire_grant(ROOT, "failed")
            else:
                close_work_item(
                    assignment,
                    "/etc/profiles/per-user/cdenneen/bin/glab",
                    gate,
                    gitlab_decision,
                    [
                        str(inspection["mr"].get("web_url") or ""),
                        str((inspection.get("pipeline") or {}).get("web_url") or ""),
                    ],
                )
                assignment["source_item"]["source_state"] = "closed"
                assignment["source_item"]["state"] = "closed"
                assignment["completion_receipt"] = completion_receipt(
                    assignment,
                    inspection,
                    test_results,
                    cleanup,
                    fresh_cycle_recognition=False,
                )
                set_lifecycle(assignment, "completed")
                result = "integrated"
                if (assignment.get("authority") or {}).get("state") == "canary":
                    expire_grant(ROOT, "consumed")
        else:
            if result == "waiting":
                set_lifecycle(assignment, "awaiting-integration")
            else:
                set_lifecycle(assignment, "blocked")
                subprocess.run(
                    [
                        sys.executable,
                        supervisorctl,
                        "release",
                        assignment["assignment_id"],
                        "--token",
                        lease["fencing_token"],
                    ],
                    check=False,
                )
                assignment["lease_id"] = None
                assignment["lease_uri"] = None
                if (assignment.get("authority") or {}).get("state") == "canary":
                    expire_grant(ROOT, "failed")
        save(path, assignment, gate)
        rebuild()
        return {"result": result, "assignment": assignment["assignment_id"]}
    scheduler = graph.get("scheduler_state") or {}
    if scheduler.get("limiting_constraint") == "model-call-budget-exhausted":
        return {"result": "model-call-budget-exhausted", "remaining": 0}
    queue_by_ref = {
        item.get("ref"): item for item in graph.get("executable_queue") or []
    }
    selected_batch = [
        queue_by_ref[item.get("ref")]
        for item in scheduler.get("selected_batch") or []
        if item.get("ref") in queue_by_ref
    ]
    if len(selected_batch) > 1:
        results = []
        for item in selected_batch:
            assignment = dispatcher.dispatch(graph, run_id, item)
            if assignment is None:
                break
            try:
                results.append(
                    execute_new_assignment(assignment, manager, supervisorctl, gate)
                )
            except Exception as exc:
                return {
                    "result": "tier-a-batch-partial",
                    "completed": results,
                    "failed_assignment": assignment["assignment_id"],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            graph = rebuild()
        return {
            "result": "tier-a-batch-complete",
            "batch_size": len(results),
            "assignments": results,
        }

    selected = selected_batch[0] if selected_batch else None
    assignment = dispatcher.dispatch(graph, run_id, selected)
    if assignment is None:
        return {"result": "no-assignment", "queue_depth": graph["queue_depth"]}
    return execute_new_assignment(assignment, manager, supervisorctl, gate)


def run_canary(
    assignment_id: str, run_id: str, hermes: str, supervisorctl: str
) -> dict:
    path = ROOT / "assignments" / f"{assignment_id}.json"
    assignment = validate_assignment(
        json.loads(path.read_text(encoding="utf-8")), ROOT
    )
    if assignment.get("lifecycle_state") != "ready-implementation":
        raise RuntimeError("canary assignment is not ready for implementation")
    if (assignment.get("authority") or {}).get("state") != "canary":
        raise RuntimeError("assignment is not bound to canary authority")
    assignment["created_by_run"] = run_id
    gate = MutationGate(ROOT, source="cycle")
    manager = HermesWorkerManager(ROOT, hermes, supervisorctl, gate)
    save(path, assignment, gate)
    return execute_new_assignment(assignment, manager, supervisorctl, gate)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("rebuild", "run-next", "run-canary"))
    parser.add_argument("--run-id")
    parser.add_argument("--assignment-id")
    parser.add_argument("--hermes", default="hermes")
    parser.add_argument(
        "--supervisorctl",
        default=str(Path.home() / ".hermes" / "scripts" / "axis-development-supervisorctl.py"),
    )
    args = parser.parse_args()
    if args.command == "rebuild":
        print(json.dumps(rebuild(), sort_keys=True))
        return 0
    if args.command in {"run-next", "run-canary"} and not args.run_id:
        parser.error("run-next requires --run-id")
    if args.command == "run-canary":
        if not args.assignment_id:
            parser.error("run-canary requires --assignment-id")
        result = run_canary(
            args.assignment_id, args.run_id, args.hermes, args.supervisorctl
        )
    else:
        result = run_next(args.run_id, args.hermes, args.supervisorctl)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
