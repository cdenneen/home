"""Evidence-backed context and cost broker for Eros."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from mcp.server.fastmcp import FastMCP

ALLOWED_DOMAINS = {"shared", "personal", "work"}
VERIFICATION_METHODS = {
    "source-backed",
    "user-confirmed",
    "test-passed",
    "provider-readback",
}
INELIGIBLE_FLAGS = {"live_state", "mutation", "session_specific", "tool_bearing"}
VECTOR_SIZE = 1024


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


TRUST_DOMAIN = env("EROS_TRUST_DOMAIN", "shared")
if TRUST_DOMAIN not in ALLOWED_DOMAINS:
    raise RuntimeError(f"invalid EROS_TRUST_DOMAIN: {TRUST_DOMAIN}")

DB_DSN = env("EROS_KNOWLEDGE_DSN", "postgresql:///eros_context?host=/run/postgresql")
LITELLM_DSN = env("EROS_LITELLM_DSN", "postgresql:///litellm?host=/run/postgresql")
QDRANT_URL = env("EROS_QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
OLLAMA_URL = env("EROS_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
EMBED_MODEL = env("EROS_EMBED_MODEL", "qwen3-embedding:0.6b")
GRAPHIFY_URL = env("EROS_GRAPHIFY_URL", "http://nyx.tail0e55.ts.net:18108/mcp")
RECALLIUM_URL = env("EROS_RECALLIUM_URL", "http://nyx.tail0e55.ts.net:18001/mcp")
SKILL_ROOTS = tuple(Path(p) for p in env("EROS_SKILL_ROOTS", "").split(":") if p)
MODEL_ROUTES = tuple(
    value for value in env("EROS_MODEL_ROUTES", "").split(",") if value
)
MCP_CATALOG = json.loads(env("EROS_MCP_CATALOG", "{}"))
EXTERNAL_PROJECTS = frozenset(json.loads(env("EROS_EXTERNAL_PROJECTS", "[]")))
REPORT_PATH = Path(env("EROS_REPORT_PATH", "/var/lib/eros-context/spend-report.json"))


def visible_domains(domain: str = TRUST_DOMAIN) -> tuple[str, ...]:
    """Expose shared knowledge plus the process's fixed private domain."""
    if domain not in ALLOWED_DOMAINS:
        raise ValueError(f"invalid trust domain: {domain}")
    return ("shared",) if domain == "shared" else ("shared", domain)


def stable_id(*parts: str) -> str:
    value = "\x1f".join(parts).encode()
    return hashlib.sha256(value).hexdigest()


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    metadata: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip():
            metadata[key.strip()] = value.strip().strip("'\"")
    return metadata, text[end + 5 :]


def parse_http_json(payload: bytes, content_type: str = "") -> Any:
    text = payload.decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    if "text/event-stream" not in content_type and not text.lstrip().startswith(
        "data:"
    ):
        return json.loads(text)
    messages = []
    for line in text.splitlines():
        if line.startswith("data:"):
            value = line[5:].strip()
            if value and value != "[DONE]":
                messages.append(json.loads(value))
    if not messages:
        raise ValueError("empty MCP event stream")
    return messages[-1]


def http_json(
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    method: str | None = None,
    timeout: float = 2.0,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=body,
        method=method or ("POST" if body is not None else "GET"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return parse_http_json(
            response.read(), response.headers.get("Content-Type", "")
        )


def embed(text: str) -> list[float] | None:
    try:
        response = http_json(
            f"{OLLAMA_URL}/api/embed",
            {"model": EMBED_MODEL, "input": text},
            timeout=4.0,
        )
        values = response.get("embeddings", [])
        return values[0] if values else None
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        return None


def ensure_collection(name: str) -> None:
    try:
        http_json(
            f"{QDRANT_URL}/collections/{name}",
            {"vectors": {"size": VECTOR_SIZE, "distance": "Cosine"}},
            method="PUT",
        )
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
        pass


def qdrant_upsert(
    collection: str, point_id: str, vector: list[float] | None, payload: dict[str, Any]
) -> None:
    if vector is None:
        return
    try:
        http_json(
            f"{QDRANT_URL}/collections/{collection}/points?wait=false",
            {
                "points": [
                    {
                        "id": str(uuid.UUID(point_id[:32])),
                        "vector": vector,
                        "payload": payload,
                    }
                ]
            },
            method="PUT",
        )
    except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
        pass


def qdrant_search(
    collection: str, vector: list[float] | None, limit: int, domains: Iterable[str]
) -> list[dict[str, Any]]:
    if vector is None:
        return []
    try:
        response = http_json(
            f"{QDRANT_URL}/collections/{collection}/points/query",
            {
                "query": vector,
                "limit": limit,
                "with_payload": True,
                "filter": {
                    "must": [{"key": "trust_domain", "match": {"any": list(domains)}}]
                },
            },
            timeout=2.0,
        )
        return response.get("result", {}).get("points", [])
    except (
        OSError,
        ValueError,
        KeyError,
        urllib.error.URLError,
        urllib.error.HTTPError,
    ):
        return []


def qdrant_scores(points: Iterable[dict[str, Any]]) -> dict[str, float]:
    return {
        str(point.get("payload", {}).get("id")): float(point.get("score", 0))
        for point in points
        if point.get("payload", {}).get("id")
    }


def db_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with (
        psycopg.connect(DB_DSN) as connection,
        connection.cursor(row_factory=psycopg.rows.dict_row) as cursor,
    ):
        cursor.execute(query, params)
        return list(cursor.fetchall())


def merge_ranked(
    groups: Iterable[Iterable[dict[str, Any]]], limit: int
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            key = str(
                item.get("id")
                or item.get("payload", {}).get("id")
                or stable_id(json.dumps(item, sort_keys=True))
            )
            candidate = dict(item.get("payload", item))
            candidate["id"] = key
            candidate["score"] = max(
                float(candidate.get("score", 0)), float(item.get("score", 0))
            )
            if key not in merged or candidate["score"] > float(
                merged[key].get("score", 0)
            ):
                merged[key] = candidate
    return sorted(
        merged.values(), key=lambda item: float(item.get("score", 0)), reverse=True
    )[:limit]


def validate_promotion(
    verification_method: str,
    source_uri: str,
    source_revision: str,
    source_digest: str,
    flags: Iterable[str],
) -> None:
    if verification_method not in VERIFICATION_METHODS:
        raise ValueError(
            "verification must be source-backed, user-confirmed, test-passed, or provider-readback"
        )
    if not all((source_uri.strip(), source_revision.strip(), source_digest.strip())):
        raise ValueError("source URI, revision, and digest are required")
    rejected = INELIGIBLE_FLAGS.intersection(flags)
    if rejected:
        raise ValueError(f"not reusable: {', '.join(sorted(rejected))}")


def _mcp_post(
    url: str, payload: dict[str, Any], session_id: str = ""
) -> tuple[Any, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST", headers=headers
    )
    with urllib.request.urlopen(request, timeout=3.0) as response:
        value = parse_http_json(
            response.read(), response.headers.get("Content-Type", "")
        )
        return value, response.headers.get("Mcp-Session-Id", session_id)


def _mcp_initialize(url: str, client_name: str) -> str:
    response, session_id = _mcp_post(
        url,
        {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": "1"},
            },
        },
    )
    if response.get("error"):
        raise RuntimeError(str(response["error"]))
    _mcp_post(
        url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id
    )
    return session_id


def mcp_call(url: str, tool: str, arguments: dict[str, Any]) -> Any:
    session_id = _mcp_initialize(url, "eros-context-broker")
    response, _ = _mcp_post(
        url,
        {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        session_id,
    )
    if response.get("error"):
        raise RuntimeError(str(response["error"]))
    return response.get("result", {})


def _external_context(query: str, project: str) -> list[dict[str, Any]]:
    if not project or project not in EXTERNAL_PROJECTS:
        return []
    results = []
    calls = (
        (GRAPHIFY_URL, "search_knowledge", {"query": query, "project": project}),
        (
            RECALLIUM_URL,
            "search_memories",
            {"query": query, "project": project, "limit": 3},
        ),
    )
    for url, tool, arguments in calls:
        try:
            results.append(
                {
                    "id": stable_id(url, query, project),
                    "kind": "external-allowlisted",
                    "title": tool,
                    "body": json.dumps(mcp_call(url, tool, arguments), default=str)[
                        :8000
                    ],
                    "source": url,
                    "score": 0.05,
                    "persisted": False,
                }
            )
        except (
            OSError,
            RuntimeError,
            ValueError,
            urllib.error.URLError,
            urllib.error.HTTPError,
        ):
            continue
    return results


mcp = FastMCP(
    "eros-context-broker", host="127.0.0.1", port=int(env("EROS_CONTEXT_PORT", "18120"))
)


@mcp.tool()
def health() -> dict[str, Any]:
    """Report canonical-store health and this process's accounting domain."""
    try:
        db_rows("SELECT 1 AS ok")
        database = "ok"
    except psycopg.Error as error:
        database = f"error: {error.__class__.__name__}"
    return {
        "status": database,
        "trust_domain": TRUST_DOMAIN,
        "visible_domains": visible_domains(),
    }


@mcp.tool()
def search_context(
    query: str,
    project: str = "",
    kinds: str = "",
    top_k: int = 6,
    include_external: bool = False,
) -> list[dict[str, Any]]:
    """Search verified assertions; optionally add allowlisted external recall."""
    limit = min(max(top_k, 1), 12)
    domains = visible_domains()
    kind_values = [value.strip() for value in kinds.split(",") if value.strip()]
    rows = db_rows(
        """
        SELECT a.id, a.kind, a.trust_domain, a.title, a.body, a.predicate,
               a.authority, a.confidence, a.verification_method, s.uri AS source_uri,
               ts_rank_cd(a.search_document, websearch_to_tsquery('english', %s)) AS score
        FROM assertions a JOIN sources s ON s.id = a.source_id
        WHERE a.status = 'active' AND a.trust_domain = ANY(%s)
          AND (a.valid_until IS NULL OR a.valid_until > now())
          AND (%s = '{}'::text[] OR a.kind = ANY(%s))
          AND a.search_document @@ websearch_to_tsquery('english', %s)
        ORDER BY score DESC LIMIT %s
        """,
        (query, list(domains), kind_values, kind_values, query, limit),
    )
    vector_scores = qdrant_scores(
        qdrant_search("eros_knowledge_v1", embed(query), limit, domains)
    )
    vector_rows = []
    if vector_scores:
        vector_rows = db_rows(
            """
            SELECT a.id, a.kind, a.trust_domain, a.title, a.body, a.predicate,
                   a.authority, a.confidence, a.verification_method, s.uri AS source_uri
            FROM assertions a JOIN sources s ON s.id = a.source_id
            WHERE a.id = ANY(%s) AND a.status = 'active'
              AND a.trust_domain = ANY(%s)
              AND (a.valid_until IS NULL OR a.valid_until > now())
              AND (%s = '{}'::text[] OR a.kind = ANY(%s))
            """,
            (list(vector_scores), list(domains), kind_values, kind_values),
        )
        for row in vector_rows:
            row["score"] = vector_scores.get(str(row["id"]), 0)
    external = _external_context(query, project) if include_external else []
    return merge_ranked((rows, vector_rows, external), limit)


@mcp.tool()
def search_graph(
    query: str, predicate: str = "", top_k: int = 8
) -> list[dict[str, Any]]:
    """Search evidence-backed entity relations in shared plus the fixed private domain."""
    limit = min(max(top_k, 1), 20)
    pattern = f"%{query}%"
    return db_rows(
        """
        SELECT r.id, r.trust_domain, subject.name AS subject, subject.kind AS subject_kind,
               r.predicate, object.name AS object, object.kind AS object_kind,
               r.confidence, s.uri AS source_uri, r.metadata
        FROM relations r
        JOIN entities subject ON subject.id = r.subject_id
        JOIN entities object ON object.id = r.object_id
        JOIN sources s ON s.id = r.source_id
        WHERE r.trust_domain = ANY(%s)
          AND (r.valid_until IS NULL OR r.valid_until > now())
          AND (%s = '' OR r.predicate = %s)
          AND (subject.name ILIKE %s OR object.name ILIKE %s OR r.predicate ILIKE %s)
        ORDER BY r.confidence DESC LIMIT %s
        """,
        (
            list(visible_domains()),
            predicate,
            predicate,
            pattern,
            pattern,
            pattern,
            limit,
        ),
    )


@mcp.tool()
def search_capabilities(
    query: str, kinds: str = "", top_k: int = 5
) -> list[dict[str, Any]]:
    """Discover a bounded set of skills, plugins, MCP tools, and model routes."""
    limit = min(max(top_k, 1), 10)
    kind_values = [value.strip() for value in kinds.split(",") if value.strip()]
    domains = visible_domains()
    rows = db_rows(
        """
        SELECT id, kind, trust_domain, name, version, description, source_uri,
               side_effect_class, cost_class,
               ts_rank_cd(search_document, websearch_to_tsquery('english', %s)) AS score
        FROM capabilities
        WHERE trust_domain = ANY(%s)
          AND (%s = '{}'::text[] OR kind = ANY(%s))
          AND search_document @@ websearch_to_tsquery('english', %s)
        ORDER BY score DESC LIMIT %s
        """,
        (query, list(domains), kind_values, kind_values, query, limit),
    )
    vector_scores = qdrant_scores(
        qdrant_search("eros_capability_v1", embed(query), limit, domains)
    )
    vector_rows = []
    if vector_scores:
        vector_rows = db_rows(
            """
            SELECT id, kind, trust_domain, name, version, description, source_uri,
                   side_effect_class, cost_class
            FROM capabilities
            WHERE id = ANY(%s) AND trust_domain = ANY(%s)
              AND (%s = '{}'::text[] OR kind = ANY(%s))
            """,
            (list(vector_scores), list(domains), kind_values, kind_values),
        )
        for row in vector_rows:
            row["score"] = vector_scores.get(str(row["id"]), 0)
    return merge_ranked((rows, vector_rows), limit)


def read_verified_skill(
    path: Path, digest: str, roots: Iterable[Path], max_chars: int
) -> str:
    resolved = path.resolve(strict=True)
    allowed_roots = tuple(root.resolve(strict=True) for root in roots)
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise ValueError("skill source is outside the managed roots")
    content = resolved.read_text(encoding="utf-8")
    if hashlib.sha256(content.encode()).hexdigest() != digest:
        raise ValueError("skill source digest changed; refresh the catalog")
    if len(content) > max_chars:
        raise ValueError(
            f"skill is {len(content)} characters; curate or split it below {max_chars}"
        )
    return content


@mcp.tool()
def get_skill(
    capability_id: str = "", name: str = "", max_chars: int = 30000
) -> dict[str, Any]:
    """Load one discovered skill after selection; large or changed skills fail closed."""
    if not capability_id and not name:
        raise ValueError("capability_id or name is required")
    limit = min(max(max_chars, 1000), 40000)
    rows = db_rows(
        """
        SELECT id, trust_domain, name, version, description, source_uri, source_digest
        FROM capabilities
        WHERE kind = 'skill' AND trust_domain = ANY(%s)
          AND (%s = '' OR id = %s) AND (%s = '' OR name = %s)
        ORDER BY updated_at DESC LIMIT 2
        """,
        (list(visible_domains()), capability_id, capability_id, name, name),
    )
    if not rows:
        raise ValueError("skill not found in the visible catalog")
    if len(rows) > 1:
        raise ValueError("skill name is ambiguous; use capability_id")
    skill = rows[0]
    skill["content"] = read_verified_skill(
        Path(skill["source_uri"]), skill["source_digest"], SKILL_ROOTS, limit
    )
    return skill


def promote_knowledge(
    title: str,
    body: str,
    kind: str,
    source_uri: str,
    source_revision: str,
    source_digest: str,
    authority: str,
    confidence: float,
    verification_method: str,
    verified_by: str,
    flags: str = "",
) -> dict[str, str]:
    """Promote evidence-backed knowledge into the caller's fixed trust domain."""
    validate_promotion(
        verification_method,
        source_uri,
        source_revision,
        source_digest,
        flags.split(","),
    )
    if kind not in {"claim", "decision", "procedure", "memory", "observation"}:
        raise ValueError("invalid assertion kind")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between zero and one")
    source_id = stable_id(source_uri, source_revision, source_digest)
    assertion_id = stable_id(TRUST_DOMAIN, kind, title, body, source_id)
    with psycopg.connect(DB_DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO sources(id, uri, revision, digest) VALUES (%s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (source_id, source_uri, source_revision, source_digest),
        )
        cursor.execute(
            """INSERT INTO assertions(
                   id, kind, trust_domain, title, body, source_id, authority,
                   confidence, verification_method, verified_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET body = EXCLUDED.body,
                   confidence = EXCLUDED.confidence, updated_at = now()""",
            (
                assertion_id,
                kind,
                TRUST_DOMAIN,
                title,
                body,
                source_id,
                authority,
                confidence,
                verification_method,
                verified_by,
            ),
        )
    qdrant_upsert(
        "eros_knowledge_v1",
        assertion_id,
        embed(f"{title}\n{body}"),
        {
            "id": assertion_id,
            "kind": kind,
            "trust_domain": TRUST_DOMAIN,
            "title": title,
            "body": body,
            "authority": authority,
            "confidence": confidence,
            "verification_method": verification_method,
            "source_uri": source_uri,
        },
    )
    return {"id": assertion_id, "status": "verified"}


def promote_relation(
    subject_name: str,
    subject_kind: str,
    predicate: str,
    object_name: str,
    object_kind: str,
    source_uri: str,
    source_revision: str,
    source_digest: str,
    confidence: float,
    verification_method: str,
    verified_by: str,
    flags: str = "",
) -> dict[str, str]:
    """Promote an evidence-backed logical relation into the fixed trust domain."""
    validate_promotion(
        verification_method,
        source_uri,
        source_revision,
        source_digest,
        flags.split(","),
    )
    if not all(
        value.strip()
        for value in (subject_name, subject_kind, predicate, object_name, object_kind)
    ):
        raise ValueError("subject, predicate, and object names/kinds are required")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between zero and one")
    source_id = stable_id(source_uri, source_revision, source_digest)
    subject_id = stable_id(TRUST_DOMAIN, subject_kind, subject_name)
    object_id = stable_id(TRUST_DOMAIN, object_kind, object_name)
    relation_id = stable_id(TRUST_DOMAIN, subject_id, predicate, object_id, source_id)
    with psycopg.connect(DB_DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO sources(id, uri, revision, digest) VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (source_id, source_uri, source_revision, source_digest),
        )
        cursor.executemany(
            """INSERT INTO entities(id, kind, trust_domain, name) VALUES (%s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, kind = EXCLUDED.kind, updated_at = now()""",
            (
                (subject_id, subject_kind, TRUST_DOMAIN, subject_name),
                (object_id, object_kind, TRUST_DOMAIN, object_name),
            ),
        )
        cursor.execute(
            """INSERT INTO relations(
                   id, trust_domain, subject_id, predicate, object_id, source_id, confidence, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (trust_domain, subject_id, predicate, object_id, source_id) DO UPDATE
               SET confidence = EXCLUDED.confidence, metadata = EXCLUDED.metadata""",
            (
                relation_id,
                TRUST_DOMAIN,
                subject_id,
                predicate,
                object_id,
                source_id,
                confidence,
                json.dumps(
                    {
                        "verification_method": verification_method,
                        "verified_by": verified_by,
                    }
                ),
            ),
        )
    return {"id": relation_id, "status": "verified"}


@mcp.tool()
def find_verified_result(
    query: str, corpus_version: str, route: str = "", top_k: int = 3
) -> list[dict[str, Any]]:
    """Find exact or semantic, unexpired, evidence-backed reusable results."""
    limit = min(max(top_k, 1), 5)
    domains = visible_domains()
    query_hash = stable_id(query.strip())
    exact = db_rows(
        """
        SELECT v.id, v.trust_domain, v.query, v.answer, v.verification_method,
               v.verified_by, v.model, v.route, v.corpus_version, s.uri AS source_uri,
               1.0 AS score
        FROM verified_results v JOIN sources s ON s.id = v.source_id
        WHERE v.trust_domain = ANY(%s) AND v.query_hash = %s
          AND v.corpus_version = %s AND (%s = '' OR v.route = %s)
          AND (v.expires_at IS NULL OR v.expires_at > now())
        LIMIT %s
        """,
        (list(domains), query_hash, corpus_version, route, route, limit),
    )
    vector_scores = qdrant_scores(
        qdrant_search("eros_verified_results_v1", embed(query), limit, domains)
    )
    semantic = []
    if vector_scores:
        semantic = db_rows(
            """
            SELECT v.id, v.trust_domain, v.query, v.answer, v.verification_method,
                   v.verified_by, v.model, v.route, v.corpus_version, s.uri AS source_uri
            FROM verified_results v JOIN sources s ON s.id = v.source_id
            WHERE v.id = ANY(%s) AND v.trust_domain = ANY(%s)
              AND v.corpus_version = %s AND (%s = '' OR v.route = %s)
              AND (v.expires_at IS NULL OR v.expires_at > now())
            """,
            (list(vector_scores), list(domains), corpus_version, route, route),
        )
        for item in semantic:
            item["score"] = vector_scores.get(str(item["id"]), 0)
    eligible = [
        item
        for item in merge_ranked((exact, semantic), limit)
        if item.get("corpus_version") == corpus_version
        and (not route or item.get("route") == route)
        and item.get("verification_method") in VERIFICATION_METHODS
    ]
    return eligible[:limit]


def promote_verified_result(
    query: str,
    answer: str,
    corpus_version: str,
    source_uri: str,
    source_revision: str,
    source_digest: str,
    verification_method: str,
    verified_by: str,
    model: str = "",
    route: str = "",
    expires_at: str = "",
    flags: str = "",
) -> dict[str, str]:
    """Store a narrowly eligible, independently verified single-shot result."""
    validate_promotion(
        verification_method,
        source_uri,
        source_revision,
        source_digest,
        flags.split(","),
    )
    source_id = stable_id(source_uri, source_revision, source_digest)
    query_hash = stable_id(query.strip())
    result_id = stable_id(TRUST_DOMAIN, query_hash, corpus_version)
    with psycopg.connect(DB_DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO sources(id, uri, revision, digest) VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            (source_id, source_uri, source_revision, source_digest),
        )
        cursor.execute(
            """INSERT INTO verified_results(
                   id, trust_domain, query, answer, query_hash, source_id,
                   verification_method, verified_by, model, route, corpus_version, expires_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULLIF(%s, '')::timestamptz)
               ON CONFLICT (trust_domain, query_hash, corpus_version) DO UPDATE
               SET answer = EXCLUDED.answer, source_id = EXCLUDED.source_id,
                   verification_method = EXCLUDED.verification_method,
                   verified_by = EXCLUDED.verified_by, model = EXCLUDED.model,
                   route = EXCLUDED.route, expires_at = EXCLUDED.expires_at,
                   created_at = now()""",
            (
                result_id,
                TRUST_DOMAIN,
                query,
                answer,
                query_hash,
                source_id,
                verification_method,
                verified_by,
                model,
                route,
                corpus_version,
                expires_at,
            ),
        )
    qdrant_upsert(
        "eros_verified_results_v1",
        result_id,
        embed(query),
        {
            "id": result_id,
            "trust_domain": TRUST_DOMAIN,
            "query": query,
            "answer": answer,
            "verification_method": verification_method,
            "verified_by": verified_by,
            "model": model,
            "route": route,
            "corpus_version": corpus_version,
            "source_uri": source_uri,
        },
    )
    return {"id": result_id, "status": "verified"}


def _spend_for_request(request_id: str) -> dict[str, Any]:
    if not request_id:
        return {}
    with (
        psycopg.connect(LITELLM_DSN) as connection,
        connection.cursor(row_factory=psycopg.rows.dict_row) as cursor,
    ):
        cursor.execute(
            """SELECT model, model_group AS route, spend,
                      prompt_tokens, completion_tokens,
                      coalesce(NULLIF(metadata::jsonb #>> '{usage_object,cache_read_input_tokens}', '')::bigint, 0)
                          AS cached_prompt_tokens
               FROM "LiteLLM_SpendLogs" WHERE request_id = %s
               ORDER BY "startTime" DESC LIMIT 1""",
            (request_id,),
        )
        return dict(cursor.fetchone() or {})


def record_outcome(
    request_id: str,
    consumer: str,
    workload: str,
    success: bool,
    accepted: bool | None = None,
    task_id: str = "",
    session_id: str = "",
    evidence_uri: str = "",
) -> dict[str, Any]:
    """Record an outcome, joining cost only from LiteLLM's authoritative spend log."""
    if not request_id:
        raise ValueError("request_id is required to join an authoritative LiteLLM cost")
    spend = _spend_for_request(request_id)
    outcome_id = stable_id(request_id, consumer, workload)
    with psycopg.connect(DB_DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO outcomes(
                   id, request_id, trust_domain, consumer, workload, task_id,
                   session_id, success, accepted, model, route, spend,
                   prompt_tokens, completion_tokens, cached_prompt_tokens, evidence_uri)
               VALUES (%s, %s, %s, %s, %s, NULLIF(%s,''), NULLIF(%s,''), %s, %s,
                   %s, %s, %s, %s, %s, %s, NULLIF(%s,''))
               ON CONFLICT (request_id, consumer, workload) DO UPDATE
               SET success = EXCLUDED.success, accepted = EXCLUDED.accepted,
                   evidence_uri = EXCLUDED.evidence_uri, metadata = outcomes.metadata""",
            (
                outcome_id,
                request_id,
                TRUST_DOMAIN,
                consumer,
                workload,
                task_id,
                session_id,
                success,
                accepted,
                spend.get("model"),
                spend.get("route"),
                spend.get("spend"),
                spend.get("prompt_tokens"),
                spend.get("completion_tokens"),
                spend.get("cached_prompt_tokens"),
                evidence_uri,
            ),
        )
    return {"id": outcome_id, "cost_joined": bool(spend), "spend": spend.get("spend")}


@mcp.tool()
def cost_efficiency(days: int = 30) -> list[dict[str, Any]]:
    """Report dollars per successful and accepted outcome by consumer/workload."""
    days = min(max(days, 1), 365)
    return db_rows(
        """
        SELECT consumer, workload, count(*) AS outcomes,
               count(*) FILTER (WHERE success) AS successful,
               count(*) FILTER (WHERE accepted) AS accepted,
               coalesce(sum(spend), 0) AS spend,
               coalesce(sum(spend), 0) / NULLIF(count(*) FILTER (WHERE success), 0) AS cost_per_success,
               coalesce(sum(spend), 0) / NULLIF(count(*) FILTER (WHERE accepted), 0) AS cost_per_accepted,
               coalesce(sum(cached_prompt_tokens), 0) AS cached_prompt_tokens
        FROM outcomes
        WHERE trust_domain = ANY(%s) AND created_at >= now() - (%s || ' days')::interval
        GROUP BY consumer, workload ORDER BY spend DESC
        """,
        (list(visible_domains()), days),
    )


def _iter_skills() -> Iterable[tuple[Path, dict[str, str], str]]:
    for root in SKILL_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("SKILL.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            metadata, body = parse_frontmatter(text)
            if not metadata.get("name"):
                continue
            yield path, metadata, body


def _upsert_capability(
    kind: str,
    name: str,
    description: str,
    source_uri: str,
    source_digest: str,
    *,
    version: str = "",
    side_effect_class: str = "unknown",
    cost_class: str = "unknown",
    metadata: dict[str, Any] | None = None,
) -> None:
    capability_id = stable_id(kind, TRUST_DOMAIN, name, version)
    with psycopg.connect(DB_DSN) as connection, connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO capabilities(
                   id, kind, trust_domain, name, version, description, source_uri,
                   source_digest, side_effect_class, cost_class, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (kind, trust_domain, name, version) DO UPDATE
               SET description = EXCLUDED.description, source_uri = EXCLUDED.source_uri,
                   source_digest = EXCLUDED.source_digest, side_effect_class = EXCLUDED.side_effect_class,
                   cost_class = EXCLUDED.cost_class, metadata = EXCLUDED.metadata, updated_at = now()""",
            (
                capability_id,
                kind,
                TRUST_DOMAIN,
                name,
                version,
                description,
                source_uri,
                source_digest,
                side_effect_class,
                cost_class,
                json.dumps(metadata or {}),
            ),
        )
    qdrant_upsert(
        "eros_capability_v1",
        capability_id,
        embed(f"{name}\n{description}"),
        {
            "id": capability_id,
            "kind": kind,
            "trust_domain": TRUST_DOMAIN,
            "name": name,
            "description": description,
            "source_uri": source_uri,
            "side_effect_class": side_effect_class,
            "cost_class": cost_class,
        },
    )


def _iter_plugins() -> Iterable[tuple[Path, dict[str, Any]]]:
    for root in SKILL_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob(".codex-plugin/plugin.json"):
            try:
                yield path, json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue


def _mcp_tools(url: str) -> list[dict[str, Any]]:
    session_id = _mcp_initialize(url, "eros-context-catalog")
    response, _ = _mcp_post(
        url,
        {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/list",
            "params": {},
        },
        session_id,
    )
    return response.get("result", {}).get("tools", [])


def classify_tool_side_effect(name: str) -> str:
    if re.search(
        r"create|update|delete|send|apply|run|execute|push|merge|restart|approve|click|close|write|edit|play",
        name,
        re.IGNORECASE,
    ):
        return "write"
    if re.search(
        r"get|list|search|find|read|describe|show|status|health|query|fetch|view|inspect|diff|log|resolve",
        name,
        re.IGNORECASE,
    ):
        return "read"
    return "unknown"


def refresh_catalog() -> dict[str, int]:
    """Index skills, plugins, MCP tools, and model routes into the capability registry."""
    counts = {
        "skills": 0,
        "plugins": 0,
        "mcp_servers": 0,
        "mcp_tools": 0,
        "model_routes": 0,
    }
    for path, metadata, body in _iter_skills():
        name = metadata.get("name", path.parent.name)
        description = metadata.get(
            "description", re.sub(r"\s+", " ", body).strip()[:1000]
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        _upsert_capability(
            "skill",
            name,
            description,
            str(path),
            digest,
            version=metadata.get("version", ""),
            cost_class="local",
            metadata={"allowed_tools": metadata.get("allowed-tools", "")},
        )
        counts["skills"] += 1
    for path, manifest in _iter_plugins():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        _upsert_capability(
            "plugin",
            str(manifest.get("name", path.parent.parent.name)),
            str(manifest.get("description", "")),
            str(path),
            digest,
            version=str(manifest.get("version", "")),
            metadata=manifest,
        )
        counts["plugins"] += 1
    for name, url in MCP_CATALOG.items():
        digest = stable_id(name, str(url))
        _upsert_capability(
            "mcp_server",
            name,
            f"MCP server {name}",
            str(url),
            digest,
            cost_class="free",
        )
        counts["mcp_servers"] += 1
        try:
            tools = _mcp_tools(str(url))
        except (
            OSError,
            RuntimeError,
            ValueError,
            urllib.error.URLError,
            urllib.error.HTTPError,
        ):
            tools = []
        for tool in tools:
            tool_name = f"{name}.{tool.get('name', 'unknown')}"
            description = str(tool.get("description", ""))[:4000]
            side_effect = classify_tool_side_effect(tool_name)
            _upsert_capability(
                "mcp_tool",
                tool_name,
                description,
                str(url),
                stable_id(digest, tool_name, description),
                side_effect_class=side_effect,
                cost_class="free",
                metadata={"inputSchema": tool.get("inputSchema", {})},
            )
            counts["mcp_tools"] += 1
    for route in MODEL_ROUTES:
        _upsert_capability(
            "model_route",
            route,
            f"LiteLLM model route {route}",
            "eros:litellm",
            stable_id(route),
            cost_class="metered",
        )
        counts["model_routes"] += 1
    return counts


def soft_budget_level(spend: float, budget: float | None) -> str:
    if not budget or budget <= 0:
        return "unconfigured"
    ratio = spend / budget
    return "critical" if ratio >= 1 else "warning" if ratio >= 0.8 else "ok"


def generate_report(path: Path = REPORT_PATH) -> dict[str, Any]:
    with (
        psycopg.connect(LITELLM_DSN) as connection,
        connection.cursor(row_factory=psycopg.rows.dict_row) as cursor,
    ):
        cursor.execute(
            """SELECT key_alias, spend,
                      NULLIF(metadata->>'soft_budget_usd','')::double precision AS soft_budget_usd,
                      metadata->>'soft_budget_window' AS soft_budget_window,
                      metadata->>'consumer' AS consumer,
                      metadata->>'trust_domain' AS trust_domain
               FROM "LiteLLM_VerificationToken" ORDER BY spend DESC"""
        )
        keys = [dict(row) for row in cursor.fetchall()]
        cursor.execute(
            """SELECT coalesce(model_group, model) AS route,
                      coalesce(sum(api_requests), 0) AS requests,
                      coalesce(sum(spend), 0) AS spend,
                      coalesce(sum(prompt_tokens), 0) AS prompt_tokens,
                      coalesce(sum(completion_tokens), 0) AS completion_tokens,
                      coalesce(sum(cache_read_input_tokens), 0) AS cached_prompt_tokens,
                      coalesce(sum(cache_creation_input_tokens), 0) AS cache_creation_prompt_tokens,
                      coalesce(sum(prompt_caching_savings_spend), 0) AS prompt_caching_savings_spend,
                      coalesce(sum(compression_savings_spend), 0) AS compression_savings_spend
               FROM "LiteLLM_DailyUserSpend"
               WHERE date::date >= current_date - 30
               GROUP BY coalesce(model_group, model) ORDER BY spend DESC"""
        )
        routes = [dict(row) for row in cursor.fetchall()]
        cursor.execute(
            """SELECT tag, coalesce(sum(api_requests), 0) AS requests,
                      coalesce(sum(spend), 0) AS spend,
                      coalesce(sum(prompt_caching_savings_spend), 0) AS prompt_caching_savings_spend
               FROM "LiteLLM_DailyTagSpend"
               WHERE date::date >= current_date - 30
                 AND (tag LIKE 'consumer:%%' OR tag LIKE 'trust_domain:%%'
                      OR tag LIKE 'workload:%%' OR tag ILIKE 'x-eros-%%')
               GROUP BY tag ORDER BY spend DESC"""
        )
        tags = [dict(row) for row in cursor.fetchall()]
    for item in keys:
        item["level"] = soft_budget_level(
            float(item.get("spend") or 0), item.get("soft_budget_usd")
        )
    for item in routes:
        prompt_tokens = int(item.get("prompt_tokens") or 0)
        cached_prompt_tokens = int(item.get("cached_prompt_tokens") or 0)
        item["cache_read_ratio"] = (
            cached_prompt_tokens / prompt_tokens if prompt_tokens else 0.0
        )
        item["counterfactual_spend_without_savings"] = (
            float(item.get("spend") or 0)
            + float(item.get("prompt_caching_savings_spend") or 0)
            + float(item.get("compression_savings_spend") or 0)
        )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "keys": keys,
        "routes_30d": routes,
        "attribution_tags_30d": tags,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as handle:
        json.dump(report, handle, indent=2, default=str)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)
    return report


def initialize() -> None:
    for collection in (
        "eros_knowledge_v1",
        "eros_verified_results_v1",
        "eros_capability_v1",
    ):
        ensure_collection(collection)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", nargs="?", choices=("serve", "sync", "report"), default="serve"
    )
    parser.add_argument("path", nargs="?", type=Path)
    arguments = parser.parse_args()
    initialize()
    if arguments.command == "sync":
        print(json.dumps(refresh_catalog()))
    elif arguments.command == "report":
        print(json.dumps(generate_report(arguments.path or REPORT_PATH), default=str))
    else:
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
