import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


SCHEMA_FILES = {
    "axis.external-development-supervisor.active-mission": "active-mission.schema.json",
    "axis.external-development-supervisor.assignment": "assignment.schema.json",
    "axis.external-development-supervisor.canary-grant": "canary-grant.schema.json",
    "axis.external-development-supervisor.control": "control.schema.json",
    "axis.external-development-supervisor.decision": "decision.schema.json",
    "axis.external-development-supervisor.decision-card": "decision-card.schema.json",
    "axis.external-development-supervisor.decision-frontier-request": "decision-frontier-request.schema.json",
    "axis.external-development-supervisor.delivery-board": "delivery-board.schema.json",
    "axis.external-development-supervisor.executable-frontier": "executable-frontier.schema.json",
    "axis.external-development-supervisor.external-implementation-adoptions": "external-implementation-adoptions.schema.json",
    "axis.external-development-supervisor.execution-graph": "execution-graph.schema.json",
    "axis.external-development-supervisor.implementation-handoff": "implementation-handoff.schema.json",
    "axis.external-development-supervisor.independent-review-output": "independent-review.schema.json",
    "axis.external-development-supervisor.integration-queue": "integration-queue.schema.json",
    "axis.external-development-supervisor.inventory": "inventory.schema.json",
    "axis.external-development-supervisor.lease": "lease.schema.json",
    "axis.external-development-supervisor.model-attempt": "model-attempt.schema.json",
    "axis.external-development-supervisor.mutation-grant": "mutation-grant.schema.json",
    "axis.external-development-supervisor.operational-event": "operational-event.schema.json",
    "axis.external-development-supervisor.observability-health": "observability-health.schema.json",
    "axis.external-development-supervisor.roadmap-semantics": "roadmap-semantics.schema.json",
    "axis.external-development-supervisor.roadmap-quality": "roadmap-quality.schema.json",
    "axis.external-development-supervisor.repository-convergence": "repository-convergence.schema.json",
    "axis.external-development-supervisor.review-evidence": "review-evidence.schema.json",
    "axis.external-development-supervisor.capability-convergence": "capability-convergence.schema.json",
    "axis.external-development-supervisor.capability-graduation": "capability-graduation.schema.json",
    "axis.external-development-supervisor.run": "run.schema.json",
    "axis.external-development-supervisor.semantic-record": "semantic-record.schema.json",
    "axis.external-development-supervisor.slack-state": "slack-state.schema.json",
    "axis.external-development-supervisor.slack-outbox": "slack-outbox.schema.json",
    "axis.external-development-supervisor.validation-evidence": "validation-evidence.schema.json",
    "axis.external-development-supervisor.validation-finding": "validation-finding.schema.json",
    "axis.external-development-supervisor.verification": "verification.schema.json",
}
SOURCE_SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"
RUNTIME_SCHEMAS = Path(
    os.environ.get(
        "AXIS_SUPERVISOR_SCHEMA_DIR",
        Path.home()
        / ".hermes"
        / "supervisor"
        / "axis-development-supervisor"
        / "schemas",
    )
)


class RecordError(ValueError):
    pass


class CorruptRecordError(RecordError):
    pass


class PartialRecordError(RecordError):
    pass


class RecordVersionError(RecordError):
    pass


class UnexpectedSchemaError(RecordError):
    pass


def _schema_directory(record_path: Path | None = None) -> Path:
    configured = os.environ.get("AXIS_SUPERVISOR_SCHEMA_DIR")
    if configured:
        return Path(configured)
    if record_path is not None:
        for parent in record_path.resolve().parents:
            candidate = parent / "schemas"
            if candidate.is_dir():
                return candidate
    return SOURCE_SCHEMAS if SOURCE_SCHEMAS.is_dir() else RUNTIME_SCHEMAS


@lru_cache(maxsize=32)
def _load_schema(schema_id: str, directory: str) -> dict[str, Any]:
    filename = SCHEMA_FILES.get(schema_id)
    if filename is None:
        raise UnexpectedSchemaError(f"unregistered schema: {schema_id}")
    path = Path(directory) / filename
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorruptRecordError(f"cannot load schema {schema_id}: {path}: {exc}") from exc
    Draft202012Validator.check_schema(value)
    return value


@lru_cache(maxsize=8)
def _schema_registry(directory: str) -> Registry:
    registry = Registry()
    for filename in sorted(set(SCHEMA_FILES.values())):
        value = json.loads((Path(directory) / filename).read_text(encoding="utf-8"))
        registry = registry.with_resource(
            str(value["$id"]), Resource.from_contents(value)
        )
    return registry


def validate_record(
    value: Any,
    expected_schema: str,
    *,
    record_path: Path | None = None,
    schema_definition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PartialRecordError("record must be a JSON object")
    actual_schema = value.get("schema")
    if actual_schema != expected_schema:
        raise UnexpectedSchemaError(
            f"expected schema {expected_schema}, found {actual_schema or '<missing>'}"
        )
    version = value.get("schema_version")
    if not version:
        raise PartialRecordError(f"{expected_schema} schema_version is missing")
    schema = schema_definition or _load_schema(
        expected_schema, str(_schema_directory(record_path))
    )
    expected_version = (
        (schema.get("properties") or {}).get("schema_version") or {}
    ).get("const")
    if expected_version and version != expected_version:
        raise RecordVersionError(
            f"unsupported {expected_schema} schema_version: {version}; expected {expected_version}"
        )
    errors = sorted(
        Draft202012Validator(
            schema,
            registry=_schema_registry(str(_schema_directory(record_path))),
            format_checker=FormatChecker(),
        ).iter_errors(value),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        message = "; ".join(error.message for error in errors[:3])
        if any(error.validator == "required" for error in errors):
            raise PartialRecordError(f"partial {expected_schema} record: {message}")
        raise RecordError(f"invalid {expected_schema} record: {message}")
    return value


def read_record(path: Path, expected_schema: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorruptRecordError(f"cannot read record {path}: {exc}") from exc
    return validate_record(value, expected_schema, record_path=path)


def write_record(path: Path, value: dict[str, Any], expected_schema: str) -> None:
    validate_record(value, expected_schema, record_path=path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)
