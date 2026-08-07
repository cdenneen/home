import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SHA_A = "a" * 40
SHA_B = "b" * 40


def policy(**overrides) -> dict:
    value = {
        "required_human_approvals": 1,
        "approved_human_reviewers": [{"id": "1001", "login": "ALICE"}],
        "required_checks": [
            {
                "name": "hermes-supervisor",
                "producer": {
                    "kind": "github_app",
                    "id": "15368",
                    "login": "github-actions",
                },
            }
        ],
    }
    value.update(overrides)
    return value


def github_evidence(sha: str = SHA_A, *, findings: list[dict] | None = None) -> dict:
    diff = f"diff --git a/state.py b/state.py\n+head={sha}\n"
    return {
        "repository": "cdenneen/home",
        "pr_number": 656,
        "pr_url": "https://github.com/cdenneen/home/pull/656",
        "author": "cdenneen",
        "current_sha": sha,
        "pr_state": "open",
        "is_draft": False,
        "requested_reviewers": ["alice"],
        "reviewers": [
            {
                "reviewer": "alice",
                "account_id": "1001",
                "kind": "human",
                "user_type": "User",
                "app_slug": None,
                "is_automation": False,
                "state": "approved",
                "reviewed_sha": sha,
                "reviewed_at": "2026-08-07T00:00:00+00:00",
            },
            {
                "reviewer": "greptile-apps",
                "account_id": "165735046",
                "kind": "automation",
                "user_type": "Bot",
                "app_slug": "greptile-apps",
                "is_automation": True,
                "state": "commented",
                "reviewed_sha": sha,
                "reviewed_at": "2026-08-07T00:00:00+00:00",
            },
        ],
        "checks": [
            {
                "check_id": "check-1",
                "name": "hermes-supervisor",
                "state": "passed",
                "url": "https://github.com/cdenneen/home/actions/runs/1",
                "sha": sha,
                "producer": {
                    "kind": "github_app",
                    "id": "15368",
                    "login": "github-actions",
                },
                "started_at": "2026-08-07T00:00:00+00:00",
                "completed_at": "2026-08-07T00:01:00+00:00",
            }
        ],
        "channels": {
            name: {
                "complete": True,
                "count": 1,
                "digest": "sha256:" + index * 64,
            }
            for name, index in zip(
                ("reviews", "review_comments", "issue_comments", "checks", "diff"),
                "12345",
                strict=True,
            )
        },
        "diff": {
            "reviewed_sha": sha,
            "digest": "sha256:"
            + __import__("hashlib").sha256(diff.encode()).hexdigest(),
            "size_bytes": len(diff.encode()),
        },
        "findings": findings or [],
        "_diff_text": diff,
    }


def independent_review(
    _repo: str, _number: int, sha: str, _diff: str, digest: str
) -> dict:
    return {
        "schema": "axis.external-development-supervisor.independent-review-output",
        "schema_version": "1.0.0",
        "reviewed_sha": sha,
        "diff_digest": digest,
        "summary": "No blocking defects remain.",
        "findings": [],
    }


def high_finding(sha: str = SHA_A) -> dict:
    from axis_supervisor.review_settling import _finding_id

    body = "P1 lost update"
    return {
        "finding_id": _finding_id(
            "review_comments", "discussion-1", "greptile-apps[bot]", sha, body
        ),
        "channel": "review_comments",
        "external_id": "discussion-1",
        "reviewer": "greptile-apps[bot]",
        "severity": "high",
        "path": "state.py",
        "line": 10,
        "body": body,
        "url": "https://github.com/cdenneen/home/pull/656#discussion_r1",
        "reviewed_sha": sha,
        "state": "active",
        "disposition": None,
    }


def fixed_disposition(finding: dict, *, sha: str | None = None) -> dict:
    return {
        "finding_id": finding["finding_id"],
        "reviewed_sha": sha or finding["reviewed_sha"],
        "status": "fixed",
        "rationale": "The exact reviewed defect is repaired.",
        "evidence": ["regression-test"],
    }


def test_greptile_approval_is_not_required_when_human_policy_is_satisfied(
    tmp_path: Path,
):
    from axis_supervisor.review_settling import settle

    record = settle(
        tmp_path / "review.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: github_evidence(),
        model_reviewer=independent_review,
    )

    assert record["status"] == "ready"
    assert record["policy"]["approved_human_reviewers"] == [
        {"id": "1001", "login": "alice"}
    ]
    assert record["reviewers"][1]["state"] == "commented"
    assert record["independent_review"]["reviewed_sha"] == SHA_A


def test_collector_fetches_all_review_comment_channels_and_exact_diff(monkeypatch):
    import axis_supervisor.review_settling as review

    pr = {
        "head": {"sha": SHA_A},
        "user": {"login": "CDenneen"},
        "requested_reviewers": [{"id": 1001, "login": "Alice", "type": "User"}],
        "html_url": "https://github.com/cdenneen/home/pull/656",
        "state": "open",
        "draft": False,
    }

    def json_response(command: list[str], timeout: int = 120):
        del timeout
        endpoint = command[2]
        if endpoint.endswith("/status"):
            return {
                "statuses": [
                    {
                        "context": "legacy-ci",
                        "id": 11,
                        "state": "success",
                        "created_at": "2026-08-07T00:00:00+00:00",
                        "updated_at": "2026-08-07T00:01:00+00:00",
                        "target_url": "https://github.com/status/1",
                        "creator": {
                            "id": 42,
                            "login": "legacy-ci[bot]",
                            "type": "Bot",
                        },
                    }
                ]
            }
        return pr

    def pages(command: list[str], timeout: int = 120):
        del timeout
        endpoint = command[2]
        if endpoint.endswith("/reviews?per_page=100"):
            return [[{
                "id": 1,
                "user": {"id": 1001, "login": "aLiCe", "type": "User"},
                "state": "APPROVED",
                "commit_id": SHA_A,
                "submitted_at": "2026-08-07T00:00:00+00:00",
                "body": "P2 review body finding",
                "html_url": "https://github.com/review/1",
            }]]
        if endpoint.endswith("/comments?per_page=100") and "/pulls/" in endpoint:
            return [[{
                "id": 2,
                "user": {"login": "greptile-apps[bot]", "type": "Bot"},
                "commit_id": SHA_A,
                "body": "P1 inline finding",
                "path": "state.py",
                "line": 10,
                "html_url": "https://github.com/comment/2",
            }]]
        if endpoint.endswith("/comments?per_page=100"):
            return [[{
                "id": 3,
                "user": {"login": "reviewer"},
                "body": "P3 issue finding",
                "html_url": "https://github.com/comment/3",
            }]]
        return [{"check_runs": [{
            "name": "hermes-supervisor",
            "id": 10,
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-08-07T00:00:00+00:00",
            "completed_at": "2026-08-07T00:01:00+00:00",
            "details_url": "https://github.com/check/1",
            "app": {"id": 15368, "slug": "github-actions"},
        }]}]

    monkeypatch.setattr(review, "_json", json_response)
    monkeypatch.setattr(review, "_pages", pages)
    monkeypatch.setattr(
        review.subprocess,
        "check_output",
        lambda *_args, **_kwargs: "diff --git a/state.py b/state.py\n",
    )

    evidence = review.collect_github("cdenneen/home", 656)

    assert len(evidence["findings"]) == 3
    assert {value["channel"] for value in evidence["findings"]} == {
        "reviews",
        "review_comments",
        "issue_comments",
    }
    assert all(evidence["channels"][name]["complete"] for name in review.CHANNELS)
    assert evidence["diff"]["reviewed_sha"] == SHA_A
    assert evidence["reviewers"][0]["state"] == "approved"
    assert evidence["author"] == "cdenneen"
    assert evidence["requested_reviewers"] == ["alice"]
    assert evidence["reviewers"][0]["reviewer"] == "alice"
    assert evidence["reviewers"][0]["user_type"] == "User"
    assert evidence["reviewers"][0]["is_automation"] is False
    assert evidence["checks"][0]["producer"] == {
        "kind": "github_app",
        "id": "15368",
        "login": "github-actions",
    }
    assert evidence["checks"][1]["producer"] == {
        "kind": "automation",
        "id": "42",
        "login": "legacy-ci[bot]",
    }


def test_policy_rejects_case_variant_duplicate_human_logins(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    duplicate_policy = policy(
        required_human_approvals=2,
        approved_human_reviewers=[
            {"id": "1001", "login": "Alice"},
            {"id": "1002", "login": "aLiCe"},
        ],
    )

    with pytest.raises(ValueError, match="must not contain duplicates"):
        settle(
            tmp_path / "duplicate-login-policy.json",
            "cdenneen/home",
            656,
            policy=duplicate_policy,
            collector=lambda _repo, _number: github_evidence(),
        )


def test_required_reviewer_and_check_absence_fail_closed(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    missing_check = github_evidence()
    missing_check["checks"] = []
    blocked = settle(
        tmp_path / "missing-check.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: dict(missing_check),
        model_reviewer=independent_review,
    )
    missing_reviewer = github_evidence()
    missing_reviewer["reviewers"] = [missing_reviewer["reviewers"][1]]
    timed_out = settle(
        tmp_path / "missing-reviewer.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: github_evidence()
        | {"reviewers": missing_reviewer["reviewers"]},
        model_reviewer=independent_review,
    )

    assert blocked["status"] == "blocked"
    assert "trusted producer is absent" in blocked["blockers"][0]
    assert timed_out["status"] == "timeout"
    assert any("approved human identity is absent" in value for value in timed_out["blockers"])


@pytest.mark.parametrize(
    ("login", "user_type", "app_slug"),
    [
        ("renovate", "Bot", None),
        ("renovate[bot]", "User", None),
        ("release-service", "User", "release-app"),
    ],
)
def test_automation_approval_cannot_satisfy_human_policy(
    tmp_path: Path,
    login: str,
    user_type: str,
    app_slug: str | None,
):
    from axis_supervisor.review_settling import settle

    evidence = github_evidence()
    evidence["requested_reviewers"] = [login]
    evidence["reviewers"] = [
        {
            "reviewer": login,
            "account_id": "2002",
            "kind": "human",
            "user_type": user_type,
            "app_slug": app_slug,
            "is_automation": True,
            "state": "approved",
            "reviewed_sha": SHA_A,
            "reviewed_at": "2026-08-07T00:00:00+00:00",
        }
    ]
    record = settle(
        tmp_path / f"automation-{login.replace('[', '_').replace(']', '_')}.json",
        "cdenneen/home",
        656,
        policy=policy(
            approved_human_reviewers=[{"id": "2002", "login": login}]
        ),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: evidence,
        model_reviewer=independent_review,
    )

    assert record["status"] == "timeout"
    assert any("approved human identity is absent" in value for value in record["blockers"])


def test_unallowlisted_machine_service_user_cannot_count_as_human(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    evidence = github_evidence()
    evidence["requested_reviewers"] = ["release-service"]
    evidence["reviewers"] = [
        {
            "reviewer": "release-service",
            "account_id": "2003",
            "kind": "human",
            "user_type": "User",
            "app_slug": None,
            "is_automation": False,
            "state": "approved",
            "reviewed_sha": SHA_A,
            "reviewed_at": "2026-08-07T00:00:00+00:00",
        }
    ]
    record = settle(
        tmp_path / "service-user.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: evidence,
        model_reviewer=independent_review,
    )

    assert record["status"] == "timeout"
    assert any("approved human identity is absent" in value for value in record["blockers"])


def test_spoofed_same_name_check_cannot_satisfy_trusted_producer(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    evidence = github_evidence()
    evidence["checks"][0]["producer"] = {
        "kind": "user",
        "id": "999",
        "login": "attacker",
    }
    record = settle(
        tmp_path / "spoofed-check.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: evidence,
        model_reviewer=independent_review,
    )

    assert record["status"] == "blocked"
    assert "trusted producer is absent" in record["blockers"][0]


@pytest.mark.parametrize("duplicate_state", ["pending", "failed", "skipped"])
def test_every_duplicate_trusted_check_run_must_succeed(
    tmp_path: Path, duplicate_state: str
):
    from axis_supervisor.review_settling import settle

    evidence = github_evidence()
    duplicate = dict(evidence["checks"][0])
    duplicate.update(
        {
            "check_id": "check-older",
            "state": duplicate_state,
            "started_at": "2026-08-06T23:00:00+00:00",
            "completed_at": None if duplicate_state == "pending" else "2026-08-06T23:01:00+00:00",
        }
    )
    evidence["checks"].append(duplicate)
    record = settle(
        tmp_path / f"duplicate-{duplicate_state}.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: evidence,
        model_reviewer=independent_review,
    )

    assert record["status"] == "blocked"
    assert "every required check run" in record["blockers"][0]


def test_stale_human_approval_is_not_accepted(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    evidence = github_evidence(SHA_B)
    evidence["reviewers"][0]["reviewed_sha"] = SHA_A
    record = settle(
        tmp_path / "stale-review.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: evidence,
        model_reviewer=independent_review,
    )

    assert record["status"] == "timeout"
    assert any("approved human identity is absent" in value for value in record["blockers"])


def test_finding_and_disposition_ids_are_bound_to_reviewed_sha(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    finding = high_finding()

    def collect(_repo: str, _number: int) -> dict:
        return github_evidence(findings=[dict(finding)])

    with pytest.raises(ValueError, match="not bound to the finding SHA"):
        settle(
            tmp_path / "wrong-sha.json",
            "cdenneen/home",
            656,
            policy=policy(),
            max_polls=1,
            run_independent_review=True,
            dispositions={
                finding["finding_id"]: fixed_disposition(finding, sha=SHA_B)
            },
            collector=collect,
            model_reviewer=independent_review,
        )
    fixed = settle(
        tmp_path / "fixed.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        dispositions={finding["finding_id"]: fixed_disposition(finding)},
        collector=collect,
        model_reviewer=independent_review,
    )

    assert fixed["status"] == "ready"
    disposition = fixed["findings"][0]["disposition"]
    assert disposition["finding_id"] == finding["finding_id"]
    assert disposition["reviewed_sha"] == SHA_A
    assert disposition["disposition_id"].startswith("disposition-")
    recollected = settle(
        tmp_path / "fixed.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        collector=collect,
    )
    assert recollected["status"] == "ready"
    assert recollected["findings"][0]["disposition"] == disposition


def test_exact_diff_is_reviewed_and_head_change_retries(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    evidence_a = github_evidence(SHA_A)
    evidence_b = github_evidence(SHA_B)
    sequence = [evidence_a, evidence_b, evidence_b, evidence_b]
    model_calls = []

    def collect(_repo: str, _number: int) -> dict:
        return json.loads(json.dumps(sequence.pop(0)))

    def review(repo: str, number: int, sha: str, diff: str, digest: str) -> dict:
        model_calls.append((sha, diff, digest))
        return independent_review(repo, number, sha, diff, digest)

    record = settle(
        tmp_path / "head-change.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=2,
        interval_seconds=0,
        run_independent_review=True,
        collector=collect,
        model_reviewer=review,
        sleeper=lambda _seconds: None,
    )

    assert record["status"] == "ready"
    assert [value[0] for value in model_calls] == [SHA_A, SHA_B]
    assert all(f"head={sha}" in diff for sha, diff, _digest_value in model_calls)
    assert record["diff"]["reviewed_sha"] == SHA_B
    assert record["independent_review"]["diff_digest"] == record["diff"]["digest"]


@pytest.mark.parametrize("refresh_change", ["failed-check", "review-request"])
def test_same_sha_refresh_replaces_evidence_and_blocks_new_failures(
    tmp_path: Path, refresh_change: str
):
    from axis_supervisor.review_settling import settle

    initial = github_evidence()
    refreshed = github_evidence()
    if refresh_change == "failed-check":
        refreshed["checks"][0]["state"] = "failed"
    else:
        refreshed["requested_reviewers"] = ["alice", "bob"]
    sequence = [initial, refreshed]

    def collect(_repo: str, _number: int) -> dict:
        return json.loads(json.dumps(sequence.pop(0)))

    record = settle(
        tmp_path / f"refresh-{refresh_change}.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=collect,
        model_reviewer=independent_review,
    )

    assert record["status"] == "blocked"
    if refresh_change == "failed-check":
        assert record["checks"][0]["state"] == "failed"
        assert "every required check run" in record["blockers"][0]
    else:
        assert record["requested_reviewers"] == ["alice", "bob"]
        assert "review requests changed" in record["blockers"][0]


def test_same_sha_refresh_unions_unresolved_findings_until_disposition(
    tmp_path: Path,
):
    from axis_supervisor.review_settling import settle

    finding = high_finding()
    sequence = [
        github_evidence(findings=[dict(finding)]),
        github_evidence(findings=[]),
    ]

    def collect(_repo: str, _number: int) -> dict:
        return json.loads(json.dumps(sequence.pop(0)))

    record = settle(
        tmp_path / "monotonic-finding.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=collect,
        model_reviewer=independent_review,
    )

    retained = next(
        value for value in record["findings"] if value["finding_id"] == finding["finding_id"]
    )
    assert record["status"] == "blocked"
    assert retained["state"] == "active"
    assert retained["disposition"] is None


def test_outage_preserves_findings_and_head_change_invalidates_them(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    path = tmp_path / "review.json"
    finding = high_finding()
    first = settle(
        path,
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: github_evidence(findings=[dict(finding)]),
        model_reviewer=independent_review,
    )
    unavailable = settle(
        path,
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        collector=lambda _repo, _number: (_ for _ in ()).throw(
            RuntimeError("GitHub unavailable")
        ),
    )
    repaired = settle(
        path,
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: github_evidence(SHA_B),
        model_reviewer=independent_review,
    )

    assert first["status"] == "blocked"
    assert unavailable["status"] == "unavailable"
    assert unavailable["findings"] == first["findings"]
    stale = next(value for value in repaired["findings"] if value["reviewed_sha"] == SHA_A)
    assert stale["state"] == "stale"
    assert stale["disposition"] is None
    assert repaired["status"] == "ready"


def test_independent_review_schema_is_strict_and_channels_must_be_complete(
    tmp_path: Path,
):
    from axis_supervisor.review_settling import settle

    def malformed(repo: str, number: int, sha: str, diff: str, digest: str) -> dict:
        return independent_review(repo, number, sha, diff, digest) | {"unexpected": True}

    unavailable = settle(
        tmp_path / "malformed.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: github_evidence(),
        model_reviewer=malformed,
    )
    wrong_binding = settle(
        tmp_path / "wrong-binding.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: github_evidence(),
        model_reviewer=lambda repo, number, sha, diff, digest: independent_review(
            repo, number, sha, diff, digest
        )
        | {"diff_digest": "sha256:" + "f" * 64},
    )
    incomplete = github_evidence()
    incomplete["channels"]["issue_comments"]["complete"] = False
    blocked = settle(
        tmp_path / "incomplete.json",
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: incomplete,
        model_reviewer=independent_review,
    )

    assert unavailable["status"] == "unavailable"
    assert "invalid" in unavailable["blockers"][0].lower()
    assert wrong_binding["status"] == "unavailable"
    assert "not bound" in wrong_binding["blockers"][0]
    assert blocked["status"] == "blocked"
    assert "incomplete review channels" in blocked["blockers"][0]


def test_deadline_and_poll_count_survive_reinvocation(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    path = tmp_path / "durable.json"
    first = settle(
        path,
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        collector=lambda _repo, _number: github_evidence(),
    )
    second = settle(
        path,
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: github_evidence(),
        model_reviewer=independent_review,
    )

    assert first["status"] == "review-required"
    assert second["status"] == "ready"
    assert first["deadline_at"] == second["deadline_at"]
    assert first["session_id"] == second["session_id"]
    assert (first["poll_count"], second["poll_count"]) == (1, 2)


def test_review_writes_use_unique_fsynced_temps_and_transaction_lock(
    monkeypatch, tmp_path: Path
):
    import axis_supervisor.review_settling as review

    path = tmp_path / "review.json"
    record = review.settle(
        path,
        "cdenneen/home",
        656,
        policy=policy(),
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: github_evidence(),
        model_reviewer=independent_review,
    )
    replacements = []
    replace = review.os.replace

    def capture(source, target):
        replacements.append(Path(source))
        replace(source, target)

    monkeypatch.setattr(review.os, "replace", capture)
    review._write_record(path, record)
    review._write_record(path, record)
    assert replacements[0] != replacements[1]
    assert not list(tmp_path.glob(".*.tmp"))

    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def first_lock():
        with review._transaction(path):
            entered.set()
            release.wait(2)

    def second_lock():
        entered.wait(2)
        with review._transaction(path):
            second_entered.set()

    first_thread = threading.Thread(target=first_lock)
    second_thread = threading.Thread(target=second_lock)
    first_thread.start()
    second_thread.start()
    assert entered.wait(1)
    time.sleep(0.05)
    assert not second_entered.is_set()
    release.set()
    first_thread.join(2)
    second_thread.join(2)
    assert second_entered.is_set()
