from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def compact_summary(text: str, max_len: int = 120) -> str:
    normalized = " ".join(str(text or "").strip().split())
    if len(normalized) <= max_len:
        return normalized
    return normalized[: max_len - 1] + "..."


def write_state(state_file: Path, payload: dict[str, Any]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def create_app(
    *,
    shared_token: str,
    state_file: str,
    repo_dir: str,
    worker_id: str,
    capabilities: list[str],
    callback_url: str,
    callback_token: str,
) -> FastAPI:
    app = FastAPI(title="Jarvis Work Runner", version="0.1.0")
    state_path = Path(state_file)
    run_lock = asyncio.Lock()
    queue_depth = 0

    def validate_token(
        authorization: str,
        x_jarvis_shared_token: str,
    ) -> None:
        if not shared_token:
            return

        header_token = x_jarvis_shared_token
        if not header_token and authorization.startswith("Bearer "):
            header_token = authorization[7:]

        if header_token != shared_token:
            raise HTTPException(status_code=401, detail="Invalid shared token")

    def send_callback(payload: dict[str, Any]) -> tuple[bool, str]:
        if not callback_url:
            return False, "callback-url-missing"
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if callback_token:
            headers["X-Jarvis-Shared-Token"] = callback_token
        req = urlrequest.Request(
            callback_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlrequest.urlopen(req, timeout=8):
                return True, "callback-sent"
        except (urlerror.URLError, TimeoutError) as exc:
            return False, f"callback-error: {exc}"

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "service": "jarvis-work-runner",
                "repo_dir": repo_dir,
                "state_file": state_file,
                "worker_id": worker_id,
                "capabilities": capabilities,
            }
        )

    @app.get("/workers")
    async def workers() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "workers": [
                    {
                        "worker_id": worker_id,
                        "capabilities": capabilities,
                        "busy": run_lock.locked(),
                        "queue_depth": queue_depth,
                    }
                ],
            }
        )

    @app.post("/run")
    async def run(
        payload: dict[str, Any],
        authorization: str = Header(default=""),
        x_jarvis_shared_token: str = Header(default=""),
    ) -> JSONResponse:
        nonlocal queue_depth
        validate_token(authorization, x_jarvis_shared_token)
        queue_depth += 1
        async with run_lock:
            queue_depth = max(0, queue_depth - 1)
            row = {
                "ok": True,
                "service": "jarvis-work-runner",
                "worker_id": worker_id,
                "capabilities": capabilities,
                "accepted_at": now_iso(),
                "payload": payload,
            }

            routed = payload.get("routed_event") if isinstance(payload.get("routed_event"), dict) else {}
            input_event = payload.get("input_event") if isinstance(payload.get("input_event"), dict) else {}
            callback_payload = {
                "thread_id": str(routed.get("thread_id") or input_event.get("thread_id") or ""),
                "task_id": str(routed.get("event_id") or ""),
                "user": str(routed.get("user") or input_event.get("user") or "nyx-worker"),
                "agent": str(routed.get("resolved_agent") or "research-agent"),
                "execution_target": "nyx",
                "worker_id": worker_id,
                "status": "running",
                "summary": compact_summary(str(routed.get("text") or input_event.get("text") or "work accepted by nyx worker"), max_len=120),
            }
            callback_ok, callback_detail = send_callback(callback_payload)
            row["callback_ok"] = callback_ok
            row["callback_detail"] = callback_detail

            write_state(state_path, row)
            return JSONResponse(row)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Jarvis work runner service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--shared-token", default="")
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--worker-id", default="nyx-worker-1")
    parser.add_argument("--capabilities", default="code,triage,documentation,investigation")
    parser.add_argument("--callback-url", default="")
    parser.add_argument("--callback-token", default="")
    args = parser.parse_args()

    capabilities = [part.strip() for part in args.capabilities.split(",") if part.strip()]

    import uvicorn

    uvicorn.run(
        create_app(
            shared_token=args.shared_token,
            state_file=args.state_file,
            repo_dir=args.repo_dir,
            worker_id=args.worker_id,
            capabilities=capabilities,
            callback_url=args.callback_url,
            callback_token=args.callback_token,
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
