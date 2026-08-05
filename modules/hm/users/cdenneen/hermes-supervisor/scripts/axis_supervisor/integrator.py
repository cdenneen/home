import json
import subprocess
from urllib.parse import quote

from .mutation import GateDecision, MutationGate, OperationClass


class Integrator:
    def __init__(self, glab: str, host: str = "gitlab.com"):
        self.glab = glab
        self.host = host

    def api(self, path: str):
        output = subprocess.check_output(
            [self.glab, "api", "--hostname", self.host, path],
            text=True,
            timeout=90,
        )
        return json.loads(output)

    def inspect_mr(
        self,
        project: str,
        iid: int,
        *,
        expected_source_branch: str | None = None,
        expected_sha: str | None = None,
    ) -> dict:
        encoded = quote(project, safe="")
        mr = self.api(f"projects/{encoded}/merge_requests/{iid}")
        discussions = self.api(f"projects/{encoded}/merge_requests/{iid}/discussions")
        approvals = self.api(f"projects/{encoded}/merge_requests/{iid}/approvals")
        pipeline = mr.get("head_pipeline") or {}
        unresolved_discussions = [
            discussion
            for discussion in discussions
            if any(
                note.get("resolvable") and not note.get("resolved")
                for note in discussion.get("notes") or []
            )
        ]
        return {
            "mr": mr,
            "discussions": discussions,
            "approvals": approvals,
            "pipeline": pipeline,
            "unresolved_discussions": unresolved_discussions,
            "review_pending": bool(
                mr.get("draft")
                or int(approvals.get("approvals_left") or 0) > 0
                or unresolved_discussions
            ),
            "merge_ready": bool(
                mr.get("state") == "opened"
                and not mr.get("draft")
                and not mr.get("has_conflicts")
                and mr.get("target_branch") == "main"
                and (
                    expected_source_branch is None
                    or mr.get("source_branch") == expected_source_branch
                )
                and (expected_sha is None or mr.get("sha") == expected_sha)
                and pipeline.get("status") == "success"
                and int(approvals.get("approvals_left") or 0) == 0
                and not unresolved_discussions
            ),
        }

    def merge_mr(
        self,
        project: str,
        iid: int,
        assignment: dict,
        gate: MutationGate,
        decision: GateDecision,
    ) -> dict:
        worker = assignment.get("worker") or {}
        inspection = self.inspect_mr(
            project,
            iid,
            expected_source_branch=worker.get("branch"),
            expected_sha=worker.get("commit"),
        )
        if not inspection["merge_ready"]:
            raise RuntimeError("merge request is not ready for deterministic integration")
        gate.require(
            decision,
            OperationClass.GITLAB,
            assignment=assignment,
            repository=project,
            effect="merge-reviewed-mr"
            if assignment.get("mutation_grant_id")
            else None,
        )
        encoded = quote(project, safe="")
        output = subprocess.check_output(
            [
                self.glab,
                "api",
                "--hostname",
                self.host,
                "--method",
                "PUT",
                "--field",
                f"sha={worker.get('commit')}",
                f"projects/{encoded}/merge_requests/{iid}/merge",
            ],
            text=True,
            timeout=120,
        )
        return json.loads(output)
