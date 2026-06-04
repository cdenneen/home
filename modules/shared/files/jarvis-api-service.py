from __future__ import annotations

import argparse
import json
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
        conn.commit()


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


def create_app(
    *,
    harness_url: str,
    work_endpoint: str,
    work_shared_token: str,
    mac_endpoint: str,
    mac_shared_token: str,
    usage_file: str,
    usage_sqlite: str,
) -> FastAPI:
    app = FastAPI(title="Jarvis API", version="0.1.0")
    usage_path = Path(usage_file)
    usage_db_path = Path(usage_sqlite) if usage_sqlite else None
    if usage_db_path is not None:
        init_usage_db(usage_db_path)

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
            }
        )

    @app.get("/api/healthz")
    async def api_healthz() -> JSONResponse:
        return await healthz()

    @app.post("/api/route")
    async def api_route(payload: dict[str, Any]) -> JSONResponse:
        try:
            routed = await route_payload(payload)
            work_result = await dispatch_work(payload, routed)
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
            insert_usage_event_db(usage_db_path, row)
        else:
            append_usage_event(usage_path, row)
        return JSONResponse({"ok": True, "recorded": row})

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
                    work_result = await dispatch_work(interaction, routed)
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
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
