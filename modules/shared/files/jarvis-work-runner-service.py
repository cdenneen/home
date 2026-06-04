from __future__ import annotations

import argparse
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


def create_app(*, shared_token: str, state_file: str, repo_dir: str) -> FastAPI:
    app = FastAPI(title="Jarvis Work Runner", version="0.1.0")
    state_path = Path(state_file)

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
            }
        )

    @app.post("/run")
    async def run(
        payload: dict[str, Any],
        authorization: str = Header(default=""),
        x_jarvis_shared_token: str = Header(default=""),
    ) -> JSONResponse:
        validate_token(authorization, x_jarvis_shared_token)
        row = {
            "ok": True,
            "service": "jarvis-work-runner",
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
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        create_app(
            shared_token=args.shared_token,
            state_file=args.state_file,
            repo_dir=args.repo_dir,
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
