"""
Shared AI Services MVP: shared reuse plane.

Five distinct reuse/avoidance mechanisms exist in this stack - they are
NOT collapsed into one generic cache:

  1. semantic_result_reuse  - near-exact prior request already has a
     verified good answer -> return it, zero fresh inference.
  2. knowledge_retrieval    - relevant prior discoveries/facts, produced
     by ANY execution candidate, injected as extra context for a fresh
     call. Provenance (which model/tier produced it) is recorded, never
     used to gate retrieval - a cheaper candidate may use knowledge a
     stronger candidate discovered.
  3. memory_retrieval       - episodic ("what happened before") and
     procedural ("how this kind of task is done") memory, same
     mechanism as (2) with a distinct collection/type so it is never
     confused with general knowledge.
  4. RAG (curated docs)     - same retrieval mechanism as (2), payload
     tagged source_type="curated_doc" vs "model_discovered" - not a
     separate pipeline.
  5. provider_prompt_cache  - Bedrock's own cache_control mechanism
     (tier2-general, coding-strong). Unrelated to this module - a
     transport-level optimization on an unchanged model/capability, not
     shared cross-candidate intelligence.

Backing store: Qdrant only (already deployed, already healthy) - no new
database introduced. Four collections:
  litellm_semantic_cache_gc2_pilot  (1)
  shared_knowledge                  (2, 4)
  shared_memory                     (3)

Trust isolation is structural, not a filter that can be forgotten: every
search call below REQUIRES a trust_domain and always includes it as a
Qdrant payload filter. Work and Personal can never see each other's
points through this module.

Default-ineligible: only requests explicitly tagged with
metadata.reuse_scope participate in retrieval or promotion at all -
tool-bearing, streaming, mutation/live_state/session_specific/no_store
requests are never eligible regardless of tag (same policy G-C2
established).
"""

import json
import time
import urllib.error
import urllib.request

QDRANT_BASE = "http://100.117.68.38:6333"
EMBED_MODEL = "local-embed"
SEMANTIC_CACHE_COLLECTION = "litellm_semantic_cache_gc2_pilot"
KNOWLEDGE_COLLECTION = "shared_knowledge"
MEMORY_COLLECTION = "shared_memory"

SEMANTIC_REUSE_SCORE_THRESHOLD = 0.93
KNOWLEDGE_SCORE_THRESHOLD = 0.75
DISQUALIFYING_METADATA_FLAGS = ("mutation", "live_state", "session_specific", "no_store")


def is_reuse_eligible(parsed_body: dict) -> tuple[bool, str]:
    metadata = parsed_body.get("metadata") or {}
    if not metadata.get("reuse_scope"):
        return False, "no_reuse_scope_tag"
    if parsed_body.get("tools") or parsed_body.get("tool_choice"):
        return False, "tool_bearing"
    if parsed_body.get("stream"):
        return False, "streaming"
    for flag in DISQUALIFYING_METADATA_FLAGS:
        if metadata.get(flag):
            return False, f"disqualifying_flag:{flag}"
    return True, "ok"


def _last_user_text(messages: list) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
    return ""


def embed_text(eros_base_url: str, eros_api_key: str, text: str) -> "list[float] | None":
    if not text:
        return None
    req = urllib.request.Request(
        f"{eros_base_url}/v1/embeddings",
        data=json.dumps({"model": EMBED_MODEL, "input": text}).encode(),
        headers={"Authorization": f"Bearer {eros_api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["data"][0]["embedding"]
    except Exception:
        return None


def _qdrant_request(method: str, path: str, body: dict) -> "dict | None":
    req = urllib.request.Request(
        f"{QDRANT_BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def qdrant_search(collection: str, vector: list, trust_domain: str, limit: int = 3) -> list:
    """Trust-domain-filtered search. Never callable without trust_domain -
    there is no code path in this module that searches unfiltered."""
    result = _qdrant_request(
        "POST",
        f"/collections/{collection}/points/search",
        {
            "vector": vector,
            "limit": limit,
            "with_payload": True,
            "filter": {"must": [{"key": "trust_domain", "match": {"value": trust_domain}}]},
        },
    )
    if result is None:
        return []
    return result.get("result", [])


def qdrant_upsert(collection: str, point_id: str, vector: list, payload: dict) -> bool:
    result = _qdrant_request(
        "PUT",
        f"/collections/{collection}/points",
        {"points": [{"id": point_id, "vector": vector, "payload": payload}]},
    )
    return result is not None and result.get("status") == "ok"


def check_semantic_reuse(eros_base_url, eros_api_key, trust_domain, question_text):
    """Returns (answer_text_or_None, evidence_dict). Only ever returns an
    answer for a score above SEMANTIC_REUSE_SCORE_THRESHOLD AND a payload
    explicitly marked verified=True - an unverified near-match is never
    substituted for fresh inference."""
    vector = embed_text(eros_base_url, eros_api_key, question_text)
    if vector is None:
        return None, {"reason": "embedding_unavailable"}
    hits = qdrant_search(SEMANTIC_CACHE_COLLECTION, vector, trust_domain, limit=1)
    if not hits:
        return None, {"reason": "no_match"}
    top = hits[0]
    if top["score"] < SEMANTIC_REUSE_SCORE_THRESHOLD:
        return None, {"reason": "below_threshold", "score": top["score"]}
    payload = top.get("payload") or {}
    if not payload.get("verified"):
        return None, {"reason": "match_not_verified", "score": top["score"]}
    return payload.get("answer"), {
        "reason": "reused", "score": top["score"], "produced_by_model": payload.get("produced_by_model"),
        "produced_by_tier": payload.get("produced_by_tier"), "point_id": top.get("id"),
    }


def retrieve_knowledge(eros_base_url, eros_api_key, trust_domain, question_text):
    """Returns a list of (collection, payload, score) for context
    injection - never returned as a direct answer, always as supporting
    context for a fresh call."""
    vector = embed_text(eros_base_url, eros_api_key, question_text)
    if vector is None:
        return []
    found = []
    for collection in (KNOWLEDGE_COLLECTION, MEMORY_COLLECTION):
        for hit in qdrant_search(collection, vector, trust_domain, limit=2):
            if hit["score"] >= KNOWLEDGE_SCORE_THRESHOLD:
                found.append((collection, hit.get("payload") or {}, hit["score"]))
    found.sort(key=lambda x: x[2], reverse=True)
    return found


def build_context_injection(hits: list) -> "str | None":
    if not hits:
        return None
    lines = ["Relevant prior verified information (may help answer this request):"]
    for collection, payload, score in hits:
        kind = "memory" if collection == MEMORY_COLLECTION else "knowledge"
        lines.append(f"- [{kind}, produced by {payload.get('produced_by_tier', 'unknown')}] {payload.get('content', '')}")
    return "\n".join(lines)


def promote_result(
    eros_base_url, eros_api_key, trust_domain, question_text, answer_text,
    produced_by_model, produced_by_tier, memory_type=None, source_type="model_discovered",
):
    """Store a verified (question, answer) pair for future semantic reuse,
    and separately as retrievable knowledge/memory. cost 2 embedding calls
    + 2 upserts - only ever called for already-eligible, already-successful
    responses (see is_reuse_eligible + caller's own success check)."""
    vector = embed_text(eros_base_url, eros_api_key, question_text)
    if vector is None:
        return False
    point_id = f"{trust_domain}-{int(time.time() * 1000)}"
    ok1 = qdrant_upsert(
        SEMANTIC_CACHE_COLLECTION, point_id, vector,
        {
            "trust_domain": trust_domain, "answer": answer_text, "verified": True,
            "produced_by_model": produced_by_model, "produced_by_tier": produced_by_tier,
            "created_at": time.time(),
        },
    )
    collection = MEMORY_COLLECTION if memory_type else KNOWLEDGE_COLLECTION
    payload = {
        "trust_domain": trust_domain, "content": answer_text, "verified": True,
        "produced_by_model": produced_by_model, "produced_by_tier": produced_by_tier,
        "source_type": source_type, "created_at": time.time(),
    }
    if memory_type:
        payload["memory_type"] = memory_type
    ok2 = qdrant_upsert(collection, point_id + "-k", vector, payload)
    return ok1 and ok2
