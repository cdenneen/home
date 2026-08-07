import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .schema_registry import RecordVersionError, read_record, validate_record

SCHEMA = "axis.external-development-supervisor.review-evidence"
SCHEMA_VERSION = "3.0.0"
INDEPENDENT_SCHEMA = "axis.external-development-supervisor.independent-review-output"
RESOLVED_DISPOSITIONS = {"fixed", "false-positive", "superseded"}
SEVERITY = {"P0": "critical", "P1": "high", "P2": "medium", "P3": "low"}
CHANNELS = ("reviews", "review_comments", "issue_comments", "checks", "diff")


class HeadChanged(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    payload = (
        value.encode()
        if isinstance(value, str)
        else json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _json(command: list[str], timeout: int = 120) -> object:
    output = subprocess.check_output(command, text=True, timeout=timeout)
    return json.loads(output)


def _pages(command: list[str], timeout: int = 120) -> list:
    value = _json([*command, "--paginate", "--slurp"], timeout)
    if not isinstance(value, list):
        raise TypeError("paginated GitHub response must be an array")
    return value


def _flatten_pages(pages: list, key: str | None = None) -> list[dict]:
    values = []
    for page in pages:
        page_values = page.get(key) if key and isinstance(page, dict) else page
        if not isinstance(page_values, list):
            raise TypeError("paginated GitHub page has an unexpected shape")
        values.extend(value for value in page_values if isinstance(value, dict))
    return values


def _finding_id(
    channel: str,
    external_id: str,
    reviewer: str,
    reviewed_sha: str,
    body: str,
) -> str:
    payload = "\x00".join(
        (channel, external_id, reviewer, reviewed_sha, _digest(body))
    ).encode()
    return "finding-" + hashlib.sha256(payload).hexdigest()[:24]


def _disposition_id(disposition: dict) -> str:
    return "disposition-" + hashlib.sha256(
        json.dumps(disposition, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:24]


def _severity(body: str) -> str | None:
    match = re.search(r'alt=["\'](P[0-3])["\']|\b(P[0-3])\b', body)
    return SEVERITY.get(next(value for value in match.groups() if value)) if match else None


def _reviewed_sha(value: dict, fallback: str) -> str:
    explicit = str(value.get("commit_id") or "")
    if re.fullmatch(r"[0-9a-f]{40}", explicit):
        return explicit
    body_match = re.search(r"/(?:commit|commits)/([0-9a-f]{40})\b", str(value.get("body") or ""))
    return body_match.group(1) if body_match else fallback


def _finding(channel: str, value: dict, head_sha: str) -> dict | None:
    body = str(value.get("body") or "")
    severity = _severity(body)
    if severity is None:
        return None
    reviewed_sha = _reviewed_sha(value, head_sha)
    reviewer = str((value.get("user") or {}).get("login") or "unknown")
    external_id = str(value.get("id") or value.get("node_id") or _digest(body))
    return {
        "finding_id": _finding_id(
            channel, external_id, reviewer, reviewed_sha, body
        ),
        "channel": channel,
        "external_id": external_id,
        "reviewer": reviewer,
        "severity": severity,
        "path": value.get("path"),
        "line": value.get("line") or value.get("original_line"),
        "body": body,
        "url": value.get("html_url"),
        "reviewed_sha": reviewed_sha,
        "state": "active" if reviewed_sha == head_sha else "stale",
        "disposition": None,
    }


def _check_state(check: dict) -> str:
    conclusion = str(check.get("conclusion") or "").upper()
    status = str(check.get("status") or check.get("state") or "").upper()
    if conclusion in {"SUCCESS", "NEUTRAL"} or status == "SUCCESS":
        return "passed"
    if conclusion == "SKIPPED":
        return "skipped"
    if conclusion in {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
        return "failed"
    return "pending"


def _channel(values: object, *, complete: bool = True) -> dict:
    count = len(values) if isinstance(values, list) else 1
    return {"complete": complete, "count": count, "digest": _digest(values)}


def _account_identity(value: dict) -> dict:
    user = value.get("user") or value
    app = value.get("performed_via_github_app") or value.get("app")
    login = str(user.get("login") or "")
    user_type = str(user.get("type") or "") or None
    app_slug = str((app or {}).get("slug") or "") or None
    is_automation = bool(
        app
        or user_type == "Bot"
        or login.lower().endswith("[bot]")
    )
    return {
        "login": login,
        "user_type": user_type,
        "app_slug": app_slug,
        "is_automation": is_automation,
    }


def _check_producer(value: dict) -> dict:
    app = value.get("app")
    if isinstance(app, dict) and app.get("id") is not None:
        return {
            "kind": "github_app",
            "id": str(app["id"]),
            "login": str(app.get("slug") or ""),
        }
    creator = value.get("creator") or {}
    identity = _account_identity(creator)
    return {
        "kind": "automation" if identity["is_automation"] else "user",
        "id": str(creator.get("id") or "unknown"),
        "login": identity["login"] or "unknown",
    }


def collect_github(repo: str, pr_number: int, gh: str = "gh") -> dict:
    endpoint = f"repos/{repo}/pulls/{pr_number}"
    pr = _json([gh, "api", endpoint])
    if not isinstance(pr, dict):
        raise TypeError("GitHub pull request response must be an object")
    head_sha = str((pr.get("head") or {}).get("sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise ValueError("GitHub pull request response omitted an exact head SHA")
    reviews = _flatten_pages(
        _pages([gh, "api", f"{endpoint}/reviews?per_page=100"])
    )
    review_comments = _flatten_pages(
        _pages([gh, "api", f"{endpoint}/comments?per_page=100"])
    )
    issue_comments = _flatten_pages(
        _pages([gh, "api", f"repos/{repo}/issues/{pr_number}/comments?per_page=100"])
    )
    check_runs = _flatten_pages(
        _pages(
            [
                gh,
                "api",
                f"repos/{repo}/commits/{head_sha}/check-runs?filter=latest&per_page=100",
            ]
        ),
        "check_runs",
    )
    status_response = _json([gh, "api", f"repos/{repo}/commits/{head_sha}/status"])
    if not isinstance(status_response, dict):
        raise TypeError("GitHub combined status response must be an object")
    statuses = [
        value
        for value in status_response.get("statuses") or []
        if isinstance(value, dict)
    ]
    diff = subprocess.check_output(
        [
            gh,
            "api",
            endpoint,
            "-H",
            "Accept: application/vnd.github.v3.diff",
        ],
        text=True,
        timeout=120,
    )
    if len(diff.encode()) > 500_000:
        raise RuntimeError("PR diff exceeds independent review bound")
    refreshed = _json([gh, "api", endpoint])
    refreshed_sha = (
        str((refreshed.get("head") or {}).get("sha") or "")
        if isinstance(refreshed, dict)
        else ""
    )
    if refreshed_sha != head_sha:
        raise HeadChanged(f"pull request head changed from {head_sha} to {refreshed_sha}")

    requested_by_login = {
        str(value.get("login") or ""): value
        for value in pr.get("requested_reviewers") or []
        if value.get("login")
    }
    latest_reviews = {}
    for review in sorted(reviews, key=lambda value: str(value.get("submitted_at") or "")):
        login = str((review.get("user") or {}).get("login") or "")
        if login:
            latest_reviews[login] = review
    reviewers = []
    for login in sorted(set(requested_by_login) | set(latest_reviews)):
        review = latest_reviews.get(login) or {}
        identity = _account_identity(
            review if review else requested_by_login[login]
        )
        reviewers.append(
            {
                "reviewer": login,
                "kind": "automation" if identity["is_automation"] else "human",
                "user_type": identity["user_type"],
                "app_slug": identity["app_slug"],
                "is_automation": identity["is_automation"],
                "state": str(review.get("state") or "PENDING").lower(),
                "reviewed_sha": review.get("commit_id"),
                "reviewed_at": review.get("submitted_at"),
            }
        )
    findings = [
        finding
        for channel_name, values in (
            ("reviews", reviews),
            ("review_comments", review_comments),
            ("issue_comments", issue_comments),
        )
        for value in values
        if (finding := _finding(channel_name, value, head_sha)) is not None
    ]
    checks = [
        {
            "name": str(value.get("name") or "unknown"),
            "state": _check_state(value),
            "url": value.get("details_url"),
            "sha": head_sha,
            "producer": _check_producer(value),
        }
        for value in check_runs
    ]
    checks.extend(
        {
            "name": str(value.get("context") or "unknown"),
            "state": "passed"
            if value.get("state") == "success"
            else "pending"
            if value.get("state") in {"pending", "expected"}
            else "failed",
            "url": value.get("target_url"),
            "sha": head_sha,
            "producer": _check_producer(value),
        }
        for value in statuses
    )
    return {
        "repository": repo,
        "pr_number": pr_number,
        "pr_url": pr.get("html_url"),
        "author": str((pr.get("user") or {}).get("login") or ""),
        "current_sha": head_sha,
        "pr_state": str(pr.get("state") or "unknown").lower(),
        "is_draft": bool(pr.get("draft")),
        "requested_reviewers": sorted(requested_by_login),
        "reviewers": reviewers,
        "findings": findings,
        "checks": checks,
        "channels": {
            "reviews": _channel(reviews),
            "review_comments": _channel(review_comments),
            "issue_comments": _channel(issue_comments),
            "checks": _channel([check_runs, statuses]),
            "diff": _channel(diff),
        },
        "diff": {
            "reviewed_sha": head_sha,
            "digest": _digest(diff),
            "size_bytes": len(diff.encode()),
        },
        "_diff_text": diff,
    }


def default_model_review(
    repo: str,
    pr_number: int,
    sha: str,
    diff: str,
    diff_digest: str,
    hermes: str,
) -> dict:
    if _digest(diff) != diff_digest:
        raise ValueError("independent review diff digest mismatch")
    prompt = f"""You are the independent GPT-5.4 merge reviewer for {repo}#{pr_number} at exact SHA {sha}.
Review the exact complete diff below for correctness, durable-state migration, concurrency, security, and missing tests.
Return only this strict JSON object:
{{"schema":"{INDEPENDENT_SCHEMA}","schema_version":"1.0.0","reviewed_sha":"{sha}","diff_digest":"{diff_digest}","summary":"...","findings":[{{"severity":"critical|high|medium|low","path":"path or null","line":1,"body":"actionable finding"}}]}}
Do not approve based on another reviewer. Empty findings is valid only when no defect remains.

{diff}
"""
    launcher = Path(shutil.which(hermes) or hermes).resolve()
    match = re.search(
        r"export HERMES_PYTHON='([^']+)'", launcher.read_text(encoding="utf-8")
    )
    if not match or not Path(match.group(1)).is_file():
        raise RuntimeError("Hermes launcher does not declare a valid HERMES_PYTHON")
    completed = subprocess.run(
        [
            match.group(1),
            str(Path(__file__).with_name("oneshot_stdin.py")),
            "--provider",
            "openai-api",
            "--model",
            "gpt-5.4",
            "--reasoning",
            "high",
            "--toolsets",
            "",
        ],
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
        timeout=900,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"independent review failed ({completed.returncode}): {completed.stdout[-4000:]}"
        )
    output = completed.stdout
    decoder = json.JSONDecoder()
    result = None
    for match in re.finditer(r"\{", output):
        try:
            candidate, _ = decoder.raw_decode(output[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            result = candidate
            break
    if result is None:
        raise ValueError("independent review output contained no JSON object")
    validate_record(result, INDEPENDENT_SCHEMA)
    if result["reviewed_sha"] != sha or result["diff_digest"] != diff_digest:
        raise ValueError("independent review output is not bound to the fetched diff")
    return result


def _validate_policy(policy: dict) -> dict:
    required = {
        "required_human_approvals",
        "required_reviewers",
        "required_checks",
    }
    if set(policy) != required:
        raise ValueError("review policy must define exactly the required policy fields")
    if type(policy["required_human_approvals"]) is not int or policy[
        "required_human_approvals"
    ] < 1:
        raise ValueError("review policy requires at least one human approval")
    reviewers = policy["required_reviewers"]
    if not isinstance(reviewers, list) or not reviewers or any(
        not isinstance(value, str) or not value for value in reviewers
    ):
        raise ValueError("review policy required_reviewers must be a non-empty string array")
    if len(reviewers) != len(set(reviewers)):
        raise ValueError("review policy required_reviewers must not contain duplicates")
    checks = policy["required_checks"]
    if not isinstance(checks, list) or not checks:
        raise ValueError("review policy required_checks must be a non-empty array")
    identities = []
    for requirement in checks:
        if not isinstance(requirement, dict) or set(requirement) != {"name", "producer"}:
            raise ValueError("required check policy must bind a name and producer")
        producer = requirement["producer"]
        if (
            not isinstance(requirement["name"], str)
            or not requirement["name"]
            or not isinstance(producer, dict)
            or set(producer) != {"kind", "id", "login"}
            or producer["kind"] not in {"github_app", "automation", "user"}
            or any(not isinstance(producer[key], str) or not producer[key] for key in ("id", "login"))
        ):
            raise ValueError("required check producer identity is invalid")
        identities.append(
            (requirement["name"], producer["kind"], producer["id"], producer["login"])
        )
    if len(identities) != len(set(identities)):
        raise ValueError("review policy required_checks must not contain duplicates")
    return policy


def _normalize_disposition(finding: dict, value: dict) -> dict:
    required = {"finding_id", "reviewed_sha", "status", "rationale", "evidence"}
    if set(value) - {"disposition_id"} != required:
        raise ValueError("finding disposition has an unexpected shape")
    if (
        value["finding_id"] != finding["finding_id"]
        or value["reviewed_sha"] != finding["reviewed_sha"]
    ):
        raise ValueError("finding disposition is not bound to the finding SHA")
    body = {key: value[key] for key in sorted(required)}
    disposition_id = _disposition_id(body)
    if value.get("disposition_id") not in {None, disposition_id}:
        raise ValueError("finding disposition ID does not match its content")
    return {"disposition_id": disposition_id, **body}


def _merge_findings(
    current_sha: str,
    collected: list[dict],
    existing: dict | None,
    supplied: dict[str, dict],
    *,
    replace_active: bool = False,
) -> list[dict]:
    collected_ids = {finding["finding_id"] for finding in collected}
    values = {
        finding["finding_id"]: dict(finding)
        for finding in (existing or {}).get("findings") or []
    }
    for finding in values.values():
        finding["state"] = (
            "active"
            if finding["reviewed_sha"] == current_sha
            and (not replace_active or finding["finding_id"] in collected_ids)
            else "stale"
        )
        if finding["state"] == "stale":
            finding["disposition"] = None
    for finding in collected:
        previous = values.get(finding["finding_id"])
        current = dict(finding)
        if (
            previous
            and previous.get("reviewed_sha") == current.get("reviewed_sha")
            and current.get("disposition") is None
        ):
            current["disposition"] = previous.get("disposition")
        values[finding["finding_id"]] = current
    for finding_id, disposition in supplied.items():
        finding = values.get(finding_id)
        if finding is None:
            raise ValueError(f"disposition references unknown finding {finding_id}")
        finding["disposition"] = _normalize_disposition(finding, disposition)
    for finding in values.values():
        disposition = finding.get("disposition")
        if disposition is not None:
            finding["disposition"] = _normalize_disposition(finding, disposition)
    return sorted(
        values.values(),
        key=lambda value: (
            value["state"] != "active",
            value["severity"],
            value["finding_id"],
        ),
    )


def _status(record: dict) -> tuple[str, list[str]]:
    blockers = []
    if record["pr_state"] != "open" or record["is_draft"]:
        blockers.append("pull request is not an open non-draft change")
    incomplete = [name for name in CHANNELS if not record["channels"][name]["complete"]]
    if incomplete:
        blockers.append("incomplete review channels: " + ", ".join(incomplete))
    for requirement in record["policy"]["required_checks"]:
        name = requirement["name"]
        trusted_producer = requirement["producer"]
        matches = [
            check
            for check in record["checks"]
            if check["sha"] == record["current_sha"]
            and check["name"] == name
            and check["producer"] == trusted_producer
        ]
        if not matches:
            blockers.append(
                f"required check from trusted producer is absent: {name}"
            )
        elif any(value["state"] == "failed" for value in matches):
            blockers.append(f"required check failed: {name}")
        elif not any(value["state"] == "passed" for value in matches):
            return "settling", blockers
    if blockers:
        return "blocked", blockers

    humans = [
        value
        for value in record["reviewers"]
        if value["kind"] == "human"
        and not value["is_automation"]
        and value["user_type"] != "Bot"
        and not value["reviewer"].lower().endswith("[bot]")
        and value["app_slug"] is None
        and value["reviewer"] != record["author"]
    ]
    fresh_approvals = {
        value["reviewer"]
        for value in humans
        if value["state"] == "approved"
        and value["reviewed_sha"] == record["current_sha"]
    }
    for reviewer in record["policy"]["required_reviewers"]:
        if reviewer not in fresh_approvals:
            return "settling", [f"fresh approval is absent for required reviewer: {reviewer}"]
    if len(fresh_approvals) < record["policy"]["required_human_approvals"]:
        return "settling", ["required fresh human approval count is not satisfied"]
    if any(
        value["state"] == "changes_requested"
        and value["reviewed_sha"] == record["current_sha"]
        for value in humans
    ):
        return "blocked", ["a fresh human review requests changes"]

    independent = record.get("independent_review")
    if (
        not independent
        or independent.get("reviewed_sha") != record["current_sha"]
        or independent.get("diff_digest") != record["diff"]["digest"]
    ):
        return "review-required", ["independent GPT-5.4 review is missing or stale"]
    for finding in record["findings"]:
        if finding["state"] != "active":
            continue
        disposition = finding.get("disposition") or {}
        if (
            disposition.get("finding_id") != finding["finding_id"]
            or disposition.get("reviewed_sha") != finding["reviewed_sha"]
        ):
            blockers.append(
                f"{finding['severity']} finding {finding['finding_id']} lacks a SHA-bound disposition"
            )
            continue
        state = disposition.get("status")
        if state in RESOLVED_DISPOSITIONS:
            continue
        if state == "accepted-risk" and finding["severity"] in {"medium", "low"}:
            continue
        blockers.append(
            f"{finding['severity']} finding {finding['finding_id']} has a blocking disposition"
        )
    return ("blocked", blockers) if blockers else ("ready", [])


def _migrate_prior(value: dict, policy: dict) -> dict:
    if value.get("schema") != SCHEMA or value.get("schema_version") not in {
        "1.0.0",
        "2.0.0",
    }:
        raise RecordVersionError("unsupported review evidence migration source")
    current_sha = str(value.get("current_sha") or "")
    findings = []
    for old in value.get("findings") or []:
        reviewed_sha = str(old.get("reviewed_sha") or current_sha)
        body = str(old.get("body") or "legacy finding")
        finding = {
            "finding_id": _finding_id(
                "review_comments",
                str(old.get("finding_id") or _digest(body)),
                str(old.get("reviewer") or "unknown"),
                reviewed_sha,
                body,
            ),
            "channel": "review_comments",
            "external_id": str(old.get("finding_id") or _digest(body)),
            "reviewer": str(old.get("reviewer") or "unknown"),
            "severity": str(old.get("severity") or "high"),
            "path": old.get("path"),
            "line": old.get("line"),
            "body": body,
            "url": old.get("url"),
            "reviewed_sha": reviewed_sha,
            "state": "stale",
            "disposition": None,
        }
        findings.append(finding)
    now = utc_now()
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "repository": value["repository"],
        "pr_number": value["pr_number"],
        "pr_url": value.get("pr_url"),
        "author": "unknown",
        "current_sha": current_sha or None,
        "status": "unavailable",
        "session_id": str(uuid.uuid4()),
        "started_at": value.get("started_at") or now,
        "updated_at": now,
        "deadline_at": value.get("deadline_at") or now,
        "poll_count": int(value.get("poll_count") or 0),
        "pr_state": value.get("pr_state") or "unknown",
        "is_draft": bool(value.get("is_draft")),
        "policy": policy,
        "requested_reviewers": [],
        "reviewers": [],
        "checks": [],
        "channels": {
            name: {"complete": False, "count": 0, "digest": _digest([])}
            for name in CHANNELS
        },
        "diff": {"reviewed_sha": None, "digest": None, "size_bytes": 0},
        "findings": findings,
        "independent_review": None,
        "blockers": ["prior review evidence requires complete identity recollection"],
        "poll_history": value.get("poll_history") or [],
        "last_error": None,
    }


def _load_existing(path: Path, policy: dict) -> dict | None:
    if not path.exists():
        return None
    try:
        return read_record(path, SCHEMA)
    except RecordVersionError:
        value = json.loads(path.read_text(encoding="utf-8"))
        return _migrate_prior(value, policy)


@contextmanager
def _transaction(path: Path) -> Iterator[None]:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _write_record(path: Path, value: dict) -> None:
    validate_record(value, SCHEMA, record_path=path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _base_record(
    existing: dict | None,
    policy: dict,
    repo: str,
    pr_number: int,
    started: datetime,
    deadline: datetime,
) -> dict:
    now = utc_now()
    if existing:
        value = dict(existing)
        value["policy"] = policy
        value["updated_at"] = now
        return value
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "repository": repo,
        "pr_number": pr_number,
        "pr_url": None,
        "author": "unknown",
        "current_sha": None,
        "status": "settling",
        "session_id": str(uuid.uuid4()),
        "started_at": started.isoformat(),
        "updated_at": now,
        "deadline_at": deadline.isoformat(),
        "poll_count": 0,
        "pr_state": "unknown",
        "is_draft": False,
        "policy": policy,
        "requested_reviewers": [],
        "reviewers": [],
        "checks": [],
        "channels": {
            name: {"complete": False, "count": 0, "digest": _digest([])}
            for name in CHANNELS
        },
        "diff": {"reviewed_sha": None, "digest": None, "size_bytes": 0},
        "findings": [],
        "independent_review": None,
        "blockers": [],
        "poll_history": [],
        "last_error": None,
    }


def settle(
    state_path: Path,
    repo: str,
    pr_number: int,
    *,
    policy: dict,
    max_polls: int = 6,
    interval_seconds: int = 30,
    timeout_seconds: int = 300,
    run_independent_review: bool = False,
    dispositions: dict[str, dict] | None = None,
    collector: Callable[[str, int], dict] = collect_github,
    model_reviewer: Callable[[str, int, str, str, str], dict] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict:
    policy = _validate_policy(dict(policy))
    supplied = dispositions or {}
    if max_polls < 1 or timeout_seconds < 1:
        raise ValueError("review settling bounds must be positive")
    with _transaction(state_path):
        invocation_started = now()
        existing = _load_existing(state_path, policy)
        if existing and existing["policy"] != policy:
            raise ValueError("review policy cannot change during a settling session")
        started = (
            datetime.fromisoformat(existing["started_at"])
            if existing
            else invocation_started
        )
        deadline = (
            datetime.fromisoformat(existing["deadline_at"])
            if existing
            else started + timedelta(seconds=timeout_seconds)
        )
        record = _base_record(existing, policy, repo, pr_number, started, deadline)
        for local_poll in range(1, max_polls + 1):
            if now() >= deadline:
                record["status"] = "timeout"
                record["updated_at"] = utc_now()
                record["blockers"] = ["review settling exceeded its durable deadline"]
                _write_record(state_path, record)
                return record
            record["poll_count"] += 1
            poll_number = record["poll_count"]
            try:
                evidence = collector(repo, pr_number)
            except HeadChanged as exc:
                record["status"] = "settling"
                record["updated_at"] = utc_now()
                record["last_error"] = str(exc)
                record["poll_history"].append(
                    {
                        "poll": poll_number,
                        "observed_at": record["updated_at"],
                        "sha": record.get("current_sha"),
                        "status": "head-changed",
                    }
                )
                _write_record(state_path, record)
                if local_poll < max_polls:
                    sleeper(interval_seconds)
                    continue
                record["status"] = "timeout"
                record["blockers"] = ["head changed throughout the bounded review window"]
                _write_record(state_path, record)
                return record
            except Exception as exc:  # noqa: BLE001 - preserve evidence on outage
                record["status"] = "unavailable"
                record["updated_at"] = utc_now()
                record["last_error"] = f"{type(exc).__name__}: {exc}"
                record["blockers"] = [
                    f"GitHub review evidence unavailable: {type(exc).__name__}: {exc}"
                ]
                record["poll_history"].append(
                    {
                        "poll": poll_number,
                        "observed_at": record["updated_at"],
                        "sha": record.get("current_sha"),
                        "status": "unavailable",
                    }
                )
                _write_record(state_path, record)
                return record

            diff_text = str(evidence["_diff_text"])
            evidence = {
                key: value for key, value in evidence.items() if key != "_diff_text"
            }
            current_sha = evidence["current_sha"]
            prior_sha = record.get("current_sha")
            record.update(evidence)
            record["last_error"] = None
            if prior_sha and prior_sha != current_sha:
                record["independent_review"] = None
            record["findings"] = _merge_findings(
                current_sha,
                record["findings"],
                existing,
                {},
            )
            post_review_refreshed = False
            refresh_blockers = []
            independent = record.get("independent_review")
            if run_independent_review and (
                not independent
                or independent.get("reviewed_sha") != current_sha
                or independent.get("diff_digest") != record["diff"]["digest"]
            ):
                if model_reviewer is None:
                    raise RuntimeError("independent review requested without a model reviewer")
                try:
                    model_result = model_reviewer(
                        repo,
                        pr_number,
                        current_sha,
                        diff_text,
                        record["diff"]["digest"],
                    )
                    validate_record(model_result, INDEPENDENT_SCHEMA)
                    if (
                        model_result["reviewed_sha"] != current_sha
                        or model_result["diff_digest"] != record["diff"]["digest"]
                    ):
                        raise ValueError(
                            "independent review is not bound to the fetched diff"
                        )
                except Exception as exc:  # noqa: BLE001 - model evidence fails closed
                    record["status"] = "unavailable"
                    record["updated_at"] = utc_now()
                    record["last_error"] = f"{type(exc).__name__}: {exc}"
                    record["blockers"] = [
                        f"independent review unavailable: {type(exc).__name__}: {exc}"
                    ]
                    _write_record(state_path, record)
                    return record
                try:
                    refreshed = collector(repo, pr_number)
                except Exception as exc:  # noqa: BLE001 - preserve pre-review evidence
                    record["status"] = "unavailable"
                    record["updated_at"] = utc_now()
                    record["last_error"] = f"{type(exc).__name__}: {exc}"
                    record["blockers"] = [
                        f"post-review head verification unavailable: {type(exc).__name__}: {exc}"
                    ]
                    _write_record(state_path, record)
                    return record
                refreshed_diff_text = str(refreshed["_diff_text"])
                refreshed = {
                    key: value
                    for key, value in refreshed.items()
                    if key != "_diff_text"
                }
                if refreshed["current_sha"] != current_sha:
                    record["status"] = "settling"
                    record["updated_at"] = utc_now()
                    record["last_error"] = "head changed during independent review"
                    record["poll_history"].append(
                        {
                            "poll": poll_number,
                            "observed_at": record["updated_at"],
                            "sha": current_sha,
                            "status": "head-changed",
                        }
                    )
                    _write_record(state_path, record)
                    if local_poll < max_polls:
                        existing = dict(record)
                        sleeper(interval_seconds)
                        continue
                    record["status"] = "timeout"
                    record["blockers"] = ["head changed during independent review"]
                    _write_record(state_path, record)
                    return record
                if refreshed["diff"]["digest"] != _digest(refreshed_diff_text):
                    refresh_blockers.append(
                        "refreshed diff content does not match its digest"
                    )
                if refreshed["diff"]["digest"] != record["diff"]["digest"]:
                    refresh_blockers.append(
                        "same-SHA diff changed during independent review"
                    )
                if refreshed["requested_reviewers"] != record["requested_reviewers"]:
                    refresh_blockers.append(
                        "review requests changed during independent review"
                    )
                independent_findings = []
                for index, value in enumerate(model_result["findings"]):
                    body = value["body"]
                    external_id = f"gpt-5.4-{index}"
                    independent_findings.append(
                        {
                            "finding_id": _finding_id(
                                "independent_review",
                                external_id,
                                "gpt-5.4-independent",
                                current_sha,
                                body,
                            ),
                            "channel": "independent_review",
                            "external_id": external_id,
                            "reviewer": "gpt-5.4-independent",
                            "severity": value["severity"],
                            "path": value.get("path"),
                            "line": value.get("line"),
                            "body": body,
                            "url": None,
                            "reviewed_sha": current_sha,
                            "state": "active",
                            "disposition": None,
                        }
                    )
                refreshed.update(
                    {
                        "independent_review": {
                    "reviewer": "gpt-5.4-independent",
                    "model": "gpt-5.4",
                    "reviewed_sha": current_sha,
                            "diff_digest": model_result["diff_digest"],
                    "reviewed_at": utc_now(),
                    "summary": model_result["summary"],
                    "finding_ids": [
                        value["finding_id"] for value in independent_findings
                    ],
                        },
                        "last_error": None,
                    }
                )
                refreshed["findings"].extend(independent_findings)
                record.update(refreshed)
                post_review_refreshed = True
            elif independent:
                current_independent_ids = set(independent.get("finding_ids") or [])
                record["findings"].extend(
                    finding
                    for finding in (existing or {}).get("findings") or []
                    if finding["finding_id"] in current_independent_ids
                )

            record["findings"] = _merge_findings(
                current_sha,
                record["findings"],
                existing,
                supplied,
                replace_active=post_review_refreshed,
            )
            record["status"], record["blockers"] = _status(record)
            if refresh_blockers:
                record["status"] = "blocked"
                record["blockers"] = [*refresh_blockers, *record["blockers"]]
            record["updated_at"] = utc_now()
            record["poll_history"].append(
                {
                    "poll": poll_number,
                    "observed_at": record["updated_at"],
                    "sha": current_sha,
                    "status": record["status"],
                }
            )
            _write_record(state_path, record)
            existing = dict(record)
            if record["status"] in {"ready", "blocked", "review-required"}:
                return record
            if local_poll < max_polls:
                sleeper(interval_seconds)
                continue
            record["status"] = "timeout"
            record["updated_at"] = utc_now()
            record["blockers"] = [
                *record["blockers"],
                "review settling exceeded its polling bound",
            ]
            _write_record(state_path, record)
            return record
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded GitHub review-settling merge gate")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path.home()
        / ".hermes"
        / "supervisor"
        / "axis-development-supervisor"
        / "review-evidence",
    )
    parser.add_argument("--max-polls", type=int, default=6)
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--dispositions", type=Path)
    parser.add_argument("--run-independent-review", action="store_true")
    parser.add_argument("--gh", default=shutil.which("gh") or "gh")
    parser.add_argument("--hermes", default=shutil.which("hermes") or "hermes")
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    dispositions = (
        json.loads(args.dispositions.read_text(encoding="utf-8"))
        if args.dispositions
        else {}
    )
    path = args.state_dir / f"{args.repo.replace('/', '_')}-{args.pr}.json"
    record = settle(
        path,
        args.repo,
        args.pr,
        policy=policy,
        max_polls=args.max_polls,
        interval_seconds=args.interval_seconds,
        timeout_seconds=args.timeout_seconds,
        run_independent_review=args.run_independent_review,
        dispositions=dispositions,
        collector=lambda repo, number: collect_github(repo, number, args.gh),
        model_reviewer=lambda repo, number, sha, diff, digest: default_model_review(
            repo, number, sha, diff, digest, args.hermes
        ),
    )
    print(json.dumps(record, sort_keys=True))
    return 0 if record["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
