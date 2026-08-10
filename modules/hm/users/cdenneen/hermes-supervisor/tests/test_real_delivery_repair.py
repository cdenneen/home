import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from axis_supervisor.dashboard import _recent_lines  # noqa: E402
from axis_supervisor.collector import normalize_open_merge_request  # noqa: E402
from axis_supervisor.merge_lanes import INTEGRATION, consume_next, reconcile  # noqa: E402
from axis_supervisor.semantic_escalation import (  # noqa: E402
    exclude_pending,
    pending,
    quarantine_failed_assignment,
)


def _semantic_assignment(repository_head: str = "a" * 40) -> dict:
    return {
        "assignment_id": "semantic-250",
        "assignment_type": "read-only-analysis",
        "target_ref": "ghostspace/axis-governance#250",
        "source_fingerprint": "source-250",
        "source_item": {
            "repository_head": repository_head,
            "authority_facts": {
                "approval_required": True,
                "decision_escalate": True,
                "record_revision": 7,
                "record_digest": "sha256:" + "a" * 64,
            },
        },
        "error": "ValueError: semantic response contract failed",
    }


def test_unchanged_escalation_failure_is_durably_quarantined(tmp_path: Path):
    failed = _semantic_assignment()
    quarantine = quarantine_failed_assignment(tmp_path, failed)

    assert quarantine is not None
    assert quarantine["state"] == "pending-human-escalation"
    assert pending(tmp_path, _semantic_assignment()) == quarantine
    assert pending(tmp_path, _semantic_assignment("b" * 40)) is None
    queue = [
        _semantic_assignment() | {"ref": "semantic-decomposition:governance-250"},
        _semantic_assignment("b" * 40)
        | {"ref": "semantic-decomposition:axis-29", "target_ref": "ghostspace/axis#29"},
    ]
    assert [entry["target_ref"] for entry in exclude_pending(tmp_path, queue)] == [
        "ghostspace/axis#29"
    ]


def test_internal_retry_never_appears_as_product_progress():
    assert _recent_lines(
        [{"event_type": "assignment_retry", "work_item": "ghostspace/axis#29"}]
    ) == ["• No material product change in the current activity window"]


class FakeIntegrator:
    def __init__(self):
        self.calls = []

    def inspect_mr(self, repository: str, iid: int, *, responsibility: str) -> dict:
        self.calls.append((repository, iid, responsibility))
        return {
            "merge_ready": True,
            "pipeline": {"status": "success"},
            "review_pending": False,
        }


def _actionable_mr(iid: int) -> dict:
    return {
        "project": "ghostspace/axis",
        "iid": iid,
        "state": "opened",
        "target_branch": "main",
        "merge_status": "mergeable",
        "pipeline_status": "success",
        "pipeline_facts_available": True,
        "approved": True,
        "approval_facts_available": True,
        "draft": False,
        "sha": f"sha-{iid}",
        "web_url": f"https://gitlab.example/ghostspace/axis/-/merge_requests/{iid}",
    }


def test_actionable_external_mrs_are_adopted_and_consumed_by_integrator(
    tmp_path: Path,
):
    inventory = {"open_merge_requests": [_actionable_mr(iid) for iid in (154, 155, 157, 158)]}

    adopted = reconcile(tmp_path, inventory)
    assert [value["lane"] for value in adopted["items"]] == [INTEGRATION] * 4
    assert {value["owner"] for value in adopted["items"]} == {"supervisor-integration"}

    integrator = FakeIntegrator()
    for _ in range(4):
        consumed = consume_next(tmp_path, inventory, integrator)
        assert consumed is not None
        assert consumed["lane"] == INTEGRATION
    assert {iid for _, iid, _ in integrator.calls} == {154, 155, 157, 158}
    persisted = json.loads((tmp_path / "merge-lanes.json").read_text(encoding="utf-8"))
    assert all(value["last_consumed_at_epoch"] for value in persisted["items"])


def test_gitlab_approval_and_detail_pipeline_facts_adopt_an_owned_lane(
    tmp_path: Path,
):
    listed_mr = {
        "iid": 154,
        "title": "Production-shaped actionable MR",
        "state": "opened",
        "target_branch": "main",
        "detailed_merge_status": "mergeable",
        "head_pipeline": None,
        "approved_by": [],
        "draft": False,
    }
    approvals = {
        "approved": True,
        "approvals_required": 0,
        "approvals_left": 0,
        "approved_by": [],
    }
    detail = {"head_pipeline": {"status": "success"}}

    merge_request = normalize_open_merge_request(
        "ghostspace/axis", listed_mr, approvals, detail
    )

    assert merge_request["approved"] is True
    assert merge_request["approved_by"] == []
    assert merge_request["approval_state"] == "approved"
    assert merge_request["pipeline_status"] == "success"
    assert merge_request["approval_facts_available"] is True
    assert merge_request["pipeline_facts_available"] is True
    adopted = reconcile(tmp_path, {"open_merge_requests": [merge_request]})
    assert adopted["items"][0]["lane"] == INTEGRATION
    assert adopted["items"][0]["owner"] == "supervisor-integration"


def test_unavailable_gitlab_facts_fail_closed_with_an_explicit_reason(tmp_path: Path):
    listed_mr = _actionable_mr(154)
    listed_mr.pop("approval_facts_available")
    listed_mr.pop("pipeline_facts_available")

    adopted = reconcile(tmp_path, {"open_merge_requests": [listed_mr]})
    assert adopted["items"][0]["lane"] != INTEGRATION
    assert adopted["items"][0]["reason"] == "GitLab approval facts are unavailable"
