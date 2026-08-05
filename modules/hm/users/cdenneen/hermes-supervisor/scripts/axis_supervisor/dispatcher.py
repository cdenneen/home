import json
import time
import uuid
from pathlib import Path

from .models import validate_assignment


class Dispatcher:
    def __init__(self, root: Path):
        self.root = root
        self.assignments = root / "assignments"
        self.assignments.mkdir(mode=0o700, parents=True, exist_ok=True)

    def active(self) -> list[dict]:
        values = []
        for path in self.assignments.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if value.get("state") not in {"complete", "completed", "cancelled", "failed"}:
                values.append(value)
        return values

    def dispatch(self, graph: dict, run_id: str, selected: dict | None = None) -> dict | None:
        if self.active() or (selected is None and not graph.get("executable_queue")):
            return None
        item = selected or graph["executable_queue"][0]
        assignment_id = f"assignment-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        assignment = {
            "schema": "axis.external-development-supervisor.assignment",
            "schema_version": "1.0.0",
            "assignment_id": assignment_id,
            "state": "ready",
            "phase": "semantic"
            if item.get("kind") in {"semantic-decomposition", "technical-revalidation"}
            else "implementation",
            "kind": item.get("kind"),
            "queue_ref": item.get("ref"),
            "target_ref": item.get("target_ref") or item.get("ref"),
            "work_item": item.get("target_ref") or item.get("ref"),
            "project": item.get("project"),
            "title": item.get("title"),
            "authority": item.get("authority"),
            "planning_record": None,
            "candidate": item.get("candidate"),
            "allowed_paths": (item.get("candidate") or {}).get("allowed_paths") or [],
            "required_tests": (item.get("candidate") or {}).get("required_tests") or [],
            "source_item": item.get("source_item"),
            "source_fingerprint": item.get("source_fingerprint"),
            "revalidation_tier": item.get("revalidation_tier"),
            "ranking_factors": item.get("ranking_factors"),
            "created_by_run": run_id,
            "created_at_epoch": int(time.time()),
            "model_attempts": 0,
            "lease": None,
            "worker": None,
            "handoff": None,
        }
        validate_assignment(assignment)
        path = self.assignments / f"{assignment_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(assignment, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
        return assignment
