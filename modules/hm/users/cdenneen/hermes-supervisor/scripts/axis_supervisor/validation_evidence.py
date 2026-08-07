import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema_registry import validate_record


SCHEMA = "axis.external-development-supervisor.validation-evidence"
SCHEMA_VERSION = "1.0.0"


class ValidationEvidenceStore:
    """Content-addressed, append-only validation evidence and findings."""

    def __init__(self, root: Path):
        self.root = root / "validation-evidence"

    def persist(self, stream: str, evidence: dict[str, Any]) -> dict[str, Any]:
        findings = [
            dict(value) if isinstance(value, dict) else str(value)
            for value in evidence.get("findings") or []
        ]
        immutable = {
            "stream": stream,
            "evidence": evidence,
            "findings": sorted(
                findings,
                key=lambda value: json.dumps(value, sort_keys=True),
            ),
        }
        evidence_id = "sha256:" + hashlib.sha256(
            json.dumps(immutable, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        record = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "evidence_id": evidence_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            **immutable,
        }
        validate_record(record, SCHEMA)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{evidence_id.removeprefix('sha256:')}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
        except FileExistsError:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if {
                "stream": existing.get("stream"),
                "evidence": existing.get("evidence"),
                "findings": existing.get("findings"),
            } != immutable:
                raise RuntimeError(f"immutable validation evidence conflict: {evidence_id}")
            record = existing
        return {
            "evidence_id": evidence_id,
            "uri": path.resolve().as_uri(),
            "findings": record["findings"],
        }
