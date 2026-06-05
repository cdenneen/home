from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse


def load_router_modules(repo_dir: str):
    src_dir = Path(repo_dir) / "src"
    if not src_dir.is_dir():
        raise RuntimeError(f"Jarvis source directory not found at {src_dir}")

    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from jarvis.harness_router import append_route, load_yaml, route
    from jarvis.delegation_policy import DelegationPolicy
    from jarvis.realm_policy import RealmLockStore, RealmPolicy

    return append_route, load_yaml, route, DelegationPolicy, RealmLockStore, RealmPolicy


def create_app(
    *,
    repo_dir: str,
    registry_path: str,
    realms_path: str,
    locks_path: str,
    routing_output: str,
    delegation_path: str,
    model_profiles_path: str,
) -> FastAPI:
    append_route, load_yaml, route, DelegationPolicy, RealmLockStore, RealmPolicy = load_router_modules(repo_dir)
    registry = load_yaml(Path(registry_path))
    realm_policy = RealmPolicy.from_file(Path(realms_path))
    lock_store = RealmLockStore(Path(locks_path))
    delegation_policy = DelegationPolicy.from_file(Path(delegation_path)) if Path(delegation_path).exists() else None
    model_profiles = load_yaml(Path(model_profiles_path)) if Path(model_profiles_path).exists() else {}
    output = Path(routing_output)

    app = FastAPI(title="Jarvis Harness", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "service": "jarvis-harness",
                "repo_dir": repo_dir,
                "routing_output": str(output),
            }
        )

    @app.post("/route")
    async def route_event(payload: dict[str, Any]) -> JSONResponse:
        try:
            routed = route(
                payload,
                registry,
                realm_policy=realm_policy,
                lock_store=lock_store,
                delegation_policy=delegation_policy,
                model_profiles=model_profiles,
            )
            append_route(output, routed)
        except Exception as exc:  # pragma: no cover - surfaced in service logs
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        return JSONResponse(routed)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Jarvis harness service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8079)
    parser.add_argument("--repo-dir", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--realms", required=True)
    parser.add_argument("--locks", required=True)
    parser.add_argument("--routing-output", required=True)
    parser.add_argument("--delegation", required=True)
    parser.add_argument("--model-profiles", required=True)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        create_app(
            repo_dir=args.repo_dir,
            registry_path=args.registry,
            realms_path=args.realms,
            locks_path=args.locks,
            routing_output=args.routing_output,
            delegation_path=args.delegation,
            model_profiles_path=args.model_profiles,
        ),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
