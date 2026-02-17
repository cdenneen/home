import asyncio
import base64
import contextlib
import gc
import hashlib
import json
import os
import re
import signal
import sqlite3
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from aiohttp import web


def _now() -> int:
    return int(time.time())


def _read_json(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_config() -> dict[str, Any]:
    default_path = os.path.expanduser("~/.config/opencode-telegram-bridge/config.json")
    config_path = os.getenv("OPENCODE_TELEGRAM_CONFIG", default_path)
    base = _read_json(config_path)

    user_default = os.path.expanduser("~/.config/telegram_bridge/config.user.json")
    user_path = os.getenv("OPENCODE_TELEGRAM_CONFIG_USER", user_default)
    override = _read_json(user_path)
    if override:
        base = _deep_merge(base, override)
    return base


def _cfg(cfg: dict[str, Any], path: tuple[str, ...], default: Any) -> Any:
    cur: Any = cfg
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _cfg_int(cfg: dict[str, Any], path: tuple[str, ...], default: int) -> int:
    value = _cfg(cfg, path, None)
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _csv_ints(value: str) -> set[int]:
    out: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        out.add(int(item))
    return out


def _truncate_telegram(text: str, limit: int = 3900) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _topic_key(chat_id: int, thread_id: int) -> str:
    return f"{chat_id}:{thread_id}"


def _find_free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class DB:
    def __init__(self, path: str):
        self._path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS topics (
              chat_id INTEGER NOT NULL,
              thread_id INTEGER NOT NULL,
              topic_title TEXT,
              workspace TEXT,
              opencode_port INTEGER,
              opencode_session_id TEXT,
              updated_at INTEGER NOT NULL,
              PRIMARY KEY(chat_id, thread_id)
            );
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kv (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def get_kv(self, key: str) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_kv(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    _MISSING = object()

    def upsert_topic(
        self,
        chat_id: int,
        thread_id: int,
        *,
        topic_title: Any = _MISSING,
        workspace: Any = _MISSING,
        opencode_port: Any = _MISSING,
        opencode_session_id: Any = _MISSING,
    ) -> None:
        cur = self._conn.execute(
            "SELECT topic_title, workspace, opencode_port, opencode_session_id FROM topics WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        ).fetchone()

        prev = {
            "topic_title": None,
            "workspace": None,
            "opencode_port": None,
            "opencode_session_id": None,
        }
        if cur is not None:
            prev = {
                "topic_title": cur[0],
                "workspace": cur[1],
                "opencode_port": cur[2],
                "opencode_session_id": cur[3],
            }

        self._conn.execute(
            """
            INSERT INTO topics(chat_id, thread_id, topic_title, workspace, opencode_port, opencode_session_id, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, thread_id) DO UPDATE SET
              topic_title = excluded.topic_title,
              workspace = excluded.workspace,
              opencode_port = excluded.opencode_port,
              opencode_session_id = excluded.opencode_session_id,
              updated_at = excluded.updated_at
            """,
            (
                chat_id,
                thread_id,
                prev["topic_title"] if topic_title is self._MISSING else topic_title,
                prev["workspace"] if workspace is self._MISSING else workspace,
                prev["opencode_port"] if opencode_port is self._MISSING else opencode_port,
                prev["opencode_session_id"] if opencode_session_id is self._MISSING else opencode_session_id,
                _now(),
            ),
        )
        self._conn.commit()

    def touch_topic(self, chat_id: int, thread_id: int) -> None:
        self.upsert_topic(chat_id, thread_id)

    def get_topic(self, chat_id: int, thread_id: int) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT topic_title, workspace, opencode_port, opencode_session_id, updated_at FROM topics WHERE chat_id = ? AND thread_id = ?",
            (chat_id, thread_id),
        ).fetchone()
        if row is None:
            return {}
        return {
            "topic_title": row[0],
            "workspace": row[1],
            "opencode_port": row[2],
            "opencode_session_id": row[3],
            "updated_at": row[4],
        }

    def list_topics(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT chat_id, thread_id, workspace, opencode_port, opencode_session_id, updated_at FROM topics ORDER BY updated_at DESC"
        ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "chat_id": int(r[0]),
                    "thread_id": int(r[1]),
                    "workspace": r[2],
                    "opencode_port": r[3],
                    "opencode_session_id": r[4],
                    "updated_at": int(r[5]),
                }
            )
        return out

    def prune_topics(self, *, retention_days: int, max_topics: int) -> None:
        if retention_days > 0:
            cutoff = _now() - (retention_days * 86400)
            self._conn.execute("DELETE FROM topics WHERE updated_at < ?", (cutoff,))

        if max_topics > 0:
            self._conn.execute(
                "DELETE FROM topics WHERE rowid IN (SELECT rowid FROM topics ORDER BY updated_at DESC LIMIT -1 OFFSET ?)",
                (max_topics,),
            )

        self._conn.commit()


class Telegram:
    def __init__(self, token: str, client: httpx.AsyncClient):
        self._token = token
        self._http = client
        self._base = f"https://api.telegram.org/bot{token}"

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        url = f"{self._base}/{method}"
        r = await self._http.post(url, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error: {data}")
        return data["result"]

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        thread_id: Optional[int] = None,
        reply_markup: Optional[dict[str, Any]] = None,
        disable_notification: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": _truncate_telegram(text),
            "disable_notification": disable_notification,
        }
        if thread_id and thread_id != 0:
            payload["message_thread_id"] = thread_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._call("sendMessage", payload)

    async def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": _truncate_telegram(text),
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._call("editMessageText", payload)

    async def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        await self._call("answerCallbackQuery", payload)

    async def get_updates(self, offset: Optional[int], timeout_sec: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout_sec,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        return await self._call("getUpdates", payload)

    async def set_webhook(self, url: str, *, secret_token: Optional[str] = None, drop_pending_updates: bool = True) -> Any:
        payload: dict[str, Any] = {
            "url": url,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": drop_pending_updates,
        }
        if secret_token:
            payload["secret_token"] = secret_token
        return await self._call("setWebhook", payload)

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> Any:
        payload: dict[str, Any] = {
            "drop_pending_updates": drop_pending_updates,
        }
        return await self._call("deleteWebhook", payload)


class OpenCodeInstance:
    def __init__(
        self,
        workspace: str,
        opencode_path: str,
        shared_port: Optional[int] = None,
        base_url: Optional[str] = None,
        auth_header: Optional[str] = None,
    ):
        self.workspace = workspace
        self.opencode_path = opencode_path
        self.port: Optional[int] = None
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._sse_task: Optional[asyncio.Task[None]] = None
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._shared_port = shared_port
        self._base_url = base_url
        self._auth_header = auth_header

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2048)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers = [x for x in self._subscribers if x is not q]

    async def ensure_running(self) -> int:
        if self._shared_port is not None:
            self.port = self._shared_port
            if self._sse_task is None:
                self._sse_task = asyncio.create_task(self._run_sse())
            return self.port
        if self.port is not None:
            if await self._healthy(self.port):
                return self.port

        port = _find_free_port()
        self.port = port

        self.proc = await asyncio.create_subprocess_exec(
            self.opencode_path,
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
            cwd=self.workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for server.
        for _ in range(80):
            if await self._healthy(port):
                break
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError(f"opencode server did not become healthy on port {port}")

        self._sse_task = asyncio.create_task(self._run_sse())
        return port

    async def stop(self) -> None:
        if self._sse_task is not None:
            self._sse_task.cancel()
            with contextlib.suppress(Exception):
                await self._sse_task
        self._sse_task = None

        if self.proc is not None and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.proc.kill()
                with contextlib.suppress(Exception):
                    await self.proc.wait()
        self.proc = None
        self.port = None

    async def _healthy(self, port: int) -> bool:
        try:
            headers = {}
            if self._auth_header:
                headers["Authorization"] = self._auth_header
            base = self._base_url or f"http://127.0.0.1:{port}"
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{base}/global/health", headers=headers, timeout=2)
                return r.status_code == 200
        except Exception:
            return False

    async def _run_sse(self) -> None:
        base = self._base_url or f"http://127.0.0.1:{self.port}"
        url = f"{base}/event"
        headers = {"Accept": "text/event-stream"}
        if self._auth_header:
            headers["Authorization"] = self._auth_header
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("GET", url, headers=headers) as r:
                r.raise_for_status()
                buf: list[str] = []
                async for line in r.aiter_lines():
                    if line == "":
                        event = self._parse_sse_event(buf)
                        buf = []
                        if event is None:
                            continue
                        for q in list(self._subscribers):
                            try:
                                q.put_nowait(event)
                            except asyncio.QueueFull:
                                # Drop if a subscriber is lagging.
                                pass
                        continue
                    buf.append(line)

    def _parse_sse_event(self, lines: list[str]) -> Optional[dict[str, Any]]:
        data_lines: list[str] = []
        for ln in lines:
            if ln.startswith("data:"):
                data_lines.append(ln[len("data:") :].lstrip())
        if not data_lines:
            return None
        raw = "\n".join(data_lines)
        try:
            return json.loads(raw)
        except Exception:
            return None


@dataclass
class TopicContext:
    chat_id: int
    thread_id: int
    workspace: str
    session_id: str


class Bridge:
    def __init__(self, db: DB, tg: Telegram, cfg: dict[str, Any]):
        self._db = db
        self._tg = tg
        self._cfg = cfg

        self._owner_chat_id: Optional[int] = None
        owner = _cfg_int(cfg, ("telegram", "owner_chat_id"), 0)
        if owner:
            self._owner_chat_id = owner

        self._allowed_chats: Optional[set[int]] = None
        allow = _cfg(cfg, ("telegram", "allowed_chat_ids"), None)
        if isinstance(allow, list):
            self._allowed_chats = {int(x) for x in allow}
        elif isinstance(allow, str) and allow.strip():
            self._allowed_chats = _csv_ints(allow)
        elif allow is not None:
            self._allowed_chats = set()
        else:
            stored = self._db.get_kv("telegram.allowed_chat_ids")
            if stored and stored.strip():
                self._allowed_chats = _csv_ints(stored)
            elif self._owner_chat_id is not None:
                # Default to the owner (typically a private chat), then allow pairing.
                self._allowed_chats = {self._owner_chat_id}
                self._db.set_kv("telegram.allowed_chat_ids", str(self._owner_chat_id))

        self._workspace_root = _cfg(cfg, ("opencode", "workspace_root"), os.path.expanduser("~/src"))
        self._opencode_bin = _cfg(cfg, ("opencode", "bin"), "opencode")
        self._max_sessions = _cfg_int(cfg, ("opencode", "max_sessions"), 5)
        self._idle_timeout = _cfg_int(cfg, ("opencode", "idle_timeout_sec"), 3600)
        self._poll_timeout = _cfg_int(cfg, ("telegram", "poll_timeout_sec"), 30)
        self._default_agent = _cfg(cfg, ("opencode", "default_agent"), None)
        self._default_model = _cfg(cfg, ("opencode", "default_model"), None)
        self._default_provider = _cfg(cfg, ("opencode", "default_provider"), "openai")
        saved_model = self._db.get_kv("telegram.default_model")
        if saved_model:
            self._default_model = saved_model

        op_cfg = _cfg(cfg, ("opencode",), {}) or {}
        self._op_use_shared = bool(op_cfg.get("use_shared_server", False))
        self._op_base_url = str(op_cfg.get("server_url", "http://127.0.0.1:4096")).rstrip("/")
        self._op_username = str(op_cfg.get("server_username") or "opencode")
        self._op_password_file = str(op_cfg.get("server_password_file") or "")
        self._op_auth_header = self._load_opencode_auth_header()
        self._op_shared_port: Optional[int] = None
        if self._op_use_shared:
            parsed = urllib.parse.urlparse(self._op_base_url)
            self._op_shared_port = parsed.port or 4096

        web_cfg = _cfg(cfg, ("web",), {}) or {}
        self._web_enabled = bool(web_cfg.get("enable", False))
        self._web_base_url = str(web_cfg.get("base_url", "http://127.0.0.1:4096")).rstrip("/")
        self._web_username = str(web_cfg.get("username") or "opencode")
        self._web_password_file = str(web_cfg.get("password_file") or "")
        self._web_sync_interval = int(web_cfg.get("sync_interval_sec") or 10)
        self._web_forward_user = bool(web_cfg.get("forward_user_prompts", False))
        self._web_forward_steps = bool(web_cfg.get("forward_agent_steps", False))
        self._web_auth_header = self._load_web_auth_header()
        self._web_task: Optional[asyncio.Task[None]] = None
        self._web_monitors: dict[str, asyncio.Task[None]] = {}

        self._db_retention_days = _cfg_int(cfg, ("telegram", "db_retention_days"), 30)
        self._db_max_topics = _cfg_int(cfg, ("telegram", "db_max_topics"), 500)

        webhook = _cfg(cfg, ("telegram", "webhook"), {})
        self._updates_mode = _cfg(cfg, ("telegram", "updates_mode"), "polling")
        self._webhook_listen_host = webhook.get("listen_host", "127.0.0.1")
        self._webhook_listen_port = int(webhook.get("listen_port", 18080))
        self._webhook_path = webhook.get("path", "/telegram")
        self._webhook_public_url = webhook.get("public_url")
        self._webhook_secret = webhook.get("secret")
        self._webhook_fallback_sec = int(webhook.get("fallback_sec", 0))
        self._last_webhook_update_ts = _now()

        self._instances: dict[str, OpenCodeInstance] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._shared_instance: Optional[OpenCodeInstance] = None
        self._last_gc_ts = 0.0

    def _load_web_auth_header(self) -> Optional[str]:
        if not self._web_enabled:
            return None
        if not self._web_password_file:
            print("web sync disabled: missing web password file")
            return None
        try:
            with open(self._web_password_file, "r", encoding="utf-8") as fh:
                password = fh.read().strip()
        except OSError:
            print("web sync disabled: cannot read web password file")
            return None
        if not password:
            print("web sync disabled: web password empty")
            return None
        token = base64.b64encode(f"{self._web_username}:{password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    def _load_opencode_auth_header(self) -> Optional[str]:
        if not self._op_use_shared:
            return None
        if not self._op_password_file:
            print("shared opencode disabled: missing server password file")
            return None
        try:
            with open(self._op_password_file, "r", encoding="utf-8") as fh:
                password = fh.read().strip()
        except OSError:
            print("shared opencode disabled: cannot read server password file")
            return None
        if not password:
            print("shared opencode disabled: server password empty")
            return None
        token = base64.b64encode(f"{self._op_username}:{password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    def _opencode_base_url(self, port: int) -> str:
        if self._op_use_shared:
            return self._op_base_url
        return f"http://127.0.0.1:{port}"

    def _opencode_headers(self) -> dict[str, str]:
        if self._op_auth_header:
            return {"Authorization": self._op_auth_header}
        return {}

    def _maybe_gc(self) -> None:
        now = _now()
        if now - self._last_gc_ts < 60:
            return
        self._last_gc_ts = now
        gc.collect()

    def _check_allowed(self, chat_id: int) -> bool:
        if self._allowed_chats is None:
            return True
        return chat_id in self._allowed_chats

    def _allow_chat(self, chat_id: int) -> None:
        if self._allowed_chats is None:
            self._allowed_chats = set()
        self._allowed_chats.add(chat_id)
        self._db.set_kv("telegram.allowed_chat_ids", ",".join(str(x) for x in sorted(self._allowed_chats)))

    def _prune_db(self) -> None:
        self._db.prune_topics(retention_days=self._db_retention_days, max_topics=self._db_max_topics)

    async def _web_list_sessions(self) -> list[dict[str, Any]]:
        if not self._web_auth_header:
            return []
        headers = {"Authorization": self._web_auth_header}
        timeout = httpx.Timeout(5.0, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout) as c:
            try:
                r = await c.get(f"{self._web_base_url}/session", headers=headers)
            except Exception as e:
                print(f"web sync list sessions failed: {e}")
                return []
            if r.status_code == 401:
                print("web sync disabled: unauthorized")
                return []
            r.raise_for_status()
            data = r.json()

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("items") or data.get("data") or []
        return []

    def _parse_web_title(self, title: str) -> Optional[tuple[int, int]]:
        m = re.search(r"tg:(\d+)/(\d+)", title)
        if not m:
            return None
        return int(m.group(1)), int(m.group(2))

    async def _web_fetch_last_assistant_info(self, session_id: str) -> dict[str, Any]:
        if not self._web_auth_header:
            return {}
        headers = {"Authorization": self._web_auth_header}
        timeout = httpx.Timeout(5.0, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout) as c:
            url = f"{self._web_base_url}/session/{urllib.parse.quote(session_id)}/message"
            try:
                r = await c.get(url, params={"limit": 50}, headers=headers)
            except Exception as e:
                print(f"web sync fetch last assistant failed: {e}")
                return {}
            r.raise_for_status()
            msgs = r.json()

        for item in reversed(msgs):
            info = item.get("info") or {}
            if info.get("role") != "assistant":
                continue
            return info
        return {}

    async def _web_fetch_last_assistant_text(self, session_id: str) -> str:
        if not self._web_auth_header:
            return ""
        headers = {"Authorization": self._web_auth_header}
        timeout = httpx.Timeout(5.0, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout) as c:
            url = f"{self._web_base_url}/session/{urllib.parse.quote(session_id)}/message"
            try:
                r = await c.get(url, params={"limit": 50}, headers=headers)
            except Exception as e:
                print(f"web sync fetch last assistant failed: {e}")
                return ""
            r.raise_for_status()
            msgs = r.json()

        for item in reversed(msgs):
            info = item.get("info") or {}
            if info.get("role") != "assistant":
                continue
            parts = item.get("parts") or []
            out = ""
            for p in parts:
                if p.get("type") == "text":
                    out += str(p.get("text") or "")
            if out.strip():
                return out
        return ""

    async def _web_fetch_last_user_text(self, session_id: str) -> str:
        if not self._web_auth_header:
            return ""
        headers = {"Authorization": self._web_auth_header}
        timeout = httpx.Timeout(5.0, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout) as c:
            url = f"{self._web_base_url}/session/{urllib.parse.quote(session_id)}/message"
            try:
                r = await c.get(url, params={"limit": 50}, headers=headers)
            except Exception as e:
                print(f"web sync fetch last user failed: {e}")
                return ""
            r.raise_for_status()
            msgs = r.json()

        for item in reversed(msgs):
            info = item.get("info") or {}
            if info.get("role") != "user":
                continue
            parts = item.get("parts") or []
            out = ""
            for p in parts:
                if p.get("type") == "text":
                    out += str(p.get("text") or "")
            if out.strip():
                return out
        return ""

    async def _web_fetch_last_assistant_steps(self, session_id: str) -> str:
        if not self._web_auth_header:
            return ""
        headers = {"Authorization": self._web_auth_header}
        timeout = httpx.Timeout(5.0, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout) as c:
            url = f"{self._web_base_url}/session/{urllib.parse.quote(session_id)}/message"
            try:
                r = await c.get(url, params={"limit": 50}, headers=headers)
            except Exception as e:
                print(f"web sync fetch steps failed: {e}")
                return ""
            r.raise_for_status()
            msgs = r.json()

        for item in reversed(msgs):
            info = item.get("info") or {}
            if info.get("role") != "assistant":
                continue
            parts = item.get("parts") or []
            steps: list[str] = []
            for p in parts:
                line = self._format_step_part(p)
                if line:
                    steps.append(line)
            if steps:
                return "\n".join(f"- {s}" for s in steps)
        return ""

    def _web_last_forwarded_key(self, session_id: str) -> str:
        return f"web.last_forwarded.{session_id}"

    def _web_last_assistant_hash_key(self, session_id: str) -> str:
        return f"web.last_assistant_hash.{session_id}"

    def _web_skip_until_key(self, session_id: str) -> str:
        return f"web.skip_until.{session_id}"

    def _web_last_user_key(self, session_id: str) -> str:
        return f"web.last_user_forwarded.{session_id}"

    def _web_last_user_from_tg_key(self, chat_id: int, thread_id: int) -> str:
        return f"web.last_user_from_tg.{chat_id}.{thread_id}"

    def _web_last_steps_key(self, session_id: str) -> str:
        return f"web.last_steps_forwarded.{session_id}"

    def _format_step_part(self, part: dict[str, Any]) -> Optional[str]:
        ptype = str(part.get("type") or "")
        if not ptype or ptype == "text":
            return None

        text = part.get("text") or part.get("message") or part.get("summary") or part.get("title")
        if isinstance(text, str) and text.strip():
            return text.strip()

        if ptype == "step-start":
            title = part.get("title") or part.get("name")
            return f"Step: {title}" if title else "Step started"

        if ptype == "step-finish":
            return "Step finished"

        if ptype in {"tool-start", "tool-finish", "tool-call", "tool-result"}:
            name = part.get("tool") or part.get("name") or part.get("command")
            label = "Tool"
            if ptype == "tool-result":
                label = "Tool result"
            if ptype == "tool-finish":
                label = "Tool finished"
            if ptype == "tool-start":
                label = "Tool start"
            return f"{label}: {name}" if name else label

        if ptype in {"message.start", "message.finish"}:
            return ptype.replace(".", " ")

        return ptype

    def _format_tokens(self, tokens: dict[str, Any]) -> str:
        total = tokens.get("total")
        inp = tokens.get("input")
        out = tokens.get("output")
        reasoning = tokens.get("reasoning")
        cache = tokens.get("cache") or {}
        cache_read = cache.get("read")
        cache_write = cache.get("write")

        bits: list[str] = []
        if total is not None:
            bits.append(f"total={total}")
        if inp is not None:
            bits.append(f"in={inp}")
        if out is not None:
            bits.append(f"out={out}")
        if reasoning:
            bits.append(f"reason={reasoning}")
        if cache_read is not None or cache_write is not None:
            bits.append(f"cache={cache_read or 0}/{cache_write or 0}")
        return "tokens(" + ", ".join(bits) + ")" if bits else ""

    async def _web_session_mappings(self) -> dict[str, tuple[int, int]]:
        mapping: dict[str, tuple[int, int]] = {}
        topics = self._db.list_topics()
        topic_used: set[tuple[int, int]] = set()
        topics_by_workspace: dict[str, tuple[int, int]] = {}

        for t in topics:
            chat_id = t.get("chat_id")
            thread_id = t.get("thread_id")
            workspace = t.get("workspace")
            if chat_id is None or thread_id is None or not workspace:
                continue
            topics_by_workspace[str(workspace)] = (int(chat_id), int(thread_id))

        sessions = await self._web_list_sessions()
        session_rows: list[dict[str, Any]] = []
        for s in sessions:
            if not isinstance(s, dict):
                continue
            session_id = s.get("id") or s.get("sessionID")
            if not session_id:
                continue
            updated = (s.get("time") or {}).get("updated") or 0
            session_rows.append({
                "id": str(session_id),
                "title": str(s.get("title") or ""),
                "directory": str(s.get("directory") or ""),
                "updated": int(updated) if isinstance(updated, (int, float)) else 0,
            })

        # 1) Explicit tg:<chat>/<thread> titles win.
        for s in session_rows:
            parsed = self._parse_web_title(s["title"])
            if not parsed:
                continue
            chat_id, thread_id = parsed
            key = (int(chat_id), int(thread_id))
            if not self._check_allowed(chat_id):
                continue
            if key in topic_used:
                continue
            mapping[s["id"]] = key
            topic_used.add(key)
            self._db.upsert_topic(int(chat_id), int(thread_id), opencode_session_id=s["id"])

        # 2) Exact workspace directory match, prefer most recent updated session.
        for workspace, key in topics_by_workspace.items():
            if key in topic_used:
                continue
            matches = [s for s in session_rows if s["directory"] == workspace]
            if not matches:
                continue
            matches.sort(key=lambda x: x["updated"], reverse=True)
            chosen = matches[0]
            mapping[chosen["id"]] = key
            topic_used.add(key)
            self._db.upsert_topic(key[0], key[1], opencode_session_id=chosen["id"])

        # 3) Fallback: match workspace basename in title, prefer latest updated.
        for workspace, key in topics_by_workspace.items():
            if key in topic_used:
                continue
            base = os.path.basename(workspace)
            if not base:
                continue
            matches = [s for s in session_rows if re.search(rf"\b{re.escape(base)}\b", s["title"])]
            if not matches:
                continue
            matches.sort(key=lambda x: x["updated"], reverse=True)
            chosen = matches[0]
            mapping[chosen["id"]] = key
            topic_used.add(key)
            self._db.upsert_topic(key[0], key[1], opencode_session_id=chosen["id"])

        return mapping

    async def _web_monitor_session(self, session_id: str, chat_id: int, thread_id: int) -> None:
        while True:
            try:
                if not self._check_allowed(chat_id):
                    await asyncio.sleep(self._web_sync_interval)
                    continue

                skip_until = self._db.get_kv(self._web_skip_until_key(session_id))
                if skip_until:
                    try:
                        if _now() < float(skip_until):
                            await asyncio.sleep(self._web_sync_interval)
                            continue
                    except Exception:
                        pass

                info = await self._web_fetch_last_assistant_info(session_id)
                msg_id = info.get("id")
                completed = (info.get("time") or {}).get("completed")

                key = self._web_last_forwarded_key(session_id)
                last = self._db.get_kv(key)
                if msg_id is not None and last is not None and last == str(msg_id):
                    await asyncio.sleep(self._web_sync_interval)
                    continue

                text = await self._web_fetch_last_assistant_text(session_id)
                if not text.strip():
                    await asyncio.sleep(self._web_sync_interval)
                    continue

                if completed is None:
                    completed = True

                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                hkey = self._web_last_assistant_hash_key(session_id)
                if self._db.get_kv(hkey) == digest:
                    await asyncio.sleep(self._web_sync_interval)
                    continue

                await self._tg.send_message(chat_id, _truncate_telegram(text), thread_id=thread_id)
                print(
                    f"web sync sent session={session_id} chat_id={chat_id} thread_id={thread_id}")
                self._db.set_kv(key, str(msg_id))
                self._db.set_kv(hkey, digest)
                print(
                    f"web sync forwarded session={session_id} chat_id={chat_id} thread_id={thread_id}")

                if self._web_forward_user:
                    user_text = await self._web_fetch_last_user_text(session_id)
                    if user_text.strip():
                        ukey = self._web_last_user_key(session_id)
                        tkey = self._web_last_user_from_tg_key(chat_id, thread_id)
                        digest = hashlib.sha256(user_text.encode("utf-8")).hexdigest()
                        if self._db.get_kv(tkey) == digest:
                            await asyncio.sleep(self._web_sync_interval)
                            continue
                        if self._db.get_kv(ukey) != digest:
                            await self._tg.send_message(
                                chat_id,
                                _truncate_telegram(f"User: {user_text}"),
                                thread_id=thread_id,
                            )
                            print(
                                f"web sync sent user session={session_id} chat_id={chat_id} thread_id={thread_id}")
                            self._db.set_kv(ukey, digest)

                if self._web_forward_steps:
                    steps_text = await self._web_fetch_last_assistant_steps(session_id)
                    if steps_text.strip():
                        skey = self._web_last_steps_key(session_id)
                        digest = hashlib.sha256(steps_text.encode("utf-8")).hexdigest()
                        if self._db.get_kv(skey) != digest:
                            await self._tg.send_message(
                                chat_id,
                                _truncate_telegram(f"Steps:\n{steps_text}"),
                                thread_id=thread_id,
                            )
                            print(
                                f"web sync sent steps session={session_id} chat_id={chat_id} thread_id={thread_id}")
                            self._db.set_kv(skey, digest)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"web sync monitor failed: {e}")

            self._maybe_gc()
            await asyncio.sleep(self._web_sync_interval)

    async def _web_sync_loop(self) -> None:
        if not self._web_enabled or not self._web_auth_header:
            return
        while True:
            try:
                desired = await self._web_session_mappings()
                desired_ids = set(desired.keys())
                existing_ids = set(self._web_monitors.keys())

                for sess in existing_ids - desired_ids:
                    task = self._web_monitors.pop(sess, None)
                    if task is not None:
                        task.cancel()

                for sess in desired_ids - existing_ids:
                    chat_id, thread_id = desired[sess]
                    print(
                        f"web sync monitor start session={sess} chat_id={chat_id} thread_id={thread_id}")
                    self._web_monitors[sess] = asyncio.create_task(
                        self._web_monitor_session(sess, chat_id, thread_id)
                    )
            except Exception as e:
                print(f"web sync loop error: {e}")
            await asyncio.sleep(self._web_sync_interval)

    async def run_polling(self) -> None:
        # Ensure webhook is disabled to avoid missing updates.
        with contextlib.suppress(Exception):
            await self._tg.delete_webhook(drop_pending_updates=False)

        if self._web_enabled and self._web_task is None:
            self._web_task = asyncio.create_task(self._web_sync_loop())

        last = self._db.get_kv("telegram.last_update_id")
        offset: Optional[int] = None
        if last is not None:
            offset = int(last) + 1

        while True:
            self._prune_db()
            await self._cleanup_idle()

            updates = await self._tg.get_updates(offset=offset, timeout_sec=self._poll_timeout)
            for u in updates:
                uid = int(u["update_id"])
                offset = uid + 1
                self._db.set_kv("telegram.last_update_id", str(uid))
                try:
                    await self._handle_update(u)
                except Exception as e:
                    # Best-effort; keep polling.
                    print(f"update handling failed: {e}")

    async def run_webhook(self) -> None:
        if self._web_enabled and self._web_task is None:
            self._web_task = asyncio.create_task(self._web_sync_loop())
        if self._webhook_public_url:
            url = self._webhook_public_url.rstrip("/") + self._webhook_path
            with contextlib.suppress(Exception):
                await self._tg.set_webhook(url, secret_token=self._webhook_secret, drop_pending_updates=True)

        app = web.Application()

        async def health(_: web.Request) -> web.Response:
            return web.json_response({"ok": True})

        async def handle(req: web.Request) -> web.Response:
            if self._webhook_secret:
                hdr = req.headers.get("X-Telegram-Bot-Api-Secret-Token")
                if hdr != self._webhook_secret:
                    return web.Response(status=401, text="unauthorized")

            try:
                update = await req.json()
            except Exception:
                return web.Response(status=400, text="bad json")

            async def runner() -> None:
                try:
                    self._last_webhook_update_ts = _now()
                    await self._handle_update(update)
                except Exception as e:
                    print(f"webhook update handling failed: {e}")

            asyncio.create_task(runner())
            return web.json_response({"ok": True})

        app.router.add_get("/health", health)
        app.router.add_post(self._webhook_path, handle)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self._webhook_listen_host, self._webhook_listen_port)
        await site.start()

        # Keep running.
        while True:
            self._prune_db()
            await self._cleanup_idle()
            if self._webhook_fallback_sec > 0:
                idle = _now() - self._last_webhook_update_ts
                if idle > self._webhook_fallback_sec:
                    print("webhook idle too long; falling back to polling")
                    await self.run_polling()
                    return
            await asyncio.sleep(10)

    async def _handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._handle_callback(update["callback_query"])
            return
        msg = update.get("message")
        if not msg:
            return

        chat = msg.get("chat") or {}
        if chat.get("id") is None:
            return
        chat_id = int(chat["id"])

        thread_id = int(msg.get("message_thread_id") or 0)
        text = msg.get("text")
        if text is None:
            return

        print(f"update: chat_id={chat_id} thread_id={thread_id} text={text[:60]}")

        from_user = msg.get("from") or {}
        from_id = from_user.get("id")

        if not self._check_allowed(chat_id):
            print(f"update: chat not allowed {chat_id}")
            # Allow pairing a new chat even if it's not allowed yet.
            if text.strip() in ("/allowhere", "/pair") and self._owner_chat_id is not None and from_id is not None:
                with contextlib.suppress(Exception):
                    if int(from_id) == int(self._owner_chat_id):
                        await self._cmd_allowhere(chat_id, thread_id, from_id)
            return

        if "forum_topic_created" in msg:
            title = (msg.get("forum_topic_created") or {}).get("name")
            if title:
                self._db.upsert_topic(chat_id, thread_id, topic_title=str(title))
            return

        if text.startswith("/map "):
            await self._cmd_map(chat_id, thread_id, text[len("/map ") :].strip())
            return
        if text.strip() in ("/id", "/ids"):
            await self._cmd_ids(chat_id, thread_id)
            return
        if text.strip() in ("/allowhere", "/pair"):
            await self._cmd_allowhere(chat_id, thread_id, from_id)
            return
        if text.strip() == "/where":
            await self._cmd_where(chat_id, thread_id)
            return
        if text.strip() == "/info":
            await self._cmd_info(chat_id, thread_id)
            return
        if text.strip() == "/models":
            await self._cmd_forward(chat_id, thread_id, "/models")
            return
        if text.strip() == "/model":
            await self._cmd_model(chat_id, thread_id, None)
            return
        if text.startswith("/model "):
            await self._cmd_model(chat_id, thread_id, text[len("/model ") :].strip())
            return
        if text.strip() == "/reset":
            await self._cmd_reset(chat_id, thread_id)
            return

        ctx = await self._resolve_topic(chat_id, thread_id)
        if ctx is None:
            await self._tg.send_message(
                chat_id,
                "This topic is not mapped to a workspace yet.\n\n"
                "- Run: /map projectA (maps to $OPENCODE_WORKSPACE_ROOT/projectA), or\n"
                "- Run: /map /absolute/path/to/workspace",
                thread_id=thread_id,
            )
            return
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self._db.set_kv(self._web_last_user_from_tg_key(chat_id, thread_id), digest)
        await self._run_prompt(ctx, text)

    async def _handle_callback(self, cq: dict[str, Any]) -> None:
        cq_id = str(cq.get("id"))
        data = str(cq.get("data") or "")
        if not data.startswith("perm:"):
            await self._tg.answer_callback_query(cq_id)
            return

        # perm:<chat_id>:<thread_id>:<permission_id>:<response>
        parts = data.split(":", 4)
        if len(parts) != 5:
            await self._tg.answer_callback_query(cq_id, "Malformed action")
            return

        chat_id = int(parts[1])
        thread_id = int(parts[2])
        permission_id = parts[3]
        response = parts[4]

        ctx = await self._resolve_topic(chat_id, thread_id)
        if ctx is None:
            await self._tg.answer_callback_query(cq_id, "Topic not mapped")
            return

        try:
            await self._reply_permission(ctx, permission_id, response)
            await self._tg.answer_callback_query(cq_id, "Sent")
        except Exception as e:
            await self._tg.answer_callback_query(cq_id, f"Failed: {e}")

    async def _cmd_map(self, chat_id: int, thread_id: int, path: str) -> None:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            # Treat as workspace name relative to OPENCODE_WORKSPACE_ROOT.
            root = os.path.abspath(os.path.expanduser(self._workspace_root))
            candidate = os.path.abspath(os.path.join(root, path))
            if os.path.commonpath([root, candidate]) != root:
                await self._tg.send_message(chat_id, "Invalid workspace path", thread_id=thread_id)
                return
            path = candidate

        if not os.path.isdir(path):
            await self._tg.send_message(chat_id, f"Not a directory: {path}", thread_id=thread_id)
            return
        self._db.upsert_topic(chat_id, thread_id, workspace=path, opencode_port=None, opencode_session_id=None)
        await self._tg.send_message(chat_id, f"Mapped this topic to: {path}", thread_id=thread_id)

    async def _cmd_ids(self, chat_id: int, thread_id: int) -> None:
        await self._tg.send_message(
            chat_id,
            "\n".join(
                [
                    f"chat_id: {chat_id}",
                    f"topic_id(message_thread_id): {thread_id}",
                ]
            ),
            thread_id=thread_id,
        )

    async def _cmd_allowhere(self, chat_id: int, thread_id: int, from_id: Any) -> None:
        if self._owner_chat_id is not None and from_id is not None:
            with contextlib.suppress(Exception):
                if int(from_id) != int(self._owner_chat_id):
                    await self._tg.send_message(chat_id, "Only the owner can pair chats.", thread_id=thread_id)
                    return
        self._allow_chat(chat_id)
        await self._tg.send_message(chat_id, f"Paired. Allowed chat_id: {chat_id}", thread_id=thread_id)

    async def _cmd_where(self, chat_id: int, thread_id: int) -> None:
        t = self._db.get_topic(chat_id, thread_id)
        ws = t.get("workspace")
        sid = t.get("opencode_session_id")
        port = t.get("opencode_port")
        await self._tg.send_message(
            chat_id,
            "\n".join(
                [
                    f"chat_id: {chat_id}",
                    f"topic_id(message_thread_id): {thread_id}",
                    f"workspace: {ws or '(unmapped)'}",
                    f"opencode_port: {port or '(none)'}",
                    f"session_id: {sid or '(none)'}",
                ]
            ),
            thread_id=thread_id,
        )

    async def _cmd_info(self, chat_id: int, thread_id: int) -> None:
        t = self._db.get_topic(chat_id, thread_id)
        ws = t.get("workspace")
        sid = t.get("opencode_session_id")
        port = t.get("opencode_port")

        provider = "(unknown)"
        last_model = "(unknown)"
        default_model = self._default_model or "(unset)"
        if ws and sid:
            inst = await self._ensure_instance(ws, chat_id, thread_id)
            port = await inst.ensure_running()
            info = await self._fetch_last_assistant_info(port, sid)
            provider = info.get("providerID") or provider
            last_model = info.get("modelID") or last_model

        await self._tg.send_message(
            chat_id,
            "\n".join(
                [
                    f"workspace: {ws or '(unmapped)'}",
                    f"session_id: {sid or '(none)'}",
                    f"opencode_port: {port or '(none)'}",
                    f"provider: {provider}",
                    f"default_model: {default_model}",
                    f"last_model: {last_model}",
                    f"updates_mode: {self._updates_mode}",
                    f"webhook_url: {self._webhook_public_url or '(none)'}",
                ]
            ),
            thread_id=thread_id,
        )

    async def _cmd_forward(self, chat_id: int, thread_id: int, prompt: str) -> None:
        ctx = await self._resolve_topic(chat_id, thread_id)
        if ctx is None:
            await self._tg.send_message(
                chat_id,
                "This topic is not mapped yet. Use /map first.",
                thread_id=thread_id,
            )
            return
        await self._run_prompt(ctx, prompt)

    async def _cmd_model(self, chat_id: int, thread_id: int, model: Optional[str]) -> None:
        ctx = await self._resolve_topic(chat_id, thread_id)
        if ctx is None:
            await self._tg.send_message(
                chat_id,
                "This topic is not mapped yet. Use /map first.",
                thread_id=thread_id,
            )
            return

        if model:
            model = model.strip()
            if model:
                if "/" not in model:
                    provider = self._default_provider
                    with contextlib.suppress(Exception):
                        inst = self._instances.get(ctx.workspace)
                        if inst is not None:
                            port = await inst.ensure_running()
                            info = await self._fetch_last_assistant_info(port, ctx.session_id)
                            provider = info.get("providerID") or provider
                    model = f"{provider}/{model}"
                self._default_model = model
                self._db.set_kv("telegram.default_model", model)
                await self._tg.send_message(
                    chat_id,
                    f"Default model set to: {model}",
                    thread_id=thread_id,
                )
                return
            return

        inst = self._instances[ctx.workspace]
        port = await inst.ensure_running()
        info = await self._fetch_last_assistant_info(port, ctx.session_id)
        provider = info.get("providerID") or "(unknown)"
        last_model = info.get("modelID") or "(unknown)"
        default_model = self._default_model or "(unset)"

        await self._tg.send_message(
            chat_id,
            "\n".join(
                [
                    f"provider: {provider}",
                    f"default_model: {default_model}",
                    f"last_model: {last_model}",
                    "auth: not exposed by opencode API",
                ]
            ),
            thread_id=thread_id,
        )

    async def _cmd_reset(self, chat_id: int, thread_id: int) -> None:
        self._db.upsert_topic(chat_id, thread_id, workspace=None, opencode_port=None, opencode_session_id=None)
        await self._tg.send_message(chat_id, "Reset mapping and session for this topic.", thread_id=thread_id)

    async def _resolve_topic(self, chat_id: int, thread_id: int) -> Optional[TopicContext]:
        t = self._db.get_topic(chat_id, thread_id)
        workspace = t.get("workspace")

        if not workspace:
            title = t.get("topic_title")
            if title:
                candidate = os.path.join(self._workspace_root, title)
                if os.path.isdir(candidate):
                    workspace = candidate
                    self._db.upsert_topic(chat_id, thread_id, workspace=workspace)

        if not workspace or not os.path.isdir(workspace):
            return None

        inst = await self._ensure_instance(workspace, chat_id, thread_id)
        port = await inst.ensure_running()
        self._db.upsert_topic(chat_id, thread_id, opencode_port=port)

        session_id = t.get("opencode_session_id")
        if not session_id:
            session_id = await self._create_session(port, title=f"tg:{chat_id}:{thread_id}")
            self._db.upsert_topic(chat_id, thread_id, opencode_session_id=session_id)

        return TopicContext(chat_id=chat_id, thread_id=thread_id, workspace=workspace, session_id=session_id)

    async def _ensure_instance(self, workspace: str, chat_id: int, thread_id: int) -> OpenCodeInstance:
        if self._op_use_shared:
            if self._shared_instance is None:
                self._shared_instance = OpenCodeInstance(
                    workspace=workspace,
                    opencode_path=self._opencode_bin,
                    shared_port=self._op_shared_port,
                    base_url=self._op_base_url,
                    auth_header=self._op_auth_header,
                )
            self._instances[workspace] = self._shared_instance
            return self._shared_instance

        inst = self._instances.get(workspace)
        if inst is not None:
            return inst

        # Enforce max instances.
        if len(self._instances) >= self._max_sessions:
            await self._evict_one()

        inst = OpenCodeInstance(workspace=workspace, opencode_path=self._opencode_bin)
        self._instances[workspace] = inst
        return inst

    async def _evict_one(self) -> None:
        if self._op_use_shared:
            return
        topics = self._db.list_topics()
        # Oldest by updated_at.
        topics = list(reversed(topics))
        for t in topics:
            ws = t.get("workspace")
            if not ws:
                continue
            inst = self._instances.get(ws)
            if inst is None:
                continue
            await inst.stop()
            self._instances.pop(ws, None)
            return

    async def _cleanup_idle(self) -> None:
        if self._op_use_shared:
            return
        cutoff = _now() - self._idle_timeout
        if cutoff <= 0:
            return
        topics = self._db.list_topics()
        for t in topics:
            ws = t.get("workspace")
            if not ws:
                continue
            if int(t.get("updated_at") or 0) >= cutoff:
                continue
            inst = self._instances.get(ws)
            if inst is None:
                continue
            await inst.stop()
            self._instances.pop(ws, None)

    async def _create_session(self, port: int, title: str) -> str:
        headers = self._opencode_headers()
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{self._opencode_base_url(port)}/session",
                json={"title": title},
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            return str(data["id"])

    async def _update_session_title(self, port: int, session_id: str, title: str) -> None:
        headers = self._opencode_headers()
        async with httpx.AsyncClient() as c:
            url = f"{self._opencode_base_url(port)}/session/{urllib.parse.quote(session_id)}"
            r = await c.patch(url, json={"title": title}, headers=headers, timeout=30)
            r.raise_for_status()

    def _session_title(self, workspace: str, thread_id: int, topic_title: Optional[str]) -> str:
        label = topic_title or os.path.basename(workspace) or "workspace"
        return f"tg:{thread_id} {label}"

    async def warm_sessions(self) -> None:
        topics = self._db.list_topics()
        warmed = 0
        for t in topics:
            if warmed >= self._max_sessions:
                break
            workspace = t.get("workspace")
            if not workspace or not os.path.isdir(workspace):
                continue

            chat_val = t.get("chat_id")
            thread_val = t.get("thread_id")
            if chat_val is None or thread_val is None:
                continue
            chat_id = int(chat_val)
            thread_id = int(thread_val)
            topic_title = t.get("topic_title")

            inst = await self._ensure_instance(workspace, chat_id, thread_id)
            port = await inst.ensure_running()
            self._db.upsert_topic(chat_id, thread_id, opencode_port=port)

            session_id = t.get("opencode_session_id")
            if not session_id:
                session_id = await self._create_session(port, title=self._session_title(workspace, thread_id, topic_title))
                self._db.upsert_topic(chat_id, thread_id, opencode_session_id=session_id)

            with contextlib.suppress(Exception):
                await self._update_session_title(
                    port,
                    str(session_id),
                    self._session_title(workspace, thread_id, topic_title),
                )

            warmed += 1

    async def _run_prompt(self, ctx: TopicContext, prompt: str) -> None:
        lock = self._session_locks.setdefault(_topic_key(ctx.chat_id, ctx.thread_id), asyncio.Lock())
        async with lock:
            self._db.touch_topic(ctx.chat_id, ctx.thread_id)

            inst = self._instances[ctx.workspace]
            port = await inst.ensure_running()

            print(
                "thinking: chat_id={} thread_id={} workspace={}".format(
                    ctx.chat_id,
                    ctx.thread_id,
                    ctx.workspace,
                )
            )
            msg = await self._tg.send_message(ctx.chat_id, "Thinking...", thread_id=ctx.thread_id)
            tg_msg_id = int(msg["message_id"])

            # Avoid web sync echoing this prompt's response back to Telegram.
            self._db.set_kv(self._web_skip_until_key(ctx.session_id), str(_now() + 120))

            q = inst.subscribe()
            try:
                try:
                    await self._prompt_async(port, ctx.session_id, prompt)
                except Exception as e:
                    print(f"prompt_async failed: {e}")
                    raise
                await self._stream_response(ctx, port, q, tg_msg_id)
            finally:
                inst.unsubscribe(q)

    async def _prompt_async(self, port: int, session_id: str, prompt: str) -> None:
        body: dict[str, Any] = {
            "parts": [{"type": "text", "text": prompt}],
        }
        if self._default_agent:
            body["agent"] = self._default_agent
        if self._default_model:
            model = self._default_model
            provider = self._default_provider
            if "/" in model:
                provider, model = model.split("/", 1)
            body["model"] = {
                "providerID": provider,
                "modelID": model,
            }

        headers = self._opencode_headers()
        async with httpx.AsyncClient() as c:
            url = f"{self._opencode_base_url(port)}/session/{urllib.parse.quote(session_id)}/prompt_async"
            r = await c.post(url, json=body, headers=headers, timeout=30)
            if r.status_code >= 400:
                print(f"prompt_async failed: status={r.status_code} body={r.text}")
                print(f"prompt_async payload: {body}")
            r.raise_for_status()

    async def _reply_permission(self, ctx: TopicContext, permission_id: str, response: str) -> None:
        inst = self._instances[ctx.workspace]
        port = await inst.ensure_running()
        url = f"{self._opencode_base_url(port)}/session/{urllib.parse.quote(ctx.session_id)}/permissions/{urllib.parse.quote(permission_id)}"
        headers = self._opencode_headers()
        async with httpx.AsyncClient() as c:
            r = await c.post(url, json={"response": response}, headers=headers, timeout=30)
            r.raise_for_status()

    async def _stream_response(
        self,
        ctx: TopicContext,
        port: int,
        q: asyncio.Queue[dict[str, Any]],
        tg_message_id: int,
    ) -> None:
        assistant_message_id: Optional[str] = None
        text = ""
        last_edit = 0.0
        completed = False
        error_text: Optional[str] = None
        start_time = time.time()

        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=10)
            except asyncio.TimeoutError:
                final = await self._poll_for_response(port, ctx.session_id, attempts=8, delay_sec=1)
                if final.strip():
                    try:
                        await self._tg.edit_message(ctx.chat_id, tg_message_id, final)
                    except Exception as e:
                        print(f"edit timeout fallback failed: {e}")
                    return

                if time.time() - start_time >= 1800:
                    error_text = "Timed out waiting for response"
                    break
                continue

            payload = ev.get("payload") or {}
            typ = payload.get("type")
            props = payload.get("properties") or {}

            if typ == "permission.updated":
                perm = props
                if str(perm.get("sessionID")) != ctx.session_id:
                    continue
                pid = str(perm.get("id"))
                title = str(perm.get("title") or "Permission requested")
                keyboard = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "Allow",
                                "callback_data": f"perm:{ctx.chat_id}:{ctx.thread_id}:{pid}:allow",
                            },
                            {
                                "text": "Deny",
                                "callback_data": f"perm:{ctx.chat_id}:{ctx.thread_id}:{pid}:deny",
                            },
                        ]
                    ]
                }
                await self._tg.send_message(
                    ctx.chat_id,
                    f"{title}\n\npermission_id: {pid}",
                    thread_id=ctx.thread_id,
                    reply_markup=keyboard,
                    disable_notification=False,
                )
                continue

            if typ == "session.error":
                if str(props.get("sessionID")) != ctx.session_id and props.get("sessionID") is not None:
                    continue
                err = props.get("error") or {}
                error_text = str((err.get("data") or {}).get("message") or err)
                break

            if typ == "message.updated":
                info = (props.get("info") or {})
                if str(info.get("sessionID")) != ctx.session_id:
                    continue
                if info.get("role") == "assistant":
                    assistant_message_id = str(info.get("id"))
                    if (info.get("time") or {}).get("completed") is not None:
                        completed = True

            if typ == "message.part.updated":
                part = props.get("part") or {}
                if str(part.get("sessionID")) != ctx.session_id:
                    continue
                if assistant_message_id is not None and str(part.get("messageID")) != assistant_message_id:
                    continue
                if part.get("type") != "text":
                    continue

                delta = props.get("delta")
                if isinstance(delta, str) and delta:
                    text += delta
                else:
                    full = str(part.get("text") or "")
                    if len(full) >= len(text):
                        text = full

                now = time.time()
                if now - last_edit >= 1.2 and text.strip():
                    try:
                        await self._tg.edit_message(ctx.chat_id, tg_message_id, text)
                    except Exception as e:
                        print(f"edit message failed: {e}")
                        break
                    last_edit = now

            if completed:
                break

            if time.time() - start_time >= 15 and not text.strip():
                final = await self._poll_for_response(port, ctx.session_id, attempts=5, delay_sec=1)
                if final.strip():
                    try:
                        await self._tg.edit_message(ctx.chat_id, tg_message_id, final)
                    except Exception as e:
                        print(f"stream watchdog edit failed: {e}")
                    return

        if error_text is not None:
            try:
                await self._tg.edit_message(ctx.chat_id, tg_message_id, f"Error: {error_text}")
            except Exception as e:
                print(f"edit error message failed: {e}")
            return

        if text.strip():
            try:
                await self._tg.edit_message(ctx.chat_id, tg_message_id, text)
            except Exception as e:
                print(f"edit final message failed: {e}")
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            self._db.set_kv(self._web_last_assistant_hash_key(ctx.session_id), digest)
            if assistant_message_id:
                self._db.set_kv(self._web_last_forwarded_key(ctx.session_id), str(assistant_message_id))
            self._db.set_kv(self._web_skip_until_key(ctx.session_id), str(_now() + 30))
        else:
            # Fallback: fetch last message.
            try:
                final = await self._fetch_last_assistant_text(port, ctx.session_id)
                try:
                    await self._tg.edit_message(ctx.chat_id, tg_message_id, final or "(no output)")
                except Exception as e:
                    print(f"edit fallback message failed: {e}")
                if final.strip():
                    digest = hashlib.sha256(final.encode("utf-8")).hexdigest()
                    self._db.set_kv(self._web_last_assistant_hash_key(ctx.session_id), digest)
                    if assistant_message_id:
                        self._db.set_kv(self._web_last_forwarded_key(ctx.session_id), str(assistant_message_id))
            except Exception:
                try:
                    await self._tg.edit_message(ctx.chat_id, tg_message_id, "(no output)")
                except Exception as e:
                    print(f"edit empty message failed: {e}")
        self._maybe_gc()

    async def _fetch_last_assistant_info(self, port: int, session_id: str) -> dict[str, Any]:
        timeout = httpx.Timeout(5.0, connect=2.0)
        headers = self._opencode_headers()
        async with httpx.AsyncClient(timeout=timeout) as c:
            url = f"{self._opencode_base_url(port)}/session/{urllib.parse.quote(session_id)}/message"
            try:
                r = await c.get(url, params={"limit": 50}, headers=headers)
            except Exception as e:
                print(f"fetch last assistant failed: {e}")
                return {}
            r.raise_for_status()
            msgs = r.json()

        # msgs is list of {info, parts}
        for item in reversed(msgs):
            info = item.get("info") or {}
            if info.get("role") != "assistant":
                continue
            return info
        return {}

    async def _fetch_last_assistant_text(self, port: int, session_id: str) -> str:
        timeout = httpx.Timeout(5.0, connect=2.0)
        headers = self._opencode_headers()
        async with httpx.AsyncClient(timeout=timeout) as c:
            url = f"{self._opencode_base_url(port)}/session/{urllib.parse.quote(session_id)}/message"
            try:
                r = await c.get(url, params={"limit": 50}, headers=headers)
            except Exception as e:
                print(f"fetch last assistant failed: {e}")
                return ""
            r.raise_for_status()
            msgs = r.json()

        # msgs is list of {info, parts}
        for item in reversed(msgs):
            info = item.get("info") or {}
            if info.get("role") != "assistant":
                continue
            parts = item.get("parts") or []
            out = ""
            for p in parts:
                if p.get("type") == "text":
                    out += str(p.get("text") or "")
            if out.strip():
                return out
        return ""

    async def _poll_for_response(self, port: int, session_id: str, attempts: int, delay_sec: int) -> str:
        for _ in range(attempts):
            final = await self._fetch_last_assistant_text(port, session_id)
            if final.strip():
                return final
            await asyncio.sleep(delay_sec)
        return ""


async def amain() -> None:
    cfg = _load_config()
    token = _cfg(cfg, ("telegram", "bot_token"), "")
    if not token:
        raise SystemExit("telegram.bot_token is required")

    data_dir = os.path.join(os.path.expanduser("~"), ".local", "share", "opencode-telegram-bridge")
    db_path = os.path.join(data_dir, "state.sqlite")
    db = DB(db_path)
    db.prune_topics(
        retention_days=_cfg_int(cfg, ("telegram", "db_retention_days"), 30),
        max_topics=_cfg_int(cfg, ("telegram", "db_max_topics"), 500),
    )

    async with httpx.AsyncClient() as client:
        tg = Telegram(token=token, client=client)
        bridge = Bridge(db=db, tg=tg, cfg=cfg)
        await bridge.warm_sessions()

        stop = asyncio.Event()

        def _signal(*_: object) -> None:
            stop.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _signal)

        if bridge._updates_mode == "webhook":
            task = asyncio.create_task(bridge.run_webhook())
        else:
            task = asyncio.create_task(bridge.run_polling())
        await stop.wait()
        task.cancel()
        if bridge._web_task is not None:
            bridge._web_task.cancel()
        for task in list(bridge._web_monitors.values()):
            task.cancel()
        with contextlib.suppress(Exception):
            await task
        if bridge._web_task is not None:
            with contextlib.suppress(Exception):
                await bridge._web_task
        for task in list(bridge._web_monitors.values()):
            with contextlib.suppress(Exception):
                await task


if __name__ == "__main__":
    asyncio.run(amain())
