import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lifecycle import is_terminal
from .mutation import MutationGate, OperationClass
from .schema_registry import RecordError, read_record, write_record

SCHEMA = "axis.external-development-supervisor.active-mission"
SCHEMA_VERSION = "1.0.0"
DESIRED_END_STATE = "all-capabilities-graduated"
MAX_ACTIONS = 8
MAX_OBSERVATIONS = 100
COMPLETED_RESULT_STATES = {
    "analysis-completed",
    "no-op-verification-completed",
    "integrated-post-main-verified",
    "repository-converged",
    "runtime-converged",
    "canonical-complete",
}
EXTERNAL_AUTHORITY_STATES = {
    "needs-product-owner",
    "needs-governance",
    "prohibited",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:16]
    return f"mission-action-{digest}"


def _assignment_summary(value: dict[str, Any]) -> dict[str, str]:
    return {
        "assignment_id": str(value.get("assignment_id") or "unknown"),
        "project": str(value.get("project") or ""),
        "work_item": str(value.get("work_item") or ""),
        "lifecycle_state": str(value.get("lifecycle_state") or "unknown"),
        "result_state": str(value.get("result_state") or "pending"),
    }


def _external_node(node: dict[str, Any]) -> bool:
    authority_state = str((node.get("authority") or {}).get("state") or "")
    blocker_type = str(node.get("blocker_type") or "").lower()
    return authority_state in EXTERNAL_AUTHORITY_STATES or any(
        marker in blocker_type
        for marker in ("external", "human", "authority", "product-owner", "governance")
    )


class ActiveMissionState:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "active-mission.json"
        self.gate = MutationGate(root, source="mission-reconciler")

    def _previous(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return read_record(self.path, SCHEMA)
        except RecordError:
            try:
                legacy = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            if not isinstance(legacy, dict):
                return {}
            observations = []
            for observation in list(legacy.get("observations") or [])[
                -MAX_OBSERVATIONS:
            ]:
                if isinstance(observation, dict):
                    observations.append(
                        {
                            "observed_at": str(
                                observation.get("observed_at") or utc_now()
                            ),
                            "source": str(observation.get("source") or "migration"),
                            "summary": str(observation.get("summary") or observation)[
                                :4096
                            ],
                        }
                    )
                else:
                    observations.append(
                        {
                            "observed_at": utc_now(),
                            "source": "migration",
                            "summary": str(observation)[:4096],
                        }
                    )
            return {
                "mission_id": str(
                    legacy.get("mission_id") or "axis-capability-graduation"
                ),
                "created_at": str(legacy.get("created_at") or utc_now()),
                "observations": observations,
            }

    def _assignments(self, inventory: dict[str, Any]) -> list[dict[str, Any]]:
        values = {
            str(value.get("assignment_id")): value
            for value in inventory.get("supervisor_assignments") or []
            if value.get("assignment_id")
        }
        for path in sorted((self.root / "assignments").glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if value.get("assignment_id"):
                values[str(value["assignment_id"])] = value
        return list(values.values())

    @staticmethod
    def _missing_gates(
        graduation: dict[str, Any], graph: dict[str, Any]
    ) -> list[dict[str, Any]]:
        nodes = {str(node.get("ref")): node for node in graph.get("nodes") or []}
        executable_capabilities = {
            str(capability)
            for entry in graph.get("executable_queue") or []
            for capability in entry.get("affected_capabilities") or []
        }
        missing = []
        for capability in graduation.get("capabilities") or []:
            name = str(capability.get("capability") or "unknown")
            linked = [
                nodes[ref]
                for ref in capability.get("linked_work_items") or []
                if ref in nodes
                and nodes[ref].get("classification")
                not in {"Completed", "Integrated", "Superseded", "Invalid"}
            ]
            linked_external = bool(linked) and all(
                _external_node(node) for node in linked
            )
            states = capability.get("graduation_state") or {}
            unresolved_names = {
                gate_name
                for gate_name, gate in states.items()
                if gate_name != "graduated"
                and gate.get("state") not in {"passed", "not-required"}
            }
            for gate_name in sorted(unresolved_names):
                gate = states[gate_name]
                external_only = gate_name == "operator_acceptance" or (
                    gate_name in {"implementation", "integration", "program_risk"}
                    and linked_external
                    and name not in executable_capabilities
                )
                missing.append(
                    {
                        "capability": name,
                        "gate": gate_name,
                        "state": str(gate.get("state") or "pending"),
                        "evidence": [
                            str(value) for value in gate.get("evidence") or []
                        ],
                        "external_only": external_only,
                    }
                )
        return missing

    @staticmethod
    def _external_blockers(
        missing: list[dict[str, Any]], graph: dict[str, Any]
    ) -> list[dict[str, str]]:
        blockers = [
            {
                "ref": f"capability:{gate['capability']}:{gate['gate']}",
                "kind": "external-graduation-gate",
                "reason": "; ".join(gate["evidence"])
                or f"{gate['gate']} requires external evidence",
            }
            for gate in missing
            if gate["external_only"]
        ]
        blockers.extend(
            {
                "ref": str(node.get("ref") or "unknown"),
                "kind": str(node.get("blocker_type") or "external-authority"),
                "reason": str(
                    (node.get("authority") or {}).get("reason")
                    or node.get("classification_rationale")
                    or "external authority is required"
                ),
            }
            for node in graph.get("nodes") or []
            if node.get("classification") == "Blocked" and _external_node(node)
        )
        unique = {(value["ref"], value["kind"]): value for value in blockers}
        return [unique[key] for key in sorted(unique)]

    @staticmethod
    def _actions(
        missing: list[dict[str, Any]],
        graph: dict[str, Any],
        graduation: dict[str, Any],
        active_assignments: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        internal = {
            (gate["capability"], gate["gate"]): gate
            for gate in missing
            if not gate["external_only"]
        }

        for assignment in active_assignments:
            target = assignment["work_item"] or assignment["assignment_id"]
            gate_name = (
                "integration"
                if assignment["lifecycle_state"] == "awaiting-integration"
                else "implementation"
            )
            actions.append(
                {
                    "action_id": _stable_id(
                        "reconcile-assignment", assignment["assignment_id"]
                    ),
                    "kind": "reconcile-active-assignment",
                    "target": target,
                    "gate": gate_name,
                    "reason": f"continue {assignment['lifecycle_state']} assignment from durable state",
                    "executable": True,
                    "attempt_limit": 1,
                    "source_ref": target,
                    "assignment_id": assignment["assignment_id"],
                }
            )

        for entry in graph.get("executable_queue") or []:
            affected = [
                str(value) for value in entry.get("affected_capabilities") or []
            ]
            gate_name = next(
                (
                    gate
                    for capability, gate in internal
                    if not affected or capability in affected
                ),
                "implementation",
            )
            target = str(entry.get("target_ref") or entry.get("ref") or "unknown")
            actions.append(
                {
                    "action_id": _stable_id(
                        "dispatch", str(entry.get("ref") or target)
                    ),
                    "kind": "dispatch-executable",
                    "target": target,
                    "gate": gate_name,
                    "reason": str(
                        entry.get("selection_rationale")
                        or "deterministic executable frontier entry"
                    ),
                    "executable": True,
                    "attempt_limit": 1,
                    "source_ref": str(entry.get("ref") or target),
                    "assignment_id": None,
                }
            )

        for capability in graduation.get("capabilities") or []:
            name = str(capability.get("capability") or "unknown")
            for scheduled in capability.get("scheduled_actions") or []:
                stages = [str(value) for value in scheduled.get("stages") or []]
                gate_name = next(
                    (stage for stage in stages if (name, stage) in internal),
                    "verification",
                )
                actions.append(
                    {
                        "action_id": _stable_id(
                            "capability-evidence",
                            name,
                            str(scheduled.get("fingerprint") or gate_name),
                        ),
                        "kind": "collect-capability-evidence",
                        "target": name,
                        "gate": gate_name,
                        "reason": str(
                            scheduled.get("reason")
                            or "capability graduation evidence is stale or missing"
                        ),
                        "executable": True,
                        "attempt_limit": 1,
                        "source_ref": str(scheduled.get("repository") or "") or None,
                        "assignment_id": None,
                    }
                )

        covered = {(action["target"], action["gate"]) for action in actions}
        for (capability, gate_name), gate in internal.items():
            if gate_name == "program_risk" or (capability, gate_name) in covered:
                continue
            actions.append(
                {
                    "action_id": _stable_id(
                        "reconcile-evidence", capability, gate_name
                    ),
                    "kind": "reconcile-missing-evidence",
                    "target": capability,
                    "gate": gate_name,
                    "reason": "; ".join(gate["evidence"])
                    or f"{gate_name} evidence is missing",
                    "executable": True,
                    "attempt_limit": 1,
                    "source_ref": None,
                    "assignment_id": None,
                }
            )

        unique = {action["action_id"]: action for action in actions}
        return list(unique.values())[:MAX_ACTIONS]

    def reconcile(
        self,
        inventory: dict[str, Any],
        graph: dict[str, Any],
        graduation: dict[str, Any],
    ) -> dict[str, Any]:
        previous = self._previous()
        assignments = self._assignments(inventory)
        active = sorted(
            (
                _assignment_summary(value)
                for value in assignments
                if not is_terminal(value)
            ),
            key=lambda value: value["assignment_id"],
        )
        completed = sorted(
            (
                _assignment_summary(value)
                for value in assignments
                if value.get("result_state") in COMPLETED_RESULT_STATES
            ),
            key=lambda value: value["assignment_id"],
        )
        missing = self._missing_gates(graduation, graph)
        blockers = self._external_blockers(missing, graph)
        actions = self._actions(missing, graph, graduation, active)
        kpi = graduation.get("primary_kpi") or {}
        graduated = int(kpi.get("count") or 0)
        denominator = int(kpi.get("denominator") or 0)
        desired_achieved = (
            denominator > 0
            and graduated == denominator
            and not missing
            and not active
            and not actions
        )
        external_only = (
            bool(missing)
            and all(gate["external_only"] for gate in missing)
            and not active
            and not actions
        )
        should_terminate = desired_achieved or external_only
        reason = (
            "desired-state-achieved"
            if desired_achieved
            else "all-remaining-paths-external-only"
            if external_only
            else "work-remains"
        )
        current_state = (
            "completed"
            if desired_achieved
            else "blocked-external"
            if external_only
            else "active"
        )
        now = utc_now()
        observations = list(previous.get("observations") or [])[-MAX_OBSERVATIONS:]
        projection = {
            "schema": SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "mission_id": str(
                previous.get("mission_id") or "axis-capability-graduation"
            ),
            "created_at": str(previous.get("created_at") or now),
            "updated_at": now,
            "desired_end_state": DESIRED_END_STATE,
            "current_state": current_state,
            "missing_gates": missing,
            "generated_actions": actions,
            "active_assignments": active,
            "completed_assignments": completed,
            "external_blockers": blockers,
            "graduation_progress": {
                "graduated": graduated,
                "denominator": denominator,
                "percent": float(kpi.get("percent") or 0),
                "missing_gate_count": len(missing),
                "active_assignment_count": len(active),
                "completed_assignment_count": len(completed),
            },
            "termination_condition": {
                "desired_state_achieved": desired_achieved,
                "every_remaining_path_external_only": external_only,
                "should_terminate": should_terminate,
                "reason": reason,
            },
            "observations": observations,
        }
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(self.path, projection, SCHEMA)
        return projection

    def observe(self, response: Any, *, source: str) -> dict[str, Any]:
        mission = read_record(self.path, SCHEMA)
        summary = (
            response
            if isinstance(response, str)
            else json.dumps(response, sort_keys=True, default=str)
        )
        mission["observations"] = (
            list(mission.get("observations") or [])
            + [{"observed_at": utc_now(), "source": source, "summary": summary[:4096]}]
        )[-MAX_OBSERVATIONS:]
        mission["updated_at"] = utc_now()
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(self.path, mission, SCHEMA)
        return mission


def mission_summary(mission: dict[str, Any]) -> dict[str, Any]:
    return {
        "mission_id": mission.get("mission_id"),
        "desired_end_state": mission.get("desired_end_state"),
        "current_state": mission.get("current_state"),
        "graduation_progress": mission.get("graduation_progress"),
        "generated_actions": mission.get("generated_actions") or [],
        "external_blockers": mission.get("external_blockers") or [],
        "termination_condition": mission.get("termination_condition"),
    }
