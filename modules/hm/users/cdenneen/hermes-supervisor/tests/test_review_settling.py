import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def github_evidence(sha: str = "a" * 40) -> dict:
    return {
        "repository": "cdenneen/home",
        "pr_number": 656,
        "pr_url": "https://github.com/cdenneen/home/pull/656",
        "current_sha": sha,
        "pr_state": "open",
        "is_draft": False,
        "reviewers": [
            {
                "reviewer": "greptile-apps",
                "kind": "bot",
                "required": False,
                "state": "commented",
                "reviewed_sha": sha,
                "reviewed_at": "2026-08-07T00:00:00+00:00",
            }
        ],
        "checks": [
            {
                "name": "hermes-supervisor",
                "state": "passed",
                "url": "https://github.com/cdenneen/home/actions/runs/1",
            }
        ],
        "findings": [],
    }


def independent_review(_repo: str, _number: int, _sha: str) -> dict:
    return {"summary": "No blocking defects remain.", "findings": []}


def test_greptile_approval_is_not_required_when_evidence_is_settled(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    record = settle(
        tmp_path / "review.json",
        "cdenneen/home",
        656,
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: github_evidence(),
        model_reviewer=independent_review,
    )

    assert record["status"] == "ready"
    assert record["independent_review"]["reviewed_sha"] == "a" * 40
    assert record["reviewers"][0]["state"] == "commented"


def test_material_repair_requires_and_runs_fresh_independent_review(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    path = tmp_path / "review.json"
    calls = []

    def review(repo: str, number: int, sha: str) -> dict:
        calls.append((repo, number, sha))
        return independent_review(repo, number, sha)

    first = settle(
        path,
        "cdenneen/home",
        656,
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: github_evidence("a" * 40),
        model_reviewer=review,
    )
    repaired = settle(
        path,
        "cdenneen/home",
        656,
        max_polls=1,
        run_independent_review=True,
        collector=lambda _repo, _number: github_evidence("b" * 40),
        model_reviewer=review,
    )

    assert first["status"] == repaired["status"] == "ready"
    assert [value[2] for value in calls] == ["a" * 40, "b" * 40]
    assert repaired["independent_review"]["reviewed_sha"] == "b" * 40


def test_high_finding_blocks_until_fixed_disposition(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    finding = {
        "finding_id": "finding-high",
        "reviewer": "greptile-apps[bot]",
        "severity": "high",
        "path": "state.py",
        "line": 10,
        "body": "P1 lost update",
        "url": "https://github.com/cdenneen/home/pull/656#discussion_r1",
        "reviewed_sha": "a" * 40,
        "disposition": None,
    }

    def collect(_repo: str, _number: int) -> dict:
        value = github_evidence()
        value["findings"] = [dict(finding)]
        return value

    blocked = settle(
        tmp_path / "review.json",
        "cdenneen/home",
        656,
        max_polls=1,
        run_independent_review=True,
        collector=collect,
        model_reviewer=independent_review,
    )
    fixed = settle(
        tmp_path / "review.json",
        "cdenneen/home",
        656,
        max_polls=1,
        dispositions={
            "finding-high": {
                "status": "fixed",
                "rationale": "Queue mutations now hold an exclusive lock.",
                "evidence": ["test_concurrent_queue_mutations_preserve_every_item_and_fault_is_atomic"],
            }
        },
        collector=collect,
        model_reviewer=independent_review,
    )

    assert blocked["status"] == "blocked"
    assert fixed["status"] == "ready"


def test_pending_review_times_out_and_unavailable_fails_closed(tmp_path: Path):
    from axis_supervisor.review_settling import settle

    pending = github_evidence()
    pending["checks"][0]["state"] = "pending"
    timed_out = settle(
        tmp_path / "timeout.json",
        "cdenneen/home",
        656,
        max_polls=2,
        interval_seconds=0,
        timeout_seconds=1,
        run_independent_review=True,
        collector=lambda _repo, _number: pending,
        model_reviewer=independent_review,
        sleeper=lambda _seconds: None,
    )
    unavailable = settle(
        tmp_path / "unavailable.json",
        "cdenneen/home",
        656,
        max_polls=1,
        collector=lambda _repo, _number: (_ for _ in ()).throw(
            RuntimeError("GitHub unavailable")
        ),
    )

    assert timed_out["status"] == "timeout"
    assert timed_out["poll_count"] == 2
    assert unavailable["status"] == "unavailable"
    assert "GitHub unavailable" in unavailable["blockers"][0]
