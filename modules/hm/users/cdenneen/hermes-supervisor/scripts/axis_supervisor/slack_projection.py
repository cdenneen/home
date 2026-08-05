import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .reporting import COMPOSITION, build_roadmap_semantics
from .lifecycle import is_terminal
from .mutation import MutationGate, OperationClass
from .schema_registry import validate_record, write_record


class SlackProjection:
    def __init__(self, root: Path):
        self.root = root
        self.state_path = root / "slack-overview-state.json"
        self.record_path = root / "slack-overview-record.json"
        self.gate = MutationGate(root, source="reporter")

    @staticmethod
    def env_file() -> dict[str, str]:
        values = {}
        path = Path.home() / ".hermes" / ".env"
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            values[key] = value
        return values

    @staticmethod
    def api(token: str, method: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"https://slack.com/api/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.load(response)
        if not value.get("ok"):
            raise RuntimeError(f"Slack {method} failed: {value.get('error')}")
        return value

    @staticmethod
    def bar(value: int, total: int, width: int = 20) -> str:
        filled = round(width * value / total) if total else 0
        return "█" * filled + "░" * (width - filled)

    @staticmethod
    def percent_text(value: dict) -> str:
        count = int(value.get("count") or 0)
        denominator = int(value.get("denominator") or 0)
        percent = float(value.get("percent") or 0)
        if count and denominator and percent < 1:
            return "<1%"
        return f"{percent:g}%"

    @staticmethod
    def scheduler_refs(values: list[dict], limit: int = 8) -> str:
        refs = [str(value.get("target_ref") or value.get("ref")) for value in values]
        visible = refs[:limit]
        suffix = f" (+{len(refs) - limit} more)" if len(refs) > limit else ""
        return ", ".join(visible) + suffix if visible else "none"

    @staticmethod
    def milestone_icon(status: str) -> str:
        return {
            "verified": "✅",
            "progressing": "🟢",
            "running": "🔵",
            "waiting": "🟡",
            "blocked": "🔴",
            "future": "⚪",
            "completed": "✅",
            "closed-pending-audit": "🟡",
            "execution-frontier": "🟣",
            "parallel-execution": "🟢",
            "critical-path": "🔴",
        }.get(status, "⚪")

    def render(
        self,
        inventory: dict,
        graph: dict,
        control: dict,
        semantics: dict | None = None,
    ) -> tuple[str, list[dict], str]:
        semantics = semantics or build_roadmap_semantics(
            inventory, graph, control, self.deployed_revision()
        )
        total = int(semantics["total_governed_items"])
        composition = semantics["composition"]
        coverage = semantics["coverage"]
        verified = int(composition["verified_complete"]["count"])
        blocked = int(composition["blocked"]["count"])
        waiting = int(composition["waiting"]["count"])
        supervisor_work = semantics["supervisor_work"]
        assignments = inventory.get("supervisor_assignments") or []
        active = [item for item in assignments if not is_terminal(item)]
        leases = inventory.get("active_leases") or []
        classification_counts = graph.get("classification_counts") or {}
        total_classified = sum(int(value) for value in classification_counts.values())
        unknown = int(classification_counts.get("Unknown", 0))
        confidence = {
            "percent": round((total_classified - unknown) * 100 / total_classified)
            if total_classified
            else 0
        }
        revision = (semantics.get("source") or {}).get("deployed_revision") or {}
        collection = inventory.get("collection_status") or {}
        health = (
            "healthy"
            if unknown == 0
            and not collection.get("state_record_errors")
            and int(collection.get("retrieval_error_count", 0)) == 0
            else "degraded"
        )
        icon = "🟢" if health == "healthy" else "🟡"

        fallback = (
            f"AXIS Build Supervisor — {health}; mode={control.get('mode')}; "
            f"governed-items={total}; verified={verified}; active={len(active)}; "
            f"work-remaining={supervisor_work['supervisor_work_remaining']}; "
            f"ready-queue={supervisor_work['ready_work_total']}; blocked={blocked}; waiting={waiting}; "
            f"confidence={confidence.get('percent', 0)}%"
        )
        composition_lines = []
        for key, _label in COMPOSITION:
            value = composition[key]
            composition_lines.append(
                f"{value['label']:<36} `{self.bar(value['count'], value['denominator'], 12)}` "
                f"{value['count']} / {value['denominator']}  {self.percent_text(value)}"
            )
        coverage_lines = [
            f"{value['label']:<31} {value['count']} / {value['denominator']}  {self.percent_text(value)}"
            for value in coverage.values()
        ]
        source_lines = [
            f"{value['label']}: {value['count']}"
            for value in semantics["executable_sources"].values()
        ]
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{icon} AXIS Build Supervisor"}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Health*\n{health.title()}"},
                    {"type": "mrkdwn", "text": f"*Mode*\n{control.get('mode')}"},
                    {"type": "mrkdwn", "text": f"*Workers*\n{len(active)}/{control.get('max_active_assignments', 1)}"},
                    {"type": "mrkdwn", "text": f"*Integrator*\n{'Active' if any(a.get('lifecycle_state') == 'awaiting-integration' for a in active) else 'Idle'}"},
                    {"type": "mrkdwn", "text": f"*Governed items*\n{total}"},
                    {"type": "mrkdwn", "text": f"*Supervisor work remaining*\n{supervisor_work['supervisor_work_remaining']}"},
                    {"type": "mrkdwn", "text": f"*Ready work queue*\n{supervisor_work['ready_work_total']}"},
                    {"type": "mrkdwn", "text": f"*Confidence*\n{confidence.get('percent', 0)}%"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Roadmap Composition — mutually exclusive*\n"
                    + "\n".join(composition_lines)
                    + f"\n*Total* {sum(value['count'] for value in composition.values())} / {total}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Audit and Readiness Coverage — not roadmap progress*\n"
                    + "\n".join(coverage_lines),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"*{semantics['verification_standard']['label']}*\n{semantics['verification_standard']['definition']}",
                    }
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Current Revalidation Plan*\n"
                    f"Closed pending: {semantics['revalidation_plan']['total_closed_pending']} | "
                    f"Revalidation remaining: {semantics['revalidation_plan']['revalidation_remaining']}\n"
                    f"Tier A evidence-only: {semantics['revalidation_plan']['tier_a_automatic_evidence']} | "
                    f"Tier B technical: {semantics['revalidation_plan']['tier_b_active_technical']} | "
                    f"Tier C corrective: {semantics['revalidation_plan']['tier_c_corrective_implementation']} | "
                    f"Tier D authority: {semantics['revalidation_plan']['tier_d_human_authority']}\n"
                    f"Active milestone impact: {semantics['revalidation_plan']['milestone_impact']['active_milestone_closed_pending']}\n"
                    "Priority: active-milestone unlocks, durable proof receipts, merged-MR evidence review, then remaining historical risk.",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Supervisor Work — unambiguous queue terms*\n"
                    f"Governed roadmap items: {supervisor_work['governed_roadmap_items']}\n"
                    f"Supervisor work remaining: {supervisor_work['supervisor_work_remaining']}\n"
                    f"Ready work queue: {supervisor_work['ready_work_total']} tasks across "
                    f"{supervisor_work['ready_work_item_count']} items\n"
                    f"Revalidation-ready: {supervisor_work['revalidation_ready']} | "
                    f"Governance reconciliation-ready: {supervisor_work['governance_reconciliation_ready']} | "
                    f"Repository convergence-ready: {supervisor_work['repository_convergence_ready']}\n"
                    f"Implementation-executable: {supervisor_work['implementation_executable']} | "
                    f"Other ready: {supervisor_work['other_ready']} | "
                    f"Waiting/blocked/other not ready: {supervisor_work['waiting_blocked_or_other_not_ready']}\n"
                    f"Need Product Owner now? *{supervisor_work['need_product_owner_now']}*\n"
                    f"_{supervisor_work['baseline_clarification']}_\n\n"
                    + "Source partition: "
                    + " | ".join(source_lines)
                    + (
                        "\n\n" + semantics["ready_queue"]["explanation"]
                        if semantics["ready_queue"].get("explanation")
                        else ""
                    ),
                },
            },
        ]

        scheduler = semantics["scheduler_state"]
        focus = semantics["current_supervisor_focus"]
        selected = scheduler.get("selected_batch") or []
        deferred = scheduler.get("deferred_items") or []
        budget = scheduler.get("available_model_call_budget")
        constraint = scheduler.get("limiting_constraint")
        blocks.extend(
            [
                {"type": "divider"},
                {"type": "header", "text": {"type": "plain_text", "text": "Active Execution"}},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Current execution frontier:* {semantics['current_execution_frontier'] or 'none'}\n"
                        f"*Observed scheduler focus:* "
                        f"`{focus.get('target_ref') or focus.get('ref') or 'none'}`\n"
                        f"*Selected:* `{self.scheduler_refs(selected)}`\n"
                        f"*Deferred:* `{self.scheduler_refs(deferred)}`\n"
                        f"*Available model-call budget:* `{budget}`\n"
                        f"*Limiting constraint:* `{constraint or 'none'}`\n\n"
                        + "\n".join(
                            f"{index}. *{milestone['key']}* — queued {milestone['queued_tasks']} "
                            f"(audit {milestone['queue_breakdown']['semantic_audit']}, implementation {milestone['queue_breakdown']['implementation']}); "
                            f"lifecycle executable {milestone['executable']}; health {milestone['health']}"
                            for index, milestone in enumerate(semantics["active_execution"], 1)
                        ),
                    },
                },
                {"type": "divider"},
                {"type": "header", "text": {"type": "plain_text", "text": "Strategic Programs"}},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "_Cross-cutting, non-exclusive execution streams._\n"
                        + "\n".join(
                            f"*{program['title']}* — items {program['total']} | queued {program['queued_tasks']} | "
                            f"executable {program['executable']} | waiting {program['waiting']} | blocked {program['blocked']} | "
                            f"confidence {self.percent_text(program['confidence'])}"
                            for program in semantics["strategic_programs"]
                        ),
                    },
                },
            ]
        )

        if semantics["complete_roadmap"]:
            blocks.extend([{"type": "divider"}, {"type": "header", "text": {"type": "plain_text", "text": "Complete Roadmap — M4 to Endpoint"}}])
        for milestone in semantics["complete_roadmap"]:
            reason = (
                f"\n*Reason executable is zero:* {milestone['zero_executable_reason']}"
                if milestone.get("zero_executable_reason")
                else ""
            )
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{self.milestone_icon(milestone['status'])} *{milestone['key']} — {milestone['title']}*\n"
                        f"Status: {milestone['status']} | Health: {milestone['health']} | "
                        f"Confidence: {milestone['confidence']['count']}/{milestone['confidence']['denominator']} "
                        f"({self.percent_text(milestone['confidence'])})\n"
                        f"Progress: {milestone['progress']['count']}/{milestone['progress']['denominator']} "
                        f"({self.percent_text(milestone['progress'])}) | Verified: {milestone['verified_complete']}/{milestone['total']}\n"
                        f"Running {milestone['running']} | Implementation-executable {milestone['executable']} | "
                        f"Revalidation-ready {milestone['revalidation_ready']} | Waiting {milestone['waiting']} | Blocked {milestone['blocked']}\n"
                        f"Highlights: {', '.join(milestone['highlights']) or 'none'}"
                        + reason,
                    },
                }
            )

        for assignment in active[:3]:
            worker = assignment.get("worker") or {}
            handoff = worker.get("handoff") or {}
            lifecycle_state = str(assignment.get("lifecycle_state") or "unknown")
            blocks.extend(
                [
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"🔵 *{assignment.get('work_item') or assignment.get('target_ref')}*\n"
                            f"Worker: {'GPT-5.4' if 'semantic' in lifecycle_state or 'integration' in lifecycle_state else 'GPT-5.3-Codex'} | "
                            f"Lifecycle: {lifecycle_state}\n"
                            f"Branch: {worker.get('branch') or 'none'} | MR: {handoff.get('mr_url') or 'none'}\n"
                            f"Next: {assignment.get('integration', {}).get('result', {}).get('next') or 'continue bounded assignment'}",
                        },
                    },
                ]
            )

        decisions = []
        for node in graph.get("nodes") or []:
            record = node.get("semantic_record") or {}
            packet = record.get("decision_packet")
            if isinstance(packet, dict):
                decisions.append((node.get("ref"), packet))
        for ref, packet in decisions[:3]:
            blocks.extend(
                [
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"🔴 *Product Owner decision — {ref}*\n"
                            f"{packet.get('decision_requested')}\n"
                            f"*Recommendation:* {packet.get('recommendation')}\n"
                            f"*Impact of waiting:* {packet.get('consequences')}\n"
                            f"*Response:* `{packet.get('response_syntax')}`",
                        },
                    },
                ]
            )

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Source {str(revision.get('revision') or 'unknown')[:12]}{' dirty' if revision.get('dirty') else ''} • Inventory {inventory.get('generation_id')} • Graph {graph.get('generation_id')} • Semantics {semantics.get('semantic_revision', '')[:12]} • Leases {len(leases)}",
                    }
                ],
            }
        )
        fingerprint = hashlib.sha256(
            json.dumps({"fallback": fallback, "blocks": blocks}, sort_keys=True).encode()
        ).hexdigest()
        return fallback, blocks, fingerprint

    def update(self, inventory: dict, graph: dict, control: dict) -> dict:
        token = self.env_file()["SLACK_BOT_TOKEN"]
        user_id = str(control.get("slack_user_id") or "")
        if not user_id:
            raise ValueError("slack_user_id is not configured")
        semantics = build_roadmap_semantics(
            inventory, graph, control, self.deployed_revision()
        )
        fallback, blocks, fingerprint = self.render(inventory, graph, control, semantics)
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(
            self.record_path,
            semantics,
            "axis.external-development-supervisor.roadmap-semantics",
        )
        state = self.load_state()
        now = datetime.now(timezone.utc).isoformat()
        if state.get("fingerprint") == fingerprint:
            new_state = state | {
                "delivery_status": "unchanged",
                "last_attempt_at": now,
                "last_successful_update_at": now,
                "last_successful_update_epoch": int(time.time()),
                "semantic_revision": semantics["semantic_revision"],
                "source_revision": (semantics.get("source") or {}).get(
                    "deployed_revision"
                )
                or {},
                "schema": "axis.external-development-supervisor.slack-state",
                "schema_version": "1.0.0",
                "record_schema": semantics["schema"],
                "record_schema_version": semantics["schema_version"],
                "last_delivery_error": None,
            }
            self.write_state(new_state)
            return {
                "updated": False,
                "channel": state.get("channel"),
                "ts": state.get("ts"),
                "delivery_status": "unchanged",
            }
        try:
            channel = state.get("channel")
            if not channel:
                channel = self.api(token, "conversations.open", {"users": user_id})[
                    "channel"
                ]["id"]
            ts = state.get("ts")
            payload = {"channel": channel, "text": fallback, "blocks": blocks}
            try:
                if ts:
                    response = self.api(token, "chat.update", payload | {"ts": ts})
                else:
                    response = self.api(token, "chat.postMessage", payload)
            except RuntimeError as exc:
                if "message_not_found" not in str(exc):
                    raise
                response = self.api(token, "chat.postMessage", payload)
        except Exception as delivery_exc:
            self.write_state(
                state
                | {
                    "delivery_status": "failed",
                    "last_attempt_at": now,
                    "last_delivery_error": str(delivery_exc),
                    "semantic_revision": semantics["semantic_revision"],
                    "source_revision": (semantics.get("source") or {}).get(
                        "deployed_revision"
                    )
                    or {},
                    "schema": "axis.external-development-supervisor.slack-state",
                    "schema_version": "1.0.0",
                    "record_schema": semantics["schema"],
                    "record_schema_version": semantics["schema_version"],
                }
            )
            raise
        new_state = {
            "channel": channel,
            "ts": response["ts"],
            "fingerprint": fingerprint,
            "updated_at_epoch": int(time.time()),
            "delivery_status": "delivered",
            "last_attempt_at": now,
            "last_successful_update_at": now,
            "last_successful_update_epoch": int(time.time()),
            "semantic_revision": semantics["semantic_revision"],
            "source_revision": (semantics.get("source") or {}).get(
                "deployed_revision"
            )
            or {},
            "schema": "axis.external-development-supervisor.slack-state",
            "schema_version": "1.0.0",
            "record_schema": semantics["schema"],
            "record_schema_version": semantics["schema_version"],
            "last_delivery_error": None,
        }
        self.write_state(new_state)
        return {"updated": True, **new_state}

    def load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if value.get("schema") != "axis.external-development-supervisor.slack-state":
            timestamp = datetime.fromtimestamp(
                int(value.get("updated_at_epoch") or time.time()), timezone.utc
            ).isoformat()
            value = value | {
                "schema": "axis.external-development-supervisor.slack-state",
                "schema_version": "1.0.0",
                "delivery_status": value.get("delivery_status") or "delivered",
                "last_attempt_at": value.get("last_attempt_at") or timestamp,
                "last_successful_update_at": value.get("last_successful_update_at")
                or timestamp,
                "last_successful_update_epoch": int(
                    value.get("last_successful_update_epoch")
                    or value.get("updated_at_epoch")
                    or time.time()
                ),
                "semantic_revision": value.get("semantic_revision") or "legacy",
                "source_revision": value.get("source_revision") or {},
                "record_schema": value.get("record_schema")
                or value.get("schema")
                or "axis.external-development-supervisor.roadmap-semantics",
                "record_schema_version": value.get("record_schema_version")
                or value.get("schema_version")
                or "1.1.0",
                "last_delivery_error": value.get("last_delivery_error"),
            }
        return validate_record(
            value,
            "axis.external-development-supervisor.slack-state",
            record_path=self.state_path,
        )

    def write_state(self, value: dict) -> None:
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(
            self.state_path,
            value,
            "axis.external-development-supervisor.slack-state",
        )

    def deployed_revision(self) -> dict:
        try:
            value = json.loads(
                (self.root / "deployed-source-revision.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
