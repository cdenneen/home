from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime
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


def create_app(
    *,
    harness_url: str,
    work_endpoint: str,
    work_shared_token: str,
) -> FastAPI:
    app = FastAPI(title="Jarvis API", version="0.1.0")

    async def route_payload(payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(f"{harness_url.rstrip('/')}/route", json=payload)
            response.raise_for_status()
            return response.json()

    async def dispatch_work(payload: dict[str, Any], routed: dict[str, Any]) -> dict[str, Any] | None:
        if routed.get("requires_approval"):
            return None
        if routed.get("execution_target") != "nyx":
            return None
        if not work_endpoint:
            return None

        headers: dict[str, str] = {}
        if work_shared_token:
            headers["X-Jarvis-Shared-Token"] = work_shared_token

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{work_endpoint.rstrip('/')}/run",
                json={
                    "input_event": payload,
                    "routed_event": routed,
                },
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "service": "jarvis-api",
                "harness_url": harness_url,
                "work_endpoint": work_endpoint,
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
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        create_app(
            harness_url=args.harness_url,
            work_endpoint=args.work_endpoint,
            work_shared_token=args.work_shared_token,
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
