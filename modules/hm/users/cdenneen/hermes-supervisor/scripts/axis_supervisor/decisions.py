import fcntl
import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .mutation import MutationGate, OperationClass
from .schema_registry import read_record, validate_record, write_record

DECISION_ID = "axis29-mcp-tranche-v2"
DECISION_DIGEST = "sha256:5ac201b880ffcfc6ca4642a7b9beb525d5e1dd0a3f784a01564139ed85c3dd3d"
DECISION_SCHEMA = "axis.external-development-supervisor.decision"
DECISION_CARD_SCHEMA = "axis.external-development-supervisor.decision-card"
DECISION_FRONTIER_SCHEMA = "axis.external-development-supervisor.decision-frontier-request"
APPROVE_ACTION_ID = "axis_decision_approve"
APPROVE_CONDITIONS_ACTION_ID = "axis_decision_approve_with_conditions"
REJECT_ACTION_ID = "axis_decision_reject"
CONDITIONS_SUBMIT_ACTION_ID = "axis_decision_conditions_submit"
CONDITIONS_BLOCK_ID = "axis_decision_conditions"
CONDITIONS_INPUT_ID = "conditions"
VERIFICATION_BLOCK_ID = "axis_decision_verification"
VERIFICATION_INPUT_ID = "verification"
MAX_CONDITIONS_LENGTH = 1200
MAX_VERIFICATION_LENGTH = 600


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") + ".json"


def decision_identity(decision_id: str, packet: dict) -> tuple[str, str]:
    identity = str(packet.get("decision_id") or decision_id)
    digest = str(packet.get("current_digest") or "").lower()
    return identity, digest


class DecisionStore:
    def __init__(self, root: Path):
        self.root = root
        self.decisions = root / "decisions"
        self.cards = root / "decision-cards"
        self.gate = MutationGate(root, source="decision-controller")

    def decision_path(self, decision_id: str) -> Path:
        return self.decisions / _filename(decision_id)

    def card_path(self, decision_id: str) -> Path:
        return self.cards / _filename(decision_id)

    def frontier_path(self, decision_id: str) -> Path:
        return self.decisions / (_filename(decision_id).removesuffix(".json") + ".frontier.json")

    def load(self, decision_id: str) -> dict | None:
        path = self.decision_path(decision_id)
        return read_record(path, DECISION_SCHEMA) if path.exists() else None

    def load_card(self, decision_id: str) -> dict | None:
        path = self.card_path(decision_id)
        return read_record(path, DECISION_CARD_SCHEMA) if path.exists() else None

    def save_card(self, value: dict) -> None:
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(self.card_path(value["decision_id"]), value, DECISION_CARD_SCHEMA)

    def load_frontier_request(self, decision_id: str) -> dict | None:
        path = self.frontier_path(decision_id)
        return read_record(path, DECISION_FRONTIER_SCHEMA) if path.exists() else None

    def request_frontier_rebuild(self, record: dict) -> dict:
        current = self.load_frontier_request(record["decision_id"])
        if current and current["status"] == "completed":
            return current
        value = {
            "schema": DECISION_FRONTIER_SCHEMA,
            "schema_version": "1.0.0",
            "decision_id": record["decision_id"],
            "digest": record["digest"],
            "status": "pending",
            "attempts": int((current or {}).get("attempts") or 0) + 1,
            "requested_at": (current or {}).get("requested_at") or utc_now(),
            "last_attempt_at": utc_now(),
            "completed_at": None,
        }
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(
            self.frontier_path(record["decision_id"]),
            value,
            DECISION_FRONTIER_SCHEMA,
        )
        return value

    def complete_frontier_rebuild(self, record: dict) -> dict:
        current = self.load_frontier_request(record["decision_id"])
        if current is None:
            raise ValueError("decision frontier rebuild was not requested")
        value = current | {"status": "completed", "completed_at": utc_now()}
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        write_record(
            self.frontier_path(record["decision_id"]),
            value,
            DECISION_FRONTIER_SCHEMA,
        )
        return value

    def persist(self, value: dict) -> tuple[dict, bool]:
        validate_record(value, DECISION_SCHEMA)
        decision_id = value["decision_id"]
        path = self.decision_path(decision_id)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = path.with_suffix(".lock")
        decision = self.gate.decide(OperationClass.RECONCILIATION)
        self.gate.require(decision, OperationClass.RECONCILIATION)
        with lock_path.open("a", encoding="utf-8") as lock:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            if path.exists():
                existing = read_record(path, DECISION_SCHEMA)
                if (
                    existing["digest"] != value["digest"]
                    or existing["outcome"] != value["outcome"]
                    or existing.get("conditions") != value.get("conditions")
                    or existing.get("verification") != value.get("verification")
                ):
                    raise ValueError("decision is already immutable with a different outcome")
                return existing, False
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("x", encoding="utf-8") as handle:
                    os.chmod(temporary, 0o600)
                    handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.link(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            return value, True

    def approval_for(self, decision_id: str, packet: dict) -> dict | None:
        identity, digest = decision_identity(decision_id, packet)
        if identity != DECISION_ID or digest != DECISION_DIGEST:
            return None
        record = self.load(identity)
        if record is None or record["digest"] != digest:
            return None
        if record["outcome"] not in {"approved", "approved-with-conditions"}:
            return None
        return record


class SlackDecisionController:
    def __init__(
        self,
        root: Path,
        api: Callable[[str, str, dict], dict],
        rebuild: Callable[[], object] | None = None,
    ):
        self.root = root
        self.api = api
        self.rebuild = rebuild
        self.store = DecisionStore(root)

    @staticmethod
    def action_value() -> str:
        return json.dumps(
            {"decision_id": DECISION_ID, "digest": DECISION_DIGEST},
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def render_card(
        cls,
        packet: dict,
        *,
        status: str = "pending",
        record: dict | None = None,
    ) -> tuple[str, list[dict]]:
        text = f"AXIS Product Owner decision: {DECISION_ID} ({status})"
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "AXIS Product Owner Decision"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Decision* `{DECISION_ID}`\n*Exact digest* `{DECISION_DIGEST}`",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Decision requested*\n{packet.get('decision_requested') or 'No request supplied.'}\n\n"
                        f"*Recommendation*\n{packet.get('recommendation') or 'No recommendation supplied.'}\n\n"
                        f"*Consequences*\n{packet.get('consequences') or 'Not supplied.'}"
                    ),
                },
            },
        ]
        if status == "pending":
            value = cls.action_value()
            blocks.append(
                {
                    "type": "actions",
                    "block_id": "axis_decision_actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Approve"},
                            "style": "primary",
                            "action_id": APPROVE_ACTION_ID,
                            "value": value,
                        },
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": "Approve with conditions",
                            },
                            "action_id": APPROVE_CONDITIONS_ACTION_ID,
                            "value": value,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Reject"},
                            "style": "danger",
                            "action_id": REJECT_ACTION_ID,
                            "value": value,
                            "confirm": {
                                "title": {"type": "plain_text", "text": "Reject decision?"},
                                "text": {
                                    "type": "mrkdwn",
                                    "text": "This immutable response stops scheduling for this digest.",
                                },
                                "confirm": {"type": "plain_text", "text": "Reject"},
                                "deny": {"type": "plain_text", "text": "Cancel"},
                            },
                        },
                    ],
                }
            )
        else:
            outcome = (record or {}).get("outcome") or status
            conditions = (record or {}).get("conditions")
            detail = f"*Status:* `{status}`\n*Immutable outcome:* `{outcome}`"
            if conditions:
                detail += f"\n*Conditions:* {conditions}"
            if status == "scheduling":
                detail += "\nFrontier rebuild requested; approved work is being scheduled."
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": detail}}
            )
        return text, blocks

    @classmethod
    def conditions_modal(cls, metadata: dict) -> dict:
        return {
            "type": "modal",
            "callback_id": "axis_decision_conditions_modal",
            "private_metadata": json.dumps(metadata, separators=(",", ":"), sort_keys=True),
            "title": {"type": "plain_text", "text": "Approval conditions"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": CONDITIONS_BLOCK_ID,
                    "label": {"type": "plain_text", "text": "Required conditions"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": CONDITIONS_INPUT_ID,
                        "multiline": True,
                        "min_length": 1,
                        "max_length": MAX_CONDITIONS_LENGTH,
                    },
                },
                {
                    "type": "input",
                    "block_id": VERIFICATION_BLOCK_ID,
                    "optional": True,
                    "label": {"type": "plain_text", "text": "Verification evidence"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": VERIFICATION_INPUT_ID,
                        "multiline": True,
                        "max_length": MAX_VERIFICATION_LENGTH,
                    },
                },
                {
                    "type": "actions",
                    "block_id": "axis_decision_conditions_actions",
                    "elements": [
                        {
                            "type": "button",
                            "style": "primary",
                            "text": {"type": "plain_text", "text": "Approve"},
                            "action_id": CONDITIONS_SUBMIT_ACTION_ID,
                            "value": cls.action_value(),
                        }
                    ],
                },
            ],
        }

    def project(
        self,
        token: str,
        *,
        workspace_id: str,
        authorized_user_id: str,
        channel: str,
        decision_id: str,
        packet: dict,
        ts: str | None,
    ) -> tuple[str, str]:
        identity, digest = decision_identity(decision_id, packet)
        if identity != DECISION_ID or digest != DECISION_DIGEST:
            raise ValueError("unsupported decision identity or digest")
        record = self.store.load(identity)
        frontier = self.store.load_frontier_request(identity)
        status = (
            "scheduling"
            if record
            and record["outcome"].startswith("approved")
            and frontier
            and frontier["status"] == "completed"
            else "approved"
            if record and record["outcome"].startswith("approved")
            else "rejected"
            if record
            else "pending"
        )
        text, blocks = self.render_card(packet, status=status, record=record)
        payload = {"channel": channel, "text": text, "blocks": blocks}
        response = self.api(
            token,
            "chat.update" if ts else "chat.postMessage",
            payload | ({"ts": ts} if ts else {}),
        )
        response_ts = str(response.get("ts") or "")
        if str(response.get("channel") or "") != channel or not response_ts:
            raise RuntimeError("Slack decision projection omitted channel or timestamp")
        self.store.save_card(
            {
                "schema": DECISION_CARD_SCHEMA,
                "schema_version": "1.0.0",
                "decision_id": identity,
                "digest": digest,
                "packet": packet,
                "workspace_id": workspace_id,
                "authorized_user_id": authorized_user_id,
                "channel": channel,
                "ts": response_ts,
                "projected_at": utc_now(),
            }
        )
        return response_ts, hashlib.sha256(
            json.dumps({"text": text, "blocks": blocks}, sort_keys=True).encode()
        ).hexdigest()

    @staticmethod
    def _action_identity(action: dict) -> tuple[str, str]:
        try:
            value = json.loads(str(action.get("value") or ""))
        except json.JSONDecodeError as exc:
            raise ValueError("Slack decision action value is invalid") from exc
        return str(value.get("decision_id") or ""), str(value.get("digest") or "").lower()

    def _validate_identity(self, body: dict, action: dict, metadata: dict | None = None) -> dict:
        decision_id, digest = self._action_identity(action)
        if decision_id != DECISION_ID or digest != DECISION_DIGEST:
            raise ValueError("Slack decision action identity or digest mismatch")
        card = self.store.load_card(decision_id)
        if card is None:
            raise ValueError("Slack decision card is not persisted")
        metadata = metadata or {}
        team_id = str((body.get("team") or {}).get("id") or "")
        user_id = str((body.get("user") or {}).get("id") or "")
        channel = str(
            (body.get("channel") or {}).get("id")
            or metadata.get("channel")
            or ""
        )
        ts = str((body.get("message") or {}).get("ts") or metadata.get("ts") or "")
        expected = {
            "workspace_id": team_id,
            "authorized_user_id": user_id,
            "channel": channel,
            "ts": ts,
            "digest": digest,
        }
        for key, value in expected.items():
            if str(card.get(key) or "") != value:
                raise PermissionError(f"Slack decision {key} mismatch")
        for key in ("workspace_id", "authorized_user_id", "channel", "ts", "digest"):
            if key in metadata and str(metadata[key]) != str(card.get(key) or ""):
                raise PermissionError(f"Slack decision modal {key} mismatch")
        return card

    @staticmethod
    def _modal_values(body: dict) -> tuple[str, str]:
        values = ((body.get("view") or {}).get("state") or {}).get("values") or {}
        conditions = str(
            ((values.get(CONDITIONS_BLOCK_ID) or {}).get(CONDITIONS_INPUT_ID) or {}).get("value")
            or ""
        ).strip()
        verification = str(
            ((values.get(VERIFICATION_BLOCK_ID) or {}).get(VERIFICATION_INPUT_ID) or {}).get("value")
            or ""
        ).strip()
        if not conditions or len(conditions) > MAX_CONDITIONS_LENGTH:
            raise ValueError("approval conditions are required and must fit the allowed bound")
        if len(verification) > MAX_VERIFICATION_LENGTH:
            raise ValueError("approval verification exceeds the allowed bound")
        return conditions, verification

    def _update_card(self, token: str, card: dict, status: str, record: dict) -> None:
        text, blocks = self.render_card(card["packet"], status=status, record=record)
        response = self.api(
            token,
            "chat.update",
            {
                "channel": card["channel"],
                "ts": card["ts"],
                "text": text,
                "blocks": blocks,
            },
        )
        if (
            str(response.get("channel") or "") != card["channel"]
            or str(response.get("ts") or "") != card["ts"]
        ):
            raise RuntimeError("Slack decision update changed channel or timestamp")

    def _ensure_frontier_rebuild(self, record: dict) -> None:
        lock_path = self.store.frontier_path(record["decision_id"]).with_suffix(
            ".rebuild.lock"
        )
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with lock_path.open("a", encoding="utf-8") as lock:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX)
            request = self.store.request_frontier_rebuild(record)
            if request["status"] == "completed":
                return
            if self.rebuild is None:
                raise RuntimeError("frontier rebuild callback is not configured")
            self.rebuild()
            self.store.complete_frontier_rebuild(record)

    def handle_action(self, token: str, body: dict, action: dict) -> dict:
        action_id = str(action.get("action_id") or "")
        metadata = None
        if action_id == CONDITIONS_SUBMIT_ACTION_ID:
            try:
                metadata = json.loads(str((body.get("view") or {}).get("private_metadata") or ""))
            except json.JSONDecodeError as exc:
                raise ValueError("Slack decision modal metadata is invalid") from exc
        card = self._validate_identity(body, action, metadata)
        existing = self.store.load(DECISION_ID)
        if existing is not None:
            action_ts = str(action.get("action_ts") or body.get("action_ts") or "")
            if (
                existing["action_id"] != action_id
                or existing["action_ts"] != action_ts
            ):
                raise ValueError("Slack decision replay conflicts with immutable outcome")
            if existing["outcome"].startswith("approved"):
                frontier = self.store.load_frontier_request(DECISION_ID)
                status = "scheduling" if frontier and frontier["status"] == "completed" else "approved"
                self._update_card(token, card, status, existing)
                if status != "scheduling":
                    self._ensure_frontier_rebuild(existing)
                    self._update_card(token, card, "scheduling", existing)
                    status = "scheduling"
            else:
                status = "rejected"
                self._update_card(token, card, status, existing)
            return {"record": existing, "replayed": True, "status": status}
        if action_id == APPROVE_CONDITIONS_ACTION_ID:
            trigger_id = str(body.get("trigger_id") or "")
            if not trigger_id:
                raise ValueError("Slack decision action omitted trigger_id")
            modal_metadata = {
                "decision_id": DECISION_ID,
                "digest": DECISION_DIGEST,
                "workspace_id": card["workspace_id"],
                "authorized_user_id": card["authorized_user_id"],
                "channel": card["channel"],
                "ts": card["ts"],
            }
            self.api(
                token,
                "views.open",
                {"trigger_id": trigger_id, "view": self.conditions_modal(modal_metadata)},
            )
            return {"modal_opened": True, "replayed": False}
        if action_id not in {
            APPROVE_ACTION_ID,
            REJECT_ACTION_ID,
            CONDITIONS_SUBMIT_ACTION_ID,
        }:
            raise ValueError("unsupported Slack decision action")
        conditions = verification = ""
        if action_id == CONDITIONS_SUBMIT_ACTION_ID:
            conditions, verification = self._modal_values(body)
        outcome = (
            "rejected"
            if action_id == REJECT_ACTION_ID
            else "approved-with-conditions"
            if conditions
            else "approved"
        )
        action_ts = str(action.get("action_ts") or body.get("action_ts") or "")
        if not action_ts:
            raise ValueError("Slack decision action omitted action_ts")
        now = utc_now()
        record = {
            "schema": DECISION_SCHEMA,
            "schema_version": "1.0.0",
            "decision_id": DECISION_ID,
            "digest": DECISION_DIGEST,
            "outcome": outcome,
            "conditions": conditions or None,
            "verification": verification or None,
            "decided_by": card["authorized_user_id"],
            "workspace_id": card["workspace_id"],
            "channel": card["channel"],
            "message_ts": card["ts"],
            "action_id": action_id,
            "action_ts": action_ts,
            "decided_at": now,
            "frontier_rebuild_requested_at": now if outcome.startswith("approved") else None,
        }
        record, created = self.store.persist(record)
        if not created:
            status = "rejected"
            if record["outcome"].startswith("approved"):
                self._ensure_frontier_rebuild(record)
                status = "scheduling"
            self._update_card(token, card, status, record)
            return {"record": record, "replayed": True, "status": status}
        status = "rejected" if outcome == "rejected" else "approved"
        self._update_card(token, card, status, record)
        if outcome.startswith("approved"):
            self._ensure_frontier_rebuild(record)
            self._update_card(token, card, "scheduling", record)
            status = "scheduling"
        return {"record": record, "replayed": False, "status": status}


def reconcile_pending_frontier_rebuilds(
    root: Path,
    rebuild: Callable[[], object],
    *,
    limit: int = 8,
) -> list[str]:
    store = DecisionStore(root)
    completed = []
    for path in sorted(store.decisions.glob("*.frontier.json"))[:limit]:
        request = read_record(path, DECISION_FRONTIER_SCHEMA)
        if request["status"] != "pending":
            continue
        record = store.load(str(request["decision_id"]))
        if record is None or not record["outcome"].startswith("approved"):
            continue
        SlackDecisionController(root, lambda *_args: {}, rebuild)._ensure_frontier_rebuild(
            record
        )
        completed.append(str(request["decision_id"]))
    return completed
