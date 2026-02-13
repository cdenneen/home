import asyncio
import contextlib
import json
import os
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


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return int(v)


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    if v is None:
        return default
    return v


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
    def __init__(self, workspace: str, opencode_path: str):
        self.workspace = workspace
        self.opencode_path = opencode_path
        self.port: Optional[int] = None
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._sse_task: Optional[asyncio.Task[None]] = None
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2048)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers = [x for x in self._subscribers if x is not q]

    async def ensure_running(self) -> int:
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
            async with httpx.AsyncClient() as c:
                r = await c.get(f"http://127.0.0.1:{port}/global/health", timeout=2)
                return r.status_code == 200
        except Exception:
            return False

    async def _run_sse(self) -> None:
        url = f"http://127.0.0.1:{self.port}/event"
        headers = {"Accept": "text/event-stream"}

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
    def __init__(self, db: DB, tg: Telegram):
        self._db = db
        self._tg = tg

        self._owner_chat_id: Optional[int] = None
        owner = os.getenv("TELEGRAM_OWNER_CHAT_ID")
        if owner and owner.strip():
            with contextlib.suppress(Exception):
                self._owner_chat_id = int(owner)

        self._allowed_chats: Optional[set[int]] = None
        allow = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS")
        if allow and allow.strip():
            self._allowed_chats = _csv_ints(allow)
        else:
            stored = self._db.get_kv("telegram.allowed_chat_ids")
            if stored and stored.strip():
                self._allowed_chats = _csv_ints(stored)
            elif self._owner_chat_id is not None:
                # Default to the owner (typically a private chat), then allow pairing.
                self._allowed_chats = {self._owner_chat_id}
                self._db.set_kv("telegram.allowed_chat_ids", str(self._owner_chat_id))

        self._workspace_root = _env_str("OPENCODE_WORKSPACE_ROOT", os.path.expanduser("~/src"))
        self._opencode_bin = _env_str("OPENCODE_BIN", "opencode")
        self._max_sessions = _env_int("OPENCODE_MAX_SESSIONS", 5)
        self._idle_timeout = _env_int("OPENCODE_IDLE_TIMEOUT_SEC", 3600)
        self._poll_timeout = _env_int("TELEGRAM_POLL_TIMEOUT_SEC", 30)
        self._default_agent = os.getenv("OPENCODE_DEFAULT_AGENT")
        self._default_model = os.getenv("OPENCODE_DEFAULT_MODEL")

        self._updates_mode = _env_str("TELEGRAM_UPDATES_MODE", "polling")
        self._webhook_listen_host = _env_str("TELEGRAM_WEBHOOK_LISTEN_HOST", "127.0.0.1")
        self._webhook_listen_port = _env_int("TELEGRAM_WEBHOOK_LISTEN_PORT", 18080)
        self._webhook_path = _env_str("TELEGRAM_WEBHOOK_PATH", "/telegram")
        self._webhook_public_url = os.getenv("TELEGRAM_WEBHOOK_PUBLIC_URL")
        self._webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET")
        self._webhook_fallback_sec = _env_int("TELEGRAM_WEBHOOK_FALLBACK_SEC", 300)
        self._last_webhook_update_ts = _now()

        self._instances: dict[str, OpenCodeInstance] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _check_allowed(self, chat_id: int) -> bool:
        if self._allowed_chats is None:
            return True
        return chat_id in self._allowed_chats

    def _allow_chat(self, chat_id: int) -> None:
        if self._allowed_chats is None:
            self._allowed_chats = set()
        self._allowed_chats.add(chat_id)
        self._db.set_kv("telegram.allowed_chat_ids", ",".join(str(x) for x in sorted(self._allowed_chats)))

    async def run_polling(self) -> None:
        # Ensure webhook is disabled to avoid missing updates.
        with contextlib.suppress(Exception):
            await self._tg.delete_webhook(drop_pending_updates=False)

        last = self._db.get_kv("telegram.last_update_id")
        offset: Optional[int] = None
        if last is not None:
            offset = int(last) + 1

        while True:
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

        from_user = msg.get("from") or {}
        from_id = from_user.get("id")

        if not self._check_allowed(chat_id):
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
        async with httpx.AsyncClient() as c:
            r = await c.post(f"http://127.0.0.1:{port}/session", json={"title": title}, timeout=30)
            r.raise_for_status()
            data = r.json()
            return str(data["id"])

    async def _run_prompt(self, ctx: TopicContext, prompt: str) -> None:
        lock = self._session_locks.setdefault(_topic_key(ctx.chat_id, ctx.thread_id), asyncio.Lock())
        async with lock:
            self._db.touch_topic(ctx.chat_id, ctx.thread_id)

            inst = self._instances[ctx.workspace]
            port = await inst.ensure_running()

            msg = await self._tg.send_message(ctx.chat_id, "Thinking...", thread_id=ctx.thread_id)
            tg_msg_id = int(msg["message_id"])

            q = inst.subscribe()
            try:
                await self._prompt_async(port, ctx.session_id, prompt)
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
            body["model"] = self._default_model

        async with httpx.AsyncClient() as c:
            url = f"http://127.0.0.1:{port}/session/{urllib.parse.quote(session_id)}/prompt_async"
            r = await c.post(url, json=body, timeout=30)
            r.raise_for_status()

    async def _reply_permission(self, ctx: TopicContext, permission_id: str, response: str) -> None:
        inst = self._instances[ctx.workspace]
        port = await inst.ensure_running()
        url = f"http://127.0.0.1:{port}/session/{urllib.parse.quote(ctx.session_id)}/permissions/{urllib.parse.quote(permission_id)}"
        async with httpx.AsyncClient() as c:
            r = await c.post(url, json={"response": response}, timeout=30)
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

        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=1800)
            except asyncio.TimeoutError:
                error_text = "Timed out waiting for response"
                break

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
                    await self._tg.edit_message(ctx.chat_id, tg_message_id, text)
                    last_edit = now

            if completed:
                break

        if error_text is not None:
            await self._tg.edit_message(ctx.chat_id, tg_message_id, f"Error: {error_text}")
            return

        if text.strip():
            await self._tg.edit_message(ctx.chat_id, tg_message_id, text)
        else:
            # Fallback: fetch last message.
            try:
                final = await self._fetch_last_assistant_text(port, ctx.session_id)
                await self._tg.edit_message(ctx.chat_id, tg_message_id, final or "(no output)")
            except Exception:
                await self._tg.edit_message(ctx.chat_id, tg_message_id, "(no output)")

    async def _fetch_last_assistant_text(self, port: int, session_id: str) -> str:
        async with httpx.AsyncClient() as c:
            url = f"http://127.0.0.1:{port}/session/{urllib.parse.quote(session_id)}/message"
            r = await c.get(url, params={"limit": 50}, timeout=30)
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


async def amain() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")

    data_dir = os.path.join(os.path.expanduser("~"), ".local", "share", "opencode-telegram-bridge")
    db_path = os.path.join(data_dir, "state.sqlite")
    db = DB(db_path)

    async with httpx.AsyncClient() as client:
        tg = Telegram(token=token, client=client)
        bridge = Bridge(db=db, tg=tg)

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
        with contextlib.suppress(Exception):
            await task


if __name__ == "__main__":
    asyncio.run(amain())
