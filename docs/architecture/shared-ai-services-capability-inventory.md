# Shared AI Services Stack — capability inventory

Graduation baseline: PR #735, canonical commit `4cad34e6`. This is an inventory of
what already exists, not a design document — see the PR/commit for implementation.
Do not add a new database, memory runtime, or knowledge service against a row below
without evidence that the existing implementation is actually insufficient for a
real workload.

| Capability | Current implementation |
| --- | --- |
| Gateway / auth / accounting | LiteLLM (`hosts/nixos/eros.nix`) |
| Normal model routing | OmniRoute (LiteLLM → OmniRoute → provider, for all tier routes except two documented direct exceptions: `tier2-general` — provider prompt-cache requires the direct Bedrock path; `tier4-frontier` — explicit-only by design, no auto-fallback) |
| Degraded routing (OmniRoute down, LiteLLM up) | `hermes-policy-endpoint/endpoint.py` — independent TCP-level OmniRoute health probe (`OutageClassifier`), remaps to a capability-compatible direct LiteLLM route via `DEGRADED_ROUTING_MAP` (currently `tier2-research`→`tier2-general`; no direct equivalent exists yet for `tier1-*`/`tier3-quality` — see backlog) |
| Total-stack continuity (LiteLLM/OmniRoute both down) | G-CONT native provider path — `endpoint.py` `_forward_continuity`, direct OpenAI call via a credential resolved by reference (`continuity.py`, `env_file:~/.hermes/.env#OPENAI_API_KEY`), bounded timeout/tokens/cost caps, cross-process-safe |
| Vector retrieval | Qdrant, three collections: `litellm_semantic_cache_gc2_pilot`, `shared_knowledge`, `shared_memory` (`shared_intelligence.py`) |
| Semantic result reuse | `shared_intelligence.check_semantic_reuse` against `litellm_semantic_cache_gc2_pilot` — near-exact question match + `verified=True` payload flag → returns the stored answer with zero fresh inference |
| Shared knowledge (cross-candidate) | `shared_intelligence.retrieve_knowledge` / `promote_result` against `shared_knowledge` — provenance (`produced_by_model`/`produced_by_tier`) recorded but never used to gate retrieval; proven live for a cheaper candidate reusing a stronger candidate's discovery |
| Episodic / procedural memory | Same mechanism as shared knowledge, distinguished via the `shared_memory` collection and an optional `memory_type` payload field (`episodic`/`procedural`). **DEFERRED** — mechanism exists and is code-capable, but no real workload has populated it yet with a `memory_type` tag. Evidence: `shared_memory` collection created and reachable, zero real promotions as of graduation. |
| RAG / curated-doc context retrieval | Same mechanism as shared knowledge, distinguished via the `source_type` payload field (`model_discovered` vs `curated_doc`). **DEFERRED** — only `model_discovered` has been exercised; no curated-document ingestion pipeline exists yet, and nothing currently blocks on one. |
| Relationship / graph model | **NOT YET REQUIRED.** No entity/relationship/traversal fields exist in the current payload schema, and no real workload has needed cross-record traversal. If one arises, the design intent (documented in earlier session discussion, not yet built) is entity/relationship/provenance fields on the existing Qdrant payload — not a new graph database — unless a real workload's actual query pattern needs genuine graph traversal characteristics. |
| Verification / outcomes | Deterministic heuristic in `shared_intelligence.promote_result`/`_looks_like_refusal`: HTTP 200 + non-empty, non-refusal content required before promotion. `state.py`'s `admission_events`/`spend_events`/`continuity_episodes` record the outcome of every request (decision, reason, cost, source). No LLM-judged verification layer exists — deliberately deferred until real workloads show the deterministic heuristic is insufficient. |
| Durable accounting | Postgres (LiteLLM's own `LiteLLM_SpendLogs`) for provider-level spend; SQLite (`state.py` `LocalState`, one per actor + one shared for the default continuity credential) for policy-endpoint-level admission/spend/continuity-episode records |
| Provider prompt caching | Bedrock `cache_control_injection_points` on `tier2-general` and the legacy `coding-strong` route — a transport-level optimization on an unchanged model/capability, distinct from and unrelated to the reuse mechanisms above |

## Operating rule

Per the graduation decision: a change to any row above requires evidence that one of
the following is materially affected — useful-work correctness, reliability,
security/privacy, cost per verified useful outcome, or consumer capability. Routine
observations from real AXIS/Alpha0 usage go to backlog, not directly into another
Shared AI Services change.
