import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .mutation import MutationGate, OperationClass
from .observability import record_event
from .schema_registry import RecordError, read_record, write_record

SCHEMA = "axis.external-development-supervisor.capability-convergence"


class CapabilityConvergenceProjector:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "capability-convergence.json"
        self.matrix_path = root / "capability-runtime-matrix.json"
        self.gate = MutationGate(root, source="cycle")

    @staticmethod
    def _run(repository: Path, *args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=repository, text=True, timeout=120
        ).strip()

    @staticmethod
    def _identity(runtime: dict) -> tuple[dict | None, str | None]:
        path = str(runtime["identity_path"])
        try:
            if runtime["host"] == "local":
                try:
                    return json.loads(Path(path).read_text(encoding="utf-8")), None
                except (OSError, PermissionError):
                    pass
                completed = subprocess.run(
                    ["sudo", "-n", "cat", path],
                    text=True,
                    capture_output=True,
                    timeout=10,
                )
                if completed.returncode != 0:
                    return None, "identity-missing"
                return json.loads(completed.stdout), None
            output = subprocess.check_output(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    runtime["host"],
                    f"if test -r {path}; then cat {path}; else echo __IDENTITY_MISSING__; fi",
                ],
                text=True,
                timeout=20,
            )
            if output.strip() == "__IDENTITY_MISSING__":
                return None, "identity-missing"
            return json.loads(output), None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _required_command_available(runtime: dict) -> bool:
        command = str(runtime.get("required_command") or "")
        if not command:
            return True
        if runtime["host"] == "local":
            return (
                subprocess.run(
                    ["sh", "-c", f"command -v {command}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                == 0
            )
        return (
            subprocess.run(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=10",
                    runtime["host"],
                    f"command -v {command}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=20,
            ).returncode
            == 0
        )

    def build(self, repository_convergence: dict) -> dict:
        matrix = json.loads(self.matrix_path.read_text(encoding="utf-8"))
        repository = Path(matrix["repository_path"])
        expected_repository_revision = self._run(repository, "rev-parse", "origin/main")
        if matrix.get("deployment_lock_path"):
            deployment_lock = json.loads(
                Path(matrix["deployment_lock_path"]).read_text(encoding="utf-8")
            )
            expected_runtime_revision = str(
                deployment_lock["nodes"]["axis"]["locked"]["rev"]
            )
        else:
            expected_runtime_revision = expected_repository_revision
        capabilities = []
        expected_by_capability = {}
        for name, definition in matrix["capabilities"].items():
            paths = definition.get("paths") or []
            revision = (
                self._run(repository, "log", "-1", "--format=%H", "--", *paths)
                if paths
                else expected_repository_revision
            )
            expected_by_capability[name] = revision
            capabilities.append(
                {
                    "capability": name,
                    "expected_revision": revision,
                    "paths": paths,
                    "projected_runtimes": definition.get("runtimes") or [],
                }
            )
        runtime_records = []
        assignments = []
        blocked_capabilities: set[str] = set()
        for runtime_name, runtime in sorted(
            matrix["runtimes"].items(), key=lambda value: int(value[1]["ring"])
        ):
            identity, error = self._identity(runtime)
            required_command_available = self._required_command_available(runtime)
            if not required_command_available and error is None:
                error = "required-command-missing:" + str(
                    runtime.get("required_command")
                )
            running_revision = (identity or {}).get("runtime_revision")
            projected = [
                capability
                for capability, definition in matrix["capabilities"].items()
                if runtime_name in (definition.get("runtimes") or [])
            ]
            behind = []
            observed_capability_revisions = (identity or {}).get(
                "capability_revisions"
            ) or {}
            for capability in projected:
                expected = expected_by_capability[capability]
                observed_revision = observed_capability_revisions.get(capability)
                if not running_revision or not observed_revision:
                    behind.append(capability)
                    continue
                contained = (
                    subprocess.run(
                        [
                            "git",
                            "merge-base",
                            "--is-ancestor",
                            expected,
                            observed_revision,
                        ],
                        cwd=repository,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    ).returncode
                    == 0
                )
                if not contained:
                    behind.append(capability)
            verification_pending = (identity or {}).get("verification_status") != "verified"
            deployment_capabilities = behind or (projected if verification_pending else [])
            blocked_by_prior_ring = sorted(
                set(deployment_capabilities) & blocked_capabilities
            )
            deployable_capabilities = [
                capability
                for capability in deployment_capabilities
                if capability not in blocked_capabilities
            ]
            status = (
                "blocked-by-prior-ring"
                if blocked_by_prior_ring and not deployable_capabilities
                else "deployment-required"
                if error == "identity-missing"
                else "unknown"
                if error
                else "converged"
                if not deployment_capabilities
                else "deployment-required"
            )
            if status != "converged":
                blocked_capabilities.update(deployment_capabilities)
            runtime_records.append(
                {
                    "runtime": runtime_name,
                    "display_name": runtime["display_name"],
                    "ring": runtime["ring"],
                    "deployment_target": runtime["deployment_target"],
                    "running_revision": running_revision,
                    "expected_repository_revision": expected_repository_revision,
                    "expected_runtime_revision": expected_runtime_revision,
                    "capabilities_behind": behind,
                    "observed_capability_revisions": observed_capability_revisions,
                    "capabilities_blocked_by_prior_ring": blocked_by_prior_ring,
                    "capability_lag": len(behind),
                    "status": status,
                    "health": (identity or {}).get("health"),
                    "last_deployment": (identity or {}).get("deployment_time"),
                    "verification_status": (identity or {}).get(
                        "verification_status"
                    ),
                    "identity_error": error,
                    "required_command": runtime.get("required_command"),
                    "required_command_available": required_command_available,
                }
            )
            if deployable_capabilities:
                assignments.append(
                    {
                        "assignment_id": f"deployment-{expected_repository_revision[:12]}-{runtime_name}",
                        "assignment_type": "capability-deployment",
                        "target_runtime": runtime_name,
                        "ring": runtime["ring"],
                        "affected_capabilities": deployable_capabilities,
                        "expected_capability_revisions": {
                            capability: expected_by_capability[capability]
                            for capability in deployable_capabilities
                        },
                        "expected_revision": expected_runtime_revision,
                        "expected_runtime_revision": expected_runtime_revision,
                        "deployment_target": runtime["deployment_target"],
                        "status": status,
                        "migration_requirements": "derive from verified capability changes before apply",
                        "verification_plan": "identity revision, service health, capability smoke tests, heartbeat",
                        "rollback_plan": "previous Nix system generation",
                        "evidence": [
                            repository_convergence.get("convergence_digest"),
                            *[
                                f"{capability}:{expected_by_capability[capability]}"
                                for capability in deployable_capabilities
                            ],
                        ],
                    }
                )
        repository_ready = repository_convergence.get("status") == "green"
        promotion_status = {
            "repository_converged": repository_ready,
            "next_ring": next(
                (
                    value["ring"]
                    for value in runtime_records
                    if value["status"] == "deployment-required"
                ),
                None,
            ),
            "blocked": not repository_ready
            or any(value["status"] == "unknown" for value in runtime_records),
            "reason": "repository convergence is incomplete"
            if not repository_ready
            else "runtime identity is unavailable"
            if any(value["status"] == "unknown" for value in runtime_records)
            else "promotion follows capability impact and ring order",
        }
        digest_payload = {
            "repository_convergence_digest": repository_convergence.get(
                "convergence_digest"
            ),
            "expected_repository_revision": expected_repository_revision,
            "expected_runtime_revision": expected_runtime_revision,
            "capabilities": capabilities,
            "runtimes": runtime_records,
            "assignments": assignments,
        }
        convergence_digest = "sha256:" + hashlib.sha256(
            json.dumps(
                digest_payload, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        projection = {
            "schema": SCHEMA,
            "schema_version": "1.0.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "convergence_digest": convergence_digest,
            "repository_convergence_digest": repository_convergence.get(
                "convergence_digest"
            ),
            "expected_repository_revision": expected_repository_revision,
            "expected_runtime_revision": expected_runtime_revision,
            "capabilities": capabilities,
            "runtimes": runtime_records,
            "deployment_assignments": assignments,
            "promotion_status": promotion_status,
        }
        try:
            previous = read_record(self.path, SCHEMA) if self.path.exists() else None
        except RecordError:
            previous = None
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(self.path, projection, SCHEMA)
        if not previous or previous.get("convergence_digest") != convergence_digest:
            record_event(
                self.root,
                "capability_convergence_updated",
                details={
                    "convergence_digest": convergence_digest,
                    "expected_repository_revision": expected_repository_revision,
                    "runtimes": runtime_records,
                    "deployment_assignments": assignments,
                },
                source="cycle",
                notify=bool(assignments),
            )
        return projection
