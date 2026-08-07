import hashlib
import json
import re
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .records import atomic_write, load_optional, timestamp

Api = Callable[[str, str, dict[str, Any]], dict[str, Any]]


def _fingerprint(text: str, blocks: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps({"text": text, "blocks": blocks}, sort_keys=True).encode()
    ).hexdigest()


class SlackProjector:
    """Projects watchdog-owned views while preserving the existing Slack map."""

    def __init__(self, supervisor_root: Path, api: Api | None = None):
        self.supervisor_root = supervisor_root
        self.state_path = supervisor_root / "slack-overview-state.json"
        self.api_call = api or self.api

    @staticmethod
    def env_file() -> dict[str, str]:
        values: dict[str, str] = {}
        path = Path.home() / ".hermes" / ".env"
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            values[key] = value
        return values

    @staticmethod
    def api(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
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
        if not isinstance(value, dict) or not value.get("ok"):
            raise RuntimeError(f"Slack {method} failed: {(value or {}).get('error')}")
        return value

    @staticmethod
    def _empty_state(at_epoch: int, user_id: str) -> dict[str, Any]:
        now = timestamp(at_epoch)
        return {
            "schema": "axis.external-development-supervisor.slack-state",
            "schema_version": "1.1.0",
            "delivery_stage": "delivery_unknown",
            "delivery_history": [{"stage": "delivery_unknown", "at": now}],
            "last_attempt_at": now,
            "semantic_revision": "watchdog-owned",
            "source_revision": {},
            "record_schema": "axis.external-development-supervisor.roadmap-semantics",
            "record_schema_version": "1.2.0",
            "last_delivery_error": None,
            "workspace_id": None,
            "workspace_name": None,
            "bot_user_id": None,
            "authorized_user_id": user_id,
            "channel": None,
            "ts": None,
            "previous_ts": None,
            "fingerprint": None,
            "message_operation": None,
            "last_verified_at": None,
            "last_successful_update_at": None,
            "last_successful_update_epoch": None,
            "updated_at_epoch": None,
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

    @staticmethod
    def _normalize_state(
        current: dict[str, Any], at_epoch: int, user_id: str
    ) -> dict[str, Any]:
        state = SlackProjector._empty_state(at_epoch, user_id)
        if current.get("schema_version") == "1.1.0":
            state.update(current)
        for key in ("projection_timestamps", "projection_fingerprints"):
            mapping = state.setdefault(key, {})
            for category in ("dashboard", "assignment", "incident", "decision"):
                mapping.setdefault(category, {})
        state["authorized_user_id"] = user_id
        return state

    @staticmethod
    def render_dashboard(report: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        summary = report["summary"]
        text = (
            f"AXIS | Watchdog {summary['overall']} | Mission {summary['mission']} | "
            f"Capabilities {summary['capabilities']} | Open incidents {summary['open_incidents']}"
        )
        blocks: list[dict[str, Any]] = []
        for title, body in report["sections"]:
            blocks.extend(
                [
                    {"type": "header", "text": {"type": "plain_text", "text": title}},
                    {
                        "type": "section",
                        "block_id": "axis_"
                        + re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_"),
                        "text": {"type": "mrkdwn", "text": str(body)[:2900]},
                    },
                ]
            )
        return text, blocks

    @staticmethod
    def render_incident(incident: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        status = str(incident["status"]).replace("-", " ").title()
        text = (
            f"AXIS Watchdog incident {incident['incident_id']} | {status} | "
            f"{incident['anomaly_code']}"
        )
        body = (
            f"*{status}: {incident['anomaly_code']}*\n"
            f"{incident['summary']}\n"
            f"Recovery level: *{incident['recovery_level']}* | "
            f"Repair target: `{incident.get('repair_repository') or 'watchdog-only'}`\n"
            f"Last observed: `{incident['observed_at']}`"
        )
        if incident.get("diagnosis"):
            body += f"\nDiagnostic: {incident['diagnosis']}"
        return text, [{"type": "section", "text": {"type": "mrkdwn", "text": body[:2900]}}]

    def _verify(self, token: str, channel: str, ts: str, expected_text: str) -> None:
        response = self.api_call(
            token,
            "conversations.history",
            {"channel": channel, "oldest": ts, "inclusive": True, "limit": 5},
        )
        message = next(
            (
                item
                for item in response.get("messages") or []
                if str(item.get("ts")) == ts
            ),
            None,
        )
        if not message or str(message.get("text") or "") != expected_text:
            raise RuntimeError("Slack message readback did not match expected text")

    def _project_one(
        self,
        token: str,
        channel: str,
        state: dict[str, Any],
        category: str,
        key: str,
        text: str,
        blocks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        fingerprint = _fingerprint(text, blocks)
        timestamps = state["projection_timestamps"][category]
        fingerprints = state["projection_fingerprints"][category]
        existing_ts = timestamps.get(key)
        if existing_ts and fingerprints.get(key) == fingerprint:
            self._verify(token, channel, existing_ts, text)
            operation = "verified"
            response_ts = existing_ts
        else:
            payload: dict[str, Any] = {"channel": channel, "text": text, "blocks": blocks}
            if existing_ts:
                payload["ts"] = existing_ts
            try:
                response = self.api_call(
                    token, "chat.update" if existing_ts else "chat.postMessage", payload
                )
            except RuntimeError as exc:
                if not existing_ts or "message_not_found" not in str(exc):
                    raise
                response = self.api_call(
                    token,
                    "chat.postMessage",
                    {"channel": channel, "text": text, "blocks": blocks},
                )
            response_ts = str(response.get("ts") or "")
            if str(response.get("channel") or "") != channel or not response_ts:
                raise RuntimeError("Slack projection omitted channel or timestamp")
            operation = "updated" if existing_ts and response_ts == existing_ts else "created"
            timestamps[key] = response_ts
            fingerprints[key] = fingerprint
            self._verify(token, channel, response_ts, text)
        return {
            "target_type": category,
            "target_id": key,
            "operation": operation,
            "status": "verified",
            "channel": channel,
            "ts": response_ts,
            "fingerprint": fingerprint,
        }

    def project(
        self,
        report: dict[str, Any],
        incidents: list[dict[str, Any]],
        at_epoch: int | None = None,
    ) -> list[dict[str, Any]]:
        at_epoch = at_epoch or int(time.time())
        control = load_optional(self.supervisor_root / "control.json")
        user_id = str(control.get("slack_user_id") or "")
        if not user_id:
            raise ValueError("supervisor slack_user_id is not configured")
        token = self.env_file()["SLACK_BOT_TOKEN"]
        state = self._normalize_state(load_optional(self.state_path), at_epoch, user_id)
        state["last_attempt_at"] = timestamp(at_epoch)
        state["delivery_stage"] = "notification_send_attempted"
        auth = self.api_call(token, "auth.test", {})
        opened = self.api_call(token, "conversations.open", {"users": user_id})
        channel = str((opened.get("channel") or {}).get("id") or "")
        if not auth.get("team_id") or not auth.get("user_id") or not channel:
            raise RuntimeError("Slack identity or DM channel was incomplete")
        if state.get("channel") and state["channel"] != channel:
            raise RuntimeError("Slack DM channel changed from the persisted Product Owner route")
        state.update(
            {
                "workspace_id": auth["team_id"],
                "workspace_name": auth.get("team"),
                "bot_user_id": auth["user_id"],
                "channel": channel,
            }
        )
        projections = []
        dashboard_text, dashboard_blocks = self.render_dashboard(report)
        projections.append(
            self._project_one(
                token,
                channel,
                state,
                "dashboard",
                "overview",
                dashboard_text,
                dashboard_blocks,
            )
        )
        state["dashboard_fallback"] = {
            "text": dashboard_text,
            "blocks": dashboard_blocks,
            "fingerprint": projections[0]["fingerprint"],
        }
        state["ts"] = projections[0]["ts"]
        state["fingerprint"] = projections[0]["fingerprint"]
        for incident in incidents:
            text, blocks = self.render_incident(incident)
            projections.append(
                self._project_one(
                    token,
                    channel,
                    state,
                    "incident",
                    str(incident["incident_id"]),
                    text,
                    blocks,
                )
            )
        verified_at = timestamp(at_epoch)
        state["delivery_stage"] = "Slack_message_verified"
        state["delivery_history"] = (
            list(state.get("delivery_history") or [])
            + [{"stage": "Slack_message_verified", "at": verified_at}]
        )[-100:]
        state["last_delivery_error"] = None
        state["last_verified_at"] = verified_at
        state["last_successful_update_at"] = verified_at
        state["last_successful_update_epoch"] = at_epoch
        state["updated_at_epoch"] = at_epoch
        state["message_operation"] = projections[0]["operation"]
        state["last_api_response"] = {
            "ok": True,
            "channel": channel,
            "ts": projections[0]["ts"],
        }
        atomic_write(self.state_path, state)
        return projections
