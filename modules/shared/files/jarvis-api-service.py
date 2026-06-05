from __future__ import annotations

import argparse
import json
import time
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def summary_text(routed: dict[str, Any], work_result: dict[str, Any] | None) -> str:
    if routed.get("requires_approval"):
        lock_realm = routed.get("lock_realm") or routed.get("realm") or "current realm"
        requested_realm = routed.get("requested_realm") or "requested realm"
        return f"Approval is required before switching from {lock_realm} to {requested_realm}."

    resolved_agent = routed.get("resolved_agent") or "jarvis"
    execution_target = routed.get("execution_target") or "ghost"

    if work_result is not None and work_result.get("ok"):
        return f"Routed to {resolved_agent} on {execution_target}. Work runner accepted the request."

    return f"Routed to {resolved_agent} on {execution_target}."


def infer_local_action_from_text(text: str) -> dict[str, Any] | None:
    lower = text.lower().strip()
    if not lower:
        return None

    app_map = {
        "slack": "Slack",
        "safari": "Safari",
        "chrome": "Google Chrome",
        "terminal": "Terminal",
        "finder": "Finder",
    }

    if lower.startswith("open "):
        requested = lower.replace("open ", "", 1).strip().strip(".")
        for keyword, app_name in app_map.items():
            if keyword in requested:
                return {
                    "action": "open_app",
                    "args": {"app": app_name},
                }

    if lower.startswith("notify ") or lower.startswith("notify me"):
        message = text.strip()
        if message.lower().startswith("notify me"):
            message = message[9:].strip(" :,-")
        elif message.lower().startswith("notify"):
            message = message[6:].strip(" :,-")
        if message:
            return {
                "action": "notify",
                "args": {"title": "Jarvis", "message": message},
            }

    if lower.startswith("copy ") and ("clipboard" in lower or "to clipboard" in lower):
        body = text.strip()[5:]
        body = body.replace("to clipboard", "").replace("clipboard", "").strip(" :,-")
        if body:
            return {
                "action": "clipboard_write",
                "args": {"text": body},
            }

    if "read clipboard" in lower or "clipboard read" in lower:
        return {"action": "clipboard_read", "args": {}}

    if lower.startswith("say "):
        speech = text.strip()[4:].strip()
        if speech:
            return {
                "action": "speak",
                "args": {"text": speech},
            }

    return None


def capability_for_route(routed: dict[str, Any]) -> str:
    agent = str(routed.get("resolved_agent", "")).lower()
    mapping = {
        "code-agent": "code",
        "incident-agent": "triage",
        "platform-agent": "investigation",
        "research-agent": "documentation",
    }
    return mapping.get(agent, "investigation")


def append_usage_event(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=True) + "\n")


def parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def summarize_usage(path: Path, hours: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    cutoff = now.timestamp() - max(1, hours) * 3600

    totals = {"events": 0, "tokens": 0, "cost_usd": 0.0}
    by_model: dict[str, dict[str, Any]] = {}
    by_agent: dict[str, dict[str, Any]] = {}

    if not path.exists():
        return {"totals": totals, "models": [], "agents": []}

    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue

        created_at = parse_iso(str(row.get("timestamp", "")))
        if created_at is None or created_at.timestamp() < cutoff:
            continue

        model = str(row.get("model", "unknown"))
        agent = str(row.get("agent", "unknown"))
        tokens = int(row.get("tokens_total", 0) or 0)
        cost = float(row.get("cost_usd", 0.0) or 0.0)

        totals["events"] += 1
        totals["tokens"] += tokens
        totals["cost_usd"] += cost

        if model not in by_model:
            by_model[model] = {"model": model, "events": 0, "tokens": 0, "cost_usd": 0.0}
        by_model[model]["events"] += 1
        by_model[model]["tokens"] += tokens
        by_model[model]["cost_usd"] += cost

        if agent not in by_agent:
            by_agent[agent] = {"agent": agent, "events": 0, "tokens": 0, "cost_usd": 0.0}
        by_agent[agent]["events"] += 1
        by_agent[agent]["tokens"] += tokens
        by_agent[agent]["cost_usd"] += cost

    models = sorted(by_model.values(), key=lambda x: x["cost_usd"], reverse=True)
    agents = sorted(by_agent.values(), key=lambda x: x["cost_usd"], reverse=True)
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return {"totals": totals, "models": models, "agents": agents}


def init_usage_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ts_epoch INTEGER NOT NULL,
                agent TEXT NOT NULL,
                model TEXT NOT NULL,
                provider TEXT NOT NULL,
                tokens_input INTEGER NOT NULL,
                tokens_output INTEGER NOT NULL,
                tokens_total INTEGER NOT NULL,
                cost_usd REAL NOT NULL,
                session_id TEXT NOT NULL,
                task TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_events_ts_epoch ON usage_events (ts_epoch)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_events_model ON usage_events (model)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_events_agent ON usage_events (agent)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ts_epoch INTEGER NOT NULL,
                task_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                user_name TEXT NOT NULL,
                agent TEXT NOT NULL,
                execution_target TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                reviewer_required INTEGER NOT NULL,
                reviewed INTEGER NOT NULL,
                detail_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_events_ts_epoch ON task_events (ts_epoch)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events (task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_events_status ON task_events (status)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_preferences (
                user_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                scope TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, agent)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_preferences_user ON approval_preferences (user_id)")
        conn.commit()


def compact_summary(text: str, max_len: int = 120) -> str:
    normalized = " ".join(str(text or "").strip().split())
    if len(normalized) <= max_len:
        return normalized
    return normalized[: max_len - 1] + "..."


def derive_task_id(routed: dict[str, Any], payload: dict[str, Any]) -> str:
    thread_id = str(routed.get("thread_id") or payload.get("thread_id") or "")
    if thread_id:
        return f"task-{thread_id}"
    return str(routed.get("event_id") or f"task-{uuid.uuid4()}")


def derive_task_id_from_thread(thread_id: str, fallback: str = "") -> str:
    tid = str(thread_id or "").strip()
    if tid:
        return f"task-{tid}"
    fb = str(fallback or "").strip()
    return fb if fb else f"task-{uuid.uuid4()}"


def insert_task_event_db(path: Path, row: dict[str, Any]) -> None:
    ts = parse_iso(str(row.get("timestamp", ""))) or datetime.now(UTC)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO task_events (
                timestamp, ts_epoch, task_id, thread_id, channel, user_name,
                agent, execution_target, stage, status, summary,
                reviewer_required, reviewed, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(row.get("timestamp", now_iso())),
                int(ts.timestamp()),
                str(row.get("task_id", "unknown")),
                str(row.get("thread_id", "")),
                str(row.get("channel", "unknown")),
                str(row.get("user_name", "unknown")),
                str(row.get("agent", "jarvis")),
                str(row.get("execution_target", "unknown")),
                str(row.get("stage", "route")),
                str(row.get("status", "queued")),
                str(row.get("summary", "")),
                1 if bool(row.get("reviewer_required", True)) else 0,
                1 if bool(row.get("reviewed", False)) else 0,
                json.dumps(row.get("detail", {}), ensure_ascii=True),
            ),
        )
        conn.commit()


def summarize_tasks_db(path: Path, hours: int, limit: int) -> dict[str, Any]:
    if not path.exists():
        return {"active": [], "recent": [], "status_totals": {}}

    cutoff = int(datetime.now(UTC).timestamp()) - max(1, hours) * 3600
    with sqlite3.connect(path) as conn:
        active_rows = conn.execute(
            """
            SELECT e.task_id, e.thread_id, e.agent, e.execution_target, e.stage, e.status, e.summary, e.timestamp, e.reviewer_required, e.reviewed
            FROM task_events e
            JOIN (
                SELECT task_id, MAX(id) AS max_id
                FROM task_events
                WHERE ts_epoch >= ?
                GROUP BY task_id
            ) latest ON latest.task_id = e.task_id AND latest.max_id = e.id
            WHERE e.status IN ('routed', 'dispatched', 'running', 'approval_required', 'review_pending', 'changes_requested')
            ORDER BY e.ts_epoch DESC
            LIMIT ?
            """,
            (cutoff, max(1, limit)),
        ).fetchall()

        recent_rows = conn.execute(
            """
            SELECT task_id, thread_id, channel, user_name, agent, execution_target, stage, status, summary, timestamp, reviewer_required, reviewed
            FROM task_events
            WHERE ts_epoch >= ?
            ORDER BY ts_epoch DESC
            LIMIT ?
            """,
            (cutoff, max(1, limit)),
        ).fetchall()

        totals_rows = conn.execute(
            """
            SELECT latest.status, COUNT(*)
            FROM (
                SELECT e.task_id, e.status
                FROM task_events e
                JOIN (
                    SELECT task_id, MAX(id) AS max_id
                    FROM task_events
                    WHERE ts_epoch >= ?
                    GROUP BY task_id
                ) grouped ON grouped.task_id = e.task_id AND grouped.max_id = e.id
            ) latest
            GROUP BY status
            ORDER BY COUNT(*) DESC
            """,
            (cutoff,),
        ).fetchall()

    active = [
        {
            "task_id": str(row[0]),
            "thread_id": str(row[1]),
            "agent": str(row[2]),
            "execution_target": str(row[3]),
            "stage": str(row[4]),
            "status": str(row[5]),
            "summary": str(row[6]),
            "timestamp": str(row[7]),
            "reviewer_required": bool(row[8]),
            "reviewed": bool(row[9]),
        }
        for row in active_rows
    ]
    recent = [
        {
            "task_id": str(row[0]),
            "thread_id": str(row[1]),
            "channel": str(row[2]),
            "user_name": str(row[3]),
            "agent": str(row[4]),
            "execution_target": str(row[5]),
            "stage": str(row[6]),
            "status": str(row[7]),
            "summary": str(row[8]),
            "timestamp": str(row[9]),
            "reviewer_required": bool(row[10]),
            "reviewed": bool(row[11]),
        }
        for row in recent_rows
    ]
    status_totals = {str(row[0]): int(row[1]) for row in totals_rows}
    return {"active": active, "recent": recent, "status_totals": status_totals}


def set_approval_preference_db(path: Path, user_id: str, agent: str, scope: str) -> None:
    clean_scope = scope if scope in {"always", "once", "none"} else "once"
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO approval_preferences (user_id, agent, scope, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, agent)
            DO UPDATE SET scope=excluded.scope, updated_at=excluded.updated_at
            """,
            (user_id, agent, clean_scope, now_iso()),
        )
        conn.commit()


def get_approval_preference_db(path: Path, user_id: str, agent: str) -> str:
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT scope FROM approval_preferences WHERE user_id = ? AND agent = ?",
            (user_id, agent),
        ).fetchone()
    if not row:
        return "none"
    value = str(row[0] or "none")
    return value if value in {"always", "once", "none"} else "none"


def insert_usage_event_db(path: Path, row: dict[str, Any]) -> None:
    ts = parse_iso(str(row.get("timestamp", ""))) or datetime.now(UTC)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO usage_events (
                timestamp, ts_epoch, agent, model, provider,
                tokens_input, tokens_output, tokens_total, cost_usd,
                session_id, task, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(row.get("timestamp", now_iso())),
                int(ts.timestamp()),
                str(row.get("agent", "unknown")),
                str(row.get("model", "unknown")),
                str(row.get("provider", "unknown")),
                int(row.get("tokens_input", 0) or 0),
                int(row.get("tokens_output", 0) or 0),
                int(row.get("tokens_total", 0) or 0),
                float(row.get("cost_usd", 0.0) or 0.0),
                str(row.get("session_id", "")),
                str(row.get("task", "")),
                json.dumps(row.get("metadata", {}), ensure_ascii=True),
            ),
        )
        conn.commit()


def summarize_usage_db(path: Path, hours: int) -> dict[str, Any]:
    if not path.exists():
        return {"totals": {"events": 0, "tokens": 0, "cost_usd": 0.0}, "models": [], "agents": []}

    cutoff = int(datetime.now(UTC).timestamp()) - max(1, hours) * 3600
    with sqlite3.connect(path) as conn:
        total_row = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(tokens_total), 0), COALESCE(SUM(cost_usd), 0.0)
            FROM usage_events
            WHERE ts_epoch >= ?
            """,
            (cutoff,),
        ).fetchone()

        model_rows = conn.execute(
            """
            SELECT model, COUNT(*) AS events, COALESCE(SUM(tokens_total), 0) AS tokens, COALESCE(SUM(cost_usd), 0.0) AS cost_usd
            FROM usage_events
            WHERE ts_epoch >= ?
            GROUP BY model
            ORDER BY cost_usd DESC
            """,
            (cutoff,),
        ).fetchall()

        agent_rows = conn.execute(
            """
            SELECT agent, COUNT(*) AS events, COALESCE(SUM(tokens_total), 0) AS tokens, COALESCE(SUM(cost_usd), 0.0) AS cost_usd
            FROM usage_events
            WHERE ts_epoch >= ?
            GROUP BY agent
            ORDER BY cost_usd DESC
            """,
            (cutoff,),
        ).fetchall()

    totals = {
        "events": int(total_row[0] if total_row else 0),
        "tokens": int(total_row[1] if total_row else 0),
        "cost_usd": round(float(total_row[2] if total_row else 0.0), 6),
    }
    models = [
        {"model": str(row[0]), "events": int(row[1]), "tokens": int(row[2]), "cost_usd": float(row[3])}
        for row in model_rows
    ]
    agents = [
        {"agent": str(row[0]), "events": int(row[1]), "tokens": int(row[2]), "cost_usd": float(row[3])}
        for row in agent_rows
    ]
    return {"totals": totals, "models": models, "agents": agents}


def model_recommendations(summary: dict[str, Any]) -> list[dict[str, str]]:
    recs: list[dict[str, str]] = []
    for row in summary.get("models", []):
        model = str(row.get("model", "unknown"))
        cost = float(row.get("cost_usd", 0.0) or 0.0)
        events = int(row.get("events", 0) or 0)

        current_tier = "efficient-default"
        if "gpt-5" in model or "planner" in model:
            current_tier = "planner-default"
        elif "nano" in model:
            current_tier = "efficient-fast"

        if cost > 2.5 and events > 10 and current_tier == "planner-default":
            recs.append(
                {
                    "model": model,
                    "current_tier": current_tier,
                    "recommended_tier": "efficient-default",
                    "reason": "High spend for sustained usage window",
                }
            )
        elif cost > 1.5 and events > 20 and current_tier == "efficient-default":
            recs.append(
                {
                    "model": model,
                    "current_tier": current_tier,
                    "recommended_tier": "efficient-fast",
                    "reason": "Frequent traffic with moderate spend",
                }
            )
    return recs


def load_project_overlap_map(path: Path, limit: int) -> dict[str, Any]:
    if not path.exists():
        return {"projects": [], "overlaps": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return {"projects": [], "overlaps": []}

    projects_obj = payload.get("projects") if isinstance(payload, dict) else {}
    overlaps_obj = payload.get("overlaps") if isinstance(payload, dict) else []

    projects: list[dict[str, Any]] = []
    if isinstance(projects_obj, dict):
        for name, data in projects_obj.items():
            if not isinstance(data, dict):
                continue
            projects.append(
                {
                    "name": str(name),
                    "repo_count": int(data.get("repo_count", 0) or 0),
                    "paths": int(data.get("paths", 0) or 0),
                    "repos": data.get("repos", []) if isinstance(data.get("repos"), list) else [],
                }
            )
    projects = sorted(projects, key=lambda r: (r.get("repo_count", 0), r.get("paths", 0)), reverse=True)

    overlaps: list[dict[str, Any]] = []
    if isinstance(overlaps_obj, list):
        for row in overlaps_obj:
            if not isinstance(row, dict):
                continue
            overlaps.append(
                {
                    "left": str(row.get("left", "")),
                    "right": str(row.get("right", "")),
                    "count": int(row.get("count", 0) or 0),
                    "shared_repos": row.get("shared_repos", []) if isinstance(row.get("shared_repos"), list) else [],
                }
            )
    overlaps = sorted(overlaps, key=lambda r: r.get("count", 0), reverse=True)

    max_rows = max(1, min(limit, 200))
    return {"projects": projects[:max_rows], "overlaps": overlaps[:max_rows]}


def summarize_stuck_tasks(path: Path, stale_after_seconds: int, limit: int) -> dict[str, Any]:
    if not path.exists():
        return {"stale_after_seconds": stale_after_seconds, "count": 0, "tasks": []}

    active = summarize_tasks_db(path, hours=48, limit=max(1, min(limit, 500))).get("active", [])
    now = datetime.now(UTC)
    stuck: list[dict[str, Any]] = []
    for row in active:
        timestamp = parse_iso(str(row.get("timestamp", "")))
        if timestamp is None:
            continue
        age_seconds = int((now - timestamp).total_seconds())
        if age_seconds < stale_after_seconds:
            continue
        stuck.append(
            {
                "task_id": row.get("task_id"),
                "thread_id": row.get("thread_id"),
                "agent": row.get("agent"),
                "execution_target": row.get("execution_target"),
                "status": row.get("status"),
                "summary": row.get("summary"),
                "age_seconds": age_seconds,
                "sla_seconds": stale_after_seconds,
                "overdue_seconds": max(0, age_seconds - stale_after_seconds),
            }
        )

    stuck = sorted(stuck, key=lambda r: r.get("overdue_seconds", 0), reverse=True)[: max(1, min(limit, 200))]
    return {"stale_after_seconds": stale_after_seconds, "count": len(stuck), "tasks": stuck}


def create_app(
    *,
    harness_url: str,
    work_endpoint: str,
    work_shared_token: str,
    mac_endpoint: str,
    mac_shared_token: str,
    usage_file: str,
    usage_sqlite: str,
    routing_events_file: str,
    project_map_file: str,
    slack_endpoint: str,
    supabase_url: str,
    supabase_key: str,
) -> FastAPI:
    app = FastAPI(title="Jarvis API", version="0.1.0")
    usage_path = Path(usage_file)
    usage_db_path = Path(usage_sqlite) if usage_sqlite else None
    routing_path = Path(routing_events_file) if routing_events_file else None
    project_map_path = Path(project_map_file) if project_map_file else None
    write_metrics_ms: list[float] = []
    max_metrics_samples = 200
    if usage_db_path is not None:
        init_usage_db(usage_db_path)

    def push_write_metric(ms: float) -> None:
        write_metrics_ms.append(ms)
        if len(write_metrics_ms) > max_metrics_samples:
            del write_metrics_ms[:-max_metrics_samples]

    def write_metrics_snapshot() -> dict[str, float | int]:
        if not write_metrics_ms:
            return {"samples": 0, "last_ms": 0.0, "avg_ms": 0.0, "p95_ms": 0.0}
        ordered = sorted(write_metrics_ms)
        p95_index = max(0, int(len(ordered) * 0.95) - 1)
        return {
            "samples": len(write_metrics_ms),
            "last_ms": round(write_metrics_ms[-1], 3),
            "avg_ms": round(sum(write_metrics_ms) / len(write_metrics_ms), 3),
            "p95_ms": round(ordered[p95_index], 3),
        }

    def record_task_event(
        *,
        payload: dict[str, Any],
        routed: dict[str, Any],
        stage: str,
        status: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if usage_db_path is None:
            return
        detail_data = detail if isinstance(detail, dict) else {}
        summary = compact_summary(
            str(payload.get("task") or payload.get("text") or routed.get("text") or stage)
        )
        row = {
            "timestamp": now_iso(),
            "task_id": derive_task_id(routed, payload),
            "thread_id": str(routed.get("thread_id") or payload.get("thread_id") or ""),
            "channel": str(payload.get("channel") or "api"),
            "user_name": str(payload.get("user") or "unknown"),
            "agent": str(routed.get("resolved_agent") or "jarvis"),
            "execution_target": str(routed.get("execution_target") or "unknown"),
            "stage": stage,
            "status": status,
            "summary": summary,
            "reviewer_required": True,
            "reviewed": False,
            "detail": detail_data,
        }
        insert_task_event_db(usage_db_path, row)

    async def probe_endpoint(
        *,
        name: str,
        base_url: str,
        path: str = "/healthz",
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not base_url:
            return {"node": name, "ok": False, "status": "not-configured", "latency_ms": None}

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                response = await client.get(f"{base_url.rstrip('/')}{path}", headers=headers or {})
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            return {
                "node": name,
                "ok": response.is_success,
                "status": "up" if response.is_success else f"http-{response.status_code}",
                "latency_ms": latency_ms,
                "url": f"{base_url.rstrip('/')}{path}",
                "payload": payload if isinstance(payload, dict) else {},
            }
        except Exception as exc:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return {
                "node": name,
                "ok": False,
                "status": "down",
                "latency_ms": latency_ms,
                "url": f"{base_url.rstrip('/')}{path}",
                "error": str(exc),
            }

    async def route_payload(payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(f"{harness_url.rstrip('/')}/route", json=payload)
            response.raise_for_status()
            return response.json()

    async def _dispatch_to_endpoint(
        *,
        endpoint: str,
        shared_token: str,
        payload: dict[str, Any],
        routed: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not endpoint:
            return None

        headers: dict[str, str] = {}
        if shared_token:
            headers["X-Jarvis-Shared-Token"] = shared_token

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{endpoint.rstrip('/')}/run",
                json={
                    "input_event": payload,
                    "routed_event": routed,
                },
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

    async def _discover_workers(endpoint: str, shared_token: str) -> list[dict[str, Any]]:
        headers: dict[str, str] = {}
        if shared_token:
            headers["X-Jarvis-Shared-Token"] = shared_token

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{endpoint.rstrip('/')}/workers", headers=headers)
            response.raise_for_status()
            payload = response.json()

        workers = payload.get("workers") if isinstance(payload, dict) else []
        return workers if isinstance(workers, list) else []

    def _pick_worker(workers: list[dict[str, Any]], required_capability: str) -> dict[str, Any] | None:
        if not workers:
            return None

        capable = []
        fallback = []
        for worker in workers:
            caps = worker.get("capabilities") if isinstance(worker.get("capabilities"), list) else []
            queue_depth = int(worker.get("queue_depth", 9999)) if str(worker.get("queue_depth", "")).isdigit() else 9999
            row = {
                "worker": worker,
                "queue_depth": queue_depth,
                "busy": bool(worker.get("busy", False)),
            }
            fallback.append(row)
            if required_capability in [str(c).lower() for c in caps]:
                capable.append(row)

        pool = capable if capable else fallback
        pool.sort(key=lambda x: (x["busy"], x["queue_depth"]))
        return pool[0]["worker"] if pool else None

    async def dispatch_work(payload: dict[str, Any], routed: dict[str, Any]) -> dict[str, Any] | None:
        if routed.get("requires_approval"):
            return None

        target = routed.get("execution_target")
        if target == "nyx":
            endpoint = work_endpoint
            selected_worker: dict[str, Any] | None = None
            try:
                workers = await _discover_workers(work_endpoint, work_shared_token)
                selected_worker = _pick_worker(workers, capability_for_route(routed))
                if selected_worker and selected_worker.get("endpoint"):
                    endpoint = str(selected_worker.get("endpoint"))
            except Exception:
                selected_worker = None

            result = await _dispatch_to_endpoint(
                endpoint=endpoint,
                shared_token=work_shared_token,
                payload=payload,
                routed=routed,
            )
            if result is not None and selected_worker is not None:
                result["selected_worker"] = selected_worker
            if result is not None and selected_worker is not None and selected_worker.get("worker_id"):
                result["worker_id"] = selected_worker.get("worker_id")
            return result

        if target == "personal-local":
            local_action = payload.get("local_action") if isinstance(payload.get("local_action"), dict) else {}
            action = str(local_action.get("action", "")).strip()
            args = local_action.get("args") if isinstance(local_action.get("args"), dict) else {}

            if not action:
                inferred = infer_local_action_from_text(str(payload.get("text", "")))
                if inferred:
                    action = str(inferred.get("action", "")).strip()
                    args = inferred.get("args") if isinstance(inferred.get("args"), dict) else {}

            if not action:
                return {
                    "ok": False,
                    "service": "jarvis-api",
                    "detail": (
                        "personal-local route requires payload.local_action.action or recognizable text "
                        "like 'open Slack', 'open Safari', 'notify me ...', 'copy ... to clipboard', 'say ...'"
                    ),
                }

            headers: dict[str, str] = {}
            if mac_shared_token:
                headers["X-Jarvis-Shared-Token"] = mac_shared_token

            if not mac_endpoint:
                return {
                    "ok": False,
                    "service": "jarvis-api",
                    "detail": "mac endpoint is not configured",
                }

            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{mac_endpoint.rstrip('/')}/run",
                    json={"action": action, "args": args},
                    headers=headers,
                )
                response.raise_for_status()
                return response.json()

        if target in {"ghost", "unknown", None}:
            return None

        return {
            "ok": False,
            "service": "jarvis-api",
            "detail": f"unsupported execution target: {target}",
        }

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "service": "jarvis-api",
                "harness_url": harness_url,
                "work_endpoint": work_endpoint,
                "mac_endpoint": mac_endpoint,
                "usage_file": usage_file,
                "usage_sqlite": usage_sqlite,
                "usage_storage": "sqlite" if usage_db_path is not None else "jsonl",
                "routing_events_file": str(routing_path) if routing_path is not None else "",
                "project_map_file": str(project_map_path) if project_map_path is not None else "",
                "slack_endpoint": slack_endpoint,
                "supabase_adapter": {
                    "mode": "dormant",
                    "configured": bool(supabase_url and supabase_key),
                },
            }
        )

    @app.get("/api/healthz")
    async def api_healthz() -> JSONResponse:
        return await healthz()

    @app.post("/api/route")
    async def api_route(payload: dict[str, Any]) -> JSONResponse:
        try:
            routed = await route_payload(payload)
            route_status = "approval_required" if routed.get("requires_approval") else "routed"
            record_task_event(
                payload=payload,
                routed=routed,
                stage="route",
                status=route_status,
                detail={"model_tier": routed.get("model_tier"), "delegation_rule": routed.get("delegation_rule")},
            )
            work_result = await dispatch_work(payload, routed)
            if work_result is not None:
                execution_target = str(routed.get("execution_target") or "")
                if not work_result.get("ok"):
                    final_status = "failed"
                elif execution_target == "nyx":
                    final_status = "running"
                else:
                    final_status = "review_pending"
                record_task_event(
                    payload=payload,
                    routed=routed,
                    stage="execution",
                    status=final_status,
                    detail={
                        "worker_id": work_result.get("worker_id") or (work_result.get("selected_worker") or {}).get("worker_id"),
                        "service": work_result.get("service"),
                        "ok": work_result.get("ok"),
                    },
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return JSONResponse(
            {
                "ok": True,
                "service": "jarvis-api",
                "routed": routed,
                "work_result": work_result,
            }
        )

    @app.post("/api/usage/record")
    async def api_usage_record(payload: dict[str, Any]) -> JSONResponse:
        row = {
            "timestamp": now_iso(),
            "agent": str(payload.get("agent", "unknown")),
            "model": str(payload.get("model", "unknown")),
            "provider": str(payload.get("provider", "unknown")),
            "tokens_input": int(payload.get("tokens_input", 0) or 0),
            "tokens_output": int(payload.get("tokens_output", 0) or 0),
            "tokens_total": int(payload.get("tokens_total", 0) or 0),
            "cost_usd": float(payload.get("cost_usd", 0.0) or 0.0),
            "session_id": str(payload.get("session_id", "")),
            "task": str(payload.get("task", "")),
            "metadata": payload.get("metadata", {}),
        }
        if usage_db_path is not None:
            started = time.perf_counter()
            insert_usage_event_db(usage_db_path, row)
            elapsed_ms = (time.perf_counter() - started) * 1000
            push_write_metric(elapsed_ms)
        else:
            append_usage_event(usage_path, row)
        return JSONResponse({"ok": True, "recorded": row})

    @app.get("/api/tasks/summary")
    async def api_tasks_summary(hours: int = 24, limit: int = 25) -> JSONResponse:
        if usage_db_path is None:
            return JSONResponse({"active": [], "recent": [], "status_totals": {}, "hours": hours})
        summary = summarize_tasks_db(usage_db_path, hours=hours, limit=max(1, min(limit, 200)))
        summary["hours"] = hours
        return JSONResponse(summary)

    @app.post("/api/tasks/review-action")
    async def api_tasks_review_action(payload: dict[str, Any]) -> JSONResponse:
        if usage_db_path is None:
            raise HTTPException(status_code=400, detail="task review actions require sqlite telemetry")

        decision = str(payload.get("decision", "")).strip().lower()
        if decision not in {"approved", "changes_requested", "rejected"}:
            raise HTTPException(status_code=400, detail="decision must be approved, changes_requested, or rejected")

        thread_id = str(payload.get("thread_id", "")).strip()
        task_id = derive_task_id_from_thread(thread_id, fallback=str(payload.get("task_id", "")))
        reviewer = str(payload.get("reviewer", "jarvis-reviewer")).strip() or "jarvis-reviewer"
        note = compact_summary(str(payload.get("note", "")) or f"Review decision: {decision}")
        scope = str(payload.get("scope", "once")).strip().lower()
        if scope not in {"once", "always"}:
            scope = "once"
        approved_agent = str(payload.get("agent", "")).strip()

        row = {
            "timestamp": now_iso(),
            "task_id": task_id,
            "thread_id": thread_id,
            "channel": str(payload.get("channel", "slack")),
            "user_name": reviewer,
            "agent": reviewer,
            "execution_target": str(payload.get("execution_target", "nyx")),
            "stage": "review",
            "status": decision,
            "summary": note,
            "reviewer_required": True,
            "reviewed": decision == "approved",
            "detail": {
                "decision": decision,
                "note": note,
                "source": "review-action-api",
                "scope": scope,
            },
        }
        insert_task_event_db(usage_db_path, row)

        if decision == "approved" and scope == "always" and approved_agent:
            set_approval_preference_db(usage_db_path, reviewer, approved_agent, "always")

        return JSONResponse({"ok": True, "task_id": task_id, "decision": decision})

    @app.post("/api/approvals/preference")
    async def api_approvals_preference(payload: dict[str, Any]) -> JSONResponse:
        if usage_db_path is None:
            raise HTTPException(status_code=400, detail="approval preferences require sqlite telemetry")
        user_id = str(payload.get("user_id", "")).strip()
        agent = str(payload.get("agent", "")).strip()
        scope = str(payload.get("scope", "none")).strip().lower()
        if not user_id or not agent:
            raise HTTPException(status_code=400, detail="user_id and agent are required")
        if scope not in {"always", "once", "none"}:
            raise HTTPException(status_code=400, detail="scope must be always, once, or none")
        set_approval_preference_db(usage_db_path, user_id, agent, scope)
        return JSONResponse({"ok": True, "user_id": user_id, "agent": agent, "scope": scope})

    @app.get("/api/approvals/preference")
    async def api_approvals_preference_get(user_id: str, agent: str) -> JSONResponse:
        if usage_db_path is None:
            return JSONResponse({"ok": True, "user_id": user_id, "agent": agent, "scope": "none"})
        scope = get_approval_preference_db(usage_db_path, user_id, agent)
        return JSONResponse({"ok": True, "user_id": user_id, "agent": agent, "scope": scope})

    @app.get("/api/system/topology")
    async def api_system_topology() -> JSONResponse:
        work_headers: dict[str, str] = {}
        if work_shared_token:
            work_headers["X-Jarvis-Shared-Token"] = work_shared_token

        mac_headers: dict[str, str] = {}
        if mac_shared_token:
            mac_headers["X-Jarvis-Shared-Token"] = mac_shared_token

        harness_probe = await probe_endpoint(name="jarvis-harness", base_url=harness_url)
        api_probe = {
            "node": "jarvis-api",
            "ok": True,
            "status": "up",
            "latency_ms": 0.0,
            "url": "/api/system/topology",
            "payload": {"service": "jarvis-api"},
        }
        slack_probe = await probe_endpoint(name="jarvis-slack-gateway", base_url=slack_endpoint)
        nyx_probe = await probe_endpoint(name="nyx-work-runner", base_url=work_endpoint, headers=work_headers)
        workers_probe = await probe_endpoint(name="nyx-workers", base_url=work_endpoint, path="/workers", headers=work_headers)
        mac_probe = await probe_endpoint(name="mac-runner", base_url=mac_endpoint, headers=mac_headers)
        voice_probe = await probe_endpoint(name="voice-edge", base_url=mac_endpoint, path="/status", headers=mac_headers)

        workers = []
        if workers_probe.get("ok") and isinstance(workers_probe.get("payload"), dict):
            payload = workers_probe.get("payload")
            workers = payload.get("workers") if isinstance(payload, dict) and isinstance(payload.get("workers"), list) else []

        active_summary = summarize_tasks_db(usage_db_path, hours=24, limit=200) if usage_db_path is not None else {"active": []}
        active_nyx = [row for row in active_summary.get("active", []) if str(row.get("execution_target", "")) == "nyx"]
        logical_busy = len(active_nyx) > 0
        if workers:
            for worker in workers:
                if logical_busy:
                    worker["logical_busy"] = True

        nodes = [api_probe, harness_probe, slack_probe, nyx_probe, mac_probe, voice_probe]
        connected = len([n for n in nodes if n.get("ok")])
        return JSONResponse(
            {
                "ok": True,
                "connected_nodes": connected,
                "total_nodes": len(nodes),
                "nodes": nodes,
                "workers": workers,
            }
        )

    @app.get("/api/system/db-metrics")
    async def api_system_db_metrics() -> JSONResponse:
        if usage_db_path is None:
            return JSONResponse({"storage": "jsonl", "path": usage_file, "write_metrics": write_metrics_snapshot()})

        db_exists = usage_db_path.exists()
        size_bytes = usage_db_path.stat().st_size if db_exists else 0
        usage_rows = 0
        task_rows = 0
        if db_exists:
            with sqlite3.connect(usage_db_path) as conn:
                usage_rows = int(conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0])
                task_rows = int(conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0])

        return JSONResponse(
            {
                "storage": "sqlite",
                "path": str(usage_db_path),
                "size_bytes": size_bytes,
                "usage_event_rows": usage_rows,
                "task_event_rows": task_rows,
                "write_metrics": write_metrics_snapshot(),
                "supabase_adapter": {
                    "mode": "dormant",
                    "configured": bool(supabase_url and supabase_key),
                    "url_present": bool(supabase_url),
                },
            }
        )

    @app.get("/api/projects/overlap")
    async def api_projects_overlap(limit: int = 20) -> JSONResponse:
        if project_map_path is None:
            return JSONResponse({"projects": [], "overlaps": [], "limit": limit})
        data = load_project_overlap_map(project_map_path, limit)
        data["limit"] = max(1, min(limit, 200))
        return JSONResponse(data)

    @app.get("/api/tasks/stuck")
    async def api_tasks_stuck(stale_after_seconds: int = 900, limit: int = 25) -> JSONResponse:
        if usage_db_path is None:
            return JSONResponse({"stale_after_seconds": stale_after_seconds, "count": 0, "tasks": []})
        return JSONResponse(summarize_stuck_tasks(usage_db_path, max(60, stale_after_seconds), limit))

    @app.get("/api/usage/summary")
    async def api_usage_summary(hours: int = 24) -> JSONResponse:
        summary = summarize_usage_db(usage_db_path, hours) if usage_db_path is not None else summarize_usage(usage_path, hours)
        return JSONResponse(summary)

    @app.get("/api/usage/recommendations")
    async def api_usage_recommendations(hours: int = 24) -> JSONResponse:
        summary = summarize_usage_db(usage_db_path, hours) if usage_db_path is not None else summarize_usage(usage_path, hours)
        recs = model_recommendations(summary)
        return JSONResponse({"recommendations": recs, "hours": hours})

    @app.websocket("/ws/voice")
    async def voice_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        session_id = str(uuid.uuid4())
        await websocket.send_json(
            {
                "type": "hello",
                "session_id": session_id,
                "service": "jarvis-api",
                "timestamp": now_iso(),
            }
        )

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "detail": "Invalid JSON payload",
                            "timestamp": now_iso(),
                        }
                    )
                    continue

                message_type = payload.get("type", "")
                if message_type == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": now_iso()})
                    continue

                if message_type == "voice_edge_hello":
                    await websocket.send_json(
                        {
                            "type": "hello_ack",
                            "session_id": session_id,
                            "timestamp": now_iso(),
                        }
                    )
                    continue

                if message_type == "interrupt":
                    await websocket.send_json({"type": "interrupt_ack", "timestamp": now_iso()})
                    continue

                text = payload.get("text", "")
                if not text:
                    await websocket.send_json(
                        {
                            "type": "ignored",
                            "detail": "No text payload received",
                            "timestamp": now_iso(),
                        }
                    )
                    continue

                interaction = {
                    "channel": "voice",
                    "user": payload.get("user", "voice-edge"),
                    "text": text,
                    "target_agent": payload.get("target_agent"),
                    "thread_id": payload.get("thread_id", session_id),
                    "realm": payload.get("realm"),
                    "timestamp": now_iso(),
                }

                try:
                    routed = await route_payload(interaction)
                    route_status = "approval_required" if routed.get("requires_approval") else "routed"
                    record_task_event(
                        payload=interaction,
                        routed=routed,
                        stage="route",
                        status=route_status,
                        detail={"source": "voice", "model_tier": routed.get("model_tier")},
                    )
                    work_result = await dispatch_work(interaction, routed)
                    if work_result is not None:
                        execution_target = str(routed.get("execution_target") or "")
                        if not work_result.get("ok"):
                            final_status = "failed"
                        elif execution_target == "nyx":
                            final_status = "running"
                        else:
                            final_status = "review_pending"
                        record_task_event(
                            payload=interaction,
                            routed=routed,
                            stage="execution",
                            status=final_status,
                            detail={
                                "source": "voice",
                                "worker_id": work_result.get("worker_id") or (work_result.get("selected_worker") or {}).get("worker_id"),
                                "ok": work_result.get("ok"),
                            },
                        )
                except httpx.HTTPError as exc:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "detail": str(exc),
                            "timestamp": now_iso(),
                        }
                    )
                    continue

                await websocket.send_json(
                    {
                        "type": "routed",
                        "session_id": session_id,
                        "routed": routed,
                        "work_result": work_result,
                        "timestamp": now_iso(),
                    }
                )
                await websocket.send_json(
                    {
                        "type": "speak_text",
                        "text": summary_text(routed, work_result),
                        "timestamp": now_iso(),
                    }
                )
        except WebSocketDisconnect:
            return

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Jarvis API service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--harness-url", required=True)
    parser.add_argument("--work-endpoint", default="")
    parser.add_argument("--work-shared-token", default="")
    parser.add_argument("--mac-endpoint", default="")
    parser.add_argument("--mac-shared-token", default="")
    parser.add_argument("--usage-file", default="")
    parser.add_argument("--usage-sqlite", default="/var/lib/jarvis/data/usage.db")
    parser.add_argument("--routing-events-file", default="/var/lib/jarvis/data/routing_events.jsonl")
    parser.add_argument("--project-map-file", default="/opt/jarvis/data/project_overlap_map.neuronet.json")
    parser.add_argument("--slack-endpoint", default="http://127.0.0.1:8081")
    parser.add_argument("--supabase-url", default="")
    parser.add_argument("--supabase-key", default="")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        create_app(
            harness_url=args.harness_url,
            work_endpoint=args.work_endpoint,
            work_shared_token=args.work_shared_token,
            mac_endpoint=args.mac_endpoint,
            mac_shared_token=args.mac_shared_token,
            usage_file=args.usage_file,
            usage_sqlite=args.usage_sqlite,
            routing_events_file=args.routing_events_file,
            project_map_file=args.project_map_file,
            slack_endpoint=args.slack_endpoint,
            supabase_url=args.supabase_url,
            supabase_key=args.supabase_key,
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
