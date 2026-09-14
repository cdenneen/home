"""Reconcile explicitly configured GitLab backlogs into Hermes Kanban boards."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PLANNING_PREFIXES = (
    "epic::",
    "gate::",
    "milestone::",
    "prio::",
    "priority::",
    "roadmap::",
    "stage::",
    "state::",
)
PERSONAL_STAGE_LABELS = {
    "blocked": "stage::blocked",
    "done": "stage::done",
    "ready": "stage::ready-implementation",
    "review": "stage::ready-verification",
    "running": "stage::implementation-in-progress",
}
WORK_STATE_LABELS = {
    "blocked": "state::blocked",
    "done": "state::done",
    "ready": "state::ready",
    "review": "state::in-progress",
    "running": "state::in-progress",
}


def fail(message: str) -> None:
    raise RuntimeError(message)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def glab_token(host: str, path: Path) -> str:
    """Read one host token from glab's stable YAML shape without a YAML dependency."""
    in_hosts = False
    host_indent: int | None = None
    for raw_line in path.read_text().splitlines():
        if raw_line == "hosts:":
            in_hosts = True
            continue
        if in_hosts and raw_line and not raw_line[0].isspace():
            break
        match = re.fullmatch(r"(\s+)([^#][^:]*):\s*", raw_line)
        if in_hosts and match:
            indent = len(match.group(1))
            key = match.group(2).strip().strip("'\"")
            if indent == 4:
                host_indent = indent if key == host else None
                continue
        if host_indent is not None:
            token_match = re.fullmatch(r"\s{8,}token:\s*(.+?)\s*", raw_line)
            if token_match:
                token = token_match.group(1).strip().strip("'\"")
                if token:
                    return token
    fail(f"could not find {host!r} token in {path}")


class GitLabClient:
    def __init__(self, host: str, token: str, timeout: int = 30) -> None:
        if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
            fail(f"invalid GitLab hostname: {host!r}")
        self.host = host
        self.token = token
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        graphql: bool = False,
    ) -> tuple[Any, dict[str, str]]:
        base = f"https://{self.host}/api/{'graphql' if graphql else 'v4'}"
        url = f"{base}/{path.lstrip('/')}" if path else base
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        body = None if payload is None else json.dumps(payload).encode()
        headers = {
            "PRIVATE-TOKEN": self.token,
            "User-Agent": "hermes-gitlab-sync/1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    result = json.load(response)
                    return result, {key.lower(): value for key, value in response.headers.items()}
            except urllib.error.HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    detail = error.read(1000).decode(errors="replace")
                    fail(f"GitLab {method} {url} failed ({error.code}): {detail}")
                delay = min(int(error.headers.get("Retry-After", "1") or "1"), 10)
            except urllib.error.URLError as error:
                if attempt == 2:
                    fail(f"GitLab {method} {url} failed: {error.reason}")
                delay = attempt + 1
            time.sleep(delay)
        fail(f"GitLab {method} {url} exhausted retries")

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)[0]

    def paged(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page = 1
        while True:
            query = dict(params or {})
            query.update({"page": page, "per_page": 100})
            batch, headers = self.request("GET", path, params=query)
            if not isinstance(batch, list):
                fail(f"expected a list from GitLab endpoint {path}")
            result.extend(batch)
            next_page = headers.get("x-next-page", "")
            if not next_page:
                return result
            page = int(next_page)

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        result, _ = self.request(
            "POST", "", payload={"query": query, "variables": variables}, graphql=True
        )
        if result.get("errors"):
            fail(f"GitLab GraphQL failed: {result['errors']}")
        return result["data"]

    def update_issue(self, project: str, iid: int, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = urllib.parse.quote(project, safe="")
        return self.request("PUT", f"projects/{encoded}/issues/{iid}", payload=payload)[0]


def planning_labels(labels: list[str]) -> list[str]:
    return sorted(label for label in labels if label.lower().startswith(PLANNING_PREFIXES))


def workflow_for(issue: dict[str, Any], scheme: str) -> str:
    if issue.get("state") == "closed":
        return "done"
    labels = {label.lower() for label in issue.get("labels", [])}
    if scheme == "personal-labels":
        if "stage::done" in labels:
            return "done"
        if "stage::blocked" in labels or labels.intersection(
            {"gate::closure-blocked", "gate::deployment-blocked"}
        ):
            return "blocked"
        if "stage::implementation-in-progress" in labels:
            return "active"
        return "ready"
    if labels.intersection(
        {"state::blocked", "state::upstream-waiting", "state::at-risk"}
    ):
        return "blocked"
    if labels.intersection({"state::in-progress", "state::active"}):
        return "active"
    if "state::done" in labels:
        return "done"
    return "ready"


def priority_for(labels: list[str]) -> int:
    lowered = {label.lower() for label in labels}
    for names, value in (
        ({"prio::p0", "priority::p0"}, 100),
        ({"prio::p1", "priority::p1", "prio::high"}, 80),
        ({"prio::p2", "priority::p2", "prio::medium"}, 60),
        ({"prio::p3", "priority::p3", "prio::low"}, 40),
    ):
        if lowered.intersection(names):
            return value
    return 0


def normalize_issue(
    host: str,
    project: dict[str, Any],
    issue: dict[str, Any],
    planning_mode: str,
    workflow_scheme: str,
) -> dict[str, Any]:
    project_id = int(issue.get("project_id") or project["id"])
    labels = list(issue.get("labels", []))
    native_milestone = issue.get("milestone")
    milestone = None if planning_mode == "labels" else native_milestone
    return {
        "key": f"gitlab:{host}:project:{project_id}:issue:{issue['iid']}",
        "kind": "issue",
        "host": host,
        "project_path": project["path"],
        "project_id": project_id,
        "logical_project": project["logical_project"],
        "iid": int(issue["iid"]),
        "title": issue["title"],
        "description": issue.get("description") or "",
        "web_url": issue["web_url"],
        "state": issue["state"],
        "workflow": workflow_for(issue, workflow_scheme),
        "workflow_scheme": workflow_scheme,
        "labels": sorted(labels),
        "planning_labels": planning_labels(labels),
        "priority": priority_for(labels),
        "assignees": sorted(item["username"] for item in issue.get("assignees", [])),
        "epic": issue.get("epic") if planning_mode == "native" else None,
        "milestone": milestone,
        "native_milestone_informational": native_milestone if planning_mode == "labels" else None,
        "updated_at": issue.get("updated_at"),
    }


EPICS_QUERY = """
query OpenEpics($fullPath: ID!, $after: String) {
  group(fullPath: $fullPath) {
    workItems(types: [EPIC], first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { id iid title description state webUrl createdAt updatedAt }
    }
  }
}
"""


def group_epics(client: GitLabClient, group: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cursor = None
    while True:
        data = client.graphql(EPICS_QUERY, {"fullPath": group["path"], "after": cursor})
        connection = data["group"]["workItems"]
        for epic in connection["nodes"]:
            result.append(
                {
                    "key": f"gitlab:{client.host}:group:{group['path']}:epic:{epic['iid']}",
                    "kind": "epic",
                    "host": client.host,
                    "group_path": group["path"],
                    "logical_project": group["logical_project"],
                    "iid": int(epic["iid"]),
                    "title": epic["title"],
                    "description": epic.get("description") or "",
                    "web_url": epic["webUrl"],
                    "state": epic["state"].lower(),
                    "workflow": "done" if epic["state"].lower() == "closed" else "planning",
                    "updated_at": epic.get("updatedAt"),
                    "priority": 0,
                }
            )
        page_info = connection["pageInfo"]
        if not page_info["hasNextPage"]:
            return result
        cursor = page_info["endCursor"]


def normalize_milestone(
    host: str,
    milestone: dict[str, Any],
    logical_project: str,
    owner_path: str,
    owner_kind: str,
) -> dict[str, Any]:
    return {
        "key": f"gitlab:{host}:milestone:{milestone['id']}",
        "kind": "milestone",
        "host": host,
        "owner_kind": owner_kind,
        "owner_path": owner_path,
        "logical_project": logical_project,
        "iid": int(milestone["iid"]),
        "title": milestone["title"],
        "description": milestone.get("description") or "",
        "web_url": milestone.get("web_url") or "",
        "state": milestone["state"],
        "workflow": "done" if milestone["state"] == "closed" else "planning",
        "start_date": milestone.get("start_date"),
        "due_date": milestone.get("due_date"),
        "updated_at": milestone.get("updated_at"),
        "priority": 0,
    }


def snapshot_source(source: dict[str, Any]) -> dict[str, Any]:
    host = source["host"]
    token_path = Path(source.get("token_config", "~/.config/glab-cli/config.yml")).expanduser()
    client = GitLabClient(host, glab_token(host, token_path))
    projects: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []
    seen_milestones: set[int] = set()
    for configured in source["projects"]:
        encoded = urllib.parse.quote(configured["path"], safe="")
        project_data = client.get(f"projects/{encoded}")
        project = {
            "id": int(project_data["id"]),
            "path": configured["path"],
            "logical_project": configured["logical_project"],
            "name": project_data["name_with_namespace"],
            "web_url": project_data["web_url"],
        }
        issues = client.paged(f"projects/{encoded}/issues", {"state": "opened"})
        known = {int(value) for value in configured.get("known_iids", [])}
        open_iids = {int(issue["iid"]) for issue in issues}
        for iid in sorted(known - open_iids):
            issues.append(client.get(f"projects/{encoded}/issues/{iid}"))
        normalized = [
            normalize_issue(
                host,
                project,
                issue,
                source["planning_mode"],
                source["workflow_scheme"],
            )
            for issue in issues
        ]
        all_items.extend(normalized)
        project_result = dict(project)
        project_result["issues"] = normalized
        if source["planning_mode"] == "native":
            milestones = client.paged(f"projects/{encoded}/milestones", {"include_parent_milestones": "true"})
            project_result["milestones"] = milestones
            for milestone in milestones:
                milestone_id = int(milestone["id"])
                if milestone_id not in seen_milestones:
                    all_items.append(
                        normalize_milestone(
                            host,
                            milestone,
                            configured["logical_project"],
                            configured["path"],
                            "project",
                        )
                    )
                    seen_milestones.add(milestone_id)
        projects.append(project_result)

    groups: list[dict[str, Any]] = []
    if source["planning_mode"] == "native":
        for configured in source.get("groups", []):
            encoded = urllib.parse.quote(configured["path"], safe="")
            group_data = client.get(f"groups/{encoded}")
            epics = group_epics(client, configured)
            milestones = client.paged(f"groups/{encoded}/milestones")
            boards = client.paged(f"groups/{encoded}/boards")
            all_items.extend(epics)
            for milestone in milestones:
                milestone_id = int(milestone["id"])
                if milestone_id not in seen_milestones:
                    all_items.append(
                        normalize_milestone(
                            host,
                            milestone,
                            configured["logical_project"],
                            configured["path"],
                            "group",
                        )
                    )
                    seen_milestones.add(milestone_id)
            groups.append(
                {
                    "id": int(group_data["id"]),
                    "path": configured["path"],
                    "logical_project": configured["logical_project"],
                    "epics": epics,
                    "milestones": milestones,
                    "boards": boards,
                }
            )
    return {
        "source_id": source["id"],
        "host": host,
        "planning_mode": source["planning_mode"],
        "collected_at": int(time.time()),
        "projects": projects,
        "groups": groups,
        "items": all_items,
    }


def mutate_issue(source: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    host = source["host"]
    token_path = Path(source.get("token_config", "~/.config/glab-cli/config.yml")).expanduser()
    client = GitLabClient(host, glab_token(host, token_path))
    project_path = request["project_path"]
    iid = int(request["iid"])
    encoded = urllib.parse.quote(project_path, safe="")
    issue = client.get(f"projects/{encoded}/issues/{iid}")
    labels = list(issue.get("labels", []))
    desired = request["native_status"]
    scheme = source["workflow_scheme"]
    mapping = PERSONAL_STAGE_LABELS if scheme == "personal-labels" else WORK_STATE_LABELS
    canonical = "ready" if desired in {"ready", "todo"} else desired
    target = mapping.get(canonical)
    if target is None:
        fail(f"native status {desired!r} is not synchronizable")
    prefix = "stage::" if scheme == "personal-labels" else "state::"
    labels = [label for label in labels if not label.lower().startswith(prefix)]
    labels.append(target)
    payload: dict[str, Any] = {"labels": ",".join(sorted(set(labels)))}
    if canonical == "done":
        payload["state_event"] = "close"
    elif issue["state"] == "closed":
        payload["state_event"] = "reopen"
    updated = client.update_issue(project_path, iid, payload)
    configured = next(item for item in source["projects"] if item["path"] == project_path)
    project = {
        "id": int(updated["project_id"]),
        "path": project_path,
        "logical_project": configured["logical_project"],
    }
    return normalize_issue(host, project, updated, source["planning_mode"], scheme)


def run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
    )


def remote_command(source: dict[str, Any], action: str, payload: dict[str, Any]) -> Any:
    executable = source.get(
        "remote_executable", "$HOME/.nix-profile/bin/hermes-gitlab-sync"
    )
    result = run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ClearAllForwardings=yes",
            source["ssh_host"],
            executable,
            action,
            "--json-stdin",
        ],
        input_text=json.dumps(payload),
    )
    return json.loads(result.stdout)


def collect(source: dict[str, Any]) -> dict[str, Any]:
    if source.get("transport", "local") == "ssh":
        return remote_command(source, "snapshot", source)
    return snapshot_source(source)


def mutate(source: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    if source.get("transport", "local") == "ssh":
        return remote_command(source, "mutate", {"source": source, "request": request})
    return mutate_issue(source, request)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def item_content(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "assignees",
            "description",
            "due_date",
            "epic",
            "labels",
            "milestone",
            "native_milestone_informational",
            "planning_labels",
            "priority",
            "start_date",
            "title",
        )
    }


def card_body(item: dict[str, Any]) -> str:
    lines = [
        f"Authoritative source: {item.get('web_url') or '(no URL returned)'}",
        f"GitLab host: {item['host']}",
        f"Source type: {item['kind']}",
        f"External workflow: {item['workflow']}",
    ]
    if item["kind"] == "issue":
        lines.extend(
            [
                f"Project: {item['project_path']}#{item['iid']}",
                f"Planning labels: {', '.join(item['planning_labels']) or '(none)'}",
                f"Assignees: {', '.join(item['assignees']) or '(none)'}",
            ]
        )
        if item.get("epic"):
            lines.append(f"Epic: {item['epic'].get('title')} ({item['epic'].get('web_url')})")
        milestone = item.get("milestone") or item.get("native_milestone_informational")
        if milestone:
            qualifier = "informational" if item.get("native_milestone_informational") else "native"
            lines.append(f"Milestone ({qualifier}): {milestone.get('title')}")
    elif item["kind"] == "epic":
        lines.append(f"Group epic: {item['group_path']}::&{item['iid']}")
        lines.append("Planning card only; delegate child issues, not this card.")
    else:
        lines.append(f"{item['owner_kind'].title()} milestone: {item['owner_path']} %{item['iid']}")
        lines.append("Planning card only; delegate linked issues, not this card.")
    if item.get("description"):
        lines.extend(["", "---", "", item["description"]])
    return "\n".join(lines)


def hermes(command: list[str], *, profile: str | None = None) -> str:
    executable = os.environ.get("HERMES_BIN", "hermes")
    prefix = [executable]
    if profile:
        prefix.extend(["-p", profile])
    return run(prefix + command).stdout


def ensure_control_plane(config: dict[str, Any], dry_run: bool) -> None:
    boards = {item["slug"] for item in json.loads(hermes(["kanban", "boards", "list", "--json"]))}
    for project in config["logical_projects"]:
        board = project["board"]
        if board not in boards:
            if dry_run:
                print(f"would create board {board}")
            else:
                hermes(
                    [
                        "kanban",
                        "boards",
                        "create",
                        board,
                        "--name",
                        project["name"],
                        "--description",
                        project["description"],
                    ]
                )
        shown = subprocess.run(
            [os.environ.get("HERMES_BIN", "hermes"), "-p", "chief-of-staff", "project", "show", project["slug"]],
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if shown.returncode != 0:
            if dry_run:
                print(f"would create project {project['slug']}")
            else:
                command = [
                    "project",
                    "create",
                    project["name"],
                    *project.get("folders", []),
                    "--slug",
                    project["slug"],
                    "--description",
                    project["description"],
                    "--board",
                    board,
                ]
                if project.get("primary"):
                    command.extend(["--primary", project["primary"]])
                hermes(command, profile="chief-of-staff")


def board_tasks(board: str) -> dict[str, dict[str, Any]]:
    tasks = json.loads(hermes(["kanban", "--board", board, "list", "--archived", "--json"]))
    return {task["id"]: task for task in tasks}


def parse_created_task(output: str) -> str:
    try:
        value = json.loads(output)
        if isinstance(value, str) and value.startswith("t_"):
            return value
        for key in ("id", "task_id"):
            if isinstance(value, dict) and str(value.get(key, "")).startswith("t_"):
                return str(value[key])
    except json.JSONDecodeError:
        pass
    match = re.search(r"\bt_[0-9a-f]+\b", output)
    if match:
        return match.group(0)
    fail(f"could not parse Hermes task id from: {output[:300]!r}")


def create_card(item: dict[str, Any], board: str, dry_run: bool) -> str:
    if dry_run:
        return f"dry_{digest(item['key'])[:8]}"
    title_prefix = {"epic": "[Epic] ", "milestone": "[Milestone] "}.get(item["kind"], "")
    command = [
        "kanban",
        "--board",
        board,
        "create",
        f"{title_prefix}{item['title']}",
        "--body",
        card_body(item),
        "--workspace",
        "scratch",
        "--idempotency-key",
        item["key"],
        "--created-by",
        "gitlab-sync",
        "--priority",
        str(item.get("priority", 0)),
        "--json",
    ]
    task_id = parse_created_task(hermes(command))
    if item["kind"] != "issue" or item["workflow"] in {"active", "blocked", "planning"}:
        hermes(
            [
                "kanban",
                "--board",
                board,
                "block",
                "--kind",
                "needs_input",
                task_id,
                "Externally active, blocked, or planning-only; Chief of Staff must explicitly delegate a child issue.",
            ]
        )
    return task_id


def apply_external(task: dict[str, Any], item: dict[str, Any], board: str, dry_run: bool) -> str:
    status = task["status"]
    desired = item["workflow"]
    if desired == "done":
        if status not in {"done", "archived"} and not dry_run:
            hermes(
                [
                    "kanban",
                    "--board",
                    board,
                    "complete",
                    task["id"],
                    "--result",
                    "Closed in authoritative GitLab backlog.",
                ]
            )
        return "done"
    if status in {"done", "archived"}:
        if not dry_run and status != "archived":
            hermes(["kanban", "--board", board, "archive", task["id"]])
        return "recreate"
    if desired in {"active", "blocked", "planning"} and status != "blocked":
        if not dry_run:
            hermes(
                [
                    "kanban",
                    "--board",
                    board,
                    "block",
                    "--kind",
                    "needs_input",
                    task["id"],
                    f"Authoritative GitLab workflow is {desired}; do not duplicate active work.",
                ]
            )
        return "blocked"
    if desired == "ready" and status in {"blocked", "scheduled"}:
        if not dry_run:
            hermes(
                [
                    "kanban",
                    "--board",
                    board,
                    "unblock",
                    task["id"],
                    "--reason",
                    "Authoritative GitLab backlog returned this item to ready.",
                ]
            )
        return "ready"
    return status


def add_refresh_comment(task_id: str, board: str, item: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        return
    text = "GitLab source metadata changed. Current authoritative projection:\n\n" + card_body(item)
    hermes(
        [
            "kanban",
            "--board",
            board,
            "comment",
            "--author",
            "gitlab-sync",
            "--max-len",
            "6000",
            task_id,
            text,
        ]
    )


def source_with_known(source: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(source))
    known: dict[str, set[int]] = {}
    for entry in ledger.get("items", {}).values():
        if entry.get("source_id") != source["id"] or entry.get("kind") != "issue":
            continue
        known.setdefault(entry["project_path"], set()).add(int(entry["iid"]))
    for project in copied["projects"]:
        project["known_iids"] = sorted(known.get(project["path"], set()))
    return copied


def reconcile(
    config: dict[str, Any], state_dir: Path, dry_run: bool, allow_outbound: bool
) -> dict[str, Any]:
    ledger_path = state_dir / "state.json"
    ledger = read_json(ledger_path, {"version": 1, "items": {}})
    snapshots = [collect(source_with_known(source, ledger)) for source in config["sources"]]
    snapshot_by_source = {snapshot["source_id"]: snapshot for snapshot in snapshots}
    source_by_id = {source["id"]: source for source in config["sources"]}
    if not dry_run:
        write_json_atomic(state_dir / "last-snapshot.json", snapshots)
    ensure_control_plane(config, dry_run)
    logical = {item["slug"]: item for item in config["logical_projects"]}
    tasks_by_board = {
        project["board"]: board_tasks(project["board"])
        for project in config["logical_projects"]
        if not dry_run or project["board"] in {
            item["slug"] for item in json.loads(hermes(["kanban", "boards", "list", "--json"]))
        }
    }
    counters = {
        "created": 0,
        "external_applied": 0,
        "gitlab_updated": 0,
        "pending_outbound": 0,
        "unchanged": 0,
    }
    for source_id, snapshot in snapshot_by_source.items():
        source = source_by_id[source_id]
        for item in snapshot["items"]:
            previous = ledger["items"].get(item["key"])
            if item["workflow"] == "done" and previous is None:
                continue
            project = logical[item["logical_project"]]
            board = project["board"]
            task = tasks_by_board.get(board, {}).get(previous["task_id"]) if previous else None
            recorded_local_status = task["status"] if task else ""
            if task is None:
                task_id = create_card(item, board, dry_run)
                task_status = "blocked" if item["kind"] != "issue" or item["workflow"] in {"active", "blocked", "planning"} else "ready"
                task = {"id": task_id, "status": task_status}
                recorded_local_status = task_status
                if not dry_run:
                    tasks_by_board.setdefault(board, {})[task_id] = task
                counters["created"] += 1
            else:
                external_changed = previous.get("external_workflow") != item["workflow"]
                local_changed = previous.get("local_status") != task["status"]
                if external_changed:
                    applied = apply_external(task, item, board, dry_run)
                    if applied == "recreate":
                        task_id = create_card(item, board, dry_run)
                        task = {"id": task_id, "status": "ready"}
                    else:
                        task["status"] = applied
                    recorded_local_status = task["status"]
                    counters["external_applied"] += 1
                elif local_changed and item["kind"] == "issue" and task["status"] in {
                    "blocked",
                    "done",
                    "ready",
                    "review",
                    "running",
                    "todo",
                }:
                    if not allow_outbound:
                        recorded_local_status = previous["local_status"]
                        counters["pending_outbound"] += 1
                    elif not dry_run:
                        item = mutate(
                            source,
                            {
                                "project_path": item["project_path"],
                                "iid": item["iid"],
                                "native_status": task["status"],
                            },
                        )
                    if allow_outbound:
                        counters["gitlab_updated"] += 1
                else:
                    counters["unchanged"] += 1
                content_hash = digest(item_content(item))
                if previous.get("content_hash") != content_hash:
                    add_refresh_comment(task["id"], board, item, dry_run)
            ledger["items"][item["key"]] = {
                "board": board,
                "content_hash": digest(item_content(item)),
                "external_workflow": item["workflow"],
                "iid": item.get("iid"),
                "kind": item["kind"],
                "local_status": recorded_local_status,
                "project_path": item.get("project_path"),
                "source_id": source_id,
                "task_id": task["id"],
                "updated_at": item.get("updated_at"),
            }
    ledger["last_successful_run"] = int(time.time())
    report = {
        "dry_run": dry_run,
        "outbound_enabled": allow_outbound,
        "sources": {
            snapshot["source_id"]: {
                "groups": len(snapshot["groups"]),
                "items": len(snapshot["items"]),
                "projects": len(snapshot["projects"]),
            }
            for snapshot in snapshots
        },
        **counters,
    }
    if not dry_run:
        write_json_atomic(ledger_path, ledger)
        write_json_atomic(state_dir / "last-run.json", report)
    return report


def stdin_json() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        fail("JSON input must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--json-stdin", action="store_true")
    mutate_parser = subparsers.add_parser("mutate")
    mutate_parser.add_argument("--json-stdin", action="store_true")
    reconcile_parser = subparsers.add_parser("reconcile")
    reconcile_parser.add_argument("--config", type=Path, required=True)
    reconcile_parser.add_argument("--state-dir", type=Path, required=True)
    reconcile_parser.add_argument("--dry-run", action="store_true")
    reconcile_parser.add_argument("--allow-outbound", action="store_true")
    arguments = parser.parse_args()
    if arguments.command == "snapshot":
        if not arguments.json_stdin:
            fail("snapshot requires --json-stdin")
        result = snapshot_source(stdin_json())
    elif arguments.command == "mutate":
        if not arguments.json_stdin:
            fail("mutate requires --json-stdin")
        payload = stdin_json()
        result = mutate_issue(payload["source"], payload["request"])
    else:
        config = json.loads(arguments.config.read_text())
        result = reconcile(
            config,
            arguments.state_dir.expanduser(),
            arguments.dry_run,
            arguments.allow_outbound,
        )
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr.rstrip(), file=sys.stderr)
        print(f"hermes-gitlab-sync: {error}", file=sys.stderr)
        raise SystemExit(1)
