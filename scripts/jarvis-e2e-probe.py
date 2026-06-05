#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from urllib import parse, request


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def http_json(method: str, url: str, payload: dict | None = None, headers: dict | None = None) -> dict:
    req_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        req_headers.update(headers)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = request.Request(url=url, data=data, headers=req_headers, method=method)
    with request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body) if body.strip() else {}


def fetch_task_events(api_url: str, task_id: str, hours: int = 24, limit: int = 200) -> list[dict]:
    summary = http_json("GET", f"{api_url.rstrip('/')}/api/tasks/summary?hours={hours}&limit={limit}")
    recent = summary.get("recent") if isinstance(summary, dict) and isinstance(summary.get("recent"), list) else []
    return [row for row in recent if str(row.get("task_id", "")) == task_id]


def check_slack_thread_format(token: str, channel: str, thread_id: str, pattern: re.Pattern[str]) -> tuple[bool, str]:
    query = parse.urlencode({"channel": channel, "ts": thread_id, "limit": 200})
    url = f"https://slack.com/api/conversations.replies?{query}"
    payload = http_json("GET", url, headers={"Authorization": f"Bearer {token}"})
    if not payload.get("ok"):
        return False, f"slack api error: {payload.get('error', 'unknown')}"
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    matches = []
    for message in messages:
        text = str(message.get("text", ""))
        if pattern.match(text):
            matches.append(text)
    if not matches:
        return False, "no Slack thread messages matched expected Nyx update format"
    return True, matches[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Jarvis route -> nyx callbacks -> task summary/slack formatting")
    parser.add_argument("--api-url", default="http://127.0.0.1:8080")
    parser.add_argument("--text", default="E2E probe: validate nyx worker callback lifecycle")
    parser.add_argument("--user", default="jarvis-e2e")
    parser.add_argument("--channel", default="slack")
    parser.add_argument("--thread-id", default=f"e2e-{int(time.time())}")
    parser.add_argument("--realm", default="")
    parser.add_argument("--target-agent", default="")
    parser.add_argument("--wait-seconds", type=int, default=20)
    parser.add_argument("--poll-interval", type=float, default=1.5)
    parser.add_argument("--check-slack", action="store_true")
    parser.add_argument("--slack-channel", default="")
    parser.add_argument("--slack-token-env", default="SLACK_BOT_TOKEN")
    args = parser.parse_args()

    interaction = {
        "channel": args.channel,
        "user": args.user,
        "text": args.text,
        "thread_id": args.thread_id,
        "timestamp": now_iso(),
    }
    if args.realm:
        interaction["realm"] = args.realm
    if args.target_agent:
        interaction["target_agent"] = args.target_agent

    print(f"[probe] posting /api/route thread_id={args.thread_id}")
    route_resp = http_json("POST", f"{args.api_url.rstrip('/')}/api/route", payload=interaction)
    routed = route_resp.get("routed") if isinstance(route_resp, dict) and isinstance(route_resp.get("routed"), dict) else {}
    print(f"[probe] routed execution_target={routed.get('execution_target')} agent={routed.get('resolved_agent')}")

    task_id = f"task-{args.thread_id}"
    deadline = time.time() + max(1, args.wait_seconds)
    events: list[dict] = []
    terminal_worker: dict | None = None

    while time.time() < deadline:
        events = fetch_task_events(args.api_url, task_id)
        worker_events = [row for row in events if str(row.get("stage", "")) == "worker"]
        if worker_events:
            top = worker_events[0]
            status = str(top.get("status", ""))
            print(f"[probe] worker status={status} summary={top.get('summary', '')}")
            if status in {"completed", "failed"}:
                terminal_worker = top
                break
        time.sleep(max(0.2, args.poll_interval))

    if terminal_worker is None:
        print("[probe] timeout waiting for terminal worker event", file=sys.stderr)
        return 2

    format_pattern = re.compile(r"^Nyx update \(.+\): `(?:running|completed|failed)` - .+$")
    sample_text = f"Nyx update (worker): `{terminal_worker['status']}` - {terminal_worker.get('summary', '')}"
    if not format_pattern.match(sample_text):
        print("[probe] failed local format validation", file=sys.stderr)
        return 3
    print("[probe] local Nyx update format template: ok")

    if args.check_slack:
        if not args.slack_channel:
            print("[probe] --check-slack requires --slack-channel", file=sys.stderr)
            return 4
        token = os.getenv(args.slack_token_env, "")
        if not token:
            print(f"[probe] missing Slack token env {args.slack_token_env}", file=sys.stderr)
            return 5
        ok, detail = check_slack_thread_format(token, args.slack_channel, args.thread_id, format_pattern)
        if not ok:
            print(f"[probe] slack format check failed: {detail}", file=sys.stderr)
            return 6
        print(f"[probe] slack Nyx update format: ok ({detail})")

    print(f"[probe] success task_id={task_id} terminal_status={terminal_worker['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
