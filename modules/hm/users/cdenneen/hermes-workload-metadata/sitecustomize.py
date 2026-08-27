"""
Propagate Hermes's already-known per-turn workload source (slack / cron /
kanban / subagent / cli) into the outbound request body as
``x_hermes_source``, so the host-local policy endpoint can derive
continuity behavior deterministically instead of inferring it.

Descriptive only: this module never decides continuity/economic policy -
it just makes an already-known fact visible on the wire. All policy
derivation lives in hermes-policy-endpoint/governor.py.

Two hook points, both already used by Hermes 0.20.0 for an analogous
purpose (see agent/portal_tags.py's Nous Portal product-attribution tags,
which use the identical ambient-ContextVar-set-at-turn-entry shape):

1. ``agent.aux_accounting.set_accounting_context(session_db, session_id)``
   - called once per turn at turn entry (confirmed call site:
   ``run_agent.py``). We wrap it to also look up the session's ``source``
   column (``session_db.get_session(session_id)["source"]``) and publish
   it on our own ContextVar.

2. ``agent.transports.chat_completions._add_prompt_cache_key(...)`` -
   called unconditionally on every Chat Completions request (confirmed:
   two call sites in chat_completions.py, neither gated by an if-check
   before the call - the flag it checks is internal to the function).
   We wrap it to also set ``extra_body["x_hermes_source"]`` from our
   ContextVar before delegating to the original.

If either hook's shape changes in a future Hermes upgrade, the wrapped
call raises and this module swallows it (never break the gateway for a
descriptive-metadata feature) - but ``selftest.py``'s functional check
(not just an attribute-identity check) is what actually catches that at
service-start time via ExecStartPre, per the existing
hermes-axis-control-gateway.nix precedent.
"""

try:
    from contextvars import ContextVar

    import agent.aux_accounting as aux_accounting
    import agent.transports.chat_completions as chat_completions

    _hermes_source_ctx: ContextVar[str | None] = ContextVar(
        "hermes_workload_metadata_source", default=None
    )

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
        return _orig_set_accounting_context(session_db, session_id)

    def _patched_add_prompt_cache_key(api_kwargs, *, messages, tools, supports_prompt_cache_key):
        source = _hermes_source_ctx.get()
        if source:
            extra_body = api_kwargs.get("extra_body")
            if not isinstance(extra_body, dict):
                extra_body = {}
                api_kwargs["extra_body"] = extra_body
            extra_body.setdefault("x_hermes_source", source)
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
