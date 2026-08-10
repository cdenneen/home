import hashlib
import json
import re
from pathlib import Path

from .canonical_work_item import projection_for
from .models import validate_semantic_record
from .mutation import MutationGate, OperationClass
from .repository_ownership import responsibility_for_repository
from .schema_registry import write_record


class SemanticDecompositionEngine:
    def __init__(self, root: Path, gate: MutationGate | None = None):
        self.root = root
        self.records = root / "decompositions"
        self.records.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.evidence = root / "decomposition-evidence"
        self.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.gate = gate or MutationGate(root, source="worker")

    @staticmethod
    def filename(ref: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", ref).strip("_") + ".json"

    @staticmethod
    def source_fingerprint(item: dict) -> str:
        evidence = dict(item.get("source_evidence") or {})
        # Ordinary discussion is retained for audit but cannot churn semantic work.
        evidence = {
            "description": evidence.get("description"),
            "notes_state": evidence.get("notes_state"),
            "notes_collector_revision": evidence.get("notes_collector_revision"),
            "canonical_finding_state": evidence.get("canonical_finding_state"),
            "authority_notes": list(evidence.get("authority_notes") or [])[:100],
            "parent_refs": evidence.get("parent_refs"),
            "related_mr_urls": evidence.get("related_mr_urls"),
        }
        payload = {
            "ref": item.get("ref"),
            "source_kind": item.get("source_kind") or item.get("kind"),
            "source_state": item.get("source_state") or item.get("state"),
            "labels": item.get("labels"),
            "milestone": item.get("milestone"),
            "authority_facts": projection_for(item).get("authority_facts") or item.get("authority_facts") or item.get("authority"),
            "blocking_dependency_refs": item.get("blocking_dependency_refs")
            or item.get("dependencies"),
            "merge_request_facts": item.get("merge_request_facts")
            or item.get("merge_requests"),
            "acceptance_facts": item.get("acceptance_facts"),
            "updated_at": item.get("updated_at"),
            "source_evidence": evidence,
            "findings": item.get("findings") or [],
            "repository_head": item.get("repository_head"),
            "retrieval_errors": item.get("retrieval_errors"),
            "mutation_allowed": item.get("mutation_allowed"),
            "convergence_facts": item.get("convergence_facts"),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def legacy_source_fingerprint(item: dict) -> str:
        payload = {
            "ref": item.get("ref"),
            "classification": item.get("classification"),
            "authority": item.get("authority"),
            "dependencies": item.get("dependencies"),
            "updated_at": item.get("updated_at"),
            "merge_requests": item.get("merge_requests"),
            "source_evidence": item.get("source_evidence"),
            "repository_head": item.get("repository_head"),
            "local": item.get("local"),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def load(
        self,
        ref: str,
        source_fingerprint: str | None = None,
        compatibility_fingerprints: set[str] | None = None,
    ) -> dict | None:
        path = self.records / self.filename(ref)
        if not path.exists():
            return None
        record = validate_semantic_record(
            json.loads(path.read_text(encoding="utf-8"))
        )
        accepted = {source_fingerprint, *(compatibility_fingerprints or set())} - {None}
        if accepted and record.get("source_fingerprint") not in accepted:
            return None
        evidence_path = self.evidence / self.filename(ref)
        if not evidence_path.exists():
            raise ValueError(f"semantic evidence is missing: {evidence_path}")
        evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        if record.get("evidence_fingerprint") != evidence_hash:
            raise ValueError(f"semantic evidence fingerprint mismatch: {ref}")
        return record

    def save(self, value: dict) -> Path:
        record = validate_semantic_record(value)
        path = self.records / self.filename(record["target_ref"])
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(path, record, "axis.external-development-supervisor.semantic-record")
        return path

    def save_evidence(self, ref: str, value: dict) -> str:
        path = self.evidence / self.filename(ref)
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        payload = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_bytes(payload)
        tmp.chmod(0o600)
        tmp.replace(path)
        return hashlib.sha256(payload).hexdigest()

    def pending_item(self, item: dict) -> dict:
        responsibility = responsibility_for_repository(
            item.get("project"), context=f"semantic-decomposition:{item['ref']}"
        )
        return {
            "ref": f"semantic-decomposition:{item['ref']}",
            "kind": "semantic-decomposition",
            "assignment_type": "read-only-analysis",
            "target_ref": item["ref"],
            "project": item.get("project"),
            "responsibility": responsibility,
            "title": f"Semantically decompose {item['ref']}: {item.get('title')}",
            "classification": "Executable",
            "ranking_score": 250,
            "authority": {
                "state": "preparation-only",
                "reason": "non-mutating research/audit is delegated",
            },
            "source_item": item,
        }
