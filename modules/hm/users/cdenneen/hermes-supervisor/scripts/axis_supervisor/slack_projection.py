import hashlib
import json
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .lifecycle import is_terminal
from .models import validate_assignment
from .mutation import MutationGate, OperationClass
from .observability import (
    DELIVERY_STAGES,
    OUTBOX_SCHEMA,
    OperationalEventLog,
    utc_now,
)
from .reporting import COMPOSITION, build_roadmap_semantics
from .schema_registry import read_record, validate_record, write_record


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
    def advance(record: dict, stage: str) -> None:
        if stage not in DELIVERY_STAGES:
            raise ValueError(f"unsupported Slack delivery stage: {stage}")
        record["current_stage"] = stage
        record.setdefault("stage_history", []).append({"stage": stage, "at": utc_now()})
        record["stage_history"] = record["stage_history"][-100:]

    def verify_message(
        self, token: str, channel: str, ts: str, expected_text: str
    ) -> dict:
        def normalized(value: str) -> str:
            return re.sub(r"(?<!<)(https?://[^\s>`]+)", r"<\1>", value)

        for delay in (0, 0.25, 0.5, 1.0, 2.0):
            if delay:
                time.sleep(delay)
            response = self.api(
                token,
                "conversations.history",
                {"channel": channel, "oldest": ts, "inclusive": True, "limit": 5},
            )
            message = next(
                (
                    item
                    for item in response.get("messages") or []
                    if str(item.get("ts")) == str(ts)
                ),
                None,
            )
            if message and normalized(str(message.get("text") or "")) == normalized(
                expected_text
            ):
                return message
        raise RuntimeError("Slack message readback did not match expected channel/ts/text")

    def load_outbox(self) -> dict:
        path = self.root / "slack-outbox.json"
        if path.exists():
            return read_record(path, OUTBOX_SCHEMA)
        return {
            "schema": OUTBOX_SCHEMA,
            "schema_version": "1.0.0",
            "notifications": [],
            "updated_at": utc_now(),
        }

    def write_outbox(self, outbox: dict) -> None:
        outbox["updated_at"] = utc_now()
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(self.root / "slack-outbox.json", outbox, OUTBOX_SCHEMA)

    def live_assignments(self) -> list[dict]:
        values = []
        for path in sorted((self.root / "assignments").glob("*.json")):
            try:
                values.append(
                    validate_assignment(
                        json.loads(path.read_text(encoding="utf-8")), self.root
                    )
                )
            except Exception:
                continue
        return values

    def cron_jobs(self) -> list[dict]:
        path = Path.home() / ".hermes" / "cron" / "jobs.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return value.get("jobs") or []

    @staticmethod
    def render_event(event: dict) -> str:
        details = event.get("details") or {}
        event_type = str(event.get("event_type") or "unknown")
        headings: dict[str, str] = {
            "assignment_selected": "Assignment selected",
            "worker_started": "Worker started",
            "assignment_retry": "Retry / recovery",
            "implementation_completed": "Implementation completed",
            "mr_created": "Merge request created",
            "mr_merged": "Merge completed",
            "post_main_verified": "Post-main verification completed",
            "grant_consumed": "Canary grant consumed",
            "assignment_disposition": "Assignment disposition",
            "observability_recovered": "Slack observability recovered",
        }
        heading = headings.get(event_type, event_type.replace("_", " ").title())
        lines = [f"*AXIS Supervisor — {heading}*"]
        for label, value in (
            ("Work item", event.get("work_item")),
            ("Assignment", event.get("assignment_id")),
            ("Repository", event.get("repository")),
            ("Lifecycle", event.get("lifecycle_state")),
            ("Assignment type", details.get("assignment_type")),
            ("Assignment result", details.get("disposition")),
            ("Work item disposition", details.get("work_item_disposition")),
            ("Model", details.get("model")),
            ("Retry", details.get("retry")),
            ("Failed gate", details.get("failed_gate")),
            ("Failure", details.get("failure_classification")),
            ("Corrective action", details.get("corrective_action")),
            ("Branch", details.get("branch")),
            ("Worktree", details.get("worktree")),
            ("Commit", details.get("commit")),
            ("MR", details.get("mr_url")),
            ("Next", details.get("expected_next_phase") or details.get("next_scheduled_work")),
        ):
            if value is not None and value != "" and value != []:
                lines.append(f"*{label}:* `{value}`" if not isinstance(value, str) or " " not in value else f"*{label}:* {value}")
        if details.get("files_changed"):
            lines.append(f"*Files:* `{', '.join(details['files_changed'])}`")
        tests = details.get("tests") or []
        if tests:
            passed = sum(int(item.get("returncode", 1)) == 0 for item in tests)
            lines.append(f"*Tests:* {passed}/{len(tests)} passed")
        if details.get("unsafe_branch_published") is False:
            lines.append("*Safety:* no unsafe branch was published")
        lines.append(f"*Recorded:* {event.get('created_at')}")
        return "\n".join(lines)

    @staticmethod
    def projection_key(event: dict) -> tuple[str, str] | None:
        details = event.get("details") or {}
        event_type = str(event.get("event_type") or "")
        assignment_id = str(event.get("assignment_id") or "")
        disposition = str(details.get("disposition") or "")
        if event_type in {"assignment_retry", "observability_recovered"} or disposition in {
            "blocked",
            "failed",
            "recovery-required",
        }:
            incident_id = str(
                details.get("incident_id")
                or assignment_id
                or event.get("event_id")
            )
            return ("incident", incident_id)
        if event_type in {"decision_required", "product_owner_decision"}:
            decision_id = str(
                details.get("decision_id")
                or event.get("work_item")
                or event.get("event_id")
            )
            return ("decision", decision_id)
        if assignment_id:
            return ("assignment", assignment_id)
        return None

    @classmethod
    def aggregate_notifications(
        cls, notifications: list[dict], window_seconds: int = 60
    ) -> list[list[dict]]:
        groups: list[list[dict]] = []
        group_keys: list[tuple[str, str] | None] = []
        semantic_fingerprints: list[set[str]] = []
        for item in sorted(
            notifications,
            key=lambda value: (
                int((value.get("event") or {}).get("created_at_epoch") or 0),
                str(value.get("notification_id") or ""),
            ),
        ):
            event = item.get("event") or {}
            key = cls.projection_key(event)
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "key": key,
                        "event_type": event.get("event_type"),
                        "lifecycle_state": event.get("lifecycle_state"),
                        "details": event.get("details") or {},
                    },
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            event_epoch = int(event.get("created_at_epoch") or 0)
            matched = None
            for index, group in enumerate(groups):
                first_epoch = int(
                    ((group[0].get("event") or {}).get("created_at_epoch") or 0)
                )
                if key is not None and group_keys[index] == key and event_epoch - first_epoch <= window_seconds:
                    matched = index
                    break
            if matched is None:
                groups.append([item])
                group_keys.append(key)
                semantic_fingerprints.append({fingerprint})
            elif fingerprint not in semantic_fingerprints[matched]:
                groups[matched].append(item)
                semantic_fingerprints[matched].add(fingerprint)
            else:
                groups[matched].append(item | {"semantic_duplicate": True})
        return groups

    @classmethod
    def render_event_group(cls, group: list[dict], recovery: bool = False) -> str:
        events = [item["event"] for item in group if not item.get("semantic_duplicate")]
        if not events:
            events = [group[-1]["event"]]
        if recovery:
            event_names = ", ".join(event["event_type"] for event in events)
            return (
                "*AXIS Supervisor — Recovered missed activity*\n"
                f"Recovered {len(group)} queued event(s): {event_names}\n"
                f"Latest: {cls.render_event(events[-1])}"
            )
        assignment_type = next(
            (
                str((event.get("details") or {}).get("assignment_type"))
                for event in reversed(events)
                if (event.get("details") or {}).get("assignment_type")
            ),
            "",
        )
        if len(group) > 1 and assignment_type in {
            "read-only-analysis",
            "no-op-verification",
        }:
            return (
                "*AXIS Supervisor — Analysis activity*\n"
                f"Collapsed {len(group)} read-only/no-op events in a 60s window.\n"
                f"Latest: {cls.render_event(events[-1])}"
            )
        if len(group) > 1:
            return (
                f"*AXIS Supervisor — {len(group)} related events (60s)*\n"
                f"Latest: {cls.render_event(events[-1])}"
            )
        return cls.render_event(events[0])

    def process_outbox(self, token: str, channel: str, state: dict | None = None) -> dict:
        outbox = self.load_outbox()
        state = state if state is not None else self.load_state()
        state.setdefault(
            "projection_timestamps",
            {"dashboard": {}, "assignment": {}, "incident": {}, "decision": {}},
        )
        state.setdefault(
            "projection_fingerprints",
            {"dashboard": {}, "assignment": {}, "incident": {}, "decision": {}},
        )
        now = int(time.time())
        pending = [
            item
            for item in outbox["notifications"]
            if item["current_stage"] != "Slack_message_verified"
            and int(item.get("next_attempt_epoch") or 0) <= now
        ]
        if not pending:
            return outbox
        recovery = any(item["current_stage"] == "delivery_failed" for item in pending)
        groups = (
            [pending[:20]]
            if recovery
            else self.aggregate_notifications(pending[:50])[:10]
        )
        for group in groups:
            text = self.render_event_group(group, recovery)
            projection = self.projection_key(group[-1]["event"])
            text_fingerprint = hashlib.sha256(text.encode()).hexdigest()
            for item in group:
                item["attempts"] += 1
                self.advance(item, "notification_send_attempted")
            self.write_outbox(outbox)
            try:
                existing_ts = (
                    (state.get("projection_timestamps") or {})
                    .get(projection[0], {})
                    .get(projection[1])
                    if projection
                    else None
                )
                existing_fingerprint = (
                    (state.get("projection_fingerprints") or {})
                    .get(projection[0], {})
                    .get(projection[1])
                    if projection
                    else None
                )
                if existing_ts and existing_fingerprint == text_fingerprint:
                    response = {"ok": True, "channel": channel, "ts": existing_ts}
                    operation_stage = "Slack_message_updated"
                elif existing_ts:
                    try:
                        response = self.api(
                            token,
                            "chat.update",
                            {"channel": channel, "ts": existing_ts, "text": text},
                        )
                        operation_stage = "Slack_message_updated"
                    except RuntimeError as exc:
                        if "message_not_found" not in str(exc):
                            raise
                        response = self.api(
                            token,
                            "chat.postMessage",
                            {"channel": channel, "text": text},
                        )
                        operation_stage = "Slack_message_created"
                else:
                    response = self.api(
                        token, "chat.postMessage", {"channel": channel, "text": text}
                    )
                    operation_stage = "Slack_message_created"
                response_channel = str(response.get("channel") or "")
                response_ts = str(response.get("ts") or "")
                if response_channel != channel or not response_ts:
                    raise RuntimeError("Slack API response omitted expected channel or timestamp")
                for item in group:
                    self.advance(item, "Slack_API_accepted")
                    self.advance(item, operation_stage)
                    item["channel"] = response_channel
                    item["ts"] = response_ts
                    item["recovery_summary"] = recovery
                if projection:
                    state["projection_timestamps"][projection[0]][projection[1]] = response_ts
                    state["projection_fingerprints"][projection[0]][projection[1]] = text_fingerprint
                    self.write_state(state)
                self.write_outbox(outbox)
                if existing_fingerprint != text_fingerprint:
                    self.verify_message(token, channel, response_ts, text)
                for item in group:
                    self.advance(item, "Slack_message_verified")
                    item["last_error"] = None
                    item["next_attempt_epoch"] = 0
                self.write_outbox(outbox)
            except Exception as exc:
                for item in group:
                    self.advance(item, "delivery_failed")
                    item["last_error"] = f"{type(exc).__name__}: {exc}"
                    item["next_attempt_epoch"] = now + min(300, 10 * (2 ** min(item["attempts"], 5)))
                self.write_outbox(outbox)
                raise
        return outbox

    def project_decisions(
        self, token: str, channel: str, graph: dict, state: dict
    ) -> None:
        for node in graph.get("nodes") or []:
            packet = ((node.get("semantic_record") or {}).get("decision_packet"))
            if not isinstance(packet, dict):
                continue
            decision_id = str(node.get("ref") or packet.get("decision_id") or "unknown")
            text = (
                f"*AXIS Supervisor — Product Owner decision — {decision_id}*\n"
                f"{packet.get('decision_requested')}\n"
                f"*Recommendation:* {packet.get('recommendation')}\n"
                f"*Response:* `{packet.get('response_syntax')}`"
            )
            fingerprint = hashlib.sha256(text.encode()).hexdigest()
            if state["projection_fingerprints"]["decision"].get(decision_id) == fingerprint:
                continue
            ts = state["projection_timestamps"]["decision"].get(decision_id)
            payload = {"channel": channel, "text": text}
            response = self.api(
                token,
                "chat.update" if ts else "chat.postMessage",
                payload | ({"ts": ts} if ts else {}),
            )
            response_ts = str(response.get("ts") or "")
            if str(response.get("channel") or "") != channel or not response_ts:
                raise RuntimeError("Slack decision projection omitted channel or timestamp")
            state["projection_timestamps"]["decision"][decision_id] = response_ts
            state["projection_fingerprints"]["decision"][decision_id] = fingerprint
            self.write_state(state)
            self.verify_message(token, channel, response_ts, text)

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
        state: dict | None = None,
        outbox: dict | None = None,
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
        state = state or {}
        outbox = outbox or self.load_outbox()
        quality_path = self.root / "roadmap-quality.json"
        roadmap_quality = (
            read_record(
                quality_path,
                "axis.external-development-supervisor.roadmap-quality",
            )
            if quality_path.exists()
            else {}
        )
        quality_metrics = roadmap_quality.get("metrics") or {}
        quality_trend = roadmap_quality.get("trend") or {}
        convergence_path = self.root / "repository-convergence.json"
        repository_convergence = (
            read_record(
                convergence_path,
                "axis.external-development-supervisor.repository-convergence",
            )
            if convergence_path.exists()
            else {}
        )
        convergence_counts = repository_convergence.get("counts") or {}
        capability_path = self.root / "capability-convergence.json"
        capability_convergence = (
            read_record(
                capability_path,
                "axis.external-development-supervisor.capability-convergence",
            )
            if capability_path.exists()
            else {}
        )
        assignments = self.live_assignments() or inventory.get("supervisor_assignments") or []
        active = [item for item in assignments if not is_terminal(item)]
        analysis_workers = [
            item
            for item in active
            if item.get("assignment_type")
            in {"read-only-analysis", "no-op-verification"}
        ]
        coding_workers = [
            item
            for item in active
            if item.get("assignment_type")
            in {
                "governance-document-mutation",
                "code-implementation",
                "ci-integration-repair",
            }
            and item.get("lifecycle_state") != "awaiting-integration"
        ]
        integration_workers = [
            item for item in active if item.get("lifecycle_state") == "awaiting-integration"
        ]
        active_grants = []
        for grant_path in (self.root / "mutation-grants").glob("*/grant.json"):
            try:
                grant = read_record(
                    grant_path,
                    "axis.external-development-supervisor.mutation-grant",
                )
            except Exception:
                continue
            if grant.get("status") == "active":
                active_grants.append(grant)
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
        jobs = self.cron_jobs()
        worker_job = next(
            (item for item in jobs if item.get("name") == "axis-development-supervisor-worker"),
            {},
        )
        reporter_job = next(
            (item for item in jobs if item.get("name") == "axis-development-supervisor-report"),
            {},
        )
        event_log = OperationalEventLog(self.root, "reporter")
        events = event_log.events(limit=50)
        now_epoch = int(time.time())
        daily_metrics = event_log.throughput_metrics(now_epoch - 86_400, now_epoch)
        weekly_metrics = event_log.throughput_metrics(
            now_epoch - 7 * 86_400, now_epoch
        )
        monthly_metrics = event_log.throughput_metrics(
            now_epoch - 30 * 86_400, now_epoch
        )
        scheduler_flow = graph.get("scheduler_state") or {}
        constraint = scheduler_flow.get("current_constraint") or {}
        flow_counts = graph.get("flow_counts") or {}
        roadmap_percent = round(verified * 100 / total) if total else 0
        weekly_daily_velocity = weekly_metrics["post_main_verified"] / 7
        velocity_direction = (
            "↑"
            if daily_metrics["post_main_verified"] > weekly_daily_velocity
            else "↓"
            if daily_metrics["post_main_verified"] < weekly_daily_velocity
            else "→"
        )
        remaining_roadmap = max(0, total - verified)
        monthly_verified = monthly_metrics["post_main_verified"]
        forecast_days = (
            round(remaining_roadmap / (monthly_verified / 30), 1)
            if monthly_verified >= 3
            else None
        )
        forecast_confidence = (
            "observed"
            if monthly_verified >= 10
            else "low-sample"
            if monthly_verified >= 3
            else "insufficient-history"
        )
        meaningful = [event for event in events if event.get("event_type") not in {"cycle_completed", "reconciliation_completed"}]
        last_progress = meaningful[-1] if meaningful else None
        implemented_since_update = sum(
            event.get("event_type") in {"mr_merged", "post_main_verified"}
            for event in meaningful
        )
        analyzed_since_update = sum(
            event.get("event_type") == "assignment_disposition"
            and (event.get("details") or {}).get("assignment_type")
            in {"read-only-analysis", "no-op-verification"}
            for event in meaningful
        )
        pending_notifications = [
            item
            for item in outbox.get("notifications") or []
            if item.get("current_stage") != "Slack_message_verified"
        ]
        scheduling_state = "green" if worker_job.get("enabled") and reporter_job.get("enabled") else "red"
        execution_state = "green" if not collection.get("state_record_errors") else "red"
        integration_state = "amber" if any(a.get("lifecycle_state") == "awaiting-integration" for a in active) else "green"
        gitlab_state = "green" if int(collection.get("retrieval_error_count", 0)) == 0 else "red"
        slack_state = (
            "green"
            if state.get("delivery_stage") == "Slack_message_verified" and not pending_notifications
            else "red"
            if state.get("delivery_stage") == "delivery_failed" or any(item.get("current_stage") == "delivery_failed" for item in pending_notifications)
            else "amber"
        )
        roadmap_advancement = (
            "green"
            if active_grants or coding_workers or integration_workers
            else "amber"
        )
        overall_operability = (
            "green"
            if {
                scheduling_state,
                execution_state,
                integration_state,
                gitlab_state,
                slack_state,
                roadmap_advancement,
            }
            == {"green"}
            else "amber"
        )
        icon = "🟢" if health == "healthy" else "🟡"

        fallback = (
            f"AXIS Build Supervisor — {health}; mode={control.get('mode')}; "
            f"governed-items={total}; verified={verified}; active={len(active)}; "
            f"work-remaining={supervisor_work['supervisor_work_remaining']}; "
            f"ready-queue={supervisor_work['ready_work_total']}; blocked={blocked}; waiting={waiting}; "
            f"confidence={confidence.get('percent', 0)}%"
            f"; observability={slack_state}; last-event={(last_progress or {}).get('event_type', 'none')}"
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
                    "text": "*Capability Deployment Rings*\n"
                    + (
                        "\n".join(
                            f"• Ring {value.get('ring')} {value.get('display_name')}: *{value.get('status')}* — running `{str(value.get('running_revision') or 'unknown')[:12]}` — {value.get('capability_lag', 0)} capability lag — {', '.join(value.get('capabilities_behind') or []) or 'converged'}"
                            for value in capability_convergence.get("runtimes") or []
                        )
                        or "Runtime capability identity has not been projected yet."
                    )
                    + f"\nPromotion: `{json.dumps(capability_convergence.get('promotion_status') or {}, sort_keys=True)}`",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Repository Convergence*\n"
                    f"Status: *{repository_convergence.get('status', 'unknown')}* | "
                    f"Active {convergence_counts.get('active_branches', 0)} | "
                    f"Merge-ready {convergence_counts.get('merge_ready_branches', 0)} | "
                    f"Cleanup-ready {convergence_counts.get('cleanup_ready_branches', 0)} | "
                    f"Retained {convergence_counts.get('retained_branches', 0)} | "
                    f"Ambiguous {convergence_counts.get('ambiguous_branches', 0)} | "
                    f"Orphan {convergence_counts.get('orphan_branches', 0)} | "
                    f"Orphan worktrees {convergence_counts.get('orphan_worktrees', 0)}\n"
                    + (
                        "\n".join(
                            f"• `{value.get('repository')}:{value.get('branch')}` — {value.get('status')} — {value.get('next_action')}"
                            for value in (repository_convergence.get("branches") or [])[:8]
                        )
                        or "All configured repositories expose only canonical main."
                    ),
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Roadmap Quality — advisory*\n"
                    f"Quality score: *{quality_metrics.get('roadmap_quality_score', 'n/a')}%* | "
                    f"Hygiene: *{quality_metrics.get('roadmap_hygiene_score', 'n/a')}%* | "
                    f"Graph completeness: *{quality_metrics.get('graph_completeness', 'n/a')}%*\n"
                    f"Milestone ownership: {quality_metrics.get('milestone_ownership_coverage', 'n/a')}% | "
                    f"Engineering owner: {quality_metrics.get('engineering_owner_coverage', 'n/a')}% | "
                    f"PlanningRecord: {quality_metrics.get('planning_record_coverage', 'n/a')}% | "
                    f"Authority: {quality_metrics.get('execution_authority_coverage', 'n/a')}%\n"
                    f"Typed dependencies: {quality_metrics.get('typed_dependency_coverage', 'n/a')}% | "
                    f"Implementation readiness: {quality_metrics.get('implementation_readiness_coverage', 'n/a')}% | "
                    f"Historical archive: {quality_metrics.get('historical_archive_coverage', 'n/a')}%\n"
                    f"Critical path: *{quality_metrics.get('critical_path_computability', 'unknown')}* | "
                    f"Quality trend: `{json.dumps(quality_trend, sort_keys=True)}`\n"
                    f"Advisory proposals: `{', '.join(value.get('proposal_id', '') for value in roadmap_quality.get('proposals') or []) or 'none'}`",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Engineering Flow & Forecast*\n"
                    f"Roadmap: *{roadmap_percent}%* ({verified}/{total} verified) | "
                    f"Velocity trend: *{velocity_direction}* | 30d verified: {monthly_verified}\n"
                    f"Flow WIP — Analysis {scheduler_flow.get('wip_counts', {}).get('analysis', 0)}/{scheduler_flow.get('wip_limits', {}).get('analysis', 0)} | "
                    f"Implementation {scheduler_flow.get('wip_counts', {}).get('implementation', 0)}/{scheduler_flow.get('wip_limits', {}).get('implementation', 0)} | "
                    f"Integration {scheduler_flow.get('wip_counts', {}).get('integration', 0)}/{scheduler_flow.get('wip_limits', {}).get('integration', 0)} | "
                    f"Verification {scheduler_flow.get('wip_counts', {}).get('verification', 0)}/{scheduler_flow.get('wip_limits', {}).get('verification', 0)}\n"
                    f"Flow inventory — Backlog {flow_counts.get('backlog', 0)} | Discovery {flow_counts.get('discovery', 0)} | "
                    f"Analysis {flow_counts.get('analysis', 0)} | Implementation-ready {flow_counts.get('implementation-ready', 0)} | "
                    f"Verification {flow_counts.get('verification', 0)} | Verified {flow_counts.get('verified-complete', 0)}\n"
                    f"Current constraint: *{constraint.get('name') or 'unknown'}*\n"
                    f"Evidence: {'; '.join(constraint.get('evidence') or ['none'])}\n"
                    f"Impact: {constraint.get('engineering_impact') or 'unknown'}\n"
                    f"Corrective action: {constraint.get('recommended_action') or 'none'}\n"
                    f"Roadmap forecast: *{str(forecast_days) + ' days' if forecast_days is not None else 'unavailable'}* "
                    f"({forecast_confidence}; assumes observed 30d verified throughput continues; uncertainty improves with more verified samples)",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Engineering Throughput — rolling 24h / 7d*\n"
                    f"Post-main verified: *{daily_metrics['post_main_verified']} / {weekly_metrics['post_main_verified']}* | "
                    f"Merged: *{daily_metrics['merged']} / {weekly_metrics['merged']}* | "
                    f"Implementation commits: *{daily_metrics['implementation_commits']} / {weekly_metrics['implementation_commits']}*\n"
                    f"Analysis completed: {daily_metrics['analysis_completed']} / {weekly_metrics['analysis_completed']} | "
                    f"Blocked/failed: {daily_metrics['blocked_or_failed']} / {weekly_metrics['blocked_or_failed']} | "
                    f"Retries: {daily_metrics['retries']} / {weekly_metrics['retries']}\n"
                    f"Analysis→implementation: *{daily_metrics['analysis_to_implementation_percent']}%* | "
                    f"Implementation→merge: *{daily_metrics['implementation_to_merge_percent']}%* | "
                    f"Merge→verified: *{daily_metrics['merge_to_verified_percent']}%*\n"
                    f"Average implementation: `{daily_metrics['average_implementation_seconds'] or 'n/a'}s` | "
                    f"Average integration: `{daily_metrics['average_integration_seconds'] or 'n/a'}s` | "
                    f"Retry rate: `{daily_metrics['retry_rate_percent']}%`",
                },
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Operational Loop*\n"
                    f"Scheduling: *{scheduling_state}* | Execution: *{execution_state}* | "
                    f"Integration: *{integration_state}* | GitLab: *{gitlab_state}* | Slack: *{slack_state}*\n"
                    f"Roadmap advancement: *{roadmap_advancement}* | Overall unattended operability: *{overall_operability}*\n"
                    f"Mutation default: *{'enabled' if control.get('allow_repository_mutation') else 'disabled'}* | Active bounded grants: {len(active_grants)}\n"
                    f"Analysis workers: {len(analysis_workers)} | Coding workers: {len(coding_workers)} | Integration workers: {len(integration_workers)}\n"
                    f"Merged/post-main events: {implemented_since_update} | Analyzed-only events: {analyzed_since_update}\n"
                    f"Last successful worker cron: `{worker_job.get('last_run_at') or 'none'}` ({worker_job.get('last_status') or 'unknown'})\n"
                    f"Last meaningful progress: `{(last_progress or {}).get('event_type') or 'none'}` at `{(last_progress or {}).get('created_at') or 'none'}`\n"
                    f"Active assignments: {len(active)}/{control.get('max_active_assignments', 1)} | Integrator: {'awaiting MR' if integration_workers else 'idle'}\n"
                    f"Pending Slack events: {len(pending_notifications)} | Last Slack verification: `{state.get('last_verified_at') or 'unverified'}`\n"
                    f"Next worker cycle: `{worker_job.get('next_run_at') or 'unknown'}` | Next status cycle: `{reporter_job.get('next_run_at') or 'unknown'}`",
                },
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
                        f"*Why selected now:* `{(selected[0] if selected else {}).get('selection_rationale') or 'no executable selection'}`\n"
                        f"*Deferred:* `{self.scheduler_refs(deferred)}`\n"
                        f"*First deferred reason:* `{(deferred[0] if deferred else {}).get('selection_rationale') or 'none'}`\n"
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
            assignment_type = str(assignment.get("assignment_type") or "unknown")
            worker_label = (
                "deterministic integrator"
                if lifecycle_state == "awaiting-integration"
                else "GPT-5.4 analysis"
                if assignment_type
                in {"read-only-analysis", "no-op-verification"}
                else "GPT-5.3-Codex coding"
            )
            blocks.extend(
                [
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"🔵 *{assignment.get('work_item') or assignment.get('target_ref')}*\n"
                            f"Worker: {worker_label} | Type: {assignment_type}\n"
                            f"Lifecycle: {lifecycle_state} | Assignment result: {assignment.get('result_state')}\n"
                            f"Work item disposition: {assignment.get('work_item_disposition')} | Grant: {assignment.get('mutation_grant_id') or 'none'}\n"
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
        state = self.load_state()
        now = utc_now()
        state = {
            "schema": "axis.external-development-supervisor.slack-state",
            "schema_version": "1.1.0",
            "delivery_stage": state.get("delivery_stage") or "delivery_unknown",
            "delivery_history": state.get("delivery_history") or [],
            "last_attempt_at": now,
            "semantic_revision": state.get("semantic_revision") or "pending",
            "source_revision": state.get("source_revision") or {},
            "record_schema": "axis.external-development-supervisor.roadmap-semantics",
            "record_schema_version": state.get("record_schema_version") or "1.2.0",
            "last_delivery_error": state.get("last_delivery_error"),
            "workspace_id": state.get("workspace_id"),
            "workspace_name": state.get("workspace_name"),
            "bot_user_id": state.get("bot_user_id"),
            "authorized_user_id": user_id,
            "channel": state.get("channel"),
            "ts": state.get("ts"),
            "previous_ts": state.get("previous_ts"),
            "fingerprint": state.get("fingerprint"),
            "message_operation": state.get("message_operation"),
            "last_verified_at": state.get("last_verified_at"),
            "last_successful_update_at": state.get("last_successful_update_at"),
            "last_successful_update_epoch": state.get("last_successful_update_epoch"),
            "updated_at_epoch": state.get("updated_at_epoch"),
            "last_api_response": state.get("last_api_response"),
            "projection_timestamps": state.get("projection_timestamps")
            or {
                "dashboard": {},
                "assignment": {},
                "incident": {},
                "decision": {},
            },
            "projection_fingerprints": state.get("projection_fingerprints")
            or {
                "dashboard": {},
                "assignment": {},
                "incident": {},
                "decision": {},
            },
            "dashboard_fallback": state.get("dashboard_fallback"),
        }

        def state_stage(stage: str) -> None:
            if stage not in DELIVERY_STAGES:
                raise ValueError(f"unsupported Slack delivery stage: {stage}")
            state["delivery_stage"] = stage
            state["delivery_history"].append({"stage": stage, "at": utc_now()})
            state["delivery_history"] = state["delivery_history"][-100:]

        semantics = build_roadmap_semantics(
            inventory, graph, control, self.deployed_revision()
        )
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(
            self.record_path,
            semantics,
            "axis.external-development-supervisor.roadmap-semantics",
        )
        try:
            auth = self.api(token, "auth.test", {})
            if not auth.get("team_id") or not auth.get("user_id"):
                raise RuntimeError("Slack auth.test omitted workspace or bot identity")
            state["workspace_id"] = auth["team_id"]
            state["workspace_name"] = auth.get("team")
            state["bot_user_id"] = auth["user_id"]
            opened = self.api(token, "conversations.open", {"users": user_id})
            channel = str((opened.get("channel") or {}).get("id") or "")
            if not channel:
                raise RuntimeError("Slack conversations.open omitted DM channel")
            if state.get("channel") and state["channel"] != channel:
                raise RuntimeError("Slack DM channel does not match persisted Product Owner route")
            state["channel"] = channel
            outbox = self.process_outbox(token, channel, state)
            fallback, blocks, fingerprint = self.render(
                inventory, graph, control, semantics, state, outbox
            )
            state["dashboard_fallback"] = {
                "text": fallback,
                "blocks": blocks,
                "fingerprint": fingerprint,
            }
            ts = state.get("ts")
            if state.get("fingerprint") == fingerprint and ts:
                self.verify_message(token, channel, ts, fallback)
                state_stage("Slack_message_verified")
                state["message_operation"] = "verified"
                state["last_verified_at"] = utc_now()
                state["last_delivery_error"] = None
                state["semantic_revision"] = semantics["semantic_revision"]
                state["source_revision"] = (semantics.get("source") or {}).get(
                    "deployed_revision"
                ) or {}
                state["last_successful_update_at"] = state["last_verified_at"]
                state["last_successful_update_epoch"] = int(time.time())
                state["updated_at_epoch"] = int(time.time())
                self.write_state(state)
                return {
                    "updated": False,
                    "channel": channel,
                    "ts": ts,
                    "delivery_stage": state["delivery_stage"],
                    "message_operation": "verified",
                    "workspace_id": state["workspace_id"],
                }
            payload = {"channel": channel, "text": fallback, "blocks": blocks}
            state_stage("notification_created")
            state_stage("notification_queued")
            state_stage("notification_send_attempted")
            self.write_state(state)
            try:
                if ts:
                    response = self.api(token, "chat.update", payload | {"ts": ts})
                    operation_stage = "Slack_message_updated"
                    operation = "updated"
                else:
                    response = self.api(token, "chat.postMessage", payload)
                    operation_stage = "Slack_message_created"
                    operation = "created"
            except RuntimeError as exc:
                if "message_not_found" not in str(exc):
                    raise
                state["previous_ts"] = ts
                response = self.api(token, "chat.postMessage", payload)
                operation_stage = "Slack_message_created"
                operation = "created"
            response_channel = str(response.get("channel") or "")
            response_ts = str(response.get("ts") or "")
            if response_channel != channel or not response_ts:
                raise RuntimeError("Slack API response omitted expected DM channel or timestamp")
            state_stage("Slack_API_accepted")
            state_stage(operation_stage)
            state["channel"] = response_channel
            state["ts"] = response_ts
            state["projection_timestamps"]["dashboard"]["overview"] = response_ts
            state["projection_fingerprints"]["dashboard"]["overview"] = fingerprint
            state["last_api_response"] = {
                "ok": bool(response.get("ok")),
                "channel": response_channel,
                "ts": response_ts,
            }
            state["message_operation"] = operation
            self.write_state(state)
            self.verify_message(token, channel, response_ts, fallback)
            state_stage("Slack_message_verified")
            self.project_decisions(token, channel, graph, state)
        except Exception as delivery_exc:
            state_stage("delivery_failed")
            state["last_delivery_error"] = f"{type(delivery_exc).__name__}: {delivery_exc}"
            state["semantic_revision"] = semantics["semantic_revision"]
            state["source_revision"] = (semantics.get("source") or {}).get(
                "deployed_revision"
            ) or {}
            self.write_state(state)
            raise
        verified_at = utc_now()
        state["fingerprint"] = fingerprint
        state["updated_at_epoch"] = int(time.time())
        state["last_verified_at"] = verified_at
        state["last_successful_update_at"] = verified_at
        state["last_successful_update_epoch"] = int(time.time())
        state["semantic_revision"] = semantics["semantic_revision"]
        state["source_revision"] = (semantics.get("source") or {}).get(
            "deployed_revision"
        ) or {}
        state["record_schema"] = semantics["schema"]
        state["record_schema_version"] = semantics["schema_version"]
        state["last_delivery_error"] = None
        self.write_state(state)
        return {
            "updated": True,
            "channel": state["channel"],
            "ts": state["ts"],
            "delivery_stage": state["delivery_stage"],
            "message_operation": state["message_operation"],
            "workspace_id": state["workspace_id"],
            "last_verified_at": state["last_verified_at"],
        }

    def load_state(self) -> dict:
        if not self.state_path.exists():
            return {}
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if value.get("schema_version") != "1.1.0":
            timestamp = datetime.fromtimestamp(
                int(value.get("updated_at_epoch") or time.time()), timezone.utc
            ).isoformat()
            value = {
                "schema": "axis.external-development-supervisor.slack-state",
                "schema_version": "1.1.0",
                "delivery_stage": "delivery_unknown",
                "delivery_history": [
                    {"stage": "delivery_unknown", "at": timestamp}
                ],
                "last_attempt_at": value.get("last_attempt_at") or timestamp,
                "semantic_revision": value.get("semantic_revision") or "legacy",
                "source_revision": value.get("source_revision") or {},
                "record_schema": value.get("record_schema") or "axis.external-development-supervisor.roadmap-semantics",
                "record_schema_version": value.get("record_schema_version")
                or "1.1.0",
                "last_delivery_error": value.get("last_delivery_error"),
                "workspace_id": None,
                "workspace_name": None,
                "bot_user_id": None,
                "authorized_user_id": None,
                "channel": value.get("channel"),
                "ts": None,
                "previous_ts": value.get("ts"),
                "fingerprint": None,
                "message_operation": None,
                "last_verified_at": None,
                "last_successful_update_at": value.get("last_successful_update_at"),
                "last_successful_update_epoch": value.get("last_successful_update_epoch"),
                "updated_at_epoch": value.get("updated_at_epoch"),
                "last_api_response": None,
                "projection_timestamps": {
                    "dashboard": {},
                    "assignment": {},
                    "incident": {},
                    "decision": {},
                },
                "projection_fingerprints": {
                    "dashboard": {},
                    "assignment": {},
                    "incident": {},
                    "decision": {},
                },
                "dashboard_fallback": None,
            }
        value.setdefault(
            "projection_timestamps",
            {"dashboard": {}, "assignment": {}, "incident": {}, "decision": {}},
        )
        value.setdefault(
            "projection_fingerprints",
            {"dashboard": {}, "assignment": {}, "incident": {}, "decision": {}},
        )
        value.setdefault("dashboard_fallback", None)
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
