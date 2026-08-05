from dataclasses import dataclass
import shlex
from typing import Any

from .verification import CHECK_NAMES, VERIFICATION_STANDARD


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
    verification = value.get("verification_result")
    if verification is not None:
        if not isinstance(verification, dict):
            raise ValueError("verification_result must be an object")
        if verification.get("standard") != VERIFICATION_STANDARD:
            raise ValueError("verification_result uses an unsupported standard")
        if verification.get("tier") not in {"A", "B", "C", "D"}:
            raise ValueError("verification_result has an invalid tier")
        if verification.get("disposition") not in {
            "verified-complete",
            "active-technical-revalidation",
            "corrective-implementation-required",
            "human-authority-required",
        }:
            raise ValueError("verification_result has an invalid disposition")
        checks = verification.get("checks")
        if not isinstance(checks, dict) or set(checks) != set(CHECK_NAMES):
            raise ValueError("verification_result must contain all nine checks")
        if any(value not in {True, False, None} for value in checks.values()):
            raise ValueError("verification checks must be true, false, or null")
        verification_evidence = require_list(
            verification.get("evidence") or [], "verification evidence"
        )
        if any(
            not (
                isinstance(evidence, str)
                and evidence.strip()
                or isinstance(evidence, dict)
                and isinstance(evidence.get("ref"), str)
                and evidence["ref"].strip()
            )
            for evidence in verification_evidence
        ):
            raise ValueError("verification evidence must contain source-linked references")
        failed_checks = require_list(
            verification.get("failed_checks") or [], "verification failed_checks"
        )
        expected_failed = {name for name in CHECK_NAMES if checks.get(name) is not True}
        if set(failed_checks) != expected_failed:
            raise ValueError("verification failed_checks do not match nine-check results")
        if verification["disposition"] == "verified-complete":
            if expected_failed:
                raise ValueError("verified-complete requires all nine checks")
            if not verification_evidence:
                raise ValueError("verified-complete requires source-linked evidence")
            if str(verification.get("failure_disposition") or "").strip():
                raise ValueError("verified-complete cannot include a failure disposition")
        elif not str(verification.get("failure_disposition") or "").strip():
            raise ValueError("incomplete verification requires a failure disposition")
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
