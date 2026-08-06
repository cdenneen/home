import json

RESPONSIBILITY_TO_CANONICAL_REPOSITORY = {
    "supervisor-orchestration/temporary-slack/cron": "cdenneen/home",
    "axis-runtime/product": "ghostspace/axis",
    "contracts/planning-records": "ghostspace/axis-governance",
    "deployment/realistic-validation": "ghostspace/axis-lab",
}
OWNERSHIP_EVIDENCE_SCOPE = (
    "schema",
    "schema_version",
    "status",
    "responsibility",
    "repository",
    "canonical_repository",
    "reason",
    "responsibility_to_canonical_repository",
)


class RepositoryOwnershipDenied(ValueError):
    def __init__(self, evidence: dict):
        self.evidence = evidence
        super().__init__(
            json.dumps(
                {"error": "repository-ownership-denied", "evidence": evidence},
                sort_keys=True,
            )
        )


def _evidence(
    *,
    status: str,
    context: str,
    responsibility: str | None,
    repository: str | None,
    canonical_repository: str | None,
    reason: str | None,
) -> dict:
    return {
        "schema": "axis.external-development-supervisor.repository-ownership-evidence",
        "schema_version": "1.0.0",
        "status": status,
        "context": context,
        "responsibility": responsibility,
        "repository": repository,
        "canonical_repository": canonical_repository,
        "reason": reason,
        "responsibility_to_canonical_repository": dict(
            RESPONSIBILITY_TO_CANONICAL_REPOSITORY
        ),
    }


def validate_repository_ownership(
    responsibility: str | None,
    repository: str | None,
    *,
    context: str,
) -> dict:
    canonical_repository = RESPONSIBILITY_TO_CANONICAL_REPOSITORY.get(
        str(responsibility or "")
    )
    if canonical_repository is None:
        raise RepositoryOwnershipDenied(
            _evidence(
                status="denied",
                context=context,
                responsibility=responsibility,
                repository=repository,
                canonical_repository=None,
                reason="unknown-or-missing-responsibility",
            )
        )
    if repository != canonical_repository:
        raise RepositoryOwnershipDenied(
            _evidence(
                status="denied",
                context=context,
                responsibility=responsibility,
                repository=repository,
                canonical_repository=canonical_repository,
                reason="repository-does-not-match-responsibility",
            )
        )
    return _evidence(
        status="validated",
        context=context,
        responsibility=responsibility,
        repository=repository,
        canonical_repository=canonical_repository,
        reason=None,
    )


def responsibility_for_repository(repository: str | None, *, context: str) -> str:
    matches = [
        responsibility
        for responsibility, canonical_repository in RESPONSIBILITY_TO_CANONICAL_REPOSITORY.items()
        if repository == canonical_repository
    ]
    if len(matches) != 1:
        raise RepositoryOwnershipDenied(
            _evidence(
                status="denied",
                context=context,
                responsibility=None,
                repository=repository,
                canonical_repository=None,
                reason="repository-is-not-an-exact-canonical-target",
            )
        )
    return matches[0]


def resolve_repository_ownership(
    declarations: list[str | None],
    repository: str | None,
    *,
    context: str,
    allow_repository_inference: bool,
) -> dict:
    explicit = {str(value) for value in declarations if value}
    if len(explicit) > 1:
        raise RepositoryOwnershipDenied(
            _evidence(
                status="denied",
                context=context,
                responsibility=",".join(sorted(explicit)),
                repository=repository,
                canonical_repository=None,
                reason="ambiguous-responsibility-declarations",
            )
        )
    if explicit:
        responsibility = next(iter(explicit))
    elif allow_repository_inference:
        responsibility = responsibility_for_repository(repository, context=context)
    else:
        raise RepositoryOwnershipDenied(
            _evidence(
                status="denied",
                context=context,
                responsibility=None,
                repository=repository,
                canonical_repository=None,
                reason="ambiguous-fallback-denied",
            )
        )
    return validate_repository_ownership(
        responsibility, repository, context=context
    )


def assignment_ownership(
    assignment: dict,
    *,
    context: str,
    allow_repository_inference: bool = False,
) -> dict:
    return resolve_repository_ownership(
        [
            assignment.get("responsibility"),
            (assignment.get("candidate") or {}).get("responsibility"),
        ],
        assignment.get("project"),
        context=context,
        allow_repository_inference=allow_repository_inference,
    )


def ownership_evidence_matches(actual: object, expected: dict) -> bool:
    return isinstance(actual, dict) and all(
        actual.get(key) == expected.get(key) for key in OWNERSHIP_EVIDENCE_SCOPE
    )


def ownership_denial(
    expected: dict,
    *,
    context: str,
    reason: str,
    actual: object,
) -> RepositoryOwnershipDenied:
    evidence = dict(expected)
    evidence.update(
        {
            "status": "denied",
            "context": context,
            "reason": reason,
            "actual_ownership": actual,
        }
    )
    return RepositoryOwnershipDenied(evidence)
