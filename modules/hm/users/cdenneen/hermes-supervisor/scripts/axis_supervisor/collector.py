#!/usr/bin/env python3
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

try:
    from .finding_ingestion import normalize_gitlab_findings
    from .lifecycle import is_terminal
    from .models import validate_assignment
    from .mutation import MutationGate, OperationClass
    from .schema_registry import read_record, write_record
except ImportError:
    from axis_supervisor.finding_ingestion import normalize_gitlab_findings
    from axis_supervisor.lifecycle import is_terminal
    from axis_supervisor.models import validate_assignment
    from axis_supervisor.mutation import MutationGate, OperationClass
    from axis_supervisor.schema_registry import read_record, write_record

ROOT = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_ROOT",
        Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor",
    )
)
CONTROL = ROOT / "control.json"
INVENTORY = ROOT / "inventory.json"
WORKSPACE = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_WORKSPACE", "/home/cdenneen/src/workspace/personal/work"
    )
)
GITLAB_HOST = "gitlab.com"
GROUP = "ghostspace"
NOTE_COLLECTION_REVISION = "gitlab-issue-notes-v1"
NOTES_OK = "NOTES_OK"
NOTES_EMPTY = "NOTES_EMPTY"
NOTES_ERROR = "NOTES_ERROR"
NOTE_PAGE_SIZE = 100
NOTE_PAGE_RETRIES = 2
NOTE_MAX_PAGES = 100
NOTE_STORAGE_LIMIT = 100
NOTE_BODY_LIMIT = 12000
_CLOSED_NOTE_TRACE_LIMIT = 500
_CLOSED_NOTE_TRACE_FIELD_LIMIT = 512
_CLOSED_NOTE_MARKERS = (
    "immutable planningrecord",
    "planningrecord v2",
    "current-main regression finding",
    "finding amendment",
)
_ISSUE_REF = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+\Z")
GLAB = (
    os.environ.get("AXIS_SUPERVISOR_GLAB")
    or shutil.which("glab")
    or "/etc/profiles/per-user/cdenneen/bin/glab"
)
GIT = (
    os.environ.get("AXIS_SUPERVISOR_GIT")
    or shutil.which("git")
    or "/etc/profiles/per-user/cdenneen/bin/git"
)


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def load_control() -> dict:
    return read_record(CONTROL, "axis.external-development-supervisor.control")


def run(args: list[str], cwd: Path | None = None, timeout: int = 60) -> str:
    return subprocess.check_output(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
    )


def decode_json_stream(raw: str) -> list:
    decoder = json.JSONDecoder()
    values = []
    index = 0
    while index < len(raw):
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index >= len(raw):
            break
        value, index = decoder.raw_decode(raw, index)
        values.extend(value if isinstance(value, list) else [value])
    return values


def glab(path: str, paginate: bool = False, timeout: int = 90):
    command = [GLAB, "api", "--hostname", GITLAB_HOST]
    if paginate:
        command.append("--paginate")
    command.append(path)
    raw = run(command, timeout=timeout)
    return decode_json_stream(raw) if paginate else json.loads(raw)


def _note_body_digest(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _stable_author(note: dict) -> tuple[dict, str]:
    author = note.get("author")
    if not isinstance(author, dict):
        raise ValueError("note author is not an object")
    user_id = author.get("id")
    username = str(author.get("username") or "")
    if isinstance(user_id, int):
        identity = f"gitlab-user:{user_id}"
    elif username:
        identity = f"gitlab-username:{username}"
    else:
        raise ValueError("note author has no stable identity")
    return {"id": user_id, "username": username or None}, identity


def _normalize_note(note: object, fetched_at: str) -> dict:
    if not isinstance(note, dict):
        raise ValueError("note is not an object")
    note_id = note.get("id")
    if not isinstance(note_id, int):
        raise ValueError("note has no integer id")
    body = note.get("body")
    if not isinstance(body, str):
        raise ValueError(f"note {note_id} body is not a string")
    author, author_identity = _stable_author(note)
    created_at = note.get("created_at")
    updated_at = note.get("updated_at")
    if not isinstance(created_at, str) or not isinstance(updated_at, str):
        raise ValueError(f"note {note_id} has invalid timestamps")
    return {
        "id": note_id,
        "author": author,
        "author_identity": author_identity,
        "created_at": created_at,
        "updated_at": updated_at,
        "body": body,
        "body_digest": _note_body_digest(body),
        "system": bool(note.get("system")),
        "fetched_at": fetched_at,
        "collector_revision": NOTE_COLLECTION_REVISION,
    }


def _read_issue_notes(request, project_id: str, issue_iid: int, fetched_at: str, retries: int):
    notes: list[dict] = []
    seen_note_ids: set[int] = set()
    for page in range(1, NOTE_MAX_PAGES + 1):
        page_notes: list[dict] = []
        path = (
            f"projects/{project_id}/issues/{issue_iid}/notes?per_page={NOTE_PAGE_SIZE}"
            f"&page={page}&order_by=created_at&sort=asc"
        )
        for attempt in range(retries + 1):
            try:
                raw_page = request(path)
                if not isinstance(raw_page, list):
                    raise ValueError("notes page is not an array")
                page_notes = [_normalize_note(value, fetched_at) for value in raw_page]
                break
            except Exception as exc:
                if attempt == retries:
                    return None, f"page {page}: {type(exc).__name__}"
                time.sleep(0.1 * (attempt + 1))
        for note in page_notes:
            if note["id"] in seen_note_ids:
                return None, f"page {page}: duplicate note id {note['id']}"
            seen_note_ids.add(note["id"])
            notes.append(note)
        if len(page_notes) < NOTE_PAGE_SIZE:
            return notes, None
    return None, f"pagination exceeded {NOTE_MAX_PAGES} pages"


def _note_snapshot_signature(notes: list[dict]) -> list[tuple]:
    return sorted(
        (
            note["id"],
            note["body_digest"],
            (note.get("author") or {}).get("id"),
            (note.get("author") or {}).get("username"),
            note["created_at"],
            note["updated_at"],
            bool(note.get("system")),
        )
        for note in notes
    )


def collect_issue_notes(
    request,
    project_id: str,
    issue_iid: int,
    *,
    fetched_at: str | None = None,
    retries: int = NOTE_PAGE_RETRIES,
) -> dict:
    """Accept only two matching complete reads of the paginated GitLab note trace."""
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    last_error = "snapshot drift"
    for attempt in range(retries + 1):
        first, error = _read_issue_notes(
            request, project_id, issue_iid, fetched_at, retries
        )
        if error or first is None:
            last_error = error or "empty primary snapshot"
            continue
        second, error = _read_issue_notes(
            request, project_id, issue_iid, fetched_at, retries
        )
        if error or second is None:
            last_error = error or "empty verification snapshot"
            continue
        if _note_snapshot_signature(first) != _note_snapshot_signature(second):
            last_error = "snapshot drift"
            continue
        first.sort(key=lambda note: (note["created_at"], note["id"]), reverse=True)
        return {
            "state": NOTES_OK if first else NOTES_EMPTY,
            "notes": first,
            "fetched_at": fetched_at,
            "collector_revision": NOTE_COLLECTION_REVISION,
        }
    return {
        "state": NOTES_ERROR,
        "notes": [],
        "fetched_at": fetched_at,
        "collector_revision": NOTE_COLLECTION_REVISION,
        "error": last_error,
    }


def _authority_note(note: dict, trusted_user_ids: set[int | str]) -> bool:
    if note.get("system") or (note.get("author") or {}).get("id") not in trusted_user_ids:
        return False
    body = str(note.get("body") or "")
    return bool(
        re.search(
            r"immutable PlanningRecord|\*\*Approve\*\*|Product Owner approval|Approved .*PlanningRecord",
            body,
            re.I,
        )
    )


def _stored_notes(notes: list[dict]) -> list[dict]:
    return [
        note | {"body": str(note["body"])[:NOTE_BODY_LIMIT]}
        for note in notes[:NOTE_STORAGE_LIMIT]
    ]


def issue_ref(project: dict, issue: dict) -> str:
    return f"{project['path_with_namespace']}#{issue['iid']}"


def _bounded_trace_value(value: object) -> str:
    return str(value)[:_CLOSED_NOTE_TRACE_FIELD_LIMIT]


def _closed_note_eligibility(
    project: dict, issue: dict, active_mission_refs: set[str]
) -> dict:
    state = issue.get("state")
    if state == "opened":
        return {
            "eligible": True,
            "reason": "opened",
            "ref": None,
            "active_mission": False,
            "marker_matches": [],
        }
    if state != "closed":
        return {
            "eligible": False,
            "reason": "state-not-eligible",
            "ref": None,
            "active_mission": False,
            "marker_matches": [],
        }
    ref = issue_ref(project, issue)
    labels = issue.get("labels") or []
    normalized_text = "\n".join(
        [
            str(issue.get("title") or ""),
            str(issue.get("description") or ""),
            *[str(label) for label in labels],
        ]
    ).lower()
    marker_matches = [
        marker for marker in _CLOSED_NOTE_MARKERS if marker in normalized_text
    ]
    active_mission = ref in active_mission_refs
    if active_mission:
        return {
            "eligible": True,
            "reason": "active-mission",
            "ref": ref,
            "active_mission": active_mission,
            "marker_matches": marker_matches,
        }
    return {
        "eligible": bool(marker_matches),
        "reason": "structured-marker" if marker_matches else "no-structured-marker",
        "ref": ref,
        "active_mission": active_mission,
        "marker_matches": marker_matches,
    }


def _write_closed_note_eligibility_trace(
    project: dict, issue: dict, decision: dict
) -> None:
    """Persist a bounded closed-issue decision trace without duplicating descriptions."""
    if issue.get("state") != "closed":
        return
    try:
        path = (
            ROOT
            / "engineering-memory"
            / "diagnostics"
            / "closed-issue-note-eligibility.jsonl"
        )
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        labels = issue.get("labels") or []
        entry = {
            "schema": "axis.supervisor.closed-issue-note-eligibility-trace.v1",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "project": _bounded_trace_value(project.get("path_with_namespace") or ""),
            "iid": issue.get("iid"),
            "state": issue.get("state"),
            "labels": [_bounded_trace_value(label) for label in labels[:50]],
            "milestone": _bounded_trace_value(issue.get("milestone") or ""),
            "title": _bounded_trace_value(issue.get("title") or ""),
            "raw_description_type": type(issue.get("description")).__name__,
            "normalized_text_marker_detection": decision["marker_matches"],
            "predicate_inputs": {
                "active_mission": decision["active_mission"],
                "closed_note_markers": list(_CLOSED_NOTE_MARKERS),
            },
            "predicate_result": decision["eligible"],
            "reason": decision["reason"],
            "notes_invocation": {"collect_issue_notes_called": decision["eligible"]},
        }
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        lines = [
            *lines[-(_CLOSED_NOTE_TRACE_LIMIT - 1) :],
            json.dumps(entry, sort_keys=True),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except (OSError, TypeError, ValueError):
        pass


def active_mission_issue_refs() -> set[str]:
    """Return exact issue references from an active, schema-valid mission only."""
    try:
        mission = read_record(
            ROOT / "active-mission.json",
            "axis.external-development-supervisor.active-mission",
        )
    except Exception:
        return set()
    if mission.get("current_state") != "active":
        return set()
    refs = set()
    for section in ("generated_actions", "active_assignments"):
        for entry in mission.get(section) or []:
            if not isinstance(entry, dict):
                continue
            for field in ("source_ref", "target", "work_item"):
                value = entry.get(field)
                if isinstance(value, str) and _ISSUE_REF.fullmatch(value):
                    refs.add(value)
    return refs


def should_collect_issue_notes(
    project: dict, issue: dict, active_mission_refs: set[str]
) -> bool:
    return _closed_note_eligibility(project, issue, active_mission_refs)["eligible"]


def collect_eligible_issue_notes(
    request,
    project: dict,
    project_id: str,
    issue: dict,
    active_mission_refs: set[str],
) -> dict:
    decision = _closed_note_eligibility(project, issue, active_mission_refs)
    if decision["eligible"]:
        snapshot = collect_issue_notes(request, project_id, int(issue["iid"]))
    else:
        snapshot = {
            "state": NOTES_EMPTY,
            "notes": [],
            "fetched_at": None,
            "collector_revision": NOTE_COLLECTION_REVISION,
        }
    _write_closed_note_eligibility_trace(project, issue, decision)
    return snapshot


def mr_mentions_issue(mr: dict, issue: dict) -> bool:
    text = f"{mr.get('title', '')}\n{mr.get('description', '')}"
    iid = str(issue["iid"])
    branch = str(mr.get("source_branch") or "")
    closing_ref = bool(
        re.search(
            rf"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(?:{re.escape(iid)})(?!\d)",
            text,
            re.I,
        )
    )
    branch_ref = bool(
        re.search(rf"(?:^|[-_/])(?:axis)?{re.escape(iid)}(?:[-_/]|$)", branch, re.I)
    )
    return closing_ref or branch_ref


def extract_authority_facts(
    text: str,
    note_bodies: list[str] | None = None,
    approval_bodies: list[str] | None = None,
) -> dict:
    digests = sorted(set(re.findall(r"sha256:[0-9a-f]{64}", text, flags=re.I)))
    approval_notes = [
        body
        for body in (approval_bodies or [])
        if re.search(
            r"\*\*Approve\*\*|Product Owner approval (?:with conditions|—)|Approved .*PlanningRecord",
            body,
            re.I,
        )
    ]
    approval_digests = sorted(
        {
            digest.lower()
            for body in approval_notes
            for digest in re.findall(r"sha256:[0-9a-f]{64}", body, flags=re.I)
        }
    )
    record_digest = None
    record_body = None
    for body in note_bodies or []:
        if body in approval_notes or not re.search(
            r"immutable PlanningRecord|PlanningRecord v1.*immutable revision",
            body,
            re.I | re.S,
        ):
            continue
        match = re.search(
            r"(?:Digest|digest):\s*`?(sha256:[0-9a-f]{64})", body, re.I
        )
        if match:
            record_digest = match.group(1).lower()
            record_body = body
            break
    approval = bool(approval_notes and approval_digests)
    approval_matches_record = bool(
        approval and record_digest is not None and record_digest in approval_digests
    )
    execution = None
    match = re.search(
        r"execution_rag:\s*(?:.|\n){0,300}?state:\s*(green|amber|red)", text, re.I
    )
    if match:
        execution = match.group(1).lower()
    approval_required = bool(
        re.search(
            r"approval-required|product_owner_approval_required:\s*true|approval_ref:\s*null",
            text,
            re.I,
        )
    )
    revision_match = re.search(r"(?:revision|Revision):\s*(\d+)", text)
    assignment_type_match = re.search(
        r"Assignment type:\s*([a-z-]+)", record_body or "", re.I
    )

    def markdown_list(label: str, following: str) -> list[str]:
        match = re.search(
            rf"{re.escape(label)}:\s*\n(?P<items>(?:- .+\n?)+?)(?={following}:|\n\n|\Z)",
            record_body or "",
            re.I,
        )
        if not match:
            return []
        return [
            line.removeprefix("- ").strip()
            for line in match.group("items").splitlines()
            if line.startswith("- ")
        ]

    return {
        "digests": digests,
        "approved": approval,
        "approval_digests": approval_digests,
        "record_digest": record_digest,
        "record_revision": int(revision_match.group(1)) if revision_match else 1,
        "approval_matches_record": approval_matches_record,
        "approval_mismatch": bool(
            approval and record_digest and not approval_matches_record
        ),
        "execution_rag": execution,
        "approval_required": approval_required,
        "decision_stop": bool(
            re.search(r"(?:outcome|decision):\s*stop", text, re.I)
        ),
        "decision_escalate": bool(
            re.search(r"(?:outcome|decision):\s*escalate", text, re.I)
        ),
        "approved_assignment_type": assignment_type_match.group(1).lower()
        if assignment_type_match
        else None,
        "approved_allowed_paths": markdown_list("Allowed paths", "Required tests"),
        "approved_required_tests": markdown_list(
            "Required tests", "execution_rag"
        ),
    }


def extract_acceptance_facts(text: str) -> dict:
    acceptance_ids = sorted(
        set(re.findall(r"acceptance_id:\s*([A-Za-z0-9._-]+)", text))
    )
    open_ids = sorted(
        {
            match.group(1)
            for match in re.finditer(
                r"acceptance_id:\s*([A-Za-z0-9._-]+)"
                r"(?:(?!acceptance_id:).){0,1200}?state:\s*(open|pending|blocked)",
                text,
                re.I | re.S,
            )
        }
    )
    return {"ids": acceptance_ids, "open_ids": open_ids}


def extract_findings(
    notes: list[dict],
    owner_ref: str,
    source_sha: str | None = None,
    trusted_principals: set[int | str] | None = None,
) -> list[dict]:
    return normalize_gitlab_findings(
        notes, owner_ref, source_sha, trusted_principals
    )


def retire_unsupported_watchdog_assignment(raw_assignment: dict) -> bool:
    """Retire the legacy watchdog analysis contract before it can be dispatched."""
    if (
        not str(raw_assignment.get("assignment_id") or "").startswith(
            "assignment-watchdog-"
        )
        or raw_assignment.get("lifecycle_state") != "ready-semantic"
    ):
        return False
    raw_assignment.update(
        {
            "lifecycle_state": "cancelled",
            "result_state": "cancelled",
            "work_item_disposition": "requires-human-decision",
            "retirement": {
                "classification": "INVALID",
                "reason": "watchdog-ready-semantic-contract-is-not-dispatchable",
                "source": "collector",
                "invalid_contract": {
                    "assignment_type": raw_assignment.get("assignment_type"),
                    "action_contract": raw_assignment.get("action_contract"),
                    "worker_path": None,
                    "handoff_path": None,
                    "review_path": None,
                },
            },
            "provenance": {
                "source": "collector",
                "invalid_contract": {
                    "assignment_type": raw_assignment.get("assignment_type"),
                    "action_contract": raw_assignment.get("action_contract"),
                    "worker_path": None,
                    "handoff_path": None,
                    "review_path": None,
                },
            },
        }
    )
    return True


def approval_note_url(
    notes: list[dict], trusted_user_ids: set[int | str], digest: str | None, issue_url: str
) -> str | None:
    if not digest:
        return None
    for note in notes:
        body = str(note.get("body") or "")
        if (
            not note.get("system")
            and (note.get("author") or {}).get("id") in trusted_user_ids
            and re.search(
                r"\*\*Approve\*\*|Product Owner approval (?:with conditions|—)|Approved .*PlanningRecord",
                body,
                re.I,
            )
            and digest.lower() in body.lower()
        ):
            return f"{issue_url}#note_{note.get('id')}"
    return None


def local_repository_state(project: dict, merge_requests: list[dict] | None = None) -> dict:
    path = WORKSPACE / project["path"]
    state = {
        "path": str(path),
        "present": (path / ".git").exists(),
        "canonical_default_branch": project.get("default_branch"),
    }
    if not state["present"]:
        return state
    try:
        subprocess.run(
            [GIT, "fetch", "--prune", "origin"],
            cwd=path,
            text=True,
            capture_output=True,
            check=True,
            timeout=120,
        )
        default_remote = f"origin/{project.get('default_branch') or 'main'}"
        state["default_remote"] = default_remote
        state["default_remote_head"] = run([GIT, "rev-parse", default_remote], path).strip()
        remote_ref = f"refs/heads/{project.get('default_branch') or 'main'}"
        remote_lines = run([GIT, "ls-remote", "origin", remote_ref], path).splitlines()
        remote_head = remote_lines[0].split()[0] if remote_lines else None
        if remote_head and remote_head != state["default_remote_head"]:
            subprocess.run(
                [GIT, "fetch", "--prune", "origin"],
                cwd=path,
                text=True,
                capture_output=True,
                check=True,
                timeout=120,
            )
            state["default_remote_head"] = run(
                [GIT, "rev-parse", default_remote], path
            ).strip()
        state["remote_fresh"] = remote_head == state["default_remote_head"]
        state["observed_remote_head"] = remote_head
        state["head"] = run([GIT, "rev-parse", "HEAD"], path).strip()
        state["branch"] = run([GIT, "branch", "--show-current"], path).strip()
        state["dirty"] = bool(run([GIT, "status", "--porcelain"], path).strip())
        worktree_raw = run([GIT, "worktree", "list", "--porcelain"], path)
        worktrees = []
        for block in (
            block for block in worktree_raw.strip().split("\n\n") if block.strip()
        ):
            fields = {}
            for line in block.splitlines():
                key, _, value = line.partition(" ")
                fields[key] = value
            worktree_path = Path(fields.get("worktree", ""))
            branch = fields.get("branch", "").removeprefix("refs/heads/")
            head = fields.get("HEAD") or fields.get("head")
            dirty = None
            if worktree_path.exists():
                try:
                    dirty = bool(
                        run([GIT, "status", "--porcelain"], worktree_path).strip()
                    )
                except Exception:
                    dirty = None
            integrated = bool(
                head
                and subprocess.run(
                    [GIT, "merge-base", "--is-ancestor", head, default_remote],
                    cwd=str(path),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                == 0
            )
            worktrees.append(
                {
                    "path": str(worktree_path),
                    "head": head,
                    "branch": branch,
                    "dirty": dirty,
                    "integrated_into_default": integrated,
                    "is_root": worktree_path.resolve() == path.resolve(),
                }
            )
        state["worktrees"] = worktrees
        branch_raw = run(
            [GIT, "for-each-ref", "--format=%(refname:short)|%(objectname)", "refs/heads"],
            path,
        )
        local_branches = []
        for line in branch_raw.splitlines():
            if "|" not in line:
                continue
            name, head = line.split("|", 1)
            integrated = (
                subprocess.run(
                    [GIT, "merge-base", "--is-ancestor", head, default_remote],
                    cwd=str(path),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                == 0
            )
            local_branches.append(
                {
                    "name": name,
                    "head": head,
                    "integrated_into_default": integrated,
                }
            )
        state["local_branches"] = local_branches
        remote_raw = run(
            [
                GIT,
                "for-each-ref",
                "--format=%(refname:short)|%(objectname)",
                "refs/remotes/origin",
            ],
            path,
        )
        mr_by_branch = {}
        for mr in sorted(
            merge_requests or [], key=lambda value: int(value.get("iid") or 0)
        ):
            source_branch = str(mr.get("source_branch") or "")
            if source_branch:
                mr_by_branch[source_branch] = mr
        remote_branches = []
        default_branch = str(project.get("default_branch") or "main")
        for line in remote_raw.splitlines():
            if "|" not in line:
                continue
            remote_name, head = line.split("|", 1)
            branch = remote_name.removeprefix("origin/")
            if remote_name in {"origin", "origin/HEAD", f"origin/{default_branch}"} or branch in {
                "HEAD",
                default_branch,
            }:
                continue
            merge_base = run(
                [GIT, "merge-base", default_remote, remote_name], path
            ).strip()
            behind, ahead = (
                run(
                    [
                        GIT,
                        "rev-list",
                        "--left-right",
                        "--count",
                        f"{default_remote}...{remote_name}",
                    ],
                    path,
                )
                .strip()
                .split()
            )
            integrated = (
                subprocess.run(
                    [GIT, "merge-base", "--is-ancestor", head, default_remote],
                    cwd=str(path),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                == 0
            )
            changed_paths = [
                value
                for value in run(
                    [GIT, "diff", "--name-only", f"{merge_base}..{remote_name}"],
                    path,
                ).splitlines()
                if value
            ]
            mr = mr_by_branch.get(branch) or {}
            remote_branches.append(
                {
                    "name": branch,
                    "head": head,
                    "merge_base": merge_base,
                    "ahead": int(ahead),
                    "behind": int(behind),
                    "integrated_into_default": integrated,
                    "changed_paths": changed_paths,
                    "owned_by_supervisor": branch.startswith("hermes/"),
                    "active_worktree": next(
                        (
                            value.get("path")
                            for value in worktrees
                            if value.get("branch") == branch
                        ),
                        None,
                    ),
                    "merge_request": {
                        "iid": mr.get("iid"),
                        "state": mr.get("state"),
                        "sha": mr.get("sha"),
                        "web_url": mr.get("web_url"),
                        "pipeline_status": (mr.get("head_pipeline") or {}).get(
                            "status"
                        ),
                    }
                    if mr
                    else None,
                }
            )
        state["remote_branches"] = remote_branches
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
    return state


def write_inventory(path: Path, inventory: dict) -> None:
    gate = MutationGate(path.parent, source="collector")
    decision = gate.decide(OperationClass.RECONCILIATION)
    gate.require(decision, OperationClass.RECONCILIATION)
    write_record(path, inventory, "axis.external-development-supervisor.inventory")


def main() -> int:
    started = time.time()
    control = load_control()
    trusted_user_ids: set[int | str] = {
        int(value)
        for value in control.get("trusted_gitlab_user_ids") or []
        if isinstance(value, int)
    }
    owned_branch_prefixes = tuple(control.get("owned_branch_prefixes") or ["hermes/"])
    owned_worktree_root = Path(
        str(
            control.get("owned_worktree_root")
            or "~/.hermes/supervisor/axis-development-supervisor/worktrees"
        )
    ).expanduser().resolve()
    repository_allowlist = set(
        control.get("repository_allowlist")
        or ["ghostspace/axis", "ghostspace/axis-governance", "ghostspace/axis-lab"]
    )
    mission_issue_refs = active_mission_issue_refs()

    def supervisor_owned_branch(branch: str) -> bool:
        return bool(branch and branch.startswith(owned_branch_prefixes))

    projects = glab(
        f"groups/{quote(GROUP, safe='')}/projects"
        "?include_subgroups=true&archived=false&per_page=100",
        paginate=True,
    )
    projects = [
        project for project in projects if str(project.get("path", "")).startswith("axis")
    ]
    projects.sort(key=lambda project: project["path_with_namespace"])

    source_items = []
    open_mrs = []
    repositories = {}
    dependency_edges = []
    milestones = []
    dependency_queries = 0
    dependency_query_failures = 0

    for project in projects:
        project_id = project["id"]
        encoded = quote(str(project_id), safe="")
        issues = glab(
            f"projects/{encoded}/issues?scope=all&state=all&per_page=100"
            "&order_by=updated_at&sort=desc",
            paginate=True,
        )
        mrs = glab(
            f"projects/{encoded}/merge_requests?scope=all&state=all&per_page=100"
            "&order_by=updated_at&sort=desc",
            paginate=True,
        )
        try:
            project_milestones = glab(
                f"projects/{encoded}/milestones?state=all&per_page=100"
            )
        except Exception:
            project_milestones = []
        milestones.extend(
            {
                "project": project["path_with_namespace"],
                "iid": milestone.get("iid"),
                "title": milestone.get("title"),
                "state": milestone.get("state"),
                "web_url": milestone.get("web_url"),
            }
            for milestone in project_milestones
        )
        project_open_mrs = [mr for mr in mrs if mr.get("state") == "opened"]
        open_mrs.extend(
            {
                "project": project["path_with_namespace"],
                "iid": mr["iid"],
                "title": mr["title"],
                "state": mr["state"],
                "source_branch": mr.get("source_branch"),
                "target_branch": mr.get("target_branch"),
                "sha": mr.get("sha"),
                "web_url": mr.get("web_url"),
                "merge_status": mr.get("detailed_merge_status")
                or mr.get("merge_status"),
            }
            for mr in project_open_mrs
        )
        repositories[project["path_with_namespace"]] = {
            "project_id": project_id,
            "default_branch": project.get("default_branch"),
            "web_url": project.get("web_url"),
            "local_facts": local_repository_state(project, mrs),
        }

        for issue in issues:
            ref = issue_ref(project, issue)
            related_mrs = [mr for mr in mrs if mr_mentions_issue(mr, issue)]
            note_snapshot = {
                "state": NOTES_EMPTY,
                "notes": [],
                "fetched_at": None,
                "collector_revision": NOTE_COLLECTION_REVISION,
            }
            notes = []
            blocking_dependencies = []
            retrieval_errors = []
            if should_collect_issue_notes(project, issue, mission_issue_refs):
                note_snapshot = collect_eligible_issue_notes(
                    glab, project, encoded, issue, mission_issue_refs
                )
                if note_snapshot["state"] == NOTES_ERROR:
                    retrieval_errors.append(
                        f"notes: {note_snapshot.get('error') or NOTES_ERROR}"
                    )
                else:
                    notes = note_snapshot["notes"]
            if issue.get("state") == "opened":
                try:
                    dependency_queries += 1
                    links = glab(f"projects/{encoded}/issues/{issue['iid']}/links")
                except Exception as exc:
                    links = []
                    dependency_query_failures += 1
                    retrieval_errors.append(f"links: {type(exc).__name__}")
                for link in links if isinstance(links, list) else []:
                    target = str(
                        link.get("references", {}).get("full") or link.get("web_url")
                    )
                    relationship = str(link.get("link_type") or "relates_to")
                    dependency_edges.append(
                        {"from_ref": ref, "to_ref": target, "relationship": relationship}
                    )
                    if relationship == "is_blocked_by" and link.get("state") == "opened":
                        blocking_dependencies.append(target)

            authority_notes = [
                note for note in notes if _authority_note(note, trusted_user_ids)
            ]
            authority_bodies = [str(note["body"]) for note in authority_notes]
            approval_bodies = [
                str(note["body"])
                for note in authority_notes
                if (note.get("author") or {}).get("id") in trusted_user_ids
                and re.search(
                    r"\*\*Approve\*\*|Product Owner approval|Approved .*PlanningRecord",
                    str(note["body"]),
                    re.I,
                )
            ]
            description = str(issue.get("description") or "")
            text = f"{description}\n{'\n'.join(authority_bodies)}"
            parent_refs = sorted(
                set(
                    blocking_dependencies
                    + re.findall(
                        r"(?:parent(?:_ref| issue| work item)?|controlled by):\s*"
                        r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)",
                        description,
                        re.I,
                    )
                )
            )
            for parent_ref in parent_refs:
                if parent_ref not in blocking_dependencies:
                    dependency_edges.append(
                        {
                            "from_ref": ref,
                            "to_ref": parent_ref,
                            "relationship": "authority_parent",
                        }
                    )
            authority_facts = extract_authority_facts(
                text, authority_bodies, approval_bodies
            )
            approved_note_url = approval_note_url(
                notes,
                trusted_user_ids,
                authority_facts.get("record_digest"),
                str(issue.get("web_url") or ""),
            )
            if approved_note_url and authority_facts.get("approval_matches_record"):
                authority_facts["approval_note"] = approved_note_url
            findings = extract_findings(
                notes,
                ref,
                repositories[project["path_with_namespace"]]["local_facts"].get(
                    "default_remote_head"
                ),
                trusted_user_ids,
            )
            source_items.append(
                {
                    "ref": ref,
                    "source_kind": "gitlab-issue",
                    "kind": issue.get("issue_type") or "issue",
                    "project": project["path_with_namespace"],
                    "iid": issue["iid"],
                    "title": issue["title"],
                    "source_state": issue["state"],
                    "created_at": issue.get("created_at"),
                    "closed_at": issue.get("closed_at"),
                    "assignees": [
                        str(value.get("username") or value.get("name") or "")
                        for value in issue.get("assignees") or []
                        if value.get("username") or value.get("name")
                    ],
                    "author": str(
                        (issue.get("author") or {}).get("username") or ""
                    )
                    or None,
                    "task_completion_status": issue.get("task_completion_status")
                    or {},
                    "labels": issue.get("labels") or [],
                    "milestone": (issue.get("milestone") or {}).get("title"),
                    "priority": issue.get("severity") or None,
                    "authority_facts": authority_facts,
                    "findings": findings,
                    "blocking_dependency_refs": sorted(set(blocking_dependencies)),
                    "merge_request_facts": [
                        {
                            "iid": mr["iid"],
                            "state": mr["state"],
                            "sha": mr.get("sha"),
                            "web_url": mr.get("web_url"),
                        }
                        for mr in related_mrs
                    ],
                    "acceptance_criteria_present": "acceptance" in text.lower()
                    or "AC-" in text,
                    "acceptance_facts": extract_acceptance_facts(text),
                    "updated_at": issue.get("updated_at"),
                    "web_url": issue.get("web_url"),
                    "source_evidence": {
                        "description": description[:12000],
                        "notes": _stored_notes(notes),
                        "authority_notes": [
                            {
                                "id": note["id"],
                                "author_identity": note["author_identity"],
                                "updated_at": note["updated_at"],
                                "body_digest": note["body_digest"],
                            }
                            for note in authority_notes[:NOTE_STORAGE_LIMIT]
                        ],
                        "notes_state": note_snapshot["state"],
                        "notes_fetched_at": note_snapshot["fetched_at"],
                        "notes_collector_revision": note_snapshot[
                            "collector_revision"
                        ],
                        "canonical_finding_state": (
                            "unknown"
                            if note_snapshot["state"] == NOTES_ERROR
                            else "present"
                            if any(finding.get("state") == "confirmed" for finding in findings)
                            else "absent"
                        ),
                        "parent_refs": parent_refs,
                        "related_mr_urls": [mr.get("web_url") for mr in related_mrs],
                    },
                    "repository_head": (
                        repositories[project["path_with_namespace"]]["local_facts"].get(
                            "default_remote_head"
                        )
                    ),
                    "retrieval_errors": retrieval_errors,
                    "mutation_allowed": project["path_with_namespace"]
                    in repository_allowlist,
                }
            )

    for project_ref, repository in sorted(repositories.items()):
        if project_ref not in repository_allowlist:
            continue
        local = repository.get("local_facts") or {}
        if not local.get("present"):
            continue

        def append_convergence_source(
            ref: str, title: str, source_kind: str, facts: dict
        ) -> None:
            source_items.append(
                {
                    "ref": ref,
                    "source_kind": source_kind,
                    "kind": "repository-convergence",
                    "project": project_ref,
                    "title": title,
                    "source_state": "opened",
                    "labels": ["repository-convergence"],
                    "milestone": None,
                    "priority": None,
                    "authority_facts": {},
                    "blocking_dependency_refs": [],
                    "merge_request_facts": [],
                    "acceptance_criteria_present": False,
                    "acceptance_facts": {"ids": [], "open_ids": []},
                    "source_evidence": {"description": "", "notes": [], "parent_refs": []},
                    "retrieval_errors": [local["error"]] if local.get("error") else [],
                    "mutation_allowed": True,
                    "convergence_facts": facts,
                }
            )

        root_branch = str(local.get("branch") or "")
        root_is_default = root_branch in {
            "main",
            "master",
            "main@personal",
            "master@personal",
        }
        if local.get("dirty") or not root_is_default:
            related_open_mr = any(
                mr["project"] == project_ref
                and mr.get("source_branch") in root_branch
                for mr in open_mrs
            )
            append_convergence_source(
                f"local-convergence:{project_ref}:root",
                f"Converge root worktree for {project_ref}",
                "repository-root",
                {
                    "scope": "root",
                    "path": local.get("path"),
                    "branch": root_branch,
                    "dirty": local.get("dirty"),
                    "related_open_merge_request": related_open_mr,
                    "integrated_into_default": False,
                    "supervisor_owned": supervisor_owned_branch(root_branch),
                    "under_owned_worktree_root": False,
                    "remote_fresh": bool(local.get("remote_fresh")),
                },
            )

        worktrees = local.get("worktrees") or []
        attached_branches = {
            entry.get("branch") for entry in worktrees if entry.get("branch")
        }
        for entry in sorted(worktrees, key=lambda value: str(value.get("path") or "")):
            if entry.get("is_root"):
                continue
            branch = str(entry.get("branch") or "detached")
            worktree_path = str(entry.get("path") or "")
            related_open_mr = any(
                mr["project"] == project_ref and mr.get("source_branch") == branch
                for mr in open_mrs
            )
            try:
                under_owned_root = Path(worktree_path).resolve().is_relative_to(
                    owned_worktree_root
                )
            except (OSError, ValueError):
                under_owned_root = False
            append_convergence_source(
                f"local-convergence:{project_ref}:worktree:{worktree_path}",
                f"Converge worktree {worktree_path}",
                "repository-worktree",
                {
                    "scope": "worktree",
                    "path": worktree_path,
                    "branch": branch,
                    "head": entry.get("head"),
                    "dirty": entry.get("dirty"),
                    "related_open_merge_request": related_open_mr,
                    "integrated_into_default": bool(
                        entry.get("integrated_into_default")
                    ),
                    "supervisor_owned": supervisor_owned_branch(branch),
                    "under_owned_worktree_root": under_owned_root,
                    "remote_fresh": bool(local.get("remote_fresh")),
                },
            )

        default_branches = {"main", "master", "main@personal", "master@personal"}
        for branch_info in sorted(
            local.get("local_branches") or [], key=lambda value: value["name"]
        ):
            branch = branch_info["name"]
            if branch in attached_branches or branch in default_branches:
                continue
            related_open_mr = any(
                mr["project"] == project_ref and mr.get("source_branch") == branch
                for mr in open_mrs
            )
            append_convergence_source(
                f"local-convergence:{project_ref}:branch:{branch}",
                f"Converge local branch {branch}",
                "repository-branch",
                {
                    "scope": "branch",
                    "branch": branch,
                    "head": branch_info.get("head"),
                    "dirty": False,
                    "related_open_merge_request": related_open_mr,
                    "integrated_into_default": bool(
                        branch_info.get("integrated_into_default")
                    ),
                    "supervisor_owned": supervisor_owned_branch(branch),
                    "under_owned_worktree_root": False,
                    "remote_fresh": bool(local.get("remote_fresh")),
                },
            )

    assignment_records = []
    state_record_errors = []
    assignment_dir = ROOT / "assignments"
    for assignment_path in (
        sorted(assignment_dir.glob("*.json")) if assignment_dir.exists() else []
    ):
        try:
            raw_assignment = load(assignment_path)
            if retire_unsupported_watchdog_assignment(raw_assignment):
                gate = MutationGate(ROOT, source="collector")
                decision = gate.decide(OperationClass.RECONCILIATION)
                gate.require(decision, OperationClass.RECONCILIATION)
                write_record(
                    assignment_path,
                    raw_assignment,
                    "axis.external-development-supervisor.assignment",
                )
            normalized_assignment = validate_assignment(raw_assignment, ROOT)
            if normalized_assignment != raw_assignment:
                gate = MutationGate(ROOT, source="collector")
                decision = gate.decide(OperationClass.RECONCILIATION)
                gate.require(decision, OperationClass.RECONCILIATION)
                write_record(
                    assignment_path,
                    normalized_assignment,
                    "axis.external-development-supervisor.assignment",
                )
            assignment_records.append(normalized_assignment)
        except Exception as exc:
            state_record_errors.append(
                f"assignment {assignment_path.name}: {type(exc).__name__}"
            )
    active_assignments = [
        item
        for item in assignment_records
        if not is_terminal(item)
    ]
    active_leases = []
    lease_root = ROOT / "leases"
    for lease_path in (
        sorted(lease_root.glob("*/lease.json")) if lease_root.exists() else []
    ):
        if lease_path.parent.name.startswith("stale-"):
            continue
        try:
            lease = read_record(
                lease_path, "axis.external-development-supervisor.lease"
            )
            if int(lease.get("expires_at_epoch") or 0) <= int(time.time()):
                state_record_errors.append(
                    f"expired lease requires recovery: {lease_path}"
                )
                continue
            active_leases.append(lease)
        except Exception as exc:
            state_record_errors.append(f"lease {lease_path}: {type(exc).__name__}")

    source_items.sort(key=lambda item: item["ref"])
    refs = [item["ref"] for item in source_items]
    if len(refs) != len(set(refs)):
        duplicates = sorted({ref for ref in refs if refs.count(ref) > 1})
        raise ValueError(f"duplicate normalized source refs: {duplicates}")
    dependency_edges = sorted(
        {
            (edge["from_ref"], edge["to_ref"], edge["relationship"])
            for edge in dependency_edges
        }
    )
    retrieval_error_count = sum(
        len(item.get("retrieval_errors") or []) for item in source_items
    )
    stale_repository_count = sum(
        1
        for repository in repositories.values()
        if (repository.get("local_facts") or {}).get("present")
        and not (repository.get("local_facts") or {}).get("remote_fresh")
    )
    inventory = {
        "schema": "axis.external-development-supervisor.inventory",
        "schema_version": "1.0.0",
        "generation_id": str(uuid.uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.time() - started, 3),
        "mode": control.get("mode"),
        "allow_repository_mutation": bool(control.get("allow_repository_mutation")),
        "repositories": repositories,
        "repository_allowlist": sorted(repository_allowlist),
        "repositories_inspected": len(repositories),
        "work_items_discovered": len(source_items),
        "work_items": source_items,
        "dependency_edges": [
            {"from_ref": source, "to_ref": target, "relationship": relationship}
            for source, target, relationship in dependency_edges
        ],
        "milestones": sorted(
            milestones,
            key=lambda value: (
                str(value.get("project") or ""),
                str(value.get("title") or ""),
            ),
        ),
        "open_merge_requests": sorted(
            open_mrs,
            key=lambda value: (
                str(value.get("project") or ""),
                int(value.get("iid") or 0),
            ),
        ),
        "supervisor_assignments": assignment_records,
        "active_leases": active_leases,
        "collection_status": {
            "configured_repository_count": len(repository_allowlist),
            "all_configured_repositories_inspected": repository_allowlist.issubset(
                repositories
            ),
            "dependency_queries": dependency_queries,
            "dependency_query_failures": dependency_query_failures,
            "retrieval_error_count": retrieval_error_count,
            "stale_repository_count": stale_repository_count,
            "state_record_errors": state_record_errors,
            "active_assignment_count": len(active_assignments),
            "active_lease_count": len(active_leases),
        },
    }
    write_inventory(INVENTORY, inventory)
    print(
        json.dumps(
            {
                "repositories_inspected": inventory["repositories_inspected"],
                "work_items_discovered": inventory["work_items_discovered"],
                "dependency_edges": len(inventory["dependency_edges"]),
                "collection_status": inventory["collection_status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
