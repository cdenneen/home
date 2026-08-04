from dataclasses import dataclass
import shlex
from typing import Any


SEMANTIC_CLASSES = {
    "Executable",
    "Running",
    "Blocked",
    "Waiting",
    "Integrated",
    "Superseded",
    "Completed",
    "Invalid",
    "Revalidation",
    "Unknown",
}
SLICE_CATEGORIES = {
    "research",
    "audit",
    "preparation",
    "tests",
    "fixtures",
    "instrumentation",
    "documentation",
    "ci",
    "convergence",
    "benchmark",
    "negative-test",
    "compatibility",
    "migration-rehearsal",
    "evidence",
    "implementation",
}
ALLOWED_TEST_PREFIXES = {
    ("pytest",),
    ("ruff", "check"),
    ("uv", "run", "pytest"),
    ("uv", "run", "ruff", "check"),
    ("uv", "run", "--extra", "dev", "pytest"),
    ("uv", "run", "--extra", "dev", "ruff", "check"),
    ("nix", "build"),
    ("nix", "flake", "check"),
}


@dataclass(frozen=True)
class CandidateSlice:
    slice_id: str
    title: str
    category: str
    result: str
    rationale: str
    project: str | None = None
    allowed_paths: tuple[str, ...] = ()
    required_tests: tuple[str, ...] = ()


def require_list(value: Any, field: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def test_command_argv(command: str) -> list[str]:
    if any(value in command for value in ("\n", ";", "|", "&", ">", "<", "$", "`")):
        raise ValueError("test command contains shell control syntax")
    argv = shlex.split(command)
    if not argv:
        raise ValueError("test command is empty")
    if not any(tuple(argv[: len(prefix)]) == prefix for prefix in ALLOWED_TEST_PREFIXES):
        raise ValueError(f"test command is not allowlisted: {command}")
    return argv


def validate_semantic_record(value: dict) -> dict:
    required = {
        "schema",
        "schema_version",
        "target_ref",
        "source_fingerprint",
        "evidence_fingerprint",
        "candidate_slices",
        "evidence_inspected",
        "permitted_actions",
        "prohibited_actions",
        "direct_blocker",
        "transitive_blocker_chain",
        "authority_source",
        "authority_resolution",
        "next_state_changing_event",
        "revalidated_at",
    }
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"semantic record missing fields: {missing}")
    if value["schema"] != "axis.external-development-supervisor.semantic-record":
        raise ValueError("unsupported semantic record schema")
    if value["schema_version"] != "1.0.0":
        raise ValueError("unsupported semantic record schema_version")
    for field in (
        "candidate_slices",
        "evidence_inspected",
        "permitted_actions",
        "prohibited_actions",
        "transitive_blocker_chain",
    ):
        require_list(value[field], field)
    if not value["evidence_inspected"]:
        raise ValueError("semantic record requires source evidence")
    if not isinstance(value["source_fingerprint"], str) or not value["source_fingerprint"]:
        raise ValueError("semantic record requires source_fingerprint")
    if not isinstance(value["evidence_fingerprint"], str) or not value["evidence_fingerprint"]:
        raise ValueError("semantic record requires evidence_fingerprint")
    for candidate in value["candidate_slices"]:
        if candidate.get("result") not in {"Executable", "Waiting", "Blocked", "Invalid"}:
            raise ValueError("candidate slice has invalid result")
        if not candidate.get("slice_id") or not candidate.get("rationale"):
            raise ValueError("candidate slice requires slice_id and rationale")
        if candidate.get("category") not in SLICE_CATEGORIES:
            raise ValueError("candidate slice has invalid category")
        for command in require_list(candidate.get("required_tests") or [], "required_tests"):
            test_command_argv(command)
    resolution = value.get("authority_resolution")
    if not isinstance(resolution, dict):
        raise ValueError("authority_resolution must be an object")
    if resolution.get("state") not in {
        "inherited",
        "preparation-only",
        "needs-product-owner",
        "needs-governance",
        "prohibited",
        "unresolved",
    }:
        raise ValueError("semantic workers cannot assert direct authority")
    if resolution.get("state") == "needs-product-owner":
        packet = value.get("decision_packet")
        required_packet = {
            "current_record",
            "current_digest",
            "decision_requested",
            "recommendation",
            "consequences",
            "downstream_effects",
            "unresolved_assumptions",
            "response_syntax",
        }
        if not isinstance(packet, dict) or not required_packet.issubset(packet):
            raise ValueError("Product Owner authority records require a complete decision_packet")
    return value


def validate_assignment(value: dict) -> dict:
    required = {
        "assignment_id",
        "state",
        "phase",
        "project",
        "work_item",
        "planning_record",
        "allowed_paths",
        "required_tests",
    }
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"assignment missing fields: {missing}")
    if value["state"] not in {
        "ready",
        "active",
        "waiting",
        "blocked",
        "complete",
        "cancelled",
        "failed",
    }:
        raise ValueError("assignment has invalid state")
    if value["phase"] not in {
        "semantic",
        "semantic-complete",
        "implementation",
        "awaiting-integration",
        "integration",
        "integrated",
        "failed",
        "recovery",
    }:
        raise ValueError("assignment has invalid phase")
    require_list(value["allowed_paths"], "allowed_paths")
    require_list(value["required_tests"], "required_tests")
    return value
