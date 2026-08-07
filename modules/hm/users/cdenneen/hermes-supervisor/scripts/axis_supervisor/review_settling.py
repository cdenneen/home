import argparse
import hashlib
import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .schema_registry import read_record, write_record

SCHEMA = "axis.external-development-supervisor.review-evidence"
SCHEMA_VERSION = "1.0.0"
RESOLVED_DISPOSITIONS = {"fixed", "false-positive", "superseded"}
BOT_REVIEWERS = {"greptile-apps", "greptile-apps[bot]"}
SEVERITY = {"P0": "critical", "P1": "high", "P2": "medium", "P3": "low"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(command: list[str], timeout: int = 120) -> object:
    output = subprocess.check_output(command, text=True, timeout=timeout)
    return json.loads(output)


def _finding_id(reviewer: str, path: str | None, body: str) -> str:
    payload = "\x00".join((reviewer, path or "", body)).encode()
    return "finding-" + hashlib.sha256(payload).hexdigest()[:20]


def _severity(body: str) -> str | None:
    match = re.search(r'alt=["\'](P[0-3])["\']|\b(P[0-3])\b', body)
    return SEVERITY.get(next(value for value in match.groups() if value)) if match else None


def _extract_json(text: str) -> dict:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("independent review output contained no JSON object")


def _check_state(check: dict) -> str:
    conclusion = str(check.get("conclusion") or "").upper()
    status = str(check.get("status") or check.get("state") or "").upper()
    if conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"} or status == "SUCCESS":
        return "passed"
    if conclusion in {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
        return "failed"
    return "pending"


def collect_github(repo: str, pr_number: int, gh: str = "gh") -> dict:
    pr = _json(
        [
            gh,
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "number,url,headRefOid,isDraft,state,reviewDecision,reviews,reviewRequests,statusCheckRollup",
        ]
    )
    comments = _json(
        [gh, "api", f"repos/{repo}/pulls/{pr_number}/comments", "--paginate"]
    )
    if not isinstance(pr, dict) or not isinstance(comments, list):
        raise TypeError("GitHub review evidence response has an unexpected shape")
    requested = {
        str((value.get("login") or (value.get("requestedReviewer") or {}).get("login")) or "")
        for value in pr.get("reviewRequests") or []
    }
    requested.discard("")
    latest_reviews = {}
    for review in pr.get("reviews") or []:
        login = str((review.get("author") or {}).get("login") or "")
        if login:
            latest_reviews[login] = review
    reviewers = []
    for login in sorted(requested | set(latest_reviews)):
        review = latest_reviews.get(login) or {}
        commit = review.get("commit") or {}
        reviewers.append(
            {
                "reviewer": login,
                "kind": "bot" if login in BOT_REVIEWERS else "human",
                "required": login in requested and login not in BOT_REVIEWERS,
                "state": str(review.get("state") or "PENDING").lower(),
                "reviewed_sha": commit.get("oid"),
                "reviewed_at": review.get("submittedAt"),
            }
        )
    findings = []
    for comment in comments:
        body = str(comment.get("body") or "")
        severity = _severity(body)
        if severity is None:
            continue
        reviewer = str((comment.get("user") or {}).get("login") or "unknown")
        path = comment.get("path")
        findings.append(
            {
                "finding_id": _finding_id(reviewer, path, body),
                "reviewer": reviewer,
                "severity": severity,
                "path": path,
                "line": comment.get("line") or comment.get("original_line"),
                "body": body,
                "url": comment.get("html_url"),
                "reviewed_sha": comment.get("commit_id"),
                "disposition": None,
            }
        )
    checks = [
        {
            "name": str(value.get("name") or value.get("context") or "unknown"),
            "state": _check_state(value),
            "url": value.get("detailsUrl") or value.get("targetUrl"),
        }
        for value in pr.get("statusCheckRollup") or []
    ]
    return {
        "repository": repo,
        "pr_number": pr_number,
        "pr_url": pr["url"],
        "current_sha": pr["headRefOid"],
        "pr_state": str(pr.get("state") or "UNKNOWN").lower(),
        "is_draft": bool(pr.get("isDraft")),
        "reviewers": reviewers,
        "findings": findings,
        "checks": checks,
    }


def default_model_review(repo: str, pr_number: int, sha: str, gh: str, hermes: str) -> dict:
    diff = subprocess.check_output(
        [gh, "pr", "diff", str(pr_number), "--repo", repo],
        text=True,
        timeout=120,
    )
    if len(diff.encode()) > 500_000:
        raise RuntimeError("PR diff exceeds independent review bound")
    prompt = f"""You are the independent GPT-5.4 merge reviewer for {repo}#{pr_number} at exact SHA {sha}.
Review the complete diff below for correctness, durable-state migration, concurrency, security, and missing tests.
Return only JSON: {{"summary":"...","findings":[{{"severity":"critical|high|medium|low","path":"path","line":1,"body":"actionable finding"}}]}}.
Do not approve based on another reviewer. Empty findings is valid only when no defect remains.

{diff}
"""
    output = subprocess.check_output(
        [
            hermes,
            "--model",
            "gpt-5.4",
            "--provider",
            "openai-api",
            "--reasoning",
            "high",
            "--toolsets",
            "",
            "--ignore-rules",
            "--oneshot",
            prompt,
        ],
        text=True,
        timeout=900,
    )
    result = _extract_json(output)
    findings = result.get("findings") or []
    if not isinstance(findings, list):
        raise TypeError("independent review findings must be an array")
    return {"summary": str(result.get("summary") or ""), "findings": findings}


def _merge_dispositions(
    findings: list[dict], existing: dict | None, supplied: dict[str, dict]
) -> list[dict]:
    prior = {
        value["finding_id"]: value.get("disposition")
        for value in (existing or {}).get("findings") or []
    }
    for finding in findings:
        finding["disposition"] = supplied.get(finding["finding_id"], prior.get(finding["finding_id"]))
    return findings


def _status(record: dict) -> tuple[str, list[str]]:
    blockers = []
    if record["pr_state"] != "open" or record["is_draft"]:
        blockers.append("pull request is not an open non-draft change")
    failed_checks = [value["name"] for value in record["checks"] if value["state"] == "failed"]
    if failed_checks:
        blockers.append("failed checks: " + ", ".join(failed_checks))
    if blockers:
        return "blocked", blockers
    if any(value["state"] == "pending" for value in record["checks"]):
        return "settling", blockers
    required = [value for value in record["reviewers"] if value["required"]]
    if any(
        value["state"] != "approved" or value["reviewed_sha"] != record["current_sha"]
        for value in required
    ):
        return "settling", blockers
    independent = record.get("independent_review")
    if not independent or independent.get("reviewed_sha") != record["current_sha"]:
        return "review-required", blockers + ["independent GPT-5.4 review is missing or stale"]
    for finding in record["findings"]:
        disposition = finding.get("disposition") or {}
        state = disposition.get("status")
        if state in RESOLVED_DISPOSITIONS:
            continue
        if state == "accepted-risk" and finding["severity"] in {"medium", "low"}:
            continue
        blockers.append(
            f"{finding['severity']} finding {finding['finding_id']} requires a blocking disposition"
        )
    return ("blocked", blockers) if blockers else ("ready", [])


def settle(
    state_path: Path,
    repo: str,
    pr_number: int,
    *,
    max_polls: int = 6,
    interval_seconds: int = 30,
    timeout_seconds: int = 300,
    run_independent_review: bool = False,
    dispositions: dict[str, dict] | None = None,
    collector: Callable[[str, int], dict] = collect_github,
    model_reviewer: Callable[[str, int, str], dict] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict:
    started = datetime.now(timezone.utc)
    deadline = started + timedelta(seconds=timeout_seconds)
    existing = read_record(state_path, SCHEMA) if state_path.exists() else None
    supplied = dispositions or {}
    history = list((existing or {}).get("poll_history") or [])
    for poll in range(1, max_polls + 1):
        try:
            evidence = collector(repo, pr_number)
        except Exception as exc:  # noqa: BLE001 - unavailability must become durable evidence
            record = {
                "schema": SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "repository": repo,
                "pr_number": pr_number,
                "pr_url": (existing or {}).get("pr_url"),
                "current_sha": (existing or {}).get("current_sha"),
                "status": "unavailable",
                "started_at": (existing or {}).get("started_at") or started.isoformat(),
                "updated_at": utc_now(),
                "deadline_at": deadline.isoformat(),
                "poll_count": poll,
                "pr_state": "unknown",
                "is_draft": False,
                "reviewers": [],
                "checks": [],
                "findings": [],
                "independent_review": (existing or {}).get("independent_review"),
                "blockers": [f"GitHub review evidence unavailable: {type(exc).__name__}: {exc}"],
                "poll_history": history,
            }
            write_record(state_path, record, SCHEMA)
            return record
        independent = (existing or {}).get("independent_review")
        if run_independent_review and (
            not independent or independent.get("reviewed_sha") != evidence["current_sha"]
        ):
            if model_reviewer is None:
                raise RuntimeError("independent review requested without a model reviewer")
            model_result = model_reviewer(repo, pr_number, evidence["current_sha"])
            independent_findings = []
            for value in model_result.get("findings") or []:
                body = str(value.get("body") or "")
                path = value.get("path")
                independent_findings.append(
                    {
                        "finding_id": _finding_id("gpt-5.4-independent", path, body),
                        "reviewer": "gpt-5.4-independent",
                        "severity": str(value.get("severity") or "high"),
                        "path": path,
                        "line": value.get("line"),
                        "body": body,
                        "url": None,
                        "reviewed_sha": evidence["current_sha"],
                        "disposition": None,
                    }
                )
            independent = {
                "reviewer": "gpt-5.4-independent",
                "model": "gpt-5.4",
                "reviewed_sha": evidence["current_sha"],
                "reviewed_at": utc_now(),
                "summary": str(model_result.get("summary") or ""),
                "finding_ids": [value["finding_id"] for value in independent_findings],
            }
            evidence["findings"].extend(independent_findings)
        elif independent:
            prior_by_id = {
                value["finding_id"]: value for value in (existing or {}).get("findings") or []
            }
            evidence["findings"].extend(
                prior_by_id[finding_id]
                for finding_id in independent.get("finding_ids") or []
                if finding_id in prior_by_id
            )
        evidence["findings"] = _merge_dispositions(
            evidence["findings"], existing, supplied
        )
        record = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            **evidence,
            "status": "settling",
            "started_at": (existing or {}).get("started_at") or started.isoformat(),
            "updated_at": utc_now(),
            "deadline_at": deadline.isoformat(),
            "poll_count": poll,
            "independent_review": independent,
            "blockers": [],
            "poll_history": history,
        }
        record["status"], record["blockers"] = _status(record)
        history.append(
            {
                "poll": poll,
                "observed_at": record["updated_at"],
                "sha": record["current_sha"],
                "status": record["status"],
            }
        )
        record["poll_history"] = history
        write_record(state_path, record, SCHEMA)
        existing = record
        if record["status"] in {"ready", "blocked", "review-required"}:
            return record
        if poll < max_polls and datetime.now(timezone.utc) < deadline:
            sleeper(interval_seconds)
            continue
        record["status"] = "timeout"
        record["updated_at"] = utc_now()
        record["blockers"].append("review settling exceeded its polling bound")
        write_record(state_path, record, SCHEMA)
        return record
    raise AssertionError("unreachable")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded GitHub review-settling merge gate")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".hermes" / "supervisor" / "axis-development-supervisor" / "review-evidence")
    parser.add_argument("--max-polls", type=int, default=6)
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--dispositions", type=Path)
    parser.add_argument("--run-independent-review", action="store_true")
    parser.add_argument("--gh", default=shutil.which("gh") or "gh")
    parser.add_argument("--hermes", default=shutil.which("hermes") or "hermes")
    args = parser.parse_args()
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
        max_polls=args.max_polls,
        interval_seconds=args.interval_seconds,
        timeout_seconds=args.timeout_seconds,
        run_independent_review=args.run_independent_review,
        dispositions=dispositions,
        collector=lambda repo, number: collect_github(repo, number, args.gh),
        model_reviewer=lambda repo, number, sha: default_model_review(
            repo, number, sha, args.gh, args.hermes
        ),
    )
    print(json.dumps(record, sort_keys=True))
    return 0 if record["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
