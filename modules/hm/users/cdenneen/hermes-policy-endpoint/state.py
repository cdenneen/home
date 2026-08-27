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
    economic_state TEXT NOT NULL,
    classification_evidence TEXT,    -- action_classification.py's per-request
                                      -- evidence dict (JSON), NULL for events
                                      -- that predate action-level classification
    trust_domain TEXT,                -- 'work' | 'personal' - hard tenant boundary
    agent TEXT,                        -- e.g. 'nyx', 'ghost' - independent of gateway/profile
    workstream TEXT,                   -- e.g. 'eks', 'gitlab', 'assistant', 'axis-control'
    resource_project_context TEXT      -- reserved, not yet populated per-request
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
    request_digest TEXT,
    trust_domain TEXT,                -- 'work' | 'personal'
    agent TEXT,
    workstream TEXT,
    resource_project_context TEXT
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
            self._migrate(conn)

    @staticmethod
    def _migrate(conn):
        # Additive columns on tables that may already exist on a
        # previously-deployed instance (PR #711) without them.
        admission_cols = {row["name"] for row in conn.execute("PRAGMA table_info(admission_events)")}
        for col in ("classification_evidence", "trust_domain", "agent", "workstream", "resource_project_context"):
            if col not in admission_cols:
                conn.execute(f"ALTER TABLE admission_events ADD COLUMN {col} TEXT")
        spend_cols = {row["name"] for row in conn.execute("PRAGMA table_info(spend_events)")}
        for col in ("trust_domain", "agent", "workstream", "resource_project_context"):
            if col not in spend_cols:
                conn.execute(f"ALTER TABLE spend_events ADD COLUMN {col} TEXT")

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
        decision, reason, economic_state, classification_evidence=None,
        trust_domain=None, agent=None, workstream=None, resource_project_context=None,
    ):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO admission_events "
                "(ts, actor, priority, continuity_class, requested_route, "
                " decision, reason, economic_state, classification_evidence, "
                " trust_domain, agent, workstream, resource_project_context) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), actor, priority, continuity_class,
                 requested_route, decision, reason, economic_state,
                 json.dumps(classification_evidence) if classification_evidence is not None else None,
                 trust_domain, agent, workstream, resource_project_context),
            )

    # --- spend -----------------------------------------------------------

    def record_spend(
        self, *, actor, requested_route, actual_provider, actual_model,
        cost_usd, input_tokens, output_tokens, source, request_digest=None,
        trust_domain=None, agent=None, workstream=None, resource_project_context=None,
    ):
        # cost_usd is Optional[float]; None is stored as NULL, never as 0.0.
        # A caller passing 0.0 explicitly (e.g. a genuinely free local model)
        # is different from "unknown" and both must remain distinguishable.
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO spend_events "
                "(ts, actor, requested_route, actual_provider, actual_model, "
                " cost_usd, input_tokens, output_tokens, source, request_digest, "
                " trust_domain, agent, workstream, resource_project_context) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), actor, requested_route, actual_provider,
                 actual_model, cost_usd, input_tokens, output_tokens,
                 source, request_digest,
                 trust_domain, agent, workstream, resource_project_context),
            )

    def burn_since(self, actor: str, since_ts: float) -> dict:
        """Rolling burn for one actor since since_ts. unknown_count>0 means
        at least one response had cost_usd IS NULL - callers must treat the
        window as untrustworthy for admission decisions, not as "$0 extra"."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT "
                "  COALESCE(SUM(cost_usd), 0.0) AS known_cost, "
                # SUM() over zero matching rows returns NULL, not 0 (unlike
                # COUNT) - an actor with no spend_events yet would otherwise
                # crash economic_state()'s `> 0` comparison on NULL.
                # Confirmed live (nyx-gitlab, 2026-08-27): first-ever POST to
                # a freshly deployed instance raised TypeError here.
                "  COALESCE(SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END), 0) AS unknown_count, "
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
