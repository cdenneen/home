import json
import hashlib
import re
from pathlib import Path

from .models import validate_semantic_record


class SemanticDecompositionEngine:
    def __init__(self, root: Path):
        self.root = root
        self.records = root / "decompositions"
        self.records.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.evidence = root / "decomposition-evidence"
        self.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)

    @staticmethod
    def filename(ref: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", ref).strip("_") + ".json"

    @staticmethod
    def source_fingerprint(item: dict) -> str:
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

    def load(self, ref: str, source_fingerprint: str | None = None) -> dict | None:
        path = self.records / self.filename(ref)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            record = validate_semantic_record(value)
            if source_fingerprint and record.get("source_fingerprint") != source_fingerprint:
                return None
            evidence_path = self.evidence / self.filename(ref)
            if not evidence_path.exists():
                return None
            evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            if record.get("evidence_fingerprint") != evidence_hash:
                return None
            return record
        except Exception:
            return None

    def save(self, value: dict) -> Path:
        record = validate_semantic_record(value)
        path = self.records / self.filename(record["target_ref"])
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
        return path

    def save_evidence(self, ref: str, value: dict) -> str:
        path = self.evidence / self.filename(ref)
        payload = json.dumps(value, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_bytes(payload)
        tmp.chmod(0o600)
        tmp.replace(path)
        return hashlib.sha256(payload).hexdigest()

    def pending_item(self, item: dict) -> dict:
        return {
            "ref": f"semantic-decomposition:{item['ref']}",
            "kind": "semantic-decomposition",
            "target_ref": item["ref"],
            "project": item.get("project"),
            "title": f"Semantically decompose {item['ref']}: {item.get('title')}",
            "classification": "Executable",
            "ranking_score": 250,
            "authority": {
                "state": "preparation-only",
                "reason": "non-mutating research/audit is delegated",
            },
            "source_item": item,
        }
