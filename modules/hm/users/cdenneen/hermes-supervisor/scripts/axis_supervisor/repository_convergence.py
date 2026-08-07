import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .lifecycle import is_terminal
from .mutation import MutationGate, OperationClass
from .observability import record_event
from .schema_registry import read_record, write_record

SCHEMA = "axis.external-development-supervisor.repository-convergence"
SCHEMA_VERSION = "2.0.0"


class RepositoryConvergenceProjector:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "repository-convergence.json"
        self.dispositions_path = root / "branch-dispositions.json"
        self.gate = MutationGate(root, source="collector")

    def _dispositions(self) -> dict:
        if not self.dispositions_path.exists():
            return {"branches": []}
        value = json.loads(self.dispositions_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("branches"), list):
            raise ValueError("branch-dispositions.json is malformed")
        return value

    def build(self, inventory: dict) -> dict:
        try:
            previous = read_record(self.path, SCHEMA) if self.path.exists() else None
        except ValueError:
            legacy = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(legacy, dict) or (
                legacy.get("schema") != SCHEMA
                or legacy.get("schema_version") != "1.0.0"
            ):
                raise
            previous = legacy
        dispositions_record = self._dispositions()
        disposition_by_key = {
            (value.get("repository"), value.get("branch")): value
            for value in dispositions_record.get("branches") or []
        }
        assignments = {
            value.get("assignment_id"): value
            for value in inventory.get("supervisor_assignments") or []
        }
        assignment_by_branch = {
            (value.get("project"), (value.get("worker") or {}).get("branch")): value
            for value in assignments.values()
            if (value.get("worker") or {}).get("branch")
        }
        repositories = []
        branches = []
        orphan_worktrees = []
        counts = {
            "active_branches": 0,
            "merge_ready_branches": 0,
            "cleanup_ready_branches": 0,
            "retained_branches": 0,
            "ambiguous_branches": 0,
            "orphan_branches": 0,
            "orphan_worktrees": 0,
        }
        root_clean = True
        root_canonical = True
        local_main_current = True
        remote_branches_explained = True

        for repository, value in sorted((inventory.get("repositories") or {}).items()):
            local = value.get("local_facts") or {}
            default_branch = str(value.get("default_branch") or "main")
            repository_root_clean = not bool(local.get("dirty"))
            repository_root_canonical = local.get("branch") in {
                default_branch,
                f"{default_branch}@personal",
            }
            repository_main_current = bool(
                local.get("head") == local.get("default_remote_head")
                and local.get("remote_fresh")
            )
            root_clean = root_clean and repository_root_clean
            root_canonical = root_canonical and repository_root_canonical
            local_main_current = local_main_current and repository_main_current
            repositories.append(
                {
                    "repository": repository,
                    "path": local.get("path"),
                    "root_clean": repository_root_clean,
                    "root_branch_canonical": repository_root_canonical,
                    "local_main_equals_origin_main": repository_main_current,
                    "head": local.get("head"),
                    "origin_main": local.get("default_remote_head"),
                    "remote_fresh": bool(local.get("remote_fresh")),
                }
            )
            for worktree in local.get("worktrees") or []:
                if worktree.get("is_root"):
                    continue
                assignment = assignment_by_branch.get(
                    (repository, worktree.get("branch"))
                )
                if assignment and not is_terminal(assignment):
                    continue
                orphan_worktrees.append(
                    {
                        "repository": repository,
                        "path": worktree.get("path"),
                        "branch": worktree.get("branch"),
                        "head": worktree.get("head"),
                        "dirty": worktree.get("dirty"),
                    }
                )
            local_by_name = {
                value["name"]: value for value in local.get("local_branches") or []
            }
            for remote in local.get("remote_branches") or []:
                branch = remote["name"]
                assignment = assignment_by_branch.get((repository, branch))
                disposition = disposition_by_key.get((repository, branch))
                status = "ambiguous"
                next_action = "record an evidence-backed branch disposition"
                if assignment and not is_terminal(assignment):
                    status = "active"
                    next_action = "continue the active governed assignment"
                elif remote.get("owned_by_supervisor") and remote.get(
                    "integrated_into_default"
                ):
                    status = "cleanup-ready"
                    next_action = "delete the owned remote branch and prune refs"
                elif (remote.get("merge_request") or {}).get("state") == "opened":
                    status = "merge-ready"
                    next_action = "complete MR integration or record its blocker"
                elif disposition:
                    status = str(disposition.get("status") or "ambiguous")
                    next_action = str(disposition.get("next_action") or "")
                elif assignment and is_terminal(assignment):
                    status = "orphan"
                    next_action = "recover or delete the terminal assignment branch"
                counts[f"{status.replace('-', '_')}_branches"] = counts.get(
                    f"{status.replace('-', '_')}_branches", 0
                ) + 1
                if status in {"ambiguous", "orphan"}:
                    remote_branches_explained = False
                branches.append(
                    {
                        "repository": repository,
                        "branch": branch,
                        "local_sha": (local_by_name.get(branch) or {}).get("head"),
                        "remote_sha": remote.get("head"),
                        "merge_base": remote.get("merge_base"),
                        "ahead": remote.get("ahead"),
                        "behind": remote.get("behind"),
                        "integrated_into_main": remote.get("integrated_into_default"),
                        "changed_paths": remote.get("changed_paths") or [],
                        "active_worktree": remote.get("active_worktree"),
                        "owned_by_supervisor": remote.get("owned_by_supervisor"),
                        "assignment_id": (assignment or {}).get("assignment_id"),
                        "grant_id": (assignment or {}).get("mutation_grant_id"),
                        "work_item": (assignment or {}).get("work_item"),
                        "merge_request": remote.get("merge_request"),
                        "status": status,
                        "disposition": (disposition or {}).get("disposition"),
                        "owner": (disposition or {}).get("owner"),
                        "blocker": (disposition or {}).get("blocker"),
                        "expiry": (disposition or {}).get("expiry"),
                        "next_action": next_action,
                        "evidence": (disposition or {}).get("evidence") or [],
                    }
                )

        counts["orphan_worktrees"] = len(orphan_worktrees)
        no_expired_leases = not any(
            int(value.get("expires_at_epoch") or 0)
            <= int(datetime.now(timezone.utc).timestamp())
            for value in inventory.get("active_leases") or []
        )
        invariants = {
            "root_worktrees_clean": root_clean,
            "root_branches_canonical": root_canonical,
            "local_main_equals_origin_main": local_main_current,
            "no_orphan_worktrees": not orphan_worktrees,
            "no_expired_leases": no_expired_leases,
            "no_ambiguous_remote_branches": remote_branches_explained,
        }
        status = (
            "green"
            if all(invariants.values())
            else "red"
            if counts["ambiguous_branches"] or counts["orphan_branches"]
            else "amber"
        )
        digest_payload = {
            "repositories": repositories,
            "branches": branches,
            "orphan_worktrees": orphan_worktrees,
            "invariants": invariants,
            "disposition_history": dispositions_record.get("branches") or [],
        }
        convergence_digest = "sha256:" + hashlib.sha256(
            json.dumps(
                digest_payload, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        previous_lifecycle = (previous or {}).get("fingerprint_lifecycle") or {}
        previous_fingerprint = previous_lifecycle.get("current") or (
            previous or {}
        ).get("convergence_digest")
        fingerprint_changed = previous_fingerprint != convergence_digest
        projection = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "inventory_generation_id": inventory.get("generation_id"),
            "convergence_digest": convergence_digest,
            "fingerprint_lifecycle": {
                "current": convergence_digest,
                "previous": previous_fingerprint,
                "changed": fingerprint_changed,
                "stable_cycles": 0
                if fingerprint_changed
                else int(previous_lifecycle.get("stable_cycles") or 0) + 1,
            },
            "status": status,
            "counts": counts,
            "repositories": repositories,
            "branches": sorted(
                branches, key=lambda value: (value["repository"], value["branch"])
            ),
            "orphan_worktrees": orphan_worktrees,
            "invariants": invariants,
            "disposition_history": dispositions_record.get("branches") or [],
        }
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(self.path, projection, SCHEMA)
        if not previous or previous.get("convergence_digest") != convergence_digest:
            record_event(
                self.root,
                "repository_convergence_updated",
                details={
                    "status": status,
                    "counts": counts,
                    "invariants": invariants,
                    "convergence_digest": convergence_digest,
                },
                source="collector",
                notify=False,
            )
        return projection
