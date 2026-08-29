"""
Propagate Hermes's already-known per-turn workload source (slack / cron /
kanban / subagent / cli) into the outbound request body as
``x_hermes_source``, so the host-local policy endpoint can derive
continuity behavior deterministically instead of inferring it.

Also propagates Hermes's own logical ``session_id`` (the same value
``agent.session_id`` holds for the whole life of that agent instance) as
``litellm_session_id`` - LiteLLM's own documented, first-class session-
tracking param (see ``_get_standard_logging_payload_trace_id`` in
litellm_core_utils/litellm_logging.py: "we recommend using
litellm_session_id for session tracking"). Unlike ``x_hermes_source``,
this is a declared parameter of litellm's own request schema - it is
extracted into litellm's internal ``litellm_params`` for logging/spend
attribution and is never forwarded into the provider-specific request
body, so it does not carry the same strict-schema risk that made
``x_hermes_source`` require ``additional_drop_params`` on Bedrock routes
(confirmed via source read of get_litellm_params.py and a live Bedrock
call - see PR description). Telemetry only: nothing in Eros's current
config reads session_id for budget/concurrency/routing decisions, and
this patch does not add any such config - it only makes the identifier
visible on the wire for future, separately-qualified use.

task_id is deliberately NOT propagated here. Hermes's per-turn
``agent._current_task_id`` (turn_context.py) is regenerated with a fresh
uuid every turn by default (its own comment: "isolate VMs between
tasks") and is only stable across calls when a caller explicitly threads
one through (confirmed for cron: ``cron/scheduler.py`` passes the cron
job's own persistent id). There is no existing ambient bridge carrying
that value to the two hook points below the way ``session_id`` already
reaches ``set_accounting_context`` directly as a function argument -
adding one would mean hooking a third, unvetted call site rather than
extending the two already-proven ones. Left as a reported gap, not
manufactured.

Descriptive only: this module never decides continuity/economic policy -
it just makes already-known facts visible on the wire. All policy
derivation lives in hermes-policy-endpoint/governor.py.

Two hook points, both already used by Hermes 0.20.0 for an analogous
purpose (see agent/portal_tags.py's Nous Portal product-attribution tags,
which use the identical ambient-ContextVar-set-at-turn-entry shape):

1. ``agent.aux_accounting.set_accounting_context(session_db, session_id)``
   - called once per turn at turn entry (confirmed call site:
   ``run_agent.py``). We wrap it to look up the session's ``source``
   column (``session_db.get_session(session_id)["source"]``) and publish
   it, plus the ``session_id`` argument itself, on our own ContextVars.

2. ``agent.transports.chat_completions._add_prompt_cache_key(...)`` -
   called unconditionally on every Chat Completions request (confirmed:
   two call sites in chat_completions.py, neither gated by an if-check
   before the call - the flag it checks is internal to the function).
   We wrap it to also set ``extra_body["x_hermes_source"]`` and
   ``extra_body["litellm_session_id"]`` from our ContextVars before
   delegating to the original.

If either hook's shape changes in a future Hermes upgrade, the wrapped
call raises and this module swallows it (never break the gateway for a
descriptive-metadata feature) - but ``selftest.py``'s functional check
(not just an attribute-identity check) is what actually catches that at
service-start time via ExecStartPre, per the existing
hermes-axis-control-gateway.nix precedent.

Also writes a small host-local side channel (roadmap amendment #40,
metadata_attestation.py) keyed by this process's own pid, so the policy
endpoint can attest x_hermes_source via kernel-guaranteed peer-process
identity (/proc) rather than trusting the request body alone. Inlined
here (no import of hermes-policy-endpoint's code) because this file runs
inside Hermes's own Python process/venv, a different trust/dependency
boundary than the endpoint - the path below must match
metadata_attestation.SHARED_PEER_SOURCE_DIR exactly. Best-effort: a
failure here never breaks the gateway and never regresses below today's
already-floor-safe ASSERTED-only behavior.
"""

try:
    import json
    import os
    import time
    from contextvars import ContextVar
    from pathlib import Path

    import agent.aux_accounting as aux_accounting
    import agent.transports.chat_completions as chat_completions

    _hermes_source_ctx: ContextVar[str | None] = ContextVar(
        "hermes_workload_metadata_source", default=None
    )
    _hermes_session_id_ctx: ContextVar[str | None] = ContextVar(
        "hermes_workload_metadata_session_id", default=None
    )

    _PEER_SOURCE_DIR = Path.home() / ".hermes-policy" / "_peer-source"

    def _write_peer_source_side_channel(source):
        try:
            _PEER_SOURCE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = _PEER_SOURCE_DIR / f"{os.getpid()}.json"
            path.write_text(json.dumps({"source": source, "ts": time.time()}))
            path.chmod(0o600)
        except OSError:
            pass

    _orig_set_accounting_context = aux_accounting.set_accounting_context
    _orig_add_prompt_cache_key = chat_completions._add_prompt_cache_key

    def _patched_set_accounting_context(session_db, session_id):
        source = None
        try:
            if session_db is not None and session_id:
                session = session_db.get_session(session_id)
                if session:
                    source = session.get("source")
        except Exception:
            pass
        _hermes_source_ctx.set(source)
        _hermes_session_id_ctx.set(session_id or None)
        _write_peer_source_side_channel(source)
        return _orig_set_accounting_context(session_db, session_id)

    def _patched_add_prompt_cache_key(api_kwargs, *, messages, tools, supports_prompt_cache_key):
        source = _hermes_source_ctx.get()
        session_id = _hermes_session_id_ctx.get()
        if source or session_id:
            extra_body = api_kwargs.get("extra_body")
            if not isinstance(extra_body, dict):
                extra_body = {}
                api_kwargs["extra_body"] = extra_body
            if source:
                extra_body.setdefault("x_hermes_source", source)
            if session_id:
                extra_body.setdefault("litellm_session_id", session_id)
        return _orig_add_prompt_cache_key(
            api_kwargs,
            messages=messages,
            tools=tools,
            supports_prompt_cache_key=supports_prompt_cache_key,
        )

    aux_accounting.set_accounting_context = _patched_set_accounting_context
    chat_completions._add_prompt_cache_key = _patched_add_prompt_cache_key
except Exception:
    pass
