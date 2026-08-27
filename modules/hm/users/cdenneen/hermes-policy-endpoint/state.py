"""
Local, Eros-independent state for the host-local policy endpoint.

Everything here must remain readable/writable with Eros, LiteLLM, and
Postgres completely unavailable (execution-contract.md 10.2, "host-local
independence"). This is a single SQLite file per host/profile; there is
no dependency on any remote database.
"""

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS admission_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    priority TEXT NOT NULL,
    continuity_class TEXT NOT NULL,
    requested_route TEXT NOT NULL,
    decision TEXT NOT NULL,          -- admit | queue | deny
    reason TEXT NOT NULL,
    economic_state TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spend_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    actor TEXT NOT NULL,
    requested_route TEXT NOT NULL,
    actual_provider TEXT,
    actual_model TEXT,
    cost_usd REAL,                   -- NULL means unknown, NEVER coerced to 0
    input_tokens INTEGER,
    output_tokens INTEGER,
    source TEXT NOT NULL,            -- 'eros' | 'continuity'
    request_digest TEXT
);

CREATE TABLE IF NOT EXISTS continuity_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    ended_at REAL,
    mode TEXT NOT NULL,              -- continuity_auto | break_glass
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    reconciled INTEGER NOT NULL DEFAULT 0
);

-- Mutating-request idempotency across a continuity -> Eros-recovery
-- transition (execution-contract.md 10.5). Keyed by a caller-supplied
-- idempotency key if present, else a digest of (actor, route, body).
CREATE TABLE IF NOT EXISTS idempotency (
    digest TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    first_seen REAL NOT NULL,
    completed_at REAL,
    response_json TEXT
);

CREATE TABLE IF NOT EXISTS state_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


class LocalState:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- admission -----------------------------------------------------

    def record_admission(
        self, *, actor, priority, continuity_class, requested_route,
        decision, reason, economic_state,
    ):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO admission_events "
                "(ts, actor, priority, continuity_class, requested_route, "
                " decision, reason, economic_state) VALUES (?,?,?,?,?,?,?,?)",
                (time.time(), actor, priority, continuity_class,
                 requested_route, decision, reason, economic_state),
            )

    # --- spend -----------------------------------------------------------

    def record_spend(
        self, *, actor, requested_route, actual_provider, actual_model,
        cost_usd, input_tokens, output_tokens, source, request_digest=None,
    ):
        # cost_usd is Optional[float]; None is stored as NULL, never as 0.0.
        # A caller passing 0.0 explicitly (e.g. a genuinely free local model)
        # is different from "unknown" and both must remain distinguishable.
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO spend_events "
                "(ts, actor, requested_route, actual_provider, actual_model, "
                " cost_usd, input_tokens, output_tokens, source, request_digest) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (time.time(), actor, requested_route, actual_provider,
                 actual_model, cost_usd, input_tokens, output_tokens,
                 source, request_digest),
            )

    def burn_since(self, actor: str, since_ts: float) -> dict:
        """Rolling burn for one actor since since_ts. unknown_count>0 means
        at least one response had cost_usd IS NULL - callers must treat the
        window as untrustworthy for admission decisions, not as "$0 extra"."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT "
                "  COALESCE(SUM(cost_usd), 0.0) AS known_cost, "
                "  SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END) AS unknown_count, "
                "  COUNT(*) AS total_count "
                "FROM spend_events WHERE actor = ? AND ts >= ?",
                (actor, since_ts),
            ).fetchone()
        return {
            "known_cost": row["known_cost"],
            "unknown_count": row["unknown_count"],
            "total_count": row["total_count"],
        }

    # --- continuity episodes ---------------------------------------------

    def start_continuity_episode(self, *, mode: str, reason: str, evidence: dict) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO continuity_episodes "
                "(started_at, mode, reason, evidence_json) VALUES (?,?,?,?)",
                (time.time(), mode, reason, json.dumps(evidence)),
            )
            return cur.lastrowid

    def end_continuity_episode(self, episode_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE continuity_episodes SET ended_at = ? WHERE id = ?",
                (time.time(), episode_id),
            )

    def open_continuity_episode(self):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM continuity_episodes WHERE ended_at IS NULL "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def unreconciled_episodes(self):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM continuity_episodes "
                "WHERE ended_at IS NOT NULL AND reconciled = 0"
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_reconciled(self, episode_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE continuity_episodes SET reconciled = 1 WHERE id = ?",
                (episode_id,),
            )

    # --- idempotency ------------------------------------------------------

    @staticmethod
    def digest_for(actor: str, route: str, body: bytes, idempotency_key: str | None) -> str:
        if idempotency_key:
            basis = f"key:{actor}:{idempotency_key}".encode()
        else:
            basis = actor.encode() + b":" + route.encode() + b":" + body
        return hashlib.sha256(basis).hexdigest()

    def idempotency_lookup(self, digest: str):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM idempotency WHERE digest = ?", (digest,)
            ).fetchone()
        return dict(row) if row else None

    def idempotency_start(self, digest: str, actor: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO idempotency (digest, actor, first_seen) "
                "VALUES (?, ?, ?)",
                (digest, actor, time.time()),
            )

    def idempotency_complete(self, digest: str, response_json: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE idempotency SET completed_at = ?, response_json = ? "
                "WHERE digest = ?",
                (time.time(), response_json, digest),
            )

    # --- misc meta ----------------------------------------------------------

    def get_meta(self, key: str, default=None):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM state_meta WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO state_meta (key, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, time.time()),
            )
