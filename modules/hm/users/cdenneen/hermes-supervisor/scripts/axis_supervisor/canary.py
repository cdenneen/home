import os
import shutil
import subprocess
import time
from pathlib import Path

from .models import validate_allowed_path
from .schema_registry import read_record, write_record


SCHEMA = "axis.external-development-supervisor.canary-grant"


class CanaryDenied(PermissionError):
    pass


def grant_path(root: Path) -> Path:
    return root / "canary-grant.json"


def load_grant(root: Path) -> dict:
    return read_record(grant_path(root), SCHEMA)


def write_grant(root: Path, grant: dict) -> None:
    write_record(grant_path(root), grant, SCHEMA)


def current_main_sha(repository: str) -> str:
    workspace = Path(
        os.environ.get(
            "AXIS_SUPERVISOR_WORKSPACE", "/home/cdenneen/src/workspace/personal/work"
        )
    )
    path = workspace / repository.split("/")[-1]
    git = shutil.which("git") or "/etc/profiles/per-user/cdenneen/bin/git"
    output = subprocess.check_output(
        [git, "ls-remote", "origin", "refs/heads/main"],
        cwd=path,
        text=True,
        timeout=60,
    ).splitlines()
    if not output:
        raise CanaryDenied("canary repository main ref is unavailable")
    return output[0].split()[0]


def merged_recovery_matches(
    grant: dict, assignment: dict, mr: dict | None, main_sha: str
) -> bool:
    if not mr or grant.get("mr_iid") is None:
        return False
    worker = assignment.get("worker") or {}
    handoff = worker.get("handoff") or {}
    bound_shas = {
        event.get("sha")
        for event in grant.get("events") or []
        if event.get("event") == "merge-request-bound"
        and int(event.get("iid") or 0) == int(grant["mr_iid"])
    }
    return bool(
        mr.get("state") == "merged"
        and int(mr.get("iid") or 0) == int(grant["mr_iid"])
        and int(handoff.get("mr_iid") or 0) == int(grant["mr_iid"])
        and mr.get("target_branch") == "main"
        and mr.get("source_branch") == grant["branch"]
        and mr.get("sha") == worker.get("commit")
        and mr.get("sha") in bound_shas
        and (mr.get("diff_refs") or {}).get("base_sha") == grant["source_sha"]
        and mr.get("merge_commit_sha") == main_sha
    )


def validate_canary(
    root: Path,
    assignment: dict,
    operation: str,
    repository: str | None,
    merged_mr: dict | None = None,
) -> dict:
    grant = load_grant(root)
    now = int(time.time())
    if grant["status"] != "active" or now >= int(grant["expires_at_epoch"]):
        raise CanaryDenied("canary grant is inactive or expired")
    if grant["assignment_id"] != assignment.get("assignment_id"):
        raise CanaryDenied("canary grant assignment mismatch")
    if grant["repository"] != repository or repository != assignment.get("project"):
        raise CanaryDenied("canary grant repository mismatch")
    if grant["target_ref"] != assignment.get("work_item"):
        raise CanaryDenied("canary grant target mismatch")
    if grant["source_sha"] != assignment.get("source_main_sha"):
        raise CanaryDenied("canary grant source SHA mismatch")
    main_sha = current_main_sha(grant["repository"])
    if main_sha != grant["source_sha"] and not merged_recovery_matches(
        grant, assignment, merged_mr, main_sha
    ):
        raise CanaryDenied("canary source SHA is stale")
    if grant["branch"] != assignment.get("canary_branch"):
        raise CanaryDenied("canary branch mismatch")
    allowed = [validate_allowed_path(path) for path in assignment.get("allowed_paths") or []]
    if allowed != grant["allowed_paths"]:
        raise CanaryDenied("canary path scope mismatch")
    if list(assignment.get("required_tests") or []) != grant["required_tests"]:
        raise CanaryDenied("canary test scope mismatch")
    if operation not in grant["operation_sequence"]:
        raise CanaryDenied("operation is outside the canary sequence")
    worker = assignment.get("worker") or {}
    handoff = worker.get("handoff") or {}
    if grant.get("mr_iid") is not None and int(handoff.get("mr_iid") or 0) not in {
        0,
        int(grant["mr_iid"]),
    }:
        raise CanaryDenied("canary merge request mismatch")
    return grant


def append_event(root: Path, event: dict) -> dict:
    grant = load_grant(root)
    grant.setdefault("events", []).append(
        {"recorded_at_epoch": int(time.time()), **event}
    )
    write_grant(root, grant)
    return grant


def bind_mr(root: Path, assignment: dict, mr: dict) -> dict | None:
    if (assignment.get("authority") or {}).get("state") != "canary":
        return None
    grant = load_grant(root)
    if grant["assignment_id"] != assignment.get("assignment_id"):
        raise CanaryDenied("cannot bind MR to another canary assignment")
    grant["mr_iid"] = int(mr["iid"])
    grant.setdefault("events", []).append(
        {
            "recorded_at_epoch": int(time.time()),
            "event": "merge-request-bound",
            "iid": int(mr["iid"]),
            "url": mr.get("web_url"),
            "sha": mr.get("sha"),
        }
    )
    write_grant(root, grant)
    return grant


def expire_grant(root: Path, status: str) -> dict:
    if status not in {"consumed", "expired", "failed"}:
        raise ValueError("invalid terminal canary status")
    grant = load_grant(root)
    grant["status"] = status
    grant["expires_at_epoch"] = min(int(grant["expires_at_epoch"]), int(time.time()))
    grant.setdefault("events", []).append(
        {"recorded_at_epoch": int(time.time()), "event": f"grant-{status}"}
    )
    write_grant(root, grant)
    control = read_record(
        root / "control.json", "axis.external-development-supervisor.control"
    )
    control["mode"] = "observing"
    control["allow_repository_mutation"] = False
    write_record(
        root / "control.json",
        control,
        "axis.external-development-supervisor.control",
    )
    return grant
