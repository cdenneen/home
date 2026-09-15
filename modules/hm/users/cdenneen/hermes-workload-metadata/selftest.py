"""
Functional (not merely attribute-identity) self-test for the
hermes-workload-metadata sitecustomize patch. Run as ExecStartPre before
the gateway starts, mirroring hermes-axis-control-gateway.nix's existing
``hermesGatewayBypassCheck`` precedent - if Hermes 0.20.0 upstream changes
either patched function's signature or behavior, this raises and the
gateway fails to start (fail closed) rather than silently losing the
workload-source signal.

This exercises the actual patched call chain end-to-end with synthetic
inputs, rather than only checking that an attribute got reassigned - a
signature change in either hook would raise a TypeError here, which a
same-identity check would not catch.
"""

import json
import os
import time
from pathlib import Path

from agent import aux_accounting
from agent.transports import chat_completions

_PEER_SOURCE_DIR = Path.home() / ".hermes-policy" / "_peer-source"


class _FakeSessionDB:
    def get_session(self, session_id):
        assert session_id == "selftest-session-id"
        return {"source": "selftest-source"}


def main() -> None:
    side_channel_path = _PEER_SOURCE_DIR / f"{os.getpid()}.json"
    side_channel_path.unlink(missing_ok=True)

    aux_accounting.set_accounting_context(_FakeSessionDB(), "selftest-session-id")

    api_kwargs: dict = {}
    chat_completions._add_prompt_cache_key(
        api_kwargs,
        messages=[],
        tools=None,
        supports_prompt_cache_key=False,  # exercises our injection independent of the original's own gate
        session_id="selftest-session-id",
        cache_scope_id="selftest-cache-scope",
    )

    extra_body = api_kwargs.get("extra_body")
    assert isinstance(extra_body, dict), f"expected extra_body dict, got {extra_body!r}"
    assert extra_body.get("x_hermes_source") == "selftest-source", (
        f"expected x_hermes_source='selftest-source', got {extra_body.get('x_hermes_source')!r} "
        f"- hermes-workload-metadata sitecustomize patch is not taking effect as expected"
    )
    assert extra_body.get("litellm_session_id") == "selftest-session-id", (
        f"expected litellm_session_id='selftest-session-id', got "
        f"{extra_body.get('litellm_session_id')!r} - session_id propagation is not taking "
        f"effect as expected"
    )
    assert extra_body.get("metadata", {}).get("tags") == [
        f"consumer:{os.environ.get('EROS_CONSUMER', 'hermes')}",
        f"trust_domain:{os.environ.get('EROS_TRUST_DOMAIN', 'shared')}",
        "workload:selftest-source",
    ], f"unexpected spend tags: {extra_body.get('metadata', {}).get('tags')!r}"
    assert api_kwargs.get("extra_headers") == {
        "x-eros-consumer": os.environ.get("EROS_CONSUMER", "hermes"),
        "x-eros-trust-domain": os.environ.get("EROS_TRUST_DOMAIN", "shared"),
        "x-eros-workload": "selftest-source",
        "x-eros-session": "selftest-session-id",
    }, f"unexpected spend headers: {api_kwargs.get('extra_headers')!r}"

    cache_kwargs: dict = {}
    chat_completions._add_prompt_cache_key(
        cache_kwargs,
        messages=[{"role": "system", "content": "stable cache prefix"}],
        tools=None,
        supports_prompt_cache_key=True,
        session_id="selftest-session-id",
        cache_scope_id="selftest-cache-scope",
    )
    cache_key = cache_kwargs.get("prompt_cache_key")
    assert isinstance(cache_key, str) and cache_key, "prompt cache key was not added"

    # #40: the peer-process attestation side channel must actually be
    # written, keyed by this process's own pid - a functional check, not
    # just checking the extra_body injection above.
    assert side_channel_path.exists(), (
        f"expected {side_channel_path} to exist after set_accounting_context - "
        f"the #40 peer-attestation side channel is not being written"
    )
    side_channel_data = json.loads(side_channel_path.read_text())
    assert side_channel_data.get("source") == "selftest-source", (
        f"side channel recorded {side_channel_data.get('source')!r}, expected 'selftest-source'"
    )
    assert time.time() - side_channel_data.get("ts", 0) < 10, (
        "side channel timestamp is stale"
    )
    side_channel_path.unlink(missing_ok=True)

    aux_accounting.reset_accounting_context(
        aux_accounting.set_accounting_context(None, None)
    )
    print("hermes-workload-metadata selftest: OK")


if __name__ == "__main__":
    main()
