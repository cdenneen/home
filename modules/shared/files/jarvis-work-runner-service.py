from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


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
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
