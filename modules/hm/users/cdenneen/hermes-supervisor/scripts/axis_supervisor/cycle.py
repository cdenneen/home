import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axis_supervisor.dispatcher import Dispatcher
from axis_supervisor.graph import ExecutionGraphBuilder
from axis_supervisor.workers import HermesWorkerManager
from axis_supervisor.integrator import Integrator
from axis_supervisor.models import validate_assignment

ROOT = Path(os.environ.get("AXIS_SUPERVISOR_ROOT", Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor"))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, value: dict) -> None:
    validate_assignment(value)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def publish_implementation(assignment: dict, result: dict, glab: str) -> dict:
    worktree = Path(result["worktree"])
    branch = result["branch"]
    subprocess.run(
        ["git", "push", "-u", "origin", branch],
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
    result["handoff"].update(
        {
            "mr_iid": mr.get("iid"),
            "mr_url": mr.get("web_url"),
            "pipeline_id": None,
            "pipeline_url": None,
        }
    )
    return result


def release_failed_assignment(
    assignment: dict, path: Path, supervisorctl: str
) -> None:
    worker = assignment.get("worker") or {}
    worktree_value = worker.get("worktree") or assignment.get("worktree")
    if worktree_value:
        worktree = Path(worktree_value)
        repo = Path("/home/cdenneen/src/workspace/personal/work") / assignment[
            "project"
        ].split("/")[-1]
        if worktree.exists():
            recovery_dir = ROOT / "recovery"
            recovery_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            patch = subprocess.run(
                ["git", "diff", "HEAD"], cwd=worktree, text=True, capture_output=True
            ).stdout
            (recovery_dir / f"{assignment['assignment_id']}.patch").write_text(
                patch, encoding="utf-8"
            )
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repo,
                check=False,
            )
        branch = worker.get("branch")
        if branch:
            subprocess.run(["git", "branch", "-D", branch], cwd=repo, check=False)
    lease = assignment.get("lease") or {}
    if lease.get("fencing_token"):
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
    assignment["state"] = "failed"
    assignment["phase"] = "failed"
    save(path, assignment)


def rebuild() -> dict:
    inventory = load(ROOT / "inventory.json")
    return ExecutionGraphBuilder(ROOT).build(inventory)


def run_next(run_id: str, hermes: str, supervisorctl: str) -> dict:
    graph = rebuild()
    dispatcher = Dispatcher(ROOT)
    active = dispatcher.active()
    manager = HermesWorkerManager(ROOT, hermes, supervisorctl)
    if active:
        assignment = validate_assignment(active[0])
        path = ROOT / "assignments" / f"{assignment['assignment_id']}.json"
        if assignment.get("phase") != "awaiting-integration":
            return {
                "result": "active-assignment-not-integrable",
                "assignment": assignment["assignment_id"],
                "phase": assignment.get("phase"),
            }
        subprocess.run(
            [
                sys.executable,
                supervisorctl,
                "heartbeat",
                assignment["assignment_id"],
                "--token",
                assignment["lease"]["fencing_token"],
                "--ttl",
                "3600",
            ],
            check=True,
        )
        integration = manager.integration(
            assignment,
            "/etc/profiles/per-user/cdenneen/bin/glab",
        )
        assignment["integration"] = integration
        result = integration["result"].get("result")
        if result == "integrated":
            handoff = (assignment.get("worker") or {}).get("handoff") or {}
            iid = int(handoff.get("mr_iid") or 0)
            inspection = Integrator(
                "/etc/profiles/per-user/cdenneen/bin/glab"
            ).inspect_mr(assignment["project"], iid)
            if inspection["mr"].get("state") != "merged":
                raise RuntimeError("integration worker claimed merge but MR is not merged")
            worktree = Path((assignment.get("worker") or {}).get("worktree"))
            repo = Path("/home/cdenneen/src/workspace/personal/work") / assignment[
                "project"
            ].split("/")[-1]
            subprocess.run(["git", "fetch", "--prune", "origin"], cwd=worktree, check=True)
            subprocess.run(
                ["git", "switch", "--detach", "origin/main"], cwd=worktree, check=True
            )
            test_results = []
            for command in assignment.get("required_tests") or []:
                completed = subprocess.run(
                    ["bash", "-lc", command], cwd=worktree, text=True, capture_output=True
                )
                test_results.append(
                    {"command": command, "returncode": completed.returncode}
                )
                if completed.returncode != 0:
                    raise RuntimeError(f"post-merge test failed: {command}")
            branch = (assignment.get("worker") or {}).get("branch")
            subprocess.run(
                ["git", "worktree", "remove", str(worktree)], cwd=repo, check=True
            )
            subprocess.run(["git", "branch", "-d", branch], cwd=repo, check=True)
            integration["verified_mr"] = inspection["mr"].get("web_url")
            integration["post_merge_tests"] = test_results
            assignment["state"] = "complete"
            assignment["phase"] = "integrated"
            subprocess.run(
                [
                    sys.executable,
                    supervisorctl,
                    "release",
                    assignment["assignment_id"],
                    "--token",
                    assignment["lease"]["fencing_token"],
                ],
                check=True,
            )
        else:
            assignment["state"] = "waiting" if result == "waiting" else "active"
        save(path, assignment)
        rebuild()
        return {"result": result, "assignment": assignment["assignment_id"]}
    assignment = dispatcher.dispatch(graph, run_id)
    if assignment is None:
        return {"result": "no-assignment", "queue_depth": graph["queue_depth"]}
    path = ROOT / "assignments" / f"{assignment['assignment_id']}.json"
    resource = f"repo:{assignment.get('project') or 'ghostspace/axis'}"
    claim_command = [
            sys.executable,
            supervisorctl,
            "claim",
            assignment["assignment_id"],
            "--run-id",
            run_id,
            "--resource",
            resource,
            "--ttl",
            "1200" if assignment["kind"] == "semantic-decomposition" else "3600",
        ]
    if assignment["kind"] == "semantic-decomposition":
        claim_command.append("--read-only")
    try:
        lease_output = subprocess.check_output(
            claim_command,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        assignment["state"] = "failed"
        assignment["error"] = f"lease claim failed: {type(exc).__name__}: {exc}"
        save(path, assignment)
        raise
    assignment["lease"] = json.loads(lease_output)
    assignment["state"] = "active"
    save(path, assignment)
    try:
        if assignment["kind"] == "semantic-decomposition":
            result = manager.semantic(assignment)
            assignment["state"] = "complete"
            assignment["phase"] = "semantic-complete"
        else:
            repo = Path("/home/cdenneen/src/workspace/personal/work") / assignment["project"].split("/")[-1]
            result = manager.implementation(assignment, repo)
            result = publish_implementation(
                assignment,
                result,
                "/etc/profiles/per-user/cdenneen/bin/glab",
            )
            assignment["state"] = "active"
            assignment["phase"] = "awaiting-integration"
        assignment["worker"] = result
        save(path, assignment)
        if assignment["state"] == "complete":
            subprocess.run(
                [
                    sys.executable,
                    supervisorctl,
                    "release",
                    assignment["assignment_id"],
                    "--token",
                    assignment["lease"]["fencing_token"],
                ],
                check=True,
            )
        rebuild()
        return {"result": assignment["phase"], "assignment": assignment["assignment_id"]}
    except Exception as exc:
        assignment["error"] = f"{type(exc).__name__}: {exc}"
        release_failed_assignment(assignment, path, supervisorctl)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("rebuild", "run-next"))
    parser.add_argument("--run-id")
    parser.add_argument("--hermes", default="hermes")
    parser.add_argument(
        "--supervisorctl",
        default=str(Path.home() / ".hermes" / "scripts" / "axis-development-supervisorctl.py"),
    )
    args = parser.parse_args()
    if args.command == "rebuild":
        print(json.dumps(rebuild(), sort_keys=True))
        return 0
    if not args.run_id:
        parser.error("run-next requires --run-id")
    print(json.dumps(run_next(args.run_id, args.hermes, args.supervisorctl), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
