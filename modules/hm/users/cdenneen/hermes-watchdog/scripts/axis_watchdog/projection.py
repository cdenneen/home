import hashlib
import html
import json
import os
import re
import shlex
import subprocess
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .cutover import CutoverCoordinator
from .records import atomic_write, load_optional, timestamp

Api = Callable[[str, str, dict[str, Any]], dict[str, Any]]


def _fingerprint(text: str, blocks: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps({"text": text, "blocks": blocks}, sort_keys=True).encode()
    ).hexdigest()


def sanitize_slack(value: object, limit: int = 500) -> str:
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(value or ""))
    text = html.escape(text, quote=False).replace("@", "＠")
    return text[:limit]


class SlackProjector:
    """Adds watchdog incident cards to the canonical supervisor projection state."""

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
    def render_incident(incident: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        status = sanitize_slack(str(incident["status"]).replace("-", " ").title())
        anomaly_code = sanitize_slack(incident["anomaly_code"], 120)
        summary = sanitize_slack(incident["summary"])
        text = (
            f"AXIS Watchdog incident {incident['incident_id']} | {status} | "
            f"{anomaly_code}"
        )
        body = (
            f"*{status}: {anomaly_code}*\n"
            f"{summary}\n"
            f"Recovery level: *{incident['recovery_level']}* | "
            f"Repair target: `{incident.get('repair_repository') or 'watchdog-only'}`\n"
            f"Last observed: `{incident['observed_at']}`"
        )
        diagnosis = incident.get("diagnosis") or {}
        if isinstance(diagnosis, dict):
            if diagnosis.get("summary"):
                body += f"\nDiagnostic: {sanitize_slack(diagnosis['summary'])}"
            if diagnosis.get("recommended_action"):
                body += f"\nRecommended: {sanitize_slack(diagnosis['recommended_action'])}"
        return text, [
            {"type": "section", "text": {"type": "mrkdwn", "text": body[:2900]}}
        ]

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

    def _persist_accepted(
        self,
        state: dict[str, Any],
        *,
        category: str,
        key: str,
        channel: str,
        response_ts: str,
        fingerprint: str,
        at_epoch: int,
    ) -> None:
        now = timestamp(at_epoch)
        state["projection_timestamps"][category][key] = response_ts
        state["projection_fingerprints"][category][key] = fingerprint
        state["delivery_stage"] = "Slack_API_accepted"
        state["delivery_history"] = (
            list(state.get("delivery_history") or [])
            + [{"stage": "Slack_API_accepted", "at": now}]
        )[-100:]
        state["last_api_response"] = {
            "ok": True,
            "channel": channel,
            "ts": response_ts,
        }
        state["updated_at_epoch"] = at_epoch
        atomic_write(self.state_path, state)

    def _project_one(
        self,
        token: str,
        channel: str,
        state: dict[str, Any],
        category: str,
        key: str,
        text: str,
        blocks: list[dict[str, Any]],
        at_epoch: int,
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
            payload: dict[str, Any] = {
                "channel": channel,
                "text": text,
                "blocks": blocks,
            }
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
            self._persist_accepted(
                state,
                category=category,
                key=key,
                channel=channel,
                response_ts=response_ts,
                fingerprint=fingerprint,
                at_epoch=at_epoch,
            )
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

    def project_incidents(
        self, incidents: list[dict[str, Any]], at_epoch: int
    ) -> list[dict[str, Any]]:
        if not incidents:
            return []
        control = load_optional(self.supervisor_root / "control.json")
        user_id = str(control.get("slack_user_id") or "")
        if not user_id:
            raise ValueError("supervisor slack_user_id is not configured")
        token = self.env_file()["SLACK_BOT_TOKEN"]
        state = self._normalize_state(load_optional(self.state_path), at_epoch, user_id)
        channel = str(state.get("channel") or "")
        if not channel:
            raise RuntimeError("canonical Slack projector did not persist a DM channel")
        projections = []
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
                    at_epoch,
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
        atomic_write(self.state_path, state)
        return projections


class CanonicalSlackProjector:
    """Runs the canonical supervisor projector under watchdog scheduling authority."""

    def __init__(
        self,
        supervisor_root: Path,
        *,
        watchdog_root: Path | None = None,
        jobs_path: Path | None = None,
        command: str | None = None,
        runner: Any | None = None,
        incident_projector: SlackProjector | None = None,
        cutover: CutoverCoordinator | None = None,
    ):
        self.supervisor_root = supervisor_root
        self.watchdog_root = watchdog_root or (
            Path.home() / ".hermes" / "supervisor" / "axis-development-watchdog"
        )
        self.jobs_path = jobs_path or (Path.home() / ".hermes" / "cron" / "jobs.json")
        self.command = command or os.environ.get(
            "AXIS_WATCHDOG_CANONICAL_PROJECTOR"
        )
        self.runner = runner or subprocess.run
        self.incident_projector = incident_projector or SlackProjector(supervisor_root)
        self.cutover = cutover or CutoverCoordinator(
            self.watchdog_root,
            self.jobs_path,
            clock=lambda: int(time.time()),
        )

    def project(
        self,
        report: dict[str, Any],
        incidents: list[dict[str, Any]],
        at_epoch: int | None = None,
    ) -> list[dict[str, Any]]:
        del report
        at_epoch = at_epoch or int(time.time())
        shadow = self.cutover.mode() == "shadow"
        if not self.command:
            error = (
                "AXIS_WATCHDOG_CANONICAL_PROJECTOR must name a deployed command"
            )
            if not shadow:
                self.cutover.record_writer(False, error)
            raise RuntimeError(error)
        command = shlex.split(self.command) + (["--shadow"] if shadow else [])
        try:
            result = self.runner(
                command,
                text=True,
                capture_output=True,
                timeout=300,
                check=False,
                env=os.environ
                | {
                    "AXIS_SUPERVISOR_ROOT": str(self.supervisor_root),
                    "AXIS_SUPERVISOR_MUTATION_SOURCE": "watchdog-projector",
                },
            )
        except OSError as exc:
            error = f"{type(exc).__name__}: {exc}"
            if not shadow:
                self.cutover.record_writer(False, error)
            raise RuntimeError(f"canonical Slack projection could not start: {error}") from exc
        if result.returncode != 0:
            output = (result.stdout or "") + (result.stderr or "")
            if not shadow:
                self.cutover.record_writer(False, output[-1200:])
            raise RuntimeError(
                f"canonical Slack projection exited {result.returncode}: {output[-2000:]}"
            )
        lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
        canonical = json.loads(lines[-1]) if lines else {}
        state = load_optional(self.supervisor_root / "slack-overview-state.json")
        if shadow:
            shadow_record = {
                "schema": "axis.development-watchdog.slack-shadow",
                "schema_version": "1.0.0",
                "fingerprint": str(canonical.get("fingerprint") or ""),
                "fallback": canonical.get("fallback"),
                "blocks": canonical.get("blocks") or [],
                "observed_at": timestamp(at_epoch),
            }
            atomic_write(self.watchdog_root / "slack-shadow.json", shadow_record)
            try:
                self.cutover.record_shadow(
                    shadow_record["fingerprint"],
                    str(state.get("fingerprint") or "") or None,
                )
            except Exception as exc:
                self.cutover.rollback(f"shadow/parity transition failed: {exc}")
                raise
            return [
                {
                    "target_type": "dashboard",
                    "target_id": "overview",
                    "operation": "shadowed",
                    "status": "verified",
                    "channel": str(state.get("channel") or ""),
                    "ts": str(state.get("ts") or ""),
                    "fingerprint": shadow_record["fingerprint"],
                }
            ]
        projections = [
            {
                "target_type": "dashboard",
                "target_id": "overview",
                "operation": str(canonical.get("message_operation") or "verified"),
                "status": "verified",
                "channel": str(canonical.get("channel") or state.get("channel") or ""),
                "ts": str(canonical.get("ts") or state.get("ts") or ""),
                "fingerprint": str(state.get("fingerprint") or "canonical"),
            }
        ]
        try:
            projections.extend(
                self.incident_projector.project_incidents(incidents, at_epoch)
            )
        except Exception as exc:
            self.cutover.record_writer(False, f"{type(exc).__name__}: {exc}")
            raise
        try:
            self.cutover.record_writer(True)
        except Exception as exc:
            self.cutover.rollback(f"writer transition failed: {exc}")
            raise
        return projections
