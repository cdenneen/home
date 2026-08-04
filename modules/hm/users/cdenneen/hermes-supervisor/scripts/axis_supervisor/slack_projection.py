import hashlib
import json
import time
import urllib.request
from collections import Counter
from pathlib import Path


class SlackProjection:
    def __init__(self, root: Path):
        self.root = root
        self.state_path = root / "slack-overview-state.json"

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

    def render(self, inventory: dict, graph: dict, control: dict) -> tuple[str, list[dict], str]:
        counts = Counter(inventory.get("classification_counts") or {})
        total = sum(counts.values()) or 1
        verified_refs = {
            node.get("ref")
            for node in graph.get("nodes") or []
            if node.get("classification") in {"Integrated", "Completed"}
            and node.get("semantic_record")
        }
        verified = len(verified_refs)
        closed_unverified = max(
            0,
            counts["Integrated"]
            + counts["Completed"]
            + counts["Revalidation"]
            - verified,
        )
        running = counts["Running"]
        executable = int(graph.get("queue_depth") or 0)
        waiting = counts["Waiting"] + counts["Revalidation"]
        blocked = counts["Blocked"]
        inactive = counts["Invalid"] + counts["Superseded"]
        assignments = inventory.get("supervisor_assignments") or []
        active = [item for item in assignments if item.get("state") not in {"complete", "completed", "cancelled", "failed"}]
        leases = inventory.get("active_leases") or []
        confidence = inventory.get("roadmap_confidence") or {}
        health = "healthy" if inventory.get("invariant", {}).get("unknown_count", 1) == 0 else "degraded"
        icon = "🟢" if health == "healthy" else "🟡"

        fallback = (
            f"AXIS Build Supervisor — {health}; mode={control.get('mode')}; "
            f"active={len(active)}; queue={executable}; blocked={blocked}; waiting={waiting}; "
            f"confidence={confidence.get('percent', 0)}%"
        )
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"{icon} AXIS Build Supervisor"}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Health*\n{health.title()}"},
                    {"type": "mrkdwn", "text": f"*Mode*\n{control.get('mode')}"},
                    {"type": "mrkdwn", "text": f"*Workers*\n{len(active)}/{control.get('max_active_assignments', 1)}"},
                    {"type": "mrkdwn", "text": f"*Integrator*\n{'Active' if any(a.get('phase') == 'awaiting-integration' for a in active) else 'Idle'}"},
                    {"type": "mrkdwn", "text": f"*Governed queue*\n{executable}"},
                    {"type": "mrkdwn", "text": f"*Confidence*\n{confidence.get('percent', 0)}%"},
                ],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Roadmap*\n"
                    f"Verified             `{self.bar(verified, total)}` {verified * 100 // total:>3}%\n"
                    f"Closed unverified    `{self.bar(closed_unverified, total)}` {closed_unverified * 100 // total:>3}%\n"
                    f"Running              `{self.bar(running, total)}` {running * 100 // total:>3}%\n"
                    f"Executable           `{self.bar(executable, total)}` {executable * 100 // total:>3}%\n"
                    f"Waiting/Revalidation `{self.bar(waiting, total)}` {waiting * 100 // total:>3}%\n"
                    f"Blocked              `{self.bar(blocked, total)}` {blocked * 100 // total:>3}%\n"
                    f"Invalid/Superseded   `{self.bar(inactive, total)}` {inactive * 100 // total:>3}%",
                },
            },
        ]

        milestones = []
        for milestone in inventory.get("milestones") or []:
            if milestone.get("state") != "active":
                continue
            title = milestone.get("title") or "Unnamed milestone"
            members = [item for item in inventory.get("work_items") or [] if item.get("milestone") == title]
            member_counts = Counter(item.get("classification") for item in members)
            completed = sum(1 for item in members if item.get("ref") in verified_refs)
            percent = completed * 100 // len(members) if members else 0
            rag = (
                "⚪"
                if not members
                else "🔴"
                if member_counts["Blocked"]
                else "🟡"
                if member_counts["Waiting"] or member_counts["Revalidation"]
                else "🔵"
                if member_counts["Running"]
                else "🟢"
            )
            milestones.append((title, rag, percent, len(members), member_counts))
        if milestones:
            lines = []
            for title, rag, percent, member_total, member_counts in milestones[:3]:
                lines.append(
                    f"{rag} *{title} — {percent}%*\n"
                    f"Completed {member_counts['Integrated'] + member_counts['Completed']}/{member_total} | "
                    f"Running {member_counts['Running']} | Executable {member_counts['Executable']} | "
                    f"Blocked {member_counts['Blocked']}"
                )
            blocks.extend([{"type": "divider"}, {"type": "section", "text": {"type": "mrkdwn", "text": "*Active Milestones*\n" + "\n\n".join(lines)}}])

        for assignment in active[:3]:
            worker = assignment.get("worker") or {}
            handoff = worker.get("handoff") or {}
            blocks.extend(
                [
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"🔵 *{assignment.get('work_item') or assignment.get('target_ref')}*\n"
                            f"Worker: {'GPT-5.4' if assignment.get('phase') == 'semantic' else 'GPT-5.3-Codex'} | "
                            f"Phase: {assignment.get('phase')}\n"
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
                        "text": f"Inventory {inventory.get('generation_id')} • {inventory.get('generated_at')} • Leases {len(leases)}",
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
        fallback, blocks, fingerprint = self.render(inventory, graph, control)
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        if state.get("fingerprint") == fingerprint:
            return {"updated": False, "channel": state.get("channel"), "ts": state.get("ts")}
        channel = state.get("channel")
        if not channel:
            channel = self.api(token, "conversations.open", {"users": user_id})["channel"]["id"]
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
        new_state = {
            "channel": channel,
            "ts": response["ts"],
            "fingerprint": fingerprint,
            "updated_at_epoch": int(time.time()),
        }
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(new_state, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.state_path)
        return {"updated": True, **new_state}
