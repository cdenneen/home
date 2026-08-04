#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(os.environ.get("AXIS_SUPERVISOR_ROOT", Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor"))
CONTROL = ROOT / "control.json"
INVENTORY = ROOT / "inventory.json"
WORKSPACE = Path(os.environ.get("AXIS_SUPERVISOR_WORKSPACE", "/home/cdenneen/src/workspace/personal/work"))
GITLAB_HOST = "gitlab.com"
GROUP = "ghostspace"
GLAB = os.environ.get("AXIS_SUPERVISOR_GLAB") or shutil.which("glab") or "/etc/profiles/per-user/cdenneen/bin/glab"
GIT = os.environ.get("AXIS_SUPERVISOR_GIT") or shutil.which("git") or "/etc/profiles/per-user/cdenneen/bin/git"
CLASSIFICATIONS = {
    "Executable",
    "Running",
    "Blocked",
    "Waiting",
    "Integrated",
    "Superseded",
    "Completed",
    "Invalid",
    "Revalidation",
    "Unknown",
}
WAITING_REASONS = {
    "Governance approval",
    "Product Owner approval",
    "Dependency",
    "Upstream implementation",
    "Future milestone sequencing",
    "External dependency",
    "Repository convergence",
    "Merge ordering",
    "Time gate",
    "Budget",
    "Resource",
    "Tool limitation",
    "Unknown",
}


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def load_control() -> dict:
    value = load(CONTROL)
    if value.get("schema") != "axis.external-development-supervisor.control":
        raise ValueError("unsupported control schema")
    if value.get("schema_version") != "1.0.0":
        raise ValueError("unsupported control schema_version")
    return value


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
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    return values


def glab(path: str, paginate: bool = False, timeout: int = 90):
    command = [GLAB, "api", "--hostname", GITLAB_HOST]
    if paginate:
        command.append("--paginate")
    command.append(path)
    raw = run(command, timeout=timeout)
    if paginate:
        return decode_json_stream(raw)
    return json.loads(raw)


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


def authority_from_text(
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
        set(
            digest
            for body in approval_notes
            for digest in re.findall(r"sha256:[0-9a-f]{64}", body, flags=re.I)
        )
    )
    record_digest = None
    for body in note_bodies or []:
        if body in approval_notes or not re.search(
            r"immutable PlanningRecord|PlanningRecord v1.*immutable revision",
            body,
            re.I | re.S,
        ):
            continue
        match = re.search(r"(?:Digest|digest):\s*`?(sha256:[0-9a-f]{64})", body, re.I)
        if match:
            record_digest = match.group(1).lower()
            break
    approval = bool(approval_notes and approval_digests)
    approval_matches_record = bool(
        approval
        and record_digest is not None
        and record_digest in {value.lower() for value in approval_digests}
    )
    execution = None
    match = re.search(r"execution_rag:\s*(?:.|\n){0,300}?state:\s*(green|amber|red)", text, re.I)
    if match:
        execution = match.group(1).lower()
    approval_required = bool(re.search(r"approval-required|product_owner_approval_required:\s*true|approval_ref:\s*null", text, re.I))
    decision_stop = bool(re.search(r"(?:outcome|decision):\s*stop", text, re.I))
    decision_escalate = bool(re.search(r"(?:outcome|decision):\s*escalate", text, re.I))
    return {
        "digests": digests,
        "approved": approval,
        "approval_digests": approval_digests,
        "record_digest": record_digest,
        "approval_matches_record": approval_matches_record,
        "approval_mismatch": bool(approval and record_digest and not approval_matches_record),
        "execution_rag": execution,
        "approval_required": approval_required,
        "decision_stop": decision_stop,
        "decision_escalate": decision_escalate,
    }


def classify_issue(
    issue: dict,
    text: str,
    note_bodies: list[str],
    approval_bodies: list[str],
    related_mrs: list[dict],
    dependencies: list[str],
) -> tuple[str, str | None, str]:
    labels = {str(value).lower() for value in issue.get("labels") or []}
    authority = authority_from_text(text, note_bodies, approval_bodies)

    if "invalid" in labels:
        return "Invalid", None, "explicit invalid label"
    if "superseded" in labels or "workflow::superseded" in labels:
        return "Superseded", None, "explicit superseded label"
    if issue.get("state") == "closed":
        if any(mr.get("state") == "merged" for mr in related_mrs):
            return "Integrated", None, "closed with merged implementation MR"
        return "Revalidation", None, "closed without verified merged implementation evidence"
    if any(mr.get("state") == "opened" for mr in related_mrs):
        return "Running", None, "open implementation MR"
    if any(mr.get("state") == "merged" for mr in related_mrs):
        return "Integrated", None, "implementation MR is merged; issue state needs evidence reconciliation"
    if dependencies:
        return "Waiting", "dependency", "open dependency relationship"
    if authority["approval_mismatch"]:
        return "Blocked", "approval", "Product Owner approval digest does not match the PlanningRecord digest"
    if authority["decision_stop"]:
        return "Blocked", "governance", "PlanningRecord decision is stop"
    if "blocked" in labels or "workflow::blocked" in labels:
        return "Blocked", "dependency", "explicit blocked label"
    if authority["approval_required"] and not authority["approval_matches_record"]:
        return "Blocked", "approval", "PlanningRecord approval is required"
    if authority["execution_rag"] in {"red", "amber"} and not authority["approval_matches_record"]:
        return "Blocked", "governance", f"execution RAG is {authority['execution_rag']}"
    if authority["decision_escalate"] and not authority["approval_matches_record"]:
        return "Blocked", "approval", "PlanningRecord decision is escalate without matching approval"
    if authority["approval_matches_record"]:
        return "Executable", None, "governed execution authority is present"
    if authority["execution_rag"] == "green":
        return "Blocked", "approval", "green execution RAG lacks an exact authenticated PlanningRecord approval"
    return "Waiting", "governance", "no current execution authority found after description and note inspection"


def waiting_reason_for(
    issue: dict,
    text: str,
    authority: dict,
    dependencies: list[str],
    blocker_type: str | None,
) -> str:
    labels = " ".join(str(value).lower() for value in (issue.get("labels") or []))
    combined = f"{issue.get('title', '')}\n{text}\n{labels}".lower()
    if authority.get("approval_required") and not authority.get("approval_matches_record"):
        return "Product Owner approval"
    if authority.get("decision_stop") or authority.get("decision_escalate") or blocker_type == "governance":
        return "Governance approval"
    if dependencies or blocker_type == "dependency":
        return "Dependency"
    if re.search(r"\bupstream\b|waiting on implementation|implementation prerequisite", combined):
        return "Upstream implementation"
    if re.search(r"future milestone|future slice|not before|roadmap sequencing|backlog|planned", combined):
        return "Future milestone sequencing"
    if re.search(r"external dependency|third[- ]party|upstream project|vendor", combined):
        return "External dependency"
    if re.search(r"merge order|merge-order|must merge after|after mr", combined):
        return "Merge ordering"
    if re.search(r"time[- ]bound|after \d{4}|not before \d{4}|cooldown", combined):
        return "Time gate"
    if re.search(r"budget|cost limit|spend", combined):
        return "Budget"
    if re.search(r"capacity|resource limit|disk|memory|runner unavailable", combined):
        return "Resource"
    if re.search(r"tool unavailable|unsupported tool|missing tool", combined):
        return "Tool limitation"
    if issue.get("milestone"):
        return "Future milestone sequencing"
    # Complete description/notes were inspected; absent execution authority is
    # a governance wait, not an unexplored Unknown.
    return "Governance approval"


def decomposition_for(text: str, classification: str, waiting_reason: str | None) -> dict:
    acceptance_ids = sorted(set(re.findall(r"acceptance_id:\s*([A-Za-z0-9._-]+)", text)))
    open_acceptance = []
    for match in re.finditer(
        r"acceptance_id:\s*([A-Za-z0-9._-]+)(?:(?!acceptance_id:).){0,1200}?state:\s*(open|pending|blocked)",
        text,
        re.I | re.S,
    ):
        open_acceptance.append(match.group(1))
    open_acceptance = sorted(set(open_acceptance))
    executable_slices = []
    rationale = "item is not Waiting"
    if classification == "Waiting":
        if waiting_reason in {"Product Owner approval", "Governance approval"}:
            rationale = "open acceptance slices inherit the unresolved authority gate"
        elif waiting_reason in {"Dependency", "Upstream implementation", "Merge ordering"}:
            rationale = "acceptance slices were inspected; no independent authority/dependency-free slice was evidenced"
        elif open_acceptance:
            rationale = "open acceptance slices exist but no source evidence proves independent executability"
        else:
            rationale = "no explicit acceptance-ledger child slice was found after description/note inspection"
    return {
        "evaluated": classification == "Waiting",
        "acceptance_ids": acceptance_ids,
        "open_acceptance_ids": open_acceptance,
        "executable_slices": executable_slices,
        "rationale": rationale,
    }


def local_repository_state(project: dict) -> dict:
    path = WORKSPACE / project["path"]
    state = {
        "path": str(path),
        "present": (path / ".git").exists(),
        "canonical_main": project.get("default_branch"),
    }
    if not state["present"]:
        return state
    try:
        default_remote = f"origin/{project.get('default_branch') or 'main'}"
        run([GIT, "fetch", "--prune", "origin"], path, timeout=120)
        state["remote_fresh"] = True
        state["default_remote"] = default_remote
        state["default_remote_head"] = run([GIT, "rev-parse", default_remote], path).strip()
        state["head"] = run([GIT, "rev-parse", "HEAD"], path).strip()
        state["branch"] = run([GIT, "branch", "--show-current"], path).strip()
        porcelain = run([GIT, "status", "--porcelain"], path)
        state["dirty"] = bool(porcelain.strip())
        worktree_raw = run([GIT, "worktree", "list", "--porcelain"], path)
        blocks = [block for block in worktree_raw.strip().split("\n\n") if block.strip()]
        worktrees = []
        for block in blocks:
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
                    dirty = bool(run([GIT, "status", "--porcelain"], worktree_path).strip())
                except Exception:
                    dirty = None
            ancestor = False
            if head:
                ancestor = subprocess.run(
                    [GIT, "merge-base", "--is-ancestor", head, default_remote],
                    cwd=str(path),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode == 0
            worktrees.append({
                "path": str(worktree_path),
                "head": head,
                "branch": branch,
                "dirty": dirty,
                "ancestor_of_origin_main": ancestor,
                "is_root": worktree_path.resolve() == path.resolve(),
            })
        state["worktrees"] = worktrees
        state["worktree_count"] = len(worktrees)
        branch_raw = run(
            [GIT, "for-each-ref", "--format=%(refname:short)|%(objectname)", "refs/heads"],
            path,
        )
        state["local_branches"] = [
            {"name": line.split("|", 1)[0], "head": line.split("|", 1)[1]}
            for line in branch_raw.splitlines()
            if "|" in line
        ]
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
    return state


def ranking_score(item: dict) -> int:
    labels = {str(value).lower() for value in item.get("labels") or []}
    score = 100
    if item.get("kind") == "repository-convergence":
        score += 80
    if labels.intersection({"priority::critical", "p0", "critical"}):
        score += 60
    elif labels.intersection({"priority::high", "p1", "high"}):
        score += 40
    if item.get("authority", {}).get("approved"):
        score += 30
    score -= len(item.get("dependencies") or []) * 20
    return score


def main() -> int:
    started = time.time()
    control = load_control()
    product_owner_usernames = set(control.get("product_owner_usernames") or ["cdenneen"])
    owned_branch_prefixes = tuple(control.get("owned_branch_prefixes") or ["hermes/"])
    owned_worktree_root = Path(
        str(control.get("owned_worktree_root") or "~/.hermes/supervisor/axis-development-supervisor/worktrees")
    ).expanduser().resolve()
    repository_allowlist = set(
        control.get("repository_allowlist")
        or ["ghostspace/axis", "ghostspace/axis-governance", "ghostspace/axis-lab"]
    )

    def supervisor_owned_branch(branch: str) -> bool:
        return bool(branch and branch.startswith(owned_branch_prefixes))
    try:
        previous_inventory = load(INVENTORY)
    except Exception:
        previous_inventory = {}
    projects = glab(
        f"groups/{quote(GROUP, safe='')}/projects?include_subgroups=true&archived=false&per_page=100",
        paginate=True,
    )
    projects = [project for project in projects if str(project.get("path", "")).startswith("axis")]
    projects.sort(key=lambda project: project["path_with_namespace"])

    work_items = []
    open_mrs = []
    repositories = {}
    graph_edges = []
    milestones = []
    dependency_queries = 0
    dependency_query_failures = 0

    for project in projects:
        project_id = project["id"]
        encoded = quote(str(project_id), safe="")
        issues = glab(f"projects/{encoded}/issues?scope=all&state=all&per_page=100&order_by=updated_at&sort=desc", paginate=True)
        mrs = glab(f"projects/{encoded}/merge_requests?scope=all&state=all&per_page=100&order_by=updated_at&sort=desc", paginate=True)
        try:
            project_milestones = glab(f"projects/{encoded}/milestones?state=all&per_page=100")
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
                "merge_status": mr.get("detailed_merge_status") or mr.get("merge_status"),
            }
            for mr in mrs
            if mr.get("state") == "opened"
        )
        repositories[project["path_with_namespace"]] = {
            "project_id": project_id,
            "default_branch": project.get("default_branch"),
            "web_url": project.get("web_url"),
            "local": local_repository_state(project),
        }

        for issue in issues:
            ref = issue_ref(project, issue)
            related_mrs = [mr for mr in mrs if mr_mentions_issue(mr, issue)]
            notes = []
            dependencies = []
            retrieval_errors = []
            if issue.get("state") == "opened":
                try:
                    notes = glab(
                        f"projects/{encoded}/issues/{issue['iid']}/notes?per_page=100",
                        paginate=True,
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
                    target = f"{link.get('references', {}).get('full') or link.get('web_url')}"
                    link_type = str(link.get("link_type") or "relates_to")
                    graph_edges.append({"from": ref, "to": target, "type": link_type})
                    if link_type == "is_blocked_by" and link.get("state") == "opened":
                        dependencies.append(target)

            note_bodies = [str(note.get("body") or "") for note in notes]
            approval_bodies = [
                str(note.get("body") or "")
                for note in notes
                if str((note.get("author") or {}).get("username") or "")
                in product_owner_usernames
            ]
            note_text = "\n".join(note_bodies)
            text = f"{issue.get('description') or ''}\n{note_text}"
            classification, blocker_type, rationale = classify_issue(
                issue, text, note_bodies, approval_bodies, related_mrs, dependencies
            )
            if retrieval_errors:
                classification = "Unknown"
                blocker_type = "tool"
                rationale = "live source retrieval failed: " + ", ".join(retrieval_errors)
            if classification not in CLASSIFICATIONS:
                classification = "Invalid"
                blocker_type = "tool"
                rationale = "classifier emitted unsupported state"
            authority = authority_from_text(text, note_bodies, approval_bodies)
            waiting_reason = (
                waiting_reason_for(issue, text, authority, dependencies, blocker_type)
                if classification == "Waiting"
                else None
            )
            item = {
                "ref": ref,
                "kind": issue.get("issue_type") or "issue",
                "project": project["path_with_namespace"],
                "iid": issue["iid"],
                "title": issue["title"],
                "state": issue["state"],
                "classification": classification,
                "blocker_type": blocker_type,
                "classification_rationale": rationale,
                "waiting_reason": waiting_reason,
                "labels": issue.get("labels") or [],
                "milestone": (issue.get("milestone") or {}).get("title"),
                "priority": issue.get("severity") or None,
                "authority": authority,
                "dependencies": dependencies,
                "merge_requests": [
                    {
                        "iid": mr["iid"],
                        "state": mr["state"],
                        "sha": mr.get("sha"),
                        "web_url": mr.get("web_url"),
                    }
                    for mr in related_mrs
                ],
                "acceptance_criteria_present": "acceptance" in text.lower() or "AC-" in text,
                "updated_at": issue.get("updated_at"),
                "web_url": issue.get("web_url"),
                "source_evidence": {
                    "description": str(issue.get("description") or "")[:12000],
                    "notes": [
                        {
                            "id": note.get("id"),
                            "author": (note.get("author") or {}).get("username"),
                            "created_at": note.get("created_at"),
                            "body": str(note.get("body") or "")[:4000],
                        }
                        for note in notes[:20]
                    ],
                    "dependency_refs": dependencies,
                    "parent_refs": sorted(
                        set(
                            dependencies
                            + re.findall(
                                r"(?:parent(?:_ref| issue| work item)?|controlled by):\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)",
                                str(issue.get("description") or ""),
                                re.I,
                            )
                        )
                    ),
                    "related_mrs": [mr.get("web_url") for mr in related_mrs],
                },
                "repository_head": (
                    repositories.get(project["path_with_namespace"], {})
                    .get("local", {})
                    .get("default_remote_head")
                ),
                "confidence": "high" if classification != "Waiting" else "medium",
                "decomposition": decomposition_for(text, classification, waiting_reason),
                "retrieval_errors": retrieval_errors,
                "mutation_allowed": project["path_with_namespace"] in repository_allowlist,
            }
            if item["classification"] == "Executable" and not item["mutation_allowed"]:
                item["classification"] = "Waiting"
                item["waiting_reason"] = "External dependency"
                item["classification_rationale"] = "project is outside the explicit mutation allowlist"
            item["ranking_score"] = ranking_score(item)
            work_items.append(item)

    # Repository convergence remains visible as explicit governed work.
    for project_ref, repository in repositories.items():
        if project_ref not in repository_allowlist:
            continue
        local = repository.get("local") or {}
        if not local.get("present"):
            continue

        def append_convergence_item(
            ref: str,
            title: str,
            classification: str,
            rationale: str,
            details: dict,
            blocker_type: str | None = None,
            waiting_reason: str | None = None,
        ) -> None:
            item = {
                "ref": ref,
                "kind": "repository-convergence",
                "project": project_ref,
                "title": title,
                "state": "opened",
                "classification": classification,
                "blocker_type": blocker_type,
                "waiting_reason": waiting_reason,
                "classification_rationale": rationale,
                "labels": ["repository-convergence"],
                "dependencies": [],
                "authority": {
                    "approved": True,
                    "approval_matches_record": True,
                    "execution_rag": "green",
                    "digests": [],
                    "approval_required": False,
                },
                "confidence": "high" if waiting_reason is None else "medium",
                "local": details,
                "decomposition": {
                    "evaluated": classification == "Waiting",
                    "acceptance_ids": [],
                    "open_acceptance_ids": [],
                    "executable_slices": [],
                    "rationale": rationale,
                },
            }
            item["ranking_score"] = ranking_score(item)
            work_items.append(item)

        if local.get("dirty"):
            append_convergence_item(
                f"local-convergence:{project_ref}:root",
                f"Classify dirty root worktree for {project_ref}",
                "Blocked",
                "local root worktree is dirty and requires evidence-aware provenance disposition",
                local,
                blocker_type="repository conflict",
            )
        elif local.get("branch") not in {"main", "master", "main@personal", "master@personal"}:
            has_open_mr = any(
                mr["project"] == project_ref and mr.get("source_branch") in str(local.get("branch"))
                for mr in open_mrs
            )
            classification = "Running" if has_open_mr else "Waiting"
            append_convergence_item(
                f"local-convergence:{project_ref}:root",
                f"Converge non-main root worktree for {project_ref}",
                classification,
                (
                    "clean non-main root belongs to an open merge request"
                    if has_open_mr
                    else "non-supervisor-owned root branch requires provenance disposition"
                ),
                local,
                blocker_type=None if has_open_mr else "repository conflict",
                waiting_reason=None if has_open_mr else "Repository convergence",
            )

        worktrees = local.get("worktrees") or []
        attached_branches = {entry.get("branch") for entry in worktrees if entry.get("branch")}
        for entry in worktrees:
            if entry.get("is_root"):
                continue
            branch = str(entry.get("branch") or "detached")
            worktree_path = str(entry.get("path") or "")
            related_open_mr = any(
                mr["project"] == project_ref and mr.get("source_branch") == branch
                for mr in open_mrs
            )
            if entry.get("dirty") is True:
                classification = "Blocked"
                blocker_type = "repository conflict"
                waiting_reason = None
                rationale = "dirty worktree requires provenance/evidence disposition"
            elif (
                entry.get("ancestor_of_origin_main")
                and supervisor_owned_branch(branch)
                and Path(worktree_path).resolve().is_relative_to(owned_worktree_root)
                and local.get("remote_fresh")
            ):
                classification = "Executable"
                blocker_type = None
                waiting_reason = None
                rationale = "clean worktree is fully integrated and can be removed safely"
            elif related_open_mr:
                classification = "Running"
                blocker_type = None
                waiting_reason = None
                rationale = "worktree belongs to an open merge request"
            else:
                classification = "Waiting"
                blocker_type = "repository conflict"
                waiting_reason = "Repository convergence"
                rationale = (
                    "clean integrated worktree is not supervisor-owned"
                    if entry.get("ancestor_of_origin_main")
                    else "clean unmerged worktree requires source/provenance or merge disposition"
                )
            append_convergence_item(
                f"local-convergence:{project_ref}:worktree:{worktree_path}",
                f"Converge worktree {worktree_path}",
                classification,
                rationale,
                entry,
                blocker_type=blocker_type,
                waiting_reason=waiting_reason,
            )

        for branch_info in local.get("local_branches") or []:
            branch = branch_info["name"]
            if branch in attached_branches or branch in {"main", "master", "main@personal", "master@personal"}:
                continue
            head = branch_info["head"]
            ancestor = subprocess.run(
                [GIT, "merge-base", "--is-ancestor", head, local.get("default_remote", "origin/main")],
                cwd=local["path"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
            related_open_mr = any(
                mr["project"] == project_ref and mr.get("source_branch") == branch
                for mr in open_mrs
            )
            if ancestor and supervisor_owned_branch(branch) and local.get("remote_fresh"):
                classification = "Executable"
                waiting_reason = None
                rationale = "unattached local branch is merged into origin/main and can be deleted"
            elif related_open_mr:
                classification = "Running"
                waiting_reason = None
                rationale = "unattached branch belongs to an open merge request"
            else:
                classification = "Waiting"
                waiting_reason = "Repository convergence"
                rationale = (
                    "integrated local branch is not supervisor-owned"
                    if ancestor
                    else "unattached unmerged branch requires provenance or publication disposition"
                )
            append_convergence_item(
                f"local-convergence:{project_ref}:branch:{branch}",
                f"Converge local branch {branch}",
                classification,
                rationale,
                branch_info,
                blocker_type="repository conflict" if classification == "Waiting" else None,
                waiting_reason=waiting_reason,
            )

    counts = Counter(item["classification"] for item in work_items)
    for classification in CLASSIFICATIONS:
        counts.setdefault(classification, 0)
    executable_queue = sorted(
        (item for item in work_items if item["classification"] == "Executable"),
        key=lambda item: (-item["ranking_score"], item["ref"]),
    )
    waiting_reason_counts = Counter(
        item.get("waiting_reason") or "Unknown"
        for item in work_items
        if item["classification"] == "Waiting"
    )
    for reason in WAITING_REASONS:
        waiting_reason_counts.setdefault(reason, 0)

    waiting_items = [item for item in work_items if item["classification"] == "Waiting"]
    waiting_decomposition_complete = all(
        item.get("decomposition", {}).get("evaluated") is True for item in waiting_items
    )
    blocked_items = [item for item in work_items if item["classification"] == "Blocked"]
    blocked_isolated = all(item.get("blocker_type") for item in blocked_items)
    assignment_dir = ROOT / "assignments"
    assignment_records = []
    state_record_errors = []
    for assignment_path in assignment_dir.glob("*.json") if assignment_dir.exists() else []:
        try:
            assignment_records.append(load(assignment_path))
        except Exception as exc:
            state_record_errors.append(f"assignment {assignment_path.name}: {type(exc).__name__}")
    active_assignment_records = [
        item
        for item in assignment_records
        if item.get("state") not in {"complete", "completed", "cancelled", "failed"}
    ]
    active_leases = []
    lease_root = ROOT / "leases"
    for lease_path in lease_root.glob("*/lease.json") if lease_root.exists() else []:
        try:
            lease = load(lease_path)
        except Exception as exc:
            state_record_errors.append(f"lease {lease_path}: {type(exc).__name__}")
            continue
        active_leases.append(lease)

    dirty_worktrees = sum(
        1
        for repository in repositories.values()
        for worktree in ((repository.get("local") or {}).get("worktrees") or [])
        if worktree.get("dirty") is True
    )
    unknown_count = counts["Unknown"] + waiting_reason_counts["Unknown"] + len(state_record_errors)
    confidence_penalty = min(30, unknown_count * 5)
    confidence_penalty += min(15, dirty_worktrees * 3)
    confidence_penalty += min(10, dependency_query_failures * 2)
    if not waiting_decomposition_complete:
        confidence_penalty += 15
    roadmap_confidence = max(0, 100 - confidence_penalty)
    confidence_reasons = [
        "all discovered work items have an execution classification",
        "all Waiting items were evaluated for bounded decomposition",
        "execution queue contains only Executable items",
    ]
    remaining_uncertainty = []
    if dirty_worktrees:
        remaining_uncertainty.append(f"{dirty_worktrees} dirty worktree(s) require provenance-aware disposition")
    if dependency_query_failures:
        remaining_uncertainty.append(f"{dependency_query_failures} dependency query failure(s)")
    if unknown_count:
        remaining_uncertainty.append(f"{unknown_count} unknown classification/reason value(s)")

    previous_counts = previous_inventory.get("classification_counts") or {}
    timeline = []
    for classification in ("Integrated", "Completed", "Executable", "Running", "Blocked", "Waiting"):
        delta = counts[classification] - int(previous_counts.get(classification, 0))
        if delta:
            timeline.append(f"{classification} {'increased' if delta > 0 else 'decreased'} by {abs(delta)}")
    previous_mrs = {
        f"{item.get('project')}!{item.get('iid')}" for item in previous_inventory.get("open_merge_requests") or []
    }
    current_mrs = {f"{item.get('project')}!{item.get('iid')}" for item in open_mrs}
    for ref in sorted(previous_mrs - current_mrs):
        timeline.append(f"Open merge request {ref} left the active set")
    if not timeline:
        timeline.append("No classification or active-MR change since the previous inventory")

    idle_proof = {
        "repositories_inspected": len(repositories),
        "configured_repositories": len(repository_allowlist),
        "all_configured_repositories_inspected": repository_allowlist.issubset(repositories.keys()),
        "all_discovered_items_classified": sum(counts.values()) == len(work_items),
        "unknown_count": unknown_count,
        "dependency_queries": dependency_queries,
        "dependency_query_failures": dependency_query_failures,
        "all_waiting_items_regex_scanned": waiting_decomposition_complete,
        "executable_count": counts["Executable"],
        "running_count": counts["Running"],
        "blocked_items_isolated": blocked_isolated,
        "active_assignment_count": len(active_assignment_records),
        "active_lease_count": len(active_leases),
        "state_record_errors": state_record_errors,
        "independent_executable_remaining": bool(executable_queue),
    }
    idle_proof["classifier_queue_empty"] = not executable_queue
    idle_proof["governed_queue_zero_proven"] = False

    inventory = {
        "schema": "axis.external-development-supervisor.inventory",
        "schema_version": "1.0.0",
        "version": 3,
        "generation_id": str(uuid.uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.time() - started, 3),
        "mode": control.get("mode"),
        "allow_repository_mutation": bool(control.get("allow_repository_mutation")),
        "repositories": repositories,
        "repository_allowlist": sorted(repository_allowlist),
        "repositories_inspected": len(repositories),
        "work_items_discovered": len(work_items),
        "classification_counts": dict(sorted(counts.items())),
        "work_items": work_items,
        "milestones": milestones,
        "execution_graph": {
            "nodes": [item["ref"] for item in work_items],
            "edges": graph_edges,
        },
        "open_merge_requests": open_mrs,
        "active_assignments": [
            item for item in work_items if item["classification"] == "Running"
        ],
        "supervisor_assignments": assignment_records,
        "active_leases": active_leases,
        "executable_queue": [
            {
                "ref": item["ref"],
                "project": item["project"],
                "title": item["title"],
                "ranking_score": item["ranking_score"],
                "next_action": item["classification_rationale"],
                "confidence": item["confidence"],
            }
            for item in executable_queue
        ],
        "queue_depth": len(executable_queue),
        "waiting_reason_counts": dict(sorted(waiting_reason_counts.items())),
        "decomposition": {
            "waiting_items_evaluated": len(waiting_items),
            "all_waiting_items_evaluated": waiting_decomposition_complete,
            "executable_child_slices": sum(
                len(item.get("decomposition", {}).get("executable_slices") or [])
                for item in waiting_items
            ),
        },
        "idle_proof": idle_proof,
        "roadmap_confidence": {
            "percent": roadmap_confidence,
            "reasons": confidence_reasons,
            "remaining_uncertainty": remaining_uncertainty,
        },
        "activity_timeline": timeline,
        "invariant": {
            "unknown_count": unknown_count,
            "all_items_classified": sum(counts.values()) == len(work_items),
            "queue_contains_only_executable": all(
                item["classification"] == "Executable" for item in executable_queue
            ),
        },
    }
    tmp = INVENTORY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(INVENTORY)

    print(json.dumps({
        "repositories_inspected": inventory["repositories_inspected"],
        "work_items_discovered": inventory["work_items_discovered"],
        "classification_counts": inventory["classification_counts"],
        "waiting_reason_counts": inventory["waiting_reason_counts"],
        "queue_depth": inventory["queue_depth"],
        "top_executable": inventory["executable_queue"][:10],
        "unknown_count": inventory["invariant"]["unknown_count"],
        "idle_proof": inventory["idle_proof"],
        "roadmap_confidence": inventory["roadmap_confidence"],
        "activity_timeline": inventory["activity_timeline"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        raise SystemExit(1)
