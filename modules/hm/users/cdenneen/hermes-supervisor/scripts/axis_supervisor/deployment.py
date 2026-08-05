import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .lifecycle import set_lifecycle
from .mutation import MutationGate, OperationClass
from .observability import record_event
from .schema_registry import write_record


def create_deployment_assignment(root: Path, plan: dict, run_id: str) -> dict:
    assignment_id = f"deployment-{plan['target_runtime']}-{uuid.uuid4().hex[:8]}"
    assignment = {
        "schema": "axis.external-development-supervisor.assignment",
        "schema_version": "1.0.0",
        "assignment_id": assignment_id,
        "assignment_type": "capability-deployment",
        "result_state": "pending",
        "work_item_disposition": "requires-runtime-convergence",
        "lifecycle_state": "ready-implementation",
        "kind": "capability-deployment",
        "queue_ref": plan["assignment_id"],
        "target_ref": f"runtime:{plan['target_runtime']}",
        "work_item": f"runtime:{plan['target_runtime']}@{plan['expected_revision']}",
        "project": "cdenneen/home",
        "title": f"Deploy {', '.join(plan['affected_capabilities'])} to {plan['target_runtime']}",
        "authority": {
            "state": "deployment-policy",
            "source": plan.get("evidence") or [],
            "reason": "verified engineering plus repository convergence",
        },
        "governance_state": "Executable",
        "planning_record": None,
        "candidate": None,
        "allowed_paths": [],
        "required_tests": [],
        "source_item": {
            "target_runtime": plan["target_runtime"],
            "ring": plan["ring"],
            "affected_capabilities": plan["affected_capabilities"],
            "expected_revision": plan["expected_revision"],
            "deployment_target": plan["deployment_target"],
            "verification_plan": plan["verification_plan"],
            "rollback_plan": plan["rollback_plan"],
            "evidence": plan.get("evidence") or [],
        },
        "source_fingerprint": plan["assignment_id"],
        "source_inventory_generation_id": None,
        "revalidation_tier": None,
        "ranking_factors": {},
        "selection_rationale": "capability drift at the next eligible deployment ring",
        "created_by_run": run_id,
        "created_at_epoch": int(time.time()),
        "lease_id": None,
        "lease_uri": None,
        "worker": None,
        "mutation_grant_id": None,
        "mutation_grant_uri": None,
        "deployment_plan": plan,
    }
    gate = MutationGate(root, source="cycle")
    decision = gate.decide(OperationClass.RECONCILIATION)
    gate.require(decision, OperationClass.RECONCILIATION)
    write_record(
        root / "assignments" / f"{assignment_id}.json",
        assignment,
        "axis.external-development-supervisor.assignment",
    )
    record_event(
        root,
        "assignment_selected",
        assignment=assignment,
        details={
            "assignment_type": "capability-deployment",
            "affected_capabilities": plan["affected_capabilities"],
            "target_runtime": plan["target_runtime"],
            "selection_rationale": assignment["selection_rationale"],
        },
        source="cycle",
    )
    return assignment


def _command(home: Path, target: str) -> list[str]:
    if target == "ghost":
        return [
            "sudo",
            "-n",
            "nixos-rebuild",
            "switch",
            "--flake",
            f"{home}#ghost",
        ]
    remote = "VNJTECMBCD" if target == "desktop" else "nyx"
    rebuild = "darwin-rebuild" if target == "desktop" else "nixos-rebuild"
    selector = "VNJTECMBCD" if target == "desktop" else "nyx"
    remote_home = "/Users/cdenneen/code/workspace/nix/home" if target == "desktop" else "/home/cdenneen/src/workspace/nix/home"
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        remote,
        (
            f"test -d {remote_home}/.git && "
            f"git -C {remote_home} pull --rebase && "
            f"sudo -n {rebuild} switch --flake {remote_home}#{selector}"
        ),
    ]


def _identity(target: str, path: str) -> dict:
    if target == "ghost":
        return json.loads(Path(path).read_text(encoding="utf-8"))
    remote = "VNJTECMBCD" if target == "desktop" else "nyx"
    return json.loads(
        subprocess.check_output(
            ["ssh", "-o", "BatchMode=yes", remote, "cat", path],
            text=True,
            timeout=30,
        )
    )


def execute_deployment_assignment(
    root: Path, assignment: dict, supervisorctl: str
) -> dict:
    path = root / "assignments" / f"{assignment['assignment_id']}.json"
    target = assignment["source_item"]["target_runtime"]
    claim = subprocess.check_output(
        [
            sys.executable,
            supervisorctl,
            "claim",
            assignment["assignment_id"],
            "--run-id",
            assignment["created_by_run"],
            "--resource",
            f"runtime:{target}",
            "--ttl",
            "3600",
        ],
        text=True,
        timeout=30,
    )
    lease = json.loads(claim)
    assignment["lease_id"] = lease["lease_id"]
    assignment["lease_uri"] = (
        root / "leases" / lease["lease_id"] / "lease.json"
    ).resolve().as_uri()
    set_lifecycle(assignment, "running-implementation")
    gate = MutationGate(root, source="cycle")
    decision = gate.decide(OperationClass.RECONCILIATION)
    gate.require(decision, OperationClass.RECONCILIATION)
    write_record(path, assignment, "axis.external-development-supervisor.assignment")
    home = Path("/home/cdenneen/src/workspace/nix/home")
    try:
        if subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=home, text=True
        ).strip():
            raise RuntimeError("deployment source worktree is dirty")
        local_head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=home, text=True
        ).strip()
        remote_head = subprocess.check_output(
            ["git", "rev-parse", "origin/main"], cwd=home, text=True
        ).strip()
        if local_head != remote_head:
            raise RuntimeError("deployment source is not pushed to origin/main")
        started = time.time()
        completed = subprocess.run(
            _command(home, target), text=True, capture_output=True, timeout=3600
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"deployment command failed: {(completed.stderr or completed.stdout)[-4000:]}"
            )
        matrix = json.loads((root / "capability-runtime-matrix.json").read_text())
        identity = _identity(target, matrix["runtimes"][target]["identity_path"])
        if identity.get("runtime_revision") != assignment["source_item"][
            "expected_revision"
        ]:
            raise RuntimeError("runtime identity does not match expected revision")
        assignment["deployment_result"] = {
            "duration_seconds": round(time.time() - started, 3),
            "identity": identity,
            "command_stdout_tail": completed.stdout[-4000:],
        }
        assignment["result_state"] = "runtime-converged"
        assignment["work_item_disposition"] = "canonical-complete"
        set_lifecycle(assignment, "runtime-converged")
        record_event(
            root,
            "capability_deployment_verified",
            assignment=assignment,
            details={
                "target_runtime": target,
                "affected_capabilities": assignment["source_item"][
                    "affected_capabilities"
                ],
                "expected_revision": identity.get("runtime_revision"),
                "duration_seconds": assignment["deployment_result"][
                    "duration_seconds"
                ],
            },
            source="cycle",
        )
    except Exception as exc:
        assignment["error"] = f"{type(exc).__name__}: {exc}"
        assignment["result_state"] = "deployment-failed"
        assignment["work_item_disposition"] = "requires-runtime-convergence"
        set_lifecycle(assignment, "deployment-failed")
        raise
    finally:
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
        decision = gate.decide(OperationClass.RECONCILIATION)
        gate.require(decision, OperationClass.RECONCILIATION)
        write_record(path, assignment, "axis.external-development-supervisor.assignment")
    return {
        "assignment": assignment["assignment_id"],
        "result": assignment["result_state"],
        "target_runtime": target,
    }
