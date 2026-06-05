from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
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


async def _git_dirty_count(repo_path: Path) -> tuple[str, int]:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo_path),
        "status",
        "--short",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        return repo_path.name, -1
    lines = out.decode("utf-8", errors="ignore").splitlines()
    return repo_path.name, len(lines)


def _find_git_repos(workspace_root: Path, limit: int = 60) -> list[Path]:
    repos: list[Path] = []
    if not workspace_root.exists():
        return repos
    for root, dirs, _ in os.walk(workspace_root):
        if ".git" in dirs:
            repos.append(Path(root))
            dirs[:] = [d for d in dirs if d != ".git"]
        if len(repos) >= limit:
            break
    return repos


def _collect_session_counts() -> dict[str, int]:
    home = Path.home()
    opencode = home / ".opencode"
    codex = home / ".codex"
    return {
        "opencode_files": len(list(opencode.rglob("*.json"))) + len(list(opencode.rglob("*.jsonl"))) if opencode.exists() else 0,
        "codex_files": len(list(codex.rglob("*.json"))) + len(list(codex.rglob("*.jsonl"))) if codex.exists() else 0,
    }


async def _run_ingestion(payload: dict[str, Any]) -> dict[str, Any]:
    routed = payload.get("routed_event") if isinstance(payload.get("routed_event"), dict) else {}
    input_event = payload.get("input_event") if isinstance(payload.get("input_event"), dict) else {}
    text = str(routed.get("text") or input_event.get("text") or "")

    workspace_root = Path(os.getenv("JARVIS_WORKSPACE_ROOT", "/home/cdenneen/src/workspace"))
    report_dir = Path(os.getenv("JARVIS_WORK_REPORT_DIR", "/var/lib/jarvis/data"))
    report_dir.mkdir(parents=True, exist_ok=True)

    agent_files = []
    if workspace_root.exists():
        for path in workspace_root.rglob("AGENTS.md"):
            agent_files.append(str(path))
            if len(agent_files) >= 200:
                break

    repos = _find_git_repos(workspace_root, limit=80)
    dirty_rows: list[dict[str, Any]] = []
    for repo in repos[:40]:
        name, dirty = await _git_dirty_count(repo)
        dirty_rows.append({"repo": name, "path": str(repo), "dirty_files": dirty})

    session_counts = _collect_session_counts()
    dirty_total = sum(max(0, int(row.get("dirty_files", 0))) for row in dirty_rows)
    dirty_repos = len([row for row in dirty_rows if int(row.get("dirty_files", 0)) > 0])

    report = {
        "timestamp": now_iso(),
        "mode": "ingestion",
        "workspace_root": str(workspace_root),
        "input_summary": compact_summary(text, max_len=180),
        "agents_files_count": len(agent_files),
        "agents_files": agent_files,
        "repos_scanned": len(dirty_rows),
        "dirty_repos": dirty_repos,
        "dirty_total_files": dirty_total,
        "repo_status": dirty_rows,
        "session_counts": session_counts,
    }

    report_path = report_dir / f"ingestion-report-{int(time.time())}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "mode": "ingestion",
        "summary": compact_summary(
            f"Ingestion complete: repos={len(dirty_rows)}, dirty_repos={dirty_repos}, AGENTS.md={len(agent_files)}, sessions(opencode={session_counts['opencode_files']}, codex={session_counts['codex_files']}). Report: {report_path}",
            max_len=220,
        ),
        "report_path": str(report_path),
        "agents_files_count": len(agent_files),
        "repos_scanned": len(dirty_rows),
        "dirty_repos": dirty_repos,
        "session_counts": session_counts,
    }


async def execute_work(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    routed = payload.get("routed_event") if isinstance(payload.get("routed_event"), dict) else {}
    input_event = payload.get("input_event") if isinstance(payload.get("input_event"), dict) else {}

    controls = payload.get("runner_control") if isinstance(payload.get("runner_control"), dict) else {}
    delay_ms = int(controls.get("delay_ms", payload.get("simulate_delay_ms", 0)) or 0)
    delay_ms = max(0, min(delay_ms, 30000))
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000)

    text = str(routed.get("text") or input_event.get("text") or "")
    if bool(controls.get("force_fail", False)) or "[force-fail]" in text.lower():
        raise RuntimeError("runner control requested failure")

    lowered = text.lower()
    if "ingest" in lowered or "workspace" in lowered or "agents.md" in lowered:
        result = await _run_ingestion(payload)
        result["duration_ms"] = int((time.perf_counter() - started) * 1000)
        result["route"] = {
            "agent": str(routed.get("resolved_agent") or "research-agent"),
            "target": str(routed.get("execution_target") or "nyx"),
        }
        return result

    return {
        "ok": True,
        "mode": "lightweight",
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "summary": compact_summary(text or "Nyx worker executed lightweight job", max_len=120),
        "route": {
            "agent": str(routed.get("resolved_agent") or "research-agent"),
            "target": str(routed.get("execution_target") or "nyx"),
        },
    }


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
        routed = payload.get("routed_event") if isinstance(payload.get("routed_event"), dict) else {}
        input_event = payload.get("input_event") if isinstance(payload.get("input_event"), dict) else {}

        callback_base = {
            "thread_id": str(routed.get("thread_id") or input_event.get("thread_id") or ""),
            "task_id": str(routed.get("event_id") or ""),
            "user": str(routed.get("user") or input_event.get("user") or "nyx-worker"),
            "agent": str(routed.get("resolved_agent") or "research-agent"),
            "execution_target": "nyx",
            "worker_id": worker_id,
        }

        queue_depth += 1
        async with run_lock:
            queue_depth = max(0, queue_depth - 1)
            execution_started_at = now_iso()
            running_payload = {
                **callback_base,
                "status": "running",
                "summary": compact_summary(
                    str(routed.get("text") or input_event.get("text") or "work accepted by nyx worker"),
                    max_len=120,
                ),
            }
            running_ok, running_detail = send_callback(running_payload)

            row = {
                "ok": True,
                "service": "jarvis-work-runner",
                "worker_id": worker_id,
                "capabilities": capabilities,
                "accepted_at": now_iso(),
                "execution_started_at": execution_started_at,
                "payload": payload,
                "callback_running_ok": running_ok,
                "callback_running_detail": running_detail,
            }

            try:
                execution_result = await execute_work(payload)
                row["execution_result"] = execution_result
                row["execution_completed_at"] = now_iso()
                write_state(state_path, row)
                completed_payload = {
                    **callback_base,
                    "status": "completed",
                    "summary": compact_summary(
                        str(execution_result.get("summary") or "Nyx worker completed lightweight execution."),
                        max_len=120,
                    ),
                }
                completed_ok, completed_detail = send_callback(completed_payload)
                row["callback_completed_ok"] = completed_ok
                row["callback_completed_detail"] = completed_detail
                write_state(state_path, row)
                return JSONResponse(row)
            except Exception as exc:
                failed_payload = {
                    **callback_base,
                    "status": "failed",
                    "summary": compact_summary(f"Worker run failed: {exc}", max_len=120),
                }
                failed_ok, failed_detail = send_callback(failed_payload)
                row["ok"] = False
                row["execution_completed_at"] = now_iso()
                row["error"] = str(exc)
                row["callback_failed_ok"] = failed_ok
                row["callback_failed_detail"] = failed_detail
                write_state(state_path, row)
                raise HTTPException(status_code=500, detail=f"worker execution failed: {exc}") from exc

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
