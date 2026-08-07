import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import test_command_argv, validate_allowed_path
from .mutation import MutationGate, OperationClass
from .repository_ownership import resolve_repository_ownership
from .schema_registry import read_record, write_record

FINDING_SCHEMA = "axis.external-development-supervisor.validation-finding"
ADOPTION_SCHEMA = (
    "axis.external-development-supervisor.external-implementation-adoptions"
)
FINDING_CLASSIFICATIONS = frozenset(
    {
        "EVIDENCE_ONLY",
        "CONFIGURATION",
        "DEPLOYMENT",
        "PRODUCT_DEFECT",
        "ROADMAP_GAP",
        "AUTHORITY_BLOCKED",
        "EXTERNAL_BLOCKED",
    }
)
MUTATING_CLASSIFICATIONS = frozenset({"PRODUCT_DEFECT", "ROADMAP_GAP"})

ADOPTION_DEFAULTS = (
    Path(__file__).resolve().parents[2]
    / "external-implementation-adoptions.defaults.json"
)


def _read_adoption_seeds(path: Path = ADOPTION_DEFAULTS) -> tuple[dict[str, Any], ...]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "1.0.0" or not isinstance(
        value.get("records"), list
    ):
        raise ValueError("unsupported external implementation adoption defaults")
    return tuple(dict(record) for record in value["records"])


EXTERNAL_IMPLEMENTATION_SEEDS = _read_adoption_seeds()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def classify_validation_finding(value: dict[str, Any]) -> str:
    explicit = str(value.get("classification") or "").upper()
    if explicit:
        if explicit not in FINDING_CLASSIFICATIONS:
            raise ValueError(f"unsupported validation finding classification: {explicit}")
        return explicit
    gate = str(value.get("gate") or "").lower()
    kind = str(value.get("kind") or value.get("type") or "").lower()
    summary = str(value.get("summary") or "").lower()
    text = " ".join((gate, kind, summary))
    if any(marker in text for marker in ("external", "vendor", "upstream")):
        return "EXTERNAL_BLOCKED"
    if any(marker in text for marker in ("authority", "approval", "governance")):
        return "AUTHORITY_BLOCKED"
    if any(marker in text for marker in ("roadmap", "missing capability", "scope gap")):
        return "ROADMAP_GAP"
    if gate == "deployment" or "deploy" in text:
        return "DEPLOYMENT"
    if any(marker in text for marker in ("config", "configuration", "credential")):
        return "CONFIGURATION"
    if any(marker in text for marker in ("defect", "bug", "incorrect", "broken")):
        return "PRODUCT_DEFECT"
    return "EVIDENCE_ONLY"


def compact_origin(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if not record:
        return None
    return {
        "finding_id": record["finding_id"],
        "fingerprint": record["fingerprint"],
        "classification": record["classification"],
        "capability": record.get("capability"),
        "gate": record.get("gate"),
        "gate_resolution": record.get("gate_resolution"),
        "stream": record["stream"],
    }


def _planning_record(value: dict[str, Any], owner: dict[str, Any] | None) -> dict | None:
    supplied = value.get("planning_record")
    if isinstance(supplied, dict) and supplied.get("digest") and supplied.get(
        "approval_note"
    ):
        return {
            "revision": int(supplied.get("revision") or 1),
            "digest": str(supplied["digest"]),
            "approval_note": str(supplied["approval_note"]),
        }
    facts = (owner or {}).get("authority_facts") or {}
    if not (
        facts.get("approval_matches_record")
        and facts.get("record_digest")
        and facts.get("approval_note")
    ):
        return None
    return {
        "revision": int(facts.get("record_revision") or 1),
        "digest": str(facts["record_digest"]),
        "approval_note": str(facts["approval_note"]),
    }


def resolve_existing_owner(
    value: dict[str, Any], inventory: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    items = list((inventory or {}).get("work_items") or [])
    by_ref = {str(item.get("ref")): item for item in items if item.get("ref")}
    declared = [
        str(ref)
        for ref in [value.get("owner_ref"), *(value.get("owner_refs") or [])]
        if ref
    ]
    candidates = [ref for ref in dict.fromkeys(declared) if ref in by_ref]
    if candidates:
        owner_ref = candidates[0]
        return (
            {
                "status": "existing-owner",
                "owner_ref": owner_ref,
                "candidate_refs": candidates,
                "child_required": False,
            },
            by_ref[owner_ref],
        )
    if classify_validation_finding(value) not in MUTATING_CLASSIFICATIONS:
        return (
            {
                "status": "not-required",
                "owner_ref": None,
                "candidate_refs": [],
                "child_required": False,
            },
            None,
        )
    return (
        {
            "status": "child-required",
            "owner_ref": None,
            "candidate_refs": [],
            "child_required": True,
        },
        None,
    )


def _decision_packet(
    finding_id: str,
    fingerprint: str,
    value: dict[str, Any],
    owner_resolution: dict[str, Any],
) -> dict[str, Any]:
    return {
        "decision_id": f"{finding_id}-authority",
        "current_record": owner_resolution.get("owner_ref") or finding_id,
        "current_digest": fingerprint,
        "decision_requested": (
            "Approve the exact bounded validation repair scope and owner, or provide "
            "an existing controlling GitLab work item."
        ),
        "recommendation": "Reuse an existing owner before authorizing any child work item.",
        "consequences": "Approval permits only the declared paths, tests, and targeted replay.",
        "downstream_effects": ["finding frontier promotion", "targeted validation replay"],
        "unresolved_assumptions": [
            "existing owner resolution is complete"
            if owner_resolution["status"] == "existing-owner"
            else "no existing controlling owner was found"
        ],
        "response_syntax": f"Approve exact digest {fingerprint}",
        "proposed_scope": {
            "repository": value.get("repository"),
            "allowed_paths": value.get("allowed_paths") or [],
            "required_tests": value.get("required_tests") or [],
        },
    }


class ExternalImplementationAdoptions:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "external-implementation-adoptions.json"
        self.gate = MutationGate(root, source="finding-reconciler")
        defaults = root / "external-implementation-adoptions.defaults.json"
        self.seeds = _read_adoption_seeds(
            defaults if defaults.exists() else ADOPTION_DEFAULTS
        )

    def _authorize(self) -> None:
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            return read_record(self.path, ADOPTION_SCHEMA)
        return {
            "schema": ADOPTION_SCHEMA,
            "schema_version": "1.0.0",
            "updated_at": utc_now(),
            "records": [],
        }

    def reconcile(self, inventory: dict[str, Any]) -> dict[str, Any]:
        previous = {record["mr_ref"]: record for record in self.load()["records"]}
        mr_facts: dict[str, dict[str, Any]] = {}
        for mr in [
            *(inventory.get("open_merge_requests") or []),
            *(inventory.get("external_implementation_merge_requests") or []),
        ]:
            ref = f"{mr.get('project')}!{mr.get('iid')}"
            mr_facts[ref] = mr
        for item in inventory.get("work_items") or []:
            for mr in item.get("merge_request_facts") or []:
                ref = f"{item.get('project')}!{mr.get('iid')}"
                mr_facts.setdefault(ref, mr)
        now = utc_now()
        records = []
        for seed in self.seeds:
            fact = mr_facts.get(seed["mr_ref"]) or {}
            prior = previous.get(seed["mr_ref"]) or {}
            state = str(prior.get("state") or seed["initial_state"])
            mr_state = str(fact.get("state") or "")
            pipeline = str(
                fact.get("pipeline_status")
                or (fact.get("head_pipeline") or {}).get("status")
                or ""
            )
            if mr_state == "merged" and state not in {"replay-pending", "verified"}:
                state = "merged-awaiting-replay"
            elif mr_state == "opened" and pipeline in {"failed", "canceled"}:
                state = "blocked"
            elif mr_state == "opened":
                state = "awaiting-integration"
            records.append(
                {
                    "record_type": "EXTERNAL_IMPLEMENTATION_ADOPTED",
                    "mr_ref": seed["mr_ref"],
                    "mr_url": str(fact.get("web_url") or seed["mr_url"]),
                    "repository": "ghostspace/axis",
                    "capabilities": seed["capabilities"],
                    "state": state,
                    "source_branch": fact.get("source_branch")
                    or prior.get("source_branch"),
                    "head_sha": fact.get("sha") or prior.get("head_sha"),
                    "pipeline_status": pipeline or prior.get("pipeline_status"),
                    "owner_refs": seed["owner_refs"],
                    "updated_at": now,
                }
            )
        value = {
            "schema": ADOPTION_SCHEMA,
            "schema_version": "1.0.0",
            "updated_at": now,
            "records": records,
        }
        self._authorize()
        write_record(self.path, value, ADOPTION_SCHEMA)
        return value

    def matching(self, capability: str | None) -> dict[str, Any] | None:
        if not capability:
            return None
        return next(
            (
                record
                for record in self.load()["records"]
                if capability in record["capabilities"] and record["state"] != "verified"
            ),
            None,
        )


class ValidationFindingStore:
    def __init__(self, root: Path):
        self.root = root
        self.directory = root / "validation-findings"
        self.lock_path = root / "validation-findings.lock"
        self.gate = MutationGate(root, source="finding-reconciler")
        self.adoptions = ExternalImplementationAdoptions(root)

    def _authorize(self) -> None:
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)

    def _path(self, finding_id: str) -> Path:
        return self.directory / f"{finding_id}.json"

    def load(self, finding_id: str) -> dict[str, Any] | None:
        path = self._path(finding_id)
        return read_record(path, FINDING_SCHEMA) if path.exists() else None

    def all(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        return [
            read_record(path, FINDING_SCHEMA)
            for path in sorted(self.directory.glob("validation-finding-*.json"))
        ]

    @staticmethod
    def identity(stream: str, value: dict[str, Any]) -> tuple[str, str]:
        identity = {
            "stream": stream,
            "classification": classify_validation_finding(value),
            "summary": str(value.get("summary") or value),
            "capability": value.get("capability"),
            "gate": value.get("gate"),
            "repository": value.get("repository"),
            "allowed_paths": sorted(set(value.get("allowed_paths") or [])),
        }
        fingerprint = _digest(identity)
        return "validation-finding-" + fingerprint.removeprefix("sha256:")[:24], fingerprint

    def promote(
        self,
        stream: str,
        evidence: dict[str, Any],
        value: dict[str, Any] | str,
        inventory: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        finding = {"summary": value} if isinstance(value, str) else dict(value)
        summary = str(finding.get("summary") or "").strip()
        if not summary:
            raise ValueError("validation finding summary is required")
        finding["summary"] = summary
        classification = classify_validation_finding(finding)
        finding_id, fingerprint = self.identity(stream, finding)
        owner_resolution, owner = resolve_existing_owner(finding, inventory)
        repository = finding.get("repository") or (owner or {}).get("project")
        responsibility = finding.get("responsibility")
        if repository and not responsibility:
            responsibility = resolve_repository_ownership(
                [],
                repository,
                context=f"validation-finding:{finding_id}",
                allow_repository_inference=True,
            )["responsibility"]
        planning = _planning_record(finding, owner)
        authority = {
            "state": "direct" if planning else "needs-product-owner",
            "source": [planning["approval_note"]] if planning else [],
            "reason": "exact approved PlanningRecord"
            if planning
            else "exact PlanningRecord approval is missing",
        }
        allowed_paths = sorted(
            {validate_allowed_path(path) for path in finding.get("allowed_paths") or []}
        )
        required_tests = list(dict.fromkeys(finding.get("required_tests") or []))
        for command in required_tests:
            test_command_argv(command)
        replay_input = finding.get("replay") or {}
        replay = (
            {
                "stream": str(replay_input.get("stream") or stream),
                "capability": finding.get("capability"),
                "gate": finding.get("gate"),
                "required_tests": list(
                    dict.fromkeys(replay_input.get("required_tests") or required_tests)
                ),
                "attempt": 0,
                "state": "not-scheduled",
                "scheduled_at": None,
                "completed_at": None,
                "evidence": [],
            }
            if classification in MUTATING_CLASSIFICATIONS
            else None
        )
        executable = bool(
            classification in MUTATING_CLASSIFICATIONS
            and owner_resolution["status"] == "existing-owner"
            and planning
            and repository
            and responsibility
            and allowed_paths
            and required_tests
            and (classification != "ROADMAP_GAP" or finding.get("roadmap_authorized"))
        )
        adoption = self.adoptions.matching(finding.get("capability"))
        status = (
            "EXTERNAL_IMPLEMENTATION_ADOPTED"
            if adoption and classification in MUTATING_CLASSIFICATIONS
            else "EXECUTABLE"
            if executable
            else "DECISION_REQUIRED"
            if classification in {"ROADMAP_GAP", "AUTHORITY_BLOCKED"}
            or (classification == "PRODUCT_DEFECT" and not executable)
            else "EXTERNAL_BLOCKED"
            if classification == "EXTERNAL_BLOCKED"
            else "ACTION_REQUIRED"
            if classification in {"CONFIGURATION", "DEPLOYMENT"}
            else "EVIDENCE_ONLY"
        )
        now = utc_now()
        evidence_ref = {
            "evidence_id": str(evidence["evidence_id"]),
            "uri": str(evidence["uri"]),
            "observed_at": now,
        }
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.lock_path.open("a", encoding="utf-8") as lock:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            existing = self.load(finding_id)
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise RuntimeError(f"validation finding identity conflict: {finding_id}")
                changed = False
                if evidence_ref["evidence_id"] not in {
                    item["evidence_id"] for item in existing["evidence"]
                }:
                    existing["evidence"].append(evidence_ref)
                    changed = True
                    if existing["status"] == "CLOSED":
                        existing["status"] = "REOPENED"
                        existing["history"].append(
                            {"event": "finding-reopened", "at": now, "reason": "new-evidence"}
                        )
                if existing["status"] in {
                    "DECISION_REQUIRED",
                    "EXTERNAL_IMPLEMENTATION_ADOPTED",
                } and (executable or adoption):
                    existing["owner_resolution"] = owner_resolution
                    existing["repository"] = repository
                    existing["responsibility"] = responsibility
                    existing["planning_record"] = planning
                    existing["authority"] = authority
                    existing["allowed_paths"] = allowed_paths
                    existing["required_tests"] = required_tests
                    existing["targeted_replay"] = replay
                    existing["external_implementation"] = adoption
                    next_status = (
                        "EXTERNAL_IMPLEMENTATION_ADOPTED" if adoption else "EXECUTABLE"
                    )
                    if existing["status"] != next_status:
                        existing["history"].append(
                            {
                                "event": "finding-authority-resolved",
                                "at": now,
                                "status": next_status,
                            }
                        )
                    existing["status"] = next_status
                    existing["decision_packet"] = None if executable else existing.get(
                        "decision_packet"
                    )
                    changed = True
                if changed:
                    existing["updated_at"] = now
                    self._authorize()
                    write_record(self._path(finding_id), existing, FINDING_SCHEMA)
                return existing
            decision_packet = (
                _decision_packet(finding_id, fingerprint, finding, owner_resolution)
                if classification in MUTATING_CLASSIFICATIONS and not executable
                else None
            )
            record = {
                "schema": FINDING_SCHEMA,
                "schema_version": "1.0.0",
                "finding_id": finding_id,
                "fingerprint": fingerprint,
                "classification": classification,
                "status": status,
                "summary": summary,
                "stream": stream,
                "capability": finding.get("capability"),
                "gate": finding.get("gate"),
                "gate_resolution": "pending",
                "repository": repository,
                "responsibility": responsibility,
                "owner_resolution": owner_resolution,
                "authority": authority,
                "planning_record": planning,
                "decision_packet": decision_packet,
                "allowed_paths": allowed_paths,
                "required_tests": required_tests,
                "targeted_replay": replay,
                "evidence": [evidence_ref],
                "external_implementation": adoption,
                "assignment_id": None,
                "created_at": now,
                "updated_at": now,
                "history": [{"event": "finding-promoted", "at": now, "status": status}],
            }
            self._authorize()
            write_record(self._path(finding_id), record, FINDING_SCHEMA)
            return record

    def promote_evidence(
        self,
        stream: str,
        evidence: dict[str, Any],
        findings: list[dict[str, Any] | str],
        inventory: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return [self.promote(stream, evidence, value, inventory) for value in findings]

    def reconcile_observed(
        self, stream: str, observed_finding_ids: set[str]
    ) -> list[dict[str, Any]]:
        closed = []
        for record in self.all():
            if (
                record.get("stream") != stream
                or record["finding_id"] in observed_finding_ids
                or record.get("status") == "CLOSED"
                or record.get("classification")
                not in {"EVIDENCE_ONLY", "CONFIGURATION", "DEPLOYMENT"}
            ):
                continue

            def close(value: dict[str, Any]) -> None:
                now = utc_now()
                value["status"] = "CLOSED"
                value["gate_resolution"] = "passed"
                value["history"].append(
                    {
                        "event": "finding-closed",
                        "at": now,
                        "reason": "no-longer-observed-in-source-stream",
                    }
                )

            closed.append(self._update(record["finding_id"], close))
        return closed

    def _update(self, finding_id: str, mutation) -> dict[str, Any]:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.lock_path.open("a", encoding="utf-8") as lock:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = self.load(finding_id)
            if current is None:
                raise ValueError(f"unknown validation finding: {finding_id}")
            mutation(current)
            current["updated_at"] = utc_now()
            self._authorize()
            write_record(self._path(finding_id), current, FINDING_SCHEMA)
            return current

    def mark_assigned(self, finding_id: str, assignment_id: str) -> dict[str, Any]:
        def mutate(record: dict[str, Any]) -> None:
            if record.get("assignment_id") not in {None, assignment_id}:
                raise RuntimeError("validation finding already has a different assignment")
            record["assignment_id"] = assignment_id
            record["status"] = "ASSIGNED"
            record["history"].append(
                {"event": "finding-assigned", "at": utc_now(), "assignment_id": assignment_id}
            )

        return self._update(finding_id, mutate)

    def mark_integrating(self, finding_id: str, assignment_id: str) -> dict[str, Any]:
        def mutate(record: dict[str, Any]) -> None:
            record["assignment_id"] = assignment_id
            record["status"] = "INTEGRATING"
            record["history"].append(
                {"event": "finding-integrating", "at": utc_now(), "assignment_id": assignment_id}
            )

        return self._update(finding_id, mutate)

    def schedule_replay(self, assignment: dict[str, Any], evidence: list[str]) -> dict | None:
        origin = assignment.get("origin_finding") or {}
        finding_id = origin.get("finding_id")
        if not finding_id:
            return None

        def mutate(record: dict[str, Any]) -> None:
            replay = record.get("targeted_replay")
            if replay is None:
                raise RuntimeError("origin finding has no targeted replay contract")
            if replay["state"] == "pending" and record.get("assignment_id") == assignment.get(
                "assignment_id"
            ):
                return
            now = utc_now()
            replay["attempt"] = int(replay.get("attempt") or 0) + 1
            replay["state"] = "pending"
            replay["scheduled_at"] = now
            replay["completed_at"] = None
            replay["evidence"] = list(dict.fromkeys([*replay["evidence"], *evidence]))
            record["status"] = "REPLAY_PENDING"
            record["gate_resolution"] = "pending"
            record["assignment_id"] = assignment.get("assignment_id")
            record["history"].append(
                {"event": "targeted-replay-scheduled", "at": now, "attempt": replay["attempt"]}
            )

        return self._update(str(finding_id), mutate)

    def complete_replay(
        self, finding_id: str, *, passed: bool, evidence: list[str]
    ) -> dict[str, Any]:
        def mutate(record: dict[str, Any]) -> None:
            replay = record.get("targeted_replay")
            if replay is None or replay.get("state") != "pending":
                raise RuntimeError("validation finding has no pending targeted replay")
            now = utc_now()
            replay["state"] = "passed" if passed else "failed"
            replay["completed_at"] = now
            replay["evidence"] = list(dict.fromkeys([*replay["evidence"], *evidence]))
            record["status"] = "CLOSED" if passed else "REOPENED"
            record["gate_resolution"] = "passed" if passed else "failed"
            record["assignment_id"] = None
            record["history"].append(
                {
                    "event": "finding-closed" if passed else "finding-reopened",
                    "at": now,
                    "gate": record.get("gate"),
                    "replay_state": replay["state"],
                }
            )

        return self._update(finding_id, mutate)

    def executable_entries(
        self, inventory: dict[str, Any], assignments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        self.reconcile_assignments(assignments)
        active_by_finding = {
            str((assignment.get("origin_finding") or {}).get("finding_id")): assignment
            for assignment in assignments
            if (assignment.get("origin_finding") or {}).get("finding_id")
            and assignment.get("lifecycle_state")
            not in {
                "completed",
                "repository-converged",
                "runtime-converged",
                "canonical-complete",
                "failed",
                "blocked",
                "waiting",
                "cancelled",
                "recovery-required",
            }
        }
        items = {str(item.get("ref")): item for item in inventory.get("work_items") or []}
        entries = []
        for record in self.all():
            if record["finding_id"] in active_by_finding:
                continue
            if record["status"] not in {"EXECUTABLE", "REOPENED", "REPLAY_PENDING"}:
                continue
            replay_only = record["status"] == "REPLAY_PENDING"
            replay = record.get("targeted_replay") or {}
            required_tests = replay.get("required_tests") if replay_only else record["required_tests"]
            if not required_tests:
                continue
            owner_ref = record["owner_resolution"].get("owner_ref")
            owner = items.get(str(owner_ref)) or {}
            repository_head = (
                ((inventory.get("repositories") or {}).get(record["repository"]) or {})
                .get("local_facts", {})
                .get("default_remote_head")
                or owner.get("repository_head")
            )
            origin = compact_origin(record)
            candidate = {
                "slice_id": record["finding_id"],
                "title": record["summary"],
                "category": "tests" if replay_only else "implementation",
                "result": "Executable",
                "rationale": "canonical validation finding promotion",
                "responsibility": record["responsibility"],
                "project": record["repository"],
                "allowed_paths": [] if replay_only else record["allowed_paths"],
                "required_tests": required_tests,
                "ranking_score": 1000,
            }
            entries.append(
                {
                    "ref": (
                        f"targeted-replay:{record['finding_id']}:{replay.get('attempt', 0)}"
                        if replay_only
                        else f"finding:{record['finding_id']}"
                    ),
                    "kind": "technical-revalidation" if replay_only else "implementation",
                    "assignment_type": "no-op-verification" if replay_only else "code-implementation",
                    "flow_stage": "verification" if replay_only else "implementation-ready",
                    "target_ref": owner_ref or record["finding_id"],
                    "project": record["repository"],
                    "responsibility": record["responsibility"],
                    "title": record["summary"],
                    "classification": "Executable",
                    "ranking_score": 1000,
                    "ranking_factors": {"validation_finding": True, "targeted_replay": replay_only},
                    "authority": record["authority"],
                    "planning_record": record["planning_record"],
                    "candidate": candidate,
                    "source_item": {
                        **owner,
                        "repository_head": repository_head,
                        "authority_facts": {
                            **(owner.get("authority_facts") or {}),
                            "approval_matches_record": bool(record["planning_record"]),
                            "record_digest": (record["planning_record"] or {}).get("digest"),
                            "approval_note": (record["planning_record"] or {}).get("approval_note"),
                            "approved_assignment_type": "code-implementation",
                            "approved_allowed_paths": record["allowed_paths"],
                            "approved_required_tests": record["required_tests"],
                        },
                    },
                    "source_fingerprint": record["fingerprint"],
                    "revalidation_tier": "B" if replay_only else None,
                    "origin_finding": origin,
                    "targeted_replay": replay if replay_only else record["targeted_replay"],
                    "affected_capabilities": [record["capability"]]
                    if record.get("capability")
                    else [],
                    "expected_gate": record.get("gate"),
                    "selection_rationale": (
                        "targeted replay for integrated validation repair"
                        if replay_only
                        else "canonical validation defect preempts unrelated analysis"
                    ),
                }
            )
        return entries

    def reconcile_assignments(
        self, assignments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        by_finding: dict[str, list[dict[str, Any]]] = {}
        for assignment in assignments:
            finding_id = str(
                (assignment.get("origin_finding") or {}).get("finding_id") or ""
            )
            if finding_id:
                by_finding.setdefault(finding_id, []).append(assignment)
        updated = []
        for finding_id, values in by_finding.items():
            latest = max(
                values,
                key=lambda value: (
                    int(value.get("created_at_epoch") or 0),
                    str(value.get("assignment_id") or ""),
                ),
            )
            record = self.load(finding_id)
            if record is None:
                continue
            lifecycle = str(latest.get("lifecycle_state") or "")
            if record["status"] in {"EXECUTABLE", "REOPENED"} and lifecycle not in {
                "completed",
                "repository-converged",
                "runtime-converged",
                "canonical-complete",
                "failed",
                "blocked",
                "cancelled",
            }:
                updated.append(self.mark_assigned(finding_id, latest["assignment_id"]))
            elif record["status"] in {"ASSIGNED", "INTEGRATING"} and lifecycle in {
                "failed",
                "blocked",
                "cancelled",
                "recovery-required",
            }:
                def reopen(value: dict[str, Any]) -> None:
                    value["status"] = "REOPENED"
                    value["gate_resolution"] = "failed"
                    value["assignment_id"] = None
                    value["history"].append(
                        {
                            "event": "finding-reopened",
                            "at": utc_now(),
                            "reason": f"assignment-{lifecycle}",
                            "assignment_id": latest.get("assignment_id"),
                        }
                    )

                updated.append(self._update(finding_id, reopen))
        return updated

    def metrics(self) -> dict[str, Any]:
        records = self.all()
        by_classification = {
            classification: sum(
                record["classification"] == classification for record in records
            )
            for classification in sorted(FINDING_CLASSIFICATIONS)
        }
        statuses = sorted({record["status"] for record in records})
        return {
            "total": len(records),
            "open": sum(record["status"] != "CLOSED" for record in records),
            "closed": sum(record["status"] == "CLOSED" for record in records),
            "reopened": sum(record["status"] == "REOPENED" for record in records),
            "replay_pending": sum(record["status"] == "REPLAY_PENDING" for record in records),
            "external_implementation_adopted": sum(
                record["status"] == "EXTERNAL_IMPLEMENTATION_ADOPTED"
                for record in records
            ),
            "by_classification": by_classification,
            "by_status": {
                status: sum(record["status"] == status for record in records)
                for status in statuses
            },
        }
