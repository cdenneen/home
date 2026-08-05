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
from axis_supervisor.assignment_grants import (
    bind_mr as bind_assignment_grant_mr,
    finish_grant as finish_assignment_grant,
)
from axis_supervisor.canary import bind_mr as bind_canary_mr
from axis_supervisor.canary import expire_grant
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
from axis_supervisor.observability import record_event
from axis_supervisor.schema_registry import (
    CorruptRecordError,
    read_record,
    validate_record,
    write_record,
)
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
        effect="push-owned-branch" if assignment.get("mutation_grant_id") else None,
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
        effect="create-owned-mr" if assignment.get("mutation_grant_id") else None,
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
    bind_canary_mr(ROOT, assignment, mr)
    if assignment.get("mutation_grant_id"):
        bind_assignment_grant_mr(ROOT, assignment, mr)
    result["handoff"].update(
        {
            "mr_iid": mr.get("iid"),
            "mr_url": mr.get("web_url"),
            "pipeline_id": None,
            "pipeline_url": None,
        }
    )
    record_event(
        ROOT,
        "mr_created",
        assignment=assignment,
        details={
            "mr_iid": mr.get("iid"),
            "mr_url": mr.get("web_url"),
            "commit": result.get("commit"),
            "branch": branch,
            "integration_state": "awaiting-pipeline",
        },
        source="cycle",
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
    close_decision: GateDecision,
    evidence_decision: GateDecision,
    evidence: list[str],
) -> None:
    target = str(assignment.get("work_item") or "")
    if "#" not in target:
        raise RuntimeError("integrated assignment has no GitLab work item ref")
    project, iid = target.rsplit("#", 1)
    encoded = quote(project, safe="")
    gate.require(
        close_decision,
        OperationClass.GITLAB,
        assignment=assignment,
        repository=project,
        effect="close-controlling-work-item"
        if assignment.get("mutation_grant_id")
        else None,
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
        evidence_decision,
        OperationClass.GITLAB,
        assignment=assignment,
        repository=project,
        effect="record-evidence" if assignment.get("mutation_grant_id") else None,
    )
    note = (
        f"Supervisor implementation assignment `{assignment['assignment_id']}` merged and passed post-main verification.\n\n"
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
    assignment["result_state"] = "failed"
    assignment["work_item_disposition"] = (
        "requires-implementation"
        if assignment.get("assignment_type")
        in {
            "governance-document-mutation",
            "code-implementation",
            "ci-integration-repair",
        }
        else "analyzed-only"
    )
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
        "1200"
        if assignment["assignment_type"]
        in {"read-only-analysis", "no-op-verification"}
        else "3600",
    ]
    if assignment["assignment_type"] in {
        "read-only-analysis",
        "no-op-verification",
    }:
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
        record_event(
            ROOT,
            "assignment_disposition",
            assignment=assignment,
            details={
                "disposition": "failed",
                "failed_gate": "lease-claim",
                "failure_classification": type(exc).__name__,
                "corrective_action": "await next governed recovery cycle",
                "unsafe_branch_published": False,
            },
            source="cycle",
        )
        if (assignment.get("authority") or {}).get("state") == "canary":
            expire_grant(ROOT, "failed")
        if assignment.get("mutation_grant_id"):
            finish_assignment_grant(ROOT, assignment, "failed")
        raise
    assignment["lease_id"] = lease["lease_id"]
    assignment["lease_uri"] = (
        ROOT / "leases" / lease["lease_id"] / "lease.json"
    ).resolve().as_uri()
    set_lifecycle(
        assignment,
        "running-semantic"
        if assignment["assignment_type"]
        in {"read-only-analysis", "no-op-verification"}
        else "running-implementation",
    )
    save(path, assignment, gate)
    record_event(
        ROOT,
        "worker_started",
        assignment=assignment,
        details={
            "model": "gpt-5.4"
            if assignment["assignment_type"]
            in {"read-only-analysis", "no-op-verification"}
            else "gpt-5.3-codex",
            "lease_id": lease["lease_id"],
            "branch": f"hermes/{assignment['assignment_id']}"
            if assignment["assignment_type"]
            not in {"read-only-analysis", "no-op-verification"}
            else None,
            "worktree": str(ROOT / "worktrees" / assignment["assignment_id"]),
            "grant": (assignment.get("authority") or {}).get("grant_id"),
            "expected_next_phase": "awaiting-integration"
            if assignment["assignment_type"]
            not in {"read-only-analysis", "no-op-verification"}
            else "completed",
        },
        source="cycle",
    )
    try:
        token = load_canonical_lease(ROOT, assignment)["fencing_token"]
        if assignment["assignment_type"] == "read-only-analysis":
            model_decision = gate.decide(
                OperationClass.MODEL_CALL,
                assignment=assignment,
                fencing_token=token,
            )
            result = manager.semantic(assignment, model_decision)
            verification = (result.get("record") or {}).get("verification_result") or {}
            assignment["result_state"] = "analysis-completed"
            assignment["work_item_disposition"] = (
                "requires-implementation"
                if verification.get("disposition")
                == "corrective-implementation-required"
                else "requires-human-decision"
                if verification.get("disposition") == "human-authority-required"
                else "analyzed-only"
            )
            set_lifecycle(assignment, "completed")
        elif assignment["assignment_type"] == "no-op-verification":
            model_decision = gate.decide(
                OperationClass.MODEL_CALL,
                assignment=assignment,
                fencing_token=token,
            )
            result = manager.technical_revalidation(assignment, model_decision)
            verification = (result.get("record") or {}).get("verification_result") or {}
            assignment["result_state"] = "no-op-verification-completed"
            assignment["work_item_disposition"] = (
                "no-op-verified"
                if verification.get("disposition") == "verified-complete"
                else "requires-implementation"
                if verification.get("disposition")
                == "corrective-implementation-required"
                else "requires-human-decision"
                if verification.get("disposition") == "human-authority-required"
                else "analyzed-only"
            )
            set_lifecycle(assignment, "completed")
        elif assignment["assignment_type"] == "repository-convergence":
            repo = Path("/home/cdenneen/src/workspace/personal/work") / assignment[
                "project"
            ].split("/")[-1]
            repository_decision = gate.decide(
                OperationClass.REPOSITORY,
                assignment=assignment,
                repository=assignment["project"],
                fencing_token=token,
                effect="clone" if assignment.get("mutation_grant_id") else None,
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
                effect="clone" if assignment.get("mutation_grant_id") else None,
            )
            model_decision = gate.decide(
                OperationClass.MODEL_CALL,
                assignment=assignment,
                fencing_token=token,
            )
            result = manager.implementation(
                assignment, repo, repository_decision, model_decision
            )
            assignment["result_state"] = "implementation-commit-created"
            assignment["work_item_disposition"] = "requires-integration"
            record_event(
                ROOT,
                "implementation_completed",
                assignment=assignment,
                details={
                    "files_changed": result.get("changed_paths") or [],
                    "tests": (result.get("handoff") or {}).get("tests") or [],
                    "commit": result.get("commit"),
                    "branch": result.get("branch"),
                    "worktree": result.get("worktree"),
                    "expected_next_phase": "MR creation",
                },
                source="cycle",
            )
            gitlab_decision = gate.decide(
                OperationClass.GITLAB,
                assignment=assignment,
                repository=assignment["project"],
                fencing_token=token,
                effect="create-owned-mr"
                if assignment.get("mutation_grant_id")
                else None,
            )
            push_decision = gate.decide(
                OperationClass.REPOSITORY,
                assignment=assignment,
                repository=assignment["project"],
                fencing_token=token,
                effect="push-owned-branch"
                if assignment.get("mutation_grant_id")
                else None,
            )
            result = publish_implementation(
                assignment,
                result,
                "/etc/profiles/per-user/cdenneen/bin/glab",
                gate,
                push_decision,
                gitlab_decision,
            )
            set_lifecycle(assignment, "awaiting-integration")
            assignment["result_state"] = "awaiting-integration"
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
            assignment["lease_id"] = None
            assignment["lease_uri"] = None
            save(path, assignment, gate)
            record_event(
                ROOT,
                "assignment_disposition",
                assignment=assignment,
                details={
                    "disposition": assignment["result_state"],
                    "work_item_disposition": assignment[
                        "work_item_disposition"
                    ],
                    "assignment_type": assignment["assignment_type"],
                    "cleanup": {"lease_removed": True},
                    "next_scheduled_work": "recompute governed frontier",
                },
                source="cycle",
            )
        rebuild()
        return {
            "result": assignment["lifecycle_state"],
            "assignment": assignment["assignment_id"],
        }
    except Exception as exc:
        assignment["error"] = f"{type(exc).__name__}: {exc}"
        release_failed_assignment(assignment, path, supervisorctl, gate)
        record_event(
            ROOT,
            "assignment_disposition",
            assignment=assignment,
            details={
                "disposition": "failed",
                "failure_classification": type(exc).__name__,
                "corrective_action": "bounded recovery required",
                "unsafe_branch_published": False,
            },
            source="cycle",
        )
        if (assignment.get("authority") or {}).get("state") == "canary":
            expire_grant(ROOT, "failed")
        if assignment.get("mutation_grant_id"):
            finish_assignment_grant(ROOT, assignment, "failed")
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
        handoff = (assignment.get("worker") or {}).get("handoff") or {}
        iid = int(handoff.get("mr_iid") or 0)
        integrator = Integrator("/etc/profiles/per-user/cdenneen/bin/glab")
        inspection = integrator.inspect_mr(
            assignment["project"],
            iid,
            expected_source_branch=(assignment.get("worker") or {}).get("branch"),
            expected_sha=(assignment.get("worker") or {}).get("commit"),
        )
        try:
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
        except CorruptRecordError:
            if inspection["mr"].get("state") != "merged":
                raise
            output = subprocess.check_output(
                [
                    sys.executable,
                    supervisorctl,
                    "claim",
                    assignment["assignment_id"],
                    "--run-id",
                    assignment["created_by_run"],
                    "--resource",
                    f"repo:{assignment['project']}",
                    "--ttl",
                    "3600",
                    "--merged-mr-json",
                    json.dumps(inspection["mr"], sort_keys=True),
                ],
                text=True,
                timeout=120,
            )
            lease = json.loads(output)
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
            if result == "integrate":
                gitlab_decision = gate.decide(
                    OperationClass.GITLAB,
                    assignment=assignment,
                    repository=assignment["project"],
                    fencing_token=lease["fencing_token"],
                    effect="merge-reviewed-mr"
                    if assignment.get("mutation_grant_id")
                    else None,
                )
                integrator.merge_mr(
                    assignment["project"], iid, assignment, gate, gitlab_decision
                )
            inspection = integrator.inspect_mr(assignment["project"], iid)
            if inspection["mr"].get("state") != "merged":
                raise RuntimeError("gated integration did not produce a merged MR")
            merged_mr = inspection["mr"]

            def post_merge_repository_decision(effect: str) -> GateDecision:
                return gate.decide(
                    OperationClass.REPOSITORY,
                    assignment=assignment,
                    repository=assignment["project"],
                    fencing_token=lease["fencing_token"],
                    merged_mr=merged_mr,
                    effect=effect if assignment.get("mutation_grant_id") else None,
                )

            def post_merge_gitlab_decision(effect: str) -> GateDecision:
                return gate.decide(
                    OperationClass.GITLAB,
                    assignment=assignment,
                    repository=assignment["project"],
                    fencing_token=lease["fencing_token"],
                    merged_mr=merged_mr,
                    effect=effect if assignment.get("mutation_grant_id") else None,
                )
            record_event(
                ROOT,
                "mr_merged",
                assignment=assignment,
                details={
                    "mr_iid": iid,
                    "mr_url": merged_mr.get("web_url"),
                    "merge_commit_sha": merged_mr.get("merge_commit_sha"),
                    "pipeline": inspection.get("pipeline"),
                    "expected_next_phase": "post-main verification",
                },
                source="cycle",
            )
            worker_record = assignment.get("worker") or {}
            worktree_value = worker_record.get("worktree")
            branch = worker_record.get("branch")
            if not worktree_value or not branch:
                raise RuntimeError("integration assignment has no worktree/branch custody")
            worktree = Path(str(worktree_value))
            repo = Path("/home/cdenneen/src/workspace/personal/work") / assignment[
                "project"
            ].split("/")[-1]
            recreated_worktree = not worktree.exists()
            if recreated_worktree:
                gate.require(
                    post_merge_repository_decision("clone"),
                    OperationClass.REPOSITORY,
                    assignment=assignment,
                    repository=assignment["project"],
                    effect="clone" if assignment.get("mutation_grant_id") else None,
                )
                remote_url = subprocess.check_output(
                    ["git", "remote", "get-url", "origin"],
                    cwd=repo,
                    text=True,
                    timeout=30,
                ).strip()
                subprocess.run(
                    ["git", "clone", "--no-hardlinks", str(repo), str(worktree)],
                    check=True,
                    timeout=120,
                )
                gate.require(
                    post_merge_repository_decision("configure-remote"),
                    OperationClass.REPOSITORY,
                    assignment=assignment,
                    repository=assignment["project"],
                    effect="configure-remote"
                    if assignment.get("mutation_grant_id")
                    else None,
                )
                subprocess.run(
                    ["git", "remote", "set-url", "origin", remote_url],
                    cwd=worktree,
                    check=True,
                    timeout=30,
                )
            gate.require(
                post_merge_repository_decision("fetch"),
                OperationClass.REPOSITORY,
                assignment=assignment,
                repository=assignment["project"],
                effect="fetch" if assignment.get("mutation_grant_id") else None,
            )
            subprocess.run(["git", "fetch", "--prune", "origin"], cwd=worktree, check=True)
            gate.require(
                post_merge_repository_decision("checkout-merged-main"),
                OperationClass.REPOSITORY,
                assignment=assignment,
                repository=assignment["project"],
                effect="checkout-merged-main"
                if assignment.get("mutation_grant_id")
                else None,
            )
            subprocess.run(
                ["git", "switch", "--detach", "origin/main"], cwd=worktree, check=True
            )
            if recreated_worktree and (worktree / "uv.lock").is_file():
                gate.require(
                    post_merge_repository_decision("provision-test-environment"),
                    OperationClass.REPOSITORY,
                    assignment=assignment,
                    repository=assignment["project"],
                    effect="provision-test-environment"
                    if assignment.get("mutation_grant_id")
                    else None,
                )
                subprocess.run(
                    [
                        shutil.which("uv") or "/etc/profiles/per-user/cdenneen/bin/uv",
                        "sync",
                        "--locked",
                        "--group",
                        "dev",
                        "--python",
                        sys.executable,
                    ],
                    cwd=worktree,
                    check=True,
                    timeout=600,
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
                    gate.require(
                        post_merge_repository_decision("run-required-tests"),
                        OperationClass.REPOSITORY,
                        assignment=assignment,
                        repository=assignment["project"],
                        effect="run-required-tests"
                        if assignment.get("mutation_grant_id")
                        else None,
                    )
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
                if assignment.get("mutation_grant_id"):
                    gate.require(
                        post_merge_repository_decision("remove-owned-worktree"),
                        OperationClass.REPOSITORY,
                        assignment=assignment,
                        repository=assignment["project"],
                        effect="remove-owned-worktree",
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
            integration["verified_mr"] = inspection["mr"].get("web_url")
            integration["post_merge_tests"] = test_results
            if not verification_error:
                record_event(
                    ROOT,
                    "post_main_verified",
                    assignment=assignment,
                    details={
                        "tests": test_results,
                        "cleanup": cleanup,
                        "mr_url": inspection["mr"].get("web_url"),
                        "expected_next_phase": "issue closure and lease release",
                    },
                    source="cycle",
                )
            if not verification_error:
                try:
                    close_work_item(
                        assignment,
                        "/etc/profiles/per-user/cdenneen/bin/glab",
                        gate,
                        post_merge_gitlab_decision("close-controlling-work-item"),
                        post_merge_gitlab_decision("record-evidence"),
                        [
                            str(inspection["mr"].get("web_url") or ""),
                            str((inspection.get("pipeline") or {}).get("web_url") or ""),
                        ],
                    )
                except Exception as exc:
                    verification_error = f"{type(exc).__name__}: {exc}"
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
            if cleanup["lease_removed"]:
                assignment["lease_id"] = None
                assignment["lease_uri"] = None
            if verification_error:
                set_lifecycle(assignment, "blocked")
                assignment["result_state"] = "blocked"
                assignment["work_item_disposition"] = "requires-implementation"
                assignment["error"] = verification_error
                result = "post-merge-verification-failed"
                if (assignment.get("authority") or {}).get("state") == "canary":
                    expire_grant(ROOT, "failed")
                if assignment.get("mutation_grant_id"):
                    finish_assignment_grant(ROOT, assignment, "failed")
            else:
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
                assignment["result_state"] = "integrated-post-main-verified"
                assignment["work_item_disposition"] = (
                    "evidence-recorded-awaiting-fresh-recognition"
                )
                result = "integrated"
                if (assignment.get("authority") or {}).get("state") == "canary":
                    expire_grant(ROOT, "consumed")
                    record_event(
                        ROOT,
                        "grant_consumed",
                        assignment=assignment,
                        details={
                            "grant_id": (assignment.get("authority") or {}).get("grant_id"),
                            "status": "consumed",
                            "global_mutation_enabled": False,
                        },
                        source="cycle",
                    )
                if assignment.get("mutation_grant_id"):
                    finish_assignment_grant(ROOT, assignment, "consumed")
                    record_event(
                        ROOT,
                        "grant_consumed",
                        assignment=assignment,
                        details={
                            "grant_id": assignment["mutation_grant_id"],
                            "status": "consumed",
                            "global_mutation_enabled": False,
                            "expected_next_phase": "fresh canonical recognition",
                        },
                        source="cycle",
                    )
        else:
            if result == "waiting":
                set_lifecycle(assignment, "awaiting-integration")
                assignment["result_state"] = "awaiting-integration"
                record_event(
                    ROOT,
                    "assignment_disposition",
                    assignment=assignment,
                    details={
                        "disposition": "awaiting-integration",
                        "mr_url": (assignment.get("worker") or {}).get("handoff", {}).get("mr_url"),
                        "expected_next_phase": integration["result"].get("next") or "next scheduled integration check",
                    },
                    source="cycle",
                )
            else:
                set_lifecycle(assignment, "blocked")
                assignment["result_state"] = "blocked"
                assignment["work_item_disposition"] = "requires-implementation"
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
                if assignment.get("mutation_grant_id"):
                    finish_assignment_grant(ROOT, assignment, "failed")
        save(path, assignment, gate)
        if assignment.get("lifecycle_state") in {"completed", "blocked", "failed", "cancelled"}:
            record_event(
                ROOT,
                "assignment_disposition",
                assignment=assignment,
                details={
                    "disposition": assignment.get("result_state"),
                    "work_item_disposition": assignment.get(
                        "work_item_disposition"
                    ),
                    "assignment_type": assignment.get("assignment_type"),
                    "post_main_verification": integration.get("post_merge_tests") or [],
                    "lease_state": "released" if assignment.get("lease_id") is None else "active",
                    "grant_state": "consumed"
                    if result == "integrated"
                    and (
                        (assignment.get("authority") or {}).get("state") == "canary"
                        or assignment.get("mutation_grant_id")
                    )
                    else None,
                    "next_scheduled_work": "recompute governed frontier",
                },
                source="cycle",
            )
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
    record_event(
        ROOT,
        "cycle_completed",
        details={"run_id": args.run_id, "result": result},
        source="cycle",
        notify=False,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
