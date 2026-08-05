#!/usr/bin/env python3
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
    from .lifecycle import is_terminal
    from .models import validate_assignment
    from .mutation import MutationGate, OperationClass
    from .schema_registry import read_record, write_record
except ImportError:
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


def issue_ref(project: dict, issue: dict) -> str:
    return f"{project['path_with_namespace']}#{issue['iid']}"


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


def approval_note_url(
    notes: list[dict], product_owner_usernames: set[str], digest: str | None, issue_url: str
) -> str | None:
    if not digest:
        return None
    for note in notes:
        body = str(note.get("body") or "")
        if (
            str((note.get("author") or {}).get("username") or "")
            in product_owner_usernames
            and re.search(
                r"\*\*Approve\*\*|Product Owner approval (?:with conditions|—)|Approved .*PlanningRecord",
                body,
                re.I,
            )
            and digest.lower() in body.lower()
        ):
            return f"{issue_url}#note_{note.get('id')}"
    return None


def local_repository_state(project: dict) -> dict:
    path = WORKSPACE / project["path"]
    state = {
        "path": str(path),
        "present": (path / ".git").exists(),
        "canonical_default_branch": project.get("default_branch"),
    }
    if not state["present"]:
        return state
    try:
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
    product_owner_usernames = set(
        control.get("product_owner_usernames") or ["cdenneen"]
    )
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
            "local_facts": local_repository_state(project),
        }

        for issue in issues:
            ref = issue_ref(project, issue)
            related_mrs = [mr for mr in mrs if mr_mentions_issue(mr, issue)]
            notes = []
            blocking_dependencies = []
            retrieval_errors = []
            if issue.get("state") == "opened":
                try:
                    notes = glab(
                        f"projects/{encoded}/issues/{issue['iid']}/notes?per_page=100",
                        paginate=True,
                    )
                    notes.sort(
                        key=lambda note: (
                            str(note.get("created_at") or ""),
                            int(note.get("id") or 0),
                        ),
                        reverse=True,
                    )
                except Exception as exc:
                    retrieval_errors.append(f"notes: {type(exc).__name__}")
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

            note_bodies = [str(note.get("body") or "") for note in notes]
            approval_bodies = [
                str(note.get("body") or "")
                for note in notes
                if str((note.get("author") or {}).get("username") or "")
                in product_owner_usernames
            ]
            description = str(issue.get("description") or "")
            text = f"{description}\n{'\n'.join(note_bodies)}"
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
                text, note_bodies, approval_bodies
            )
            approved_note_url = approval_note_url(
                notes,
                product_owner_usernames,
                authority_facts.get("record_digest"),
                str(issue.get("web_url") or ""),
            )
            if approved_note_url and authority_facts.get("approval_matches_record"):
                authority_facts["approval_note"] = approved_note_url
            source_items.append(
                {
                    "ref": ref,
                    "source_kind": "gitlab-issue",
                    "kind": issue.get("issue_type") or "issue",
                    "project": project["path_with_namespace"],
                    "iid": issue["iid"],
                    "title": issue["title"],
                    "source_state": issue["state"],
                    "labels": issue.get("labels") or [],
                    "milestone": (issue.get("milestone") or {}).get("title"),
                    "priority": issue.get("severity") or None,
                    "authority_facts": authority_facts,
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
                        "notes": [
                            {
                                "id": note.get("id"),
                                "author": (note.get("author") or {}).get("username"),
                                "created_at": note.get("created_at"),
                                "body": str(note.get("body") or "")[:4000],
                            }
                            for note in notes[:20]
                        ],
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
        try:
            active_leases.append(
                read_record(lease_path, "axis.external-development-supervisor.lease")
            )
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
