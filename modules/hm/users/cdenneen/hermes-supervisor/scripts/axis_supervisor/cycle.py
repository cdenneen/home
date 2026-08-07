import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axis_supervisor.accounting import AccountingLedger
from axis_supervisor.assignment_grants import (
    bind_mr as bind_assignment_grant_mr,
)
from axis_supervisor.assignment_grants import (
    finish_grant as finish_assignment_grant,
)
from axis_supervisor.canary import bind_mr as bind_canary_mr
from axis_supervisor.canary import expire_grant
from axis_supervisor.capability_convergence import CapabilityConvergenceProjector
from axis_supervisor.capability_graduation import (
    CapabilityGraduationProjector,
    assignment_is_satisfied,
)
from axis_supervisor.decisions import reconcile_pending_frontier_rebuilds
from axis_supervisor.deployment import (
    create_deployment_assignment,
    execute_deployment_assignment,
)
from axis_supervisor.dispatcher import Dispatcher
from axis_supervisor.graph import ExecutionGraphBuilder
from axis_supervisor.integrator import Integrator
from axis_supervisor.lifecycle import (
    is_completed,
    is_integrable,
    is_terminal,
    set_lifecycle,
)
from axis_supervisor.missions import ActiveMissionState, mission_summary
from axis_supervisor.models import validate_assignment
from axis_supervisor.mutation import (
    GateDecision,
    MutationGate,
    OperationClass,
    load_canonical_lease,
)
from axis_supervisor.observability import (
    OperationalEventLog,
    record_engineering_retrospective,
    record_event,
)
from axis_supervisor.repository_convergence import RepositoryConvergenceProjector
from axis_supervisor.roadmap_quality import RoadmapQualityProjector
from axis_supervisor.schema_registry import (
    CorruptRecordError,
    read_record,
    validate_record,
    write_record,
)
from axis_supervisor.verification import completion_receipt
from axis_supervisor.workers import HermesWorkerManager, run_isolated_test
from axis_supervisor.workflow_state import WorkflowState, classify_main_advance

ROOT = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_ROOT",
        Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor",
    )
)


def deployed_source_revision() -> dict:
    try:
        value = json.loads(
            (ROOT / "deployed-source-revision.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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
    current_issue = json.loads(
        subprocess.check_output(
            [
                glab,
                "api",
                "--hostname",
                "gitlab.com",
                f"projects/{encoded}/issues/{int(iid)}",
            ],
            text=True,
            timeout=120,
        )
    )
    description = str(current_issue.get("description") or "")
    completed_description = description.replace("- [ ]", "- [x]")
    if "## Supervisor completion evidence" not in completed_description:
        completed_description += "\n\n## Supervisor completion evidence\n" + "\n".join(
            f"- {ref}" for ref in evidence if ref
        )
    gate.require(
        evidence_decision,
        OperationClass.GITLAB,
        assignment=assignment,
        repository=project,
        effect="record-evidence" if assignment.get("mutation_grant_id") else None,
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
            f"description={completed_description}",
            f"projects/{encoded}/issues/{int(iid)}",
        ],
        text=True,
        timeout=120,
    )
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
                recovery_worktree = (
                    recovery_dir / "worktrees" / assignment["assignment_id"]
                )
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
    def active_assignments() -> list[dict]:
        return [
            assignment
            for path in (ROOT / "assignments").glob("*.json")
            if not is_terminal(
                assignment := validate_assignment(
                    json.loads(path.read_text(encoding="utf-8")), ROOT
                )
            )
        ]

    active = active_assignments()
    now = int(time.time())
    graph = ExecutionGraphBuilder(ROOT).build(
        inventory,
        {
            "available_model_call_budget": remaining,
            "active_assignments": active,
            "engineering_metrics": OperationalEventLog(
                ROOT, "cycle"
            ).throughput_metrics(now - 30 * 86_400, now),
        },
    )
    RoadmapQualityProjector(ROOT).build(inventory, graph)
    repository_convergence = RepositoryConvergenceProjector(ROOT).build(inventory)
    capability_convergence = CapabilityConvergenceProjector(ROOT).build(
        repository_convergence
    )
    graduation = CapabilityGraduationProjector(ROOT).build(
        inventory, graph, capability_convergence
    )
    assignment_by_id = {
        value.get("assignment_id"): value
        for value in inventory.get("supervisor_assignments") or []
    }
    collapsed = []
    collapse_gate = MutationGate(ROOT, source="cycle")
    for path in sorted((ROOT / "assignments").glob("*.json")):
        assignment = validate_assignment(
            json.loads(path.read_text(encoding="utf-8")), ROOT
        )
        if is_terminal(assignment):
            continue
        lease_id = assignment.get("lease_id")
        if lease_id and (ROOT / "leases" / str(lease_id) / "lease.json").exists():
            continue
        if not assignment_is_satisfied(
            assignment,
            graph,
            repository_convergence,
            capability_convergence,
            graduation,
        ):
            continue
        assignment_type = assignment.get("assignment_type")
        if assignment_type == "capability-deployment":
            set_lifecycle(assignment, "runtime-converged")
            assignment["result_state"] = "runtime-converged"
            assignment["work_item_disposition"] = "canonical-complete"
        elif assignment_type == "repository-convergence":
            set_lifecycle(assignment, "repository-converged")
            assignment["result_state"] = "repository-converged"
            assignment["work_item_disposition"] = "canonical-complete"
        else:
            set_lifecycle(assignment, "completed")
            assignment["result_state"] = "no-op-verification-completed"
            assignment["work_item_disposition"] = "no-op-verified"
        assignment["collapsed_as_stale"] = True
        save(path, assignment, collapse_gate)
        assignment_by_id[assignment["assignment_id"]] = assignment
        collapsed.append(assignment["assignment_id"])
    if collapsed:
        inventory["supervisor_assignments"] = list(assignment_by_id.values())
        active = active_assignments()
        graph = ExecutionGraphBuilder(ROOT).build(
            inventory,
            {
                "available_model_call_budget": remaining,
                "active_assignments": active,
                "engineering_metrics": OperationalEventLog(
                    ROOT, "cycle"
                ).throughput_metrics(now - 30 * 86_400, now),
            },
        )
        RoadmapQualityProjector(ROOT).build(inventory, graph)
    graduation = CapabilityGraduationProjector(ROOT).build(
        inventory, graph, capability_convergence
    )
    ActiveMissionState(ROOT).reconcile(inventory, graph, graduation)
    reconcile_pending_frontier_rebuilds(ROOT, lambda: None)
    return graph


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
        if assignment["assignment_type"] in {"read-only-analysis", "no-op-verification"}
        else "3600",
    ]
    if assignment["assignment_type"] in {
        "read-only-analysis",
        "no-op-verification",
    }:
        claim_command.append("--read-only")
    try:
        claim_started = time.time()
        completed_claim = subprocess.run(
            claim_command, text=True, capture_output=True, timeout=30
        )
        if completed_claim.returncode != 0:
            diagnostic = {
                "schema": "axis.supervisor.lease-claim-diagnostic.v1",
                "assignment_id": assignment["assignment_id"],
                "work_item": assignment["work_item"],
                "repository": assignment["project"],
                "argv": claim_command,
                "cwd": str(Path.cwd()),
                "started_at_epoch": claim_started,
                "ended_at_epoch": time.time(),
                "exit_code": completed_claim.returncode,
                "stdout": completed_claim.stdout,
                "stderr": completed_claim.stderr,
                "timeout": False,
                "requested_lease_key": assignment["assignment_id"],
                "requested_scope": [resource],
                "supervisor_revision": deployed_source_revision(),
                "inventory_revision": assignment.get("source_inventory_generation_id"),
            }
            diagnostic_path = (
                ROOT
                / "engineering-memory"
                / "lease-incidents"
                / f"{assignment['assignment_id']}.json"
            )
            diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
            diagnostic_path.write_text(
                json.dumps(diagnostic, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            raise RuntimeError(
                f"lease infrastructure failure; diagnostic={diagnostic_path}; "
                f"stdout={completed_claim.stdout[-1000:]}; "
                f"stderr={completed_claim.stderr[-1000:]}"
            )
        lease_output = completed_claim.stdout
        lease = validate_record(
            json.loads(lease_output), "axis.external-development-supervisor.lease"
        )
    except Exception as exc:
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
        record_engineering_retrospective(ROOT, assignment, source="cycle")
        if (assignment.get("authority") or {}).get("state") == "canary":
            expire_grant(ROOT, "failed")
        if assignment.get("mutation_grant_id"):
            finish_assignment_grant(ROOT, assignment, "failed")
        raise
    assignment["lease_id"] = lease["lease_id"]
    assignment["lease_uri"] = (
        (ROOT / "leases" / lease["lease_id"] / "lease.json").resolve().as_uri()
    )
    set_lifecycle(
        assignment,
        "running-semantic"
        if assignment["assignment_type"] in {"read-only-analysis", "no-op-verification"}
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
            repo = (
                Path("/home/cdenneen/src/workspace/personal/work")
                / assignment["project"].split("/")[-1]
            )
            repository_decision = gate.decide(
                OperationClass.REPOSITORY,
                assignment=assignment,
                repository=assignment["project"],
                fencing_token=token,
                effect="clone" if assignment.get("mutation_grant_id") else None,
            )
            result = converge_repository(assignment, repo, gate, repository_decision)
            set_lifecycle(assignment, "completed")
        else:
            repo = (
                Path("/home/cdenneen/src/workspace/personal/work")
                / assignment["project"].split("/")[-1]
            )
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
            set_lifecycle(assignment, "implementation-complete")
            assignment["result_state"] = "implementation-complete"
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
            record_engineering_retrospective(ROOT, assignment, source="cycle")
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
            workflow = WorkflowState(ROOT)
            implementation_handoff = workflow.persist_handoff(assignment, result)
            control = read_record(
                ROOT / "control.json",
                "axis.external-development-supervisor.control",
            )
            reviewers = control.get("product_owner_usernames") or ["unassigned"]
            integration_item = workflow.enqueue(
                assignment, implementation_handoff, str(reviewers[0])
            )
            result["implementation_handoff_uri"] = integration_item["handoff_uri"]
            set_lifecycle(assignment, "awaiting-integration")
            assignment["result_state"] = "awaiting-integration"
            record_event(
                ROOT,
                "frontier_refill_requested",
                assignment=assignment,
                details={
                    "released_stage": "implementation",
                    "occupied_stage": "integration",
                    "reason": "implementation handoff persisted and queued",
                },
                source="cycle",
                notify=False,
            )
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
                    "work_item_disposition": assignment["work_item_disposition"],
                    "assignment_type": assignment["assignment_type"],
                    "cleanup": {"lease_removed": True},
                    "next_scheduled_work": "recompute governed frontier",
                },
                source="cycle",
            )
            record_engineering_retrospective(ROOT, assignment, source="cycle")
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
        record_engineering_retrospective(ROOT, assignment, source="cycle")
        if (assignment.get("authority") or {}).get("state") == "canary":
            expire_grant(ROOT, "failed")
        if assignment.get("mutation_grant_id"):
            finish_assignment_grant(ROOT, assignment, "failed")
        raise


def run_next(run_id: str, hermes: str, supervisorctl: str) -> dict:
    graph = rebuild()
    mission = read_record(
        ROOT / "active-mission.json",
        "axis.external-development-supervisor.active-mission",
    )
    if (mission.get("termination_condition") or {}).get("should_terminate"):
        return {
            "result": "mission-terminated",
            "mission": mission_summary(mission),
        }
    capability_convergence = read_record(
        ROOT / "capability-convergence.json",
        "axis.external-development-supervisor.capability-convergence",
    )
    deployment_plans = sorted(
        (
            value
            for value in capability_convergence.get("deployment_assignments") or []
            if value.get("status") == "deployment-required"
        ),
        key=lambda value: int(value.get("ring") or 0),
    )
    if deployment_plans:
        plan = deployment_plans[0]
        existing = []
        for assignment_path in (ROOT / "assignments").glob("*.json"):
            value = validate_assignment(
                json.loads(assignment_path.read_text(encoding="utf-8")), ROOT
            )
            if (
                value.get("assignment_type") == "capability-deployment"
                and value.get("source_fingerprint") == plan["assignment_id"]
                and not is_terminal(value)
            ):
                existing.append(value)
        if not existing:
            deployment_assignment = create_deployment_assignment(ROOT, plan, run_id)
            return execute_deployment_assignment(
                ROOT, deployment_assignment, supervisorctl
            )
        resumable = next(
            (
                value
                for value in existing
                if value.get("lifecycle_state")
                in {"ready-implementation", "running-implementation"}
                and (
                    not value.get("lease_id")
                    or not (
                        ROOT / "leases" / str(value.get("lease_id")) / "lease.json"
                    ).exists()
                )
            ),
            None,
        )
        if resumable is not None:
            return execute_deployment_assignment(ROOT, resumable, supervisorctl)
    dispatcher = Dispatcher(ROOT)
    active = dispatcher.active()
    gate = MutationGate(ROOT, source="cycle")
    manager = HermesWorkerManager(ROOT, hermes, supervisorctl, gate)

    def dispatch_first_available(current_graph: dict) -> dict | None:
        queue_by_ref = {
            item.get("ref"): item
            for item in current_graph.get("executable_queue") or []
        }
        frontier = read_record(
            ROOT / "executable-frontier.json",
            "axis.external-development-supervisor.executable-frontier",
        )
        frontier_refs = set(frontier.get("selected") or [])
        ordered = [
            queue_by_ref[item.get("ref")]
            for item in (current_graph.get("scheduler_state") or {}).get(
                "selected_batch"
            )
            or []
            if item.get("ref") in queue_by_ref and item.get("ref") in frontier_refs
        ]
        selected_refs = {item.get("ref") for item in ordered}
        ordered.extend(
            queue_by_ref[ref]
            for ref in frontier.get("selected") or []
            if ref in queue_by_ref and ref not in selected_refs
        )
        for item in ordered:
            dispatched = dispatcher.dispatch(current_graph, run_id, item)
            if dispatched is not None:
                return dispatched
        return None

    def execute_with_continuation(assignment: dict) -> dict:
        first = execute_new_assignment(assignment, manager, supervisorctl, gate)
        if (
            assignment.get("assignment_type")
            not in {"read-only-analysis", "no-op-verification"}
            or assignment.get("work_item_disposition") != "requires-implementation"
        ):
            return first
        continuation_graph = rebuild()
        continuation = next(
            (
                item
                for item in continuation_graph.get("executable_queue") or []
                if item.get("target_ref") == assignment.get("work_item")
                and item.get("assignment_type")
                in {
                    "governance-document-mutation",
                    "code-implementation",
                    "ci-integration-repair",
                }
                and (item.get("authority") or {}).get("state")
                in {"direct", "inherited"}
            ),
            None,
        )
        if continuation is None:
            return first
        continuation = dict(continuation)
        continuation["selection_rationale"] = (
            "immediate authorized continuation from analysis; implementation-ready "
            "work preempts unrelated analysis"
        )
        next_assignment = dispatcher.dispatch(continuation_graph, run_id, continuation)
        if next_assignment is None:
            return first
        second = execute_new_assignment(next_assignment, manager, supervisorctl, gate)
        return {
            "result": "analysis-to-implementation-continuation",
            "analysis": first,
            "implementation": second,
        }

    if active:
        integrable = [value for value in active if is_integrable(value)]
        if not integrable:
            assignment = dispatch_first_available(graph)
            if assignment is not None:
                return execute_with_continuation(assignment)
            return {
                "result": "active-assignment-not-integrable",
                "assignments": [value["assignment_id"] for value in active],
                "lifecycle_states": [value.get("lifecycle_state") for value in active],
            }
        assignment = validate_assignment(integrable[0])
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
            responsibility=assignment["responsibility"],
            expected_source_branch=(assignment.get("worker") or {}).get("branch"),
            expected_sha=(assignment.get("worker") or {}).get("commit"),
            source_main_sha=(assignment.get("source_item") or {}).get(
                "repository_head"
            ),
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
            else "integrate"
            if inspection.get("merge_ready")
            else "waiting"
            if pipeline_status
            in {
                "created",
                "pending",
                "preparing",
                "running",
                "scheduled",
                "waiting_for_resource",
            }
            or inspection.get("review_pending")
            else "blocked"
        )
        integration: dict = {
            "result": {
                "result": integration_result,
                "evidence": [inspection["mr"].get("web_url")],
                "next": "merge when ready" if integration_result == "waiting" else "",
            },
            "pipeline": inspection.get("pipeline"),
            "merge_request": {
                "iid": inspection["mr"].get("iid"),
                "sha": inspection["mr"].get("sha"),
                "has_conflicts": inspection["mr"].get("has_conflicts"),
            },
        }
        worker_record = assignment.get("worker") or {}
        mr_value = inspection.get("mr") or {}
        diff_refs = mr_value.get("diff_refs") or {}
        main_advance = classify_main_advance(
            (assignment.get("source_item") or {}).get("repository_head"),
            diff_refs.get("start_sha") or diff_refs.get("base_sha"),
            worker_record.get("changed_paths") or assignment.get("allowed_paths"),
            mr_value.get("main_changed_paths"),
            merge_commit_sha=mr_value.get("merge_commit_sha"),
        )
        integration["main_advance"] = main_advance
        assignment["integration"] = integration
        assignment["last_integration_check_epoch"] = int(time.time())
        result = integration["result"].get("result")
        workflow = WorkflowState(ROOT)
        workflow.update_integration(
            assignment["assignment_id"],
            state=(
                "integrating"
                if result in {"integrate", "integrated-existing"}
                else "waiting-ci"
                if result == "waiting"
                else "blocked"
            ),
            main_advance=main_advance,
            last_error=None
            if result != "blocked"
            else "merge request is not integrable",
        )
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
            inspection = integrator.inspect_mr(
                assignment["project"],
                iid,
                responsibility=assignment["responsibility"],
                source_main_sha=(assignment.get("source_item") or {}).get(
                    "repository_head"
                ),
            )
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
                raise RuntimeError(
                    "integration assignment has no worktree/branch custody"
                )
            worktree = Path(str(worktree_value))
            repo = (
                Path("/home/cdenneen/src/workspace/personal/work")
                / assignment["project"].split("/")[-1]
            )
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
            subprocess.run(
                ["git", "fetch", "--prune", "origin"], cwd=worktree, check=True
            )
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
            elif recreated_worktree and (worktree / "pyproject.toml").is_file():
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
                        "--extra",
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
                cleanup["local_branch_deleted"] = (
                    subprocess.run(
                        [
                            "git",
                            "show-ref",
                            "--verify",
                            "--quiet",
                            f"refs/heads/{branch}",
                        ],
                        cwd=repo,
                        timeout=30,
                    ).returncode
                    != 0
                )
                cleanup["remote_source_branch_absent"] = (
                    subprocess.run(
                        [
                            "git",
                            "ls-remote",
                            "--exit-code",
                            "--heads",
                            "origin",
                            branch,
                        ],
                        cwd=repo,
                        text=True,
                        capture_output=True,
                        timeout=60,
                    ).returncode
                    != 0
                )
            if not all(
                cleanup[key]
                for key in (
                    "worktree_removed",
                    "local_branch_deleted",
                    "remote_source_branch_absent",
                )
            ):
                verification_error = (
                    "repository convergence incomplete after post-main verification: "
                    + json.dumps(cleanup, sort_keys=True)
                )
            integration["verified_mr"] = inspection["mr"].get("web_url")
            integration["post_merge_tests"] = test_results
            if not verification_error:
                set_lifecycle(assignment, "integrated-post-main-verified")
                assignment["result_state"] = "integrated-post-main-verified"
                assignment["work_item_disposition"] = "requires-repository-convergence"
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
                            str(
                                (inspection.get("pipeline") or {}).get("web_url") or ""
                            ),
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
                set_lifecycle(assignment, "repository-converged")
                assignment["result_state"] = "repository-converged"
                assignment["work_item_disposition"] = "requires-runtime-convergence"
                result = "integrated"
                workflow.update_integration(
                    assignment["assignment_id"],
                    state="integrated",
                    main_advance="integrated",
                )
                if (assignment.get("authority") or {}).get("state") == "canary":
                    expire_grant(ROOT, "consumed")
                    record_event(
                        ROOT,
                        "grant_consumed",
                        assignment=assignment,
                        details={
                            "grant_id": (assignment.get("authority") or {}).get(
                                "grant_id"
                            ),
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
                        "mr_url": (assignment.get("worker") or {})
                        .get("handoff", {})
                        .get("mr_url"),
                        "expected_next_phase": integration["result"].get("next")
                        or "next scheduled integration check",
                    },
                    source="cycle",
                )
                record_event(
                    ROOT,
                    "frontier_refill_requested",
                    assignment=assignment,
                    details={
                        "released_stage": "implementation",
                        "occupied_stage": "integration",
                        "reason": "CI or review waiting does not consume implementation capacity",
                    },
                    source="cycle",
                    notify=False,
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
        if assignment.get("lifecycle_state") in {
            "completed",
            "blocked",
            "failed",
            "cancelled",
        }:
            record_event(
                ROOT,
                "assignment_disposition",
                assignment=assignment,
                details={
                    "disposition": assignment.get("result_state"),
                    "work_item_disposition": assignment.get("work_item_disposition"),
                    "assignment_type": assignment.get("assignment_type"),
                    "post_main_verification": integration.get("post_merge_tests") or [],
                    "lease_state": "released"
                    if assignment.get("lease_id") is None
                    else "active",
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
            record_engineering_retrospective(ROOT, assignment, source="cycle")
        current_graph = rebuild()
        response = {"result": result, "assignment": assignment["assignment_id"]}
        if result in {"waiting", "blocked"}:
            capacity_assignment = dispatch_first_available(current_graph)
            if capacity_assignment is not None:
                response["capacity_reused_by"] = execute_with_continuation(
                    capacity_assignment
                )
        return response
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
        failures = []
        for item in selected_batch:
            assignment = dispatcher.dispatch(graph, run_id, item)
            if assignment is None:
                break
            try:
                results.append(execute_with_continuation(assignment))
            except Exception as exc:
                failures.append(
                    {
                        "assignment": assignment["assignment_id"],
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            graph = rebuild()
        return {
            "result": "tier-a-batch-partial" if failures else "tier-a-batch-complete",
            "batch_size": len(results),
            "assignments": results,
            "failures": failures,
        }

    assignment = dispatch_first_available(graph)
    if assignment is None:
        return {"result": "no-assignment", "queue_depth": graph["queue_depth"]}
    return execute_with_continuation(assignment)


def run_canary(
    assignment_id: str, run_id: str, hermes: str, supervisorctl: str
) -> dict:
    path = ROOT / "assignments" / f"{assignment_id}.json"
    assignment = validate_assignment(json.loads(path.read_text(encoding="utf-8")), ROOT)
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
        default=str(
            Path.home() / ".hermes" / "scripts" / "axis-development-supervisorctl.py"
        ),
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
    mission = ActiveMissionState(ROOT).observe(result, source="cycle-response")
    result = {**result, "mission": mission_summary(mission)}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
