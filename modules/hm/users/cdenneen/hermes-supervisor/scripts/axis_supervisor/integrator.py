import json
import subprocess
from urllib.parse import quote


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

    def inspect_mr(self, project: str, iid: int) -> dict:
        encoded = quote(project, safe="")
        mr = self.api(f"projects/{encoded}/merge_requests/{iid}")
        discussions = self.api(f"projects/{encoded}/merge_requests/{iid}/discussions")
        approvals = self.api(f"projects/{encoded}/merge_requests/{iid}/approvals")
        pipeline = mr.get("head_pipeline") or {}
        return {
            "mr": mr,
            "discussions": discussions,
            "approvals": approvals,
            "pipeline": pipeline,
            "merge_ready": bool(
                mr.get("state") == "opened"
                and not mr.get("has_conflicts")
                and pipeline.get("status") == "success"
                and int(approvals.get("approvals_left") or 0) == 0
            ),
        }
