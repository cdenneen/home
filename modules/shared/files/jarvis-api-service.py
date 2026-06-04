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


def create_app(
    *,
    harness_url: str,
    work_endpoint: str,
    work_shared_token: str,
    mac_endpoint: str,
    mac_shared_token: str,
) -> FastAPI:
    app = FastAPI(title="Jarvis API", version="0.1.0")

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

    async def dispatch_work(payload: dict[str, Any], routed: dict[str, Any]) -> dict[str, Any] | None:
        if routed.get("requires_approval"):
            return None

        target = routed.get("execution_target")
        if target == "nyx":
            return await _dispatch_to_endpoint(
                endpoint=work_endpoint,
                shared_token=work_shared_token,
                payload=payload,
                routed=routed,
            )

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
    parser.add_argument("--mac-endpoint", default="")
    parser.add_argument("--mac-shared-token", default="")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        create_app(
            harness_url=args.harness_url,
            work_endpoint=args.work_endpoint,
            work_shared_token=args.work_shared_token,
            mac_endpoint=args.mac_endpoint,
            mac_shared_token=args.mac_shared_token,
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
