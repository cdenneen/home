"""Durable, idempotent Hermes peer-run dispatch and completion tracking."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
KEY_RE = re.compile(r"^[!-~]{1,255}$")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read JSON config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TypeError(f"JSON config must be an object: {path}")
    return value


def connect(state_dir: Path) -> sqlite3.Connection:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state_dir, 0o700)
    database = state_dir / "ledger.sqlite3"
    connection = sqlite3.connect(database)
    os.chmod(database, 0o600)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
          run_id TEXT PRIMARY KEY,
          target TEXT NOT NULL,
          idempotency_key TEXT NOT NULL UNIQUE,
          request_sha256 TEXT,
          board TEXT NOT NULL,
          task_id TEXT NOT NULL,
          status TEXT NOT NULL,
          created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL,
          terminal_at INTEGER,
          result_path TEXT,
          result_sha256 TEXT,
          delivered_at INTEGER
        )
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
    if "request_sha256" not in columns:
        connection.execute("ALTER TABLE runs ADD COLUMN request_sha256 TEXT")
    return connection


def peer(config: dict[str, Any], target: str) -> tuple[str, str, str]:
    peer_name, separator, profile = target.partition("/")
    peers = config.get("peers")
    entry = peers.get(peer_name) if isinstance(peers, dict) else None
    if not separator or not profile or not isinstance(entry, dict):
        raise RuntimeError(f"invalid or unknown target {target!r}; expected <peer>/<profile>")
    url = str(entry.get("url") or "").rstrip("/")
    key_file = Path(str(entry.get("key_file") or "")).expanduser()
    if not url.startswith(("http://", "https://")):
        raise RuntimeError(f"peer {peer_name!r} has no valid HTTP URL")
    try:
        key = key_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"cannot read key file for peer {peer_name!r}: {exc}") from exc
    if not key:
        raise RuntimeError(f"key file for peer {peer_name!r} is empty")
    base = f"{url}/p/{urllib.parse.quote(profile, safe='')}"
    return peer_name, base, key


def request(
    config: dict[str, Any],
    target: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    peer_name, base, key = peer(config, target)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "hermes-peer-dispatch",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
    api_request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect).open(api_request, timeout=30) as response:
            payload = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"peer {peer_name!r} returned HTTP {exc.code}: {detail}") from exc
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"cannot reach peer {peer_name!r}: {exc}") from exc
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"peer {peer_name!r} returned non-JSON data") from exc
    if not isinstance(value, dict):
        raise TypeError(f"peer {peer_name!r} returned a non-object response")
    return value


def run_hermes(config: dict[str, Any], board: str, arguments: list[str]) -> str:
    command = [
        str(config["hermes_bin"]),
        "-p",
        str(config.get("kanban_profile", "chief-of-staff")),
        "kanban",
        "--board",
        board,
        *arguments,
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Hermes Kanban command failed")
    return result.stdout


def comment_once(
    config: dict[str, Any], board: str, task_id: str, marker: str, message: str
) -> bool:
    task = json.loads(run_hermes(config, board, ["show", task_id, "--json"]))
    comments = task.get("comments") if isinstance(task, dict) else None
    if any(marker in str(item.get("body") or "") for item in comments or [] if isinstance(item, dict)):
        return False
    run_hermes(
        config,
        board,
        ["comment", task_id, f"{marker} {message}", "--author", "peer-run-watcher"],
    )
    return True


def atomic_result(state_dir: Path, run_id: str, payload: dict[str, Any]) -> tuple[str, str]:
    results_dir = state_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(data).hexdigest()
    destination = results_dir / f"{run_id}.json"
    with tempfile.NamedTemporaryFile(dir=results_dir, delete=False) as temporary:
        temporary.write(data)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_name = temporary.name
    os.chmod(temporary_name, 0o600)
    os.replace(temporary_name, destination)
    return str(destination), digest


def start(args: argparse.Namespace, config: dict[str, Any], connection: sqlite3.Connection) -> None:
    key = args.idempotency_key.strip()
    if not KEY_RE.fullmatch(key):
        raise RuntimeError("idempotency key must be 1-255 visible ASCII characters")
    message = args.message.strip()
    if not message:
        raise RuntimeError("message cannot be empty")
    request_sha256 = hashlib.sha256(message.encode()).hexdigest()
    existing = connection.execute(
        "SELECT run_id, target, board, task_id, status, request_sha256 FROM runs WHERE idempotency_key = ?",
        (key,),
    ).fetchone()
    if existing:
        if (existing["target"], existing["board"], existing["task_id"]) != (
            args.target,
            args.board,
            args.task,
        ):
            raise RuntimeError("idempotency key is already bound to a different dispatch")
        if existing["request_sha256"] not in (None, request_sha256):
            raise RuntimeError("idempotency key is already bound to a different message")
        print(json.dumps({"run_id": existing["run_id"], "status": existing["status"], "replayed": True}))
        return
    run_hermes(config, args.board, ["show", args.task, "--json"])
    response = request(config, args.target, "POST", "/v1/runs", {"input": message}, key)
    run_id = str(response.get("run_id") or "")
    if not run_id:
        raise RuntimeError("peer did not return a run ID")
    now = int(time.time())
    connection.execute(
        """
        INSERT INTO runs (
          run_id, target, idempotency_key, request_sha256, board, task_id, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at
        """,
        (
            run_id,
            args.target,
            key,
            request_sha256,
            args.board,
            args.task,
            str(response.get("status") or "started"),
            now,
            now,
        ),
    )
    connection.commit()
    comment_once(
        config,
        args.board,
        args.task,
        f"[peer-run:{run_id}:started]",
        f"Dispatched isolated run to `{args.target}`; idempotency key `{key}`.",
    )
    print(json.dumps({"run_id": run_id, "status": response.get("status"), "replayed": response.get("replayed", False)}))


def tracked_run(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise RuntimeError(f"unknown tracked run {run_id!r}")
    return row


def control(
    args: argparse.Namespace, config: dict[str, Any], connection: sqlite3.Connection, action: str
) -> None:
    row = tracked_run(connection, args.run_id)
    body = {"input": args.message} if action == "steer" else {}
    response = request(config, row["target"], "POST", f"/v1/runs/{urllib.parse.quote(args.run_id)}/{action}", body)
    print(json.dumps(response, sort_keys=True))


def show_status(args: argparse.Namespace, config: dict[str, Any], connection: sqlite3.Connection) -> None:
    row = tracked_run(connection, args.run_id)
    response = request(config, row["target"], "GET", f"/v1/runs/{urllib.parse.quote(args.run_id)}")
    print(json.dumps(response, indent=2, sort_keys=True))


def list_runs(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT run_id, target, board, task_id, status, updated_at, delivered_at FROM runs ORDER BY created_at DESC"
    ).fetchall()
    print(json.dumps([dict(row) for row in rows], indent=2, sort_keys=True))


def approval_marker(status: dict[str, Any]) -> tuple[str, str] | None:
    approval = status.get("approval")
    if not isinstance(approval, dict):
        return None
    request_id = str(approval.get("request_id") or "unknown")
    detail = str(approval.get("description") or approval.get("command") or "approval required")
    return request_id, detail[:500]


def reconcile(config: dict[str, Any], state_dir: Path, connection: sqlite3.Connection) -> None:
    checked = delivered = approvals = 0
    rows = connection.execute("SELECT * FROM runs WHERE delivered_at IS NULL ORDER BY created_at").fetchall()
    for row in rows:
        checked += 1
        status = request(
            config,
            row["target"],
            "GET",
            f"/v1/runs/{urllib.parse.quote(row['run_id'])}",
        )
        state = str(status.get("status") or "unknown")
        now = int(time.time())
        connection.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
            (state, now, row["run_id"]),
        )
        connection.commit()
        if state == "waiting_for_approval":
            pending = approval_marker(status)
            if pending:
                request_id, detail = pending
                marker = f"[peer-run:{row['run_id']}:approval:{request_id}]"
                approvals += int(
                    comment_once(
                        config,
                        row["board"],
                        row["task_id"],
                        marker,
                        f"Run on `{row['target']}` requires explicit approval: {detail}",
                    )
                )
        if state not in TERMINAL_STATUSES:
            continue
        result_path, digest = atomic_result(state_dir, row["run_id"], status)
        marker = f"[peer-run:{row['run_id']}:terminal]"
        output = status.get("output")
        quality = "output captured" if isinstance(output, str) and output.strip() else "no final output; review required"
        comment_once(
            config,
            row["board"],
            row["task_id"],
            marker,
            f"Run on `{row['target']}` reached `{state}` ({quality}). Result `{result_path}`; sha256 `{digest}`. Transport completion does not approve or complete the task.",
        )
        connection.execute(
            """
            UPDATE runs SET terminal_at = ?, result_path = ?, result_sha256 = ?, delivered_at = ?, updated_at = ?
            WHERE run_id = ?
            """,
            (now, result_path, digest, now, now, row["run_id"]),
        )
        connection.commit()
        delivered += 1
    print(json.dumps({"checked": checked, "delivered": delivered, "approval_notices": approvals}))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--config", type=Path, required=True)
    result.add_argument("--state-dir", type=Path, required=True)
    commands = result.add_subparsers(dest="command", required=True)
    start_parser = commands.add_parser("start")
    start_parser.add_argument("target")
    start_parser.add_argument("--idempotency-key", required=True)
    start_parser.add_argument("--board", required=True)
    start_parser.add_argument("--task", required=True)
    start_parser.add_argument("message")
    for name in ("status", "stop"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
    steer_parser = commands.add_parser("steer")
    steer_parser.add_argument("run_id")
    steer_parser.add_argument("message")
    commands.add_parser("list")
    commands.add_parser("reconcile")
    return result


def main() -> int:
    args = parser().parse_args()
    config = load_json(args.config)
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.state_dir / ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with connect(args.state_dir) as connection:
            if args.command == "start":
                start(args, config, connection)
            elif args.command in {"steer", "stop"}:
                control(args, config, connection, args.command)
            elif args.command == "status":
                show_status(args, config, connection)
            elif args.command == "list":
                list_runs(connection)
            else:
                reconcile(config, args.state_dir, connection)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"hermes-peer-dispatch: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
