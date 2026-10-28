"""Strip Anthropic server-side tools from requests bound for Bedrock.

Anthropic's provider-executed tools (web search, web fetch, code execution) are
implemented by Anthropic's own API. Bedrock's invoke API has no equivalent, so a
request that carries one fails closed:

    400 {"message": "tool type 'web_search_20250305' is not supported for this model"}

That 400 is not retryable, and eros deliberately has no cross-tier fallback, so
the turn dies and the consumer reports a bare "the model provider failed after
retries" with nothing actionable in it. Observed 2026-09-17: 592 such failures
in two hours across claude-opus-5 and claude-sonnet-5, reproducible with a
two-line curl.

This hook drops the offending tool entries before the request leaves the proxy,
but only for model groups that actually resolve to a bedrock/* deployment, and
logs every drop so the lost capability is visible instead of silent. Consumers
that want real web search should use the MCP gateway's search tools, which work
regardless of the upstream provider.

Exception: the "claude-desktop" workload (identified via the x-eros-workload
request header) sends web_search_* and web_fetch_* as native Anthropic tools and
expects the upstream to execute them. Claude Desktop 4.29.0+ no longer has a
code path to fall back to MCP tools when native tools are present, so stripping
them leaves the session with no web access at all. This workload is therefore
exempted from stripping — Bedrock will 400 on those tool types regardless, so
the correct long-term fix is routing claude-desktop to an Anthropic-direct
provider. Until that route exists, we pass the tools through and let the error
surface rather than silently removing capability.

Three deliberate choices:

- It fails open. Any unexpected error returns None and the request proceeds
  untouched. A guard that can 500 the gateway is worse than the bug it prevents.
- Dropping every tool also drops tool_choice, because a tool_choice naming a
  tool that is no longer present is itself a 400.
- The claude-desktop exemption is workload-scoped, not key-scoped, so a future
  key rotation does not silently re-enable stripping for that client.
"""

from typing import Any, List, Optional, Tuple

try:  # inside the proxy
    from litellm._logging import verbose_proxy_logger as _log
    from litellm.integrations.custom_logger import CustomLogger
except Exception:  # running the self-check standalone
    import logging

    _log = logging.getLogger(__name__)
    CustomLogger = object  # type: ignore[assignment,misc]

# Provider-executed tool types. Client-executed types that Bedrock does accept
# (bash_*, text_editor_*, computer_*) are deliberately absent - stripping those
# would break working setups.
SERVER_SIDE_TOOL_PREFIXES = ("web_search_", "web_fetch_", "code_execution_")

# Workloads that send native Anthropic server-side tools and expect the upstream
# to execute them. Stripping tools for these workloads removes capability rather
# than preventing errors, so they are exempted from the guard. The long-term fix
# for each exempted workload is routing it to an Anthropic-direct provider.
EXEMPTED_WORKLOADS = frozenset({"claude-desktop"})


def is_server_side_tool(tool: Any) -> bool:
    """True for an Anthropic tool entry that the provider, not the client, runs."""
    if not isinstance(tool, dict):
        return False
    tool_type = tool.get("type")
    return isinstance(tool_type, str) and tool_type.startswith(SERVER_SIDE_TOOL_PREFIXES)


def split_tools(tools: List[Any]) -> Tuple[List[Any], List[Any]]:
    """Partition a tools list into (kept, dropped)."""
    kept: List[Any] = []
    dropped: List[Any] = []
    for tool in tools:
        (dropped if is_server_side_tool(tool) else kept).append(tool)
    return kept, dropped


def targets_bedrock(model: Any, model_list: Any) -> bool:
    """True if any deployment behind this model group is a bedrock/* route.

    Checked with `any` rather than `all`: in a mixed group the router may still
    pick the Bedrock deployment, and a hard 400 is worse than a dropped tool.
    """
    if not isinstance(model, str) or not model:
        return False
    if not isinstance(model_list, list):
        return False
    return any(
        isinstance(entry, dict)
        and entry.get("model_name") == model
        and str((entry.get("litellm_params") or {}).get("model", "")).startswith("bedrock/")
        for entry in model_list
    )


def get_workload(data: dict) -> str:
    """Extract the x-eros-workload header value from the request metadata."""
    try:
        headers = (data.get("metadata") or {}).get("headers") or {}
        # Headers may be a dict or a starlette Headers object; .get() works for both.
        workload = headers.get("x-eros-workload") or headers.get("X-Eros-Workload") or ""
        return str(workload).strip().lower()
    except Exception:
        return ""


def candidate_model_lists() -> List[Any]:
    """Every place the proxy keeps its resolved model config.

    Both are consulted because which one is populated depends on how the proxy
    was started; they are module-level globals set during startup, so they are
    only visible from inside the server process (a standalone `python -c` in the
    same container sees None - not a failure).
    """
    try:
        from litellm.proxy import proxy_server
    except Exception:
        return []

    lists: List[Any] = []
    router = getattr(proxy_server, "llm_router", None)
    router_list = getattr(router, "model_list", None) if router is not None else None
    if isinstance(router_list, list):
        lists.append(router_list)
    proxy_list = getattr(proxy_server, "llm_model_list", None)
    if isinstance(proxy_list, list):
        lists.append(proxy_list)
    return lists


class BedrockServerSideToolGuard(CustomLogger):  # type: ignore[misc,valid-type]
    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: dict,
        call_type: str,
    ) -> Optional[dict]:
        try:
            if not isinstance(data, dict):
                return None
            tools = data.get("tools")
            if not isinstance(tools, list) or not tools:
                return None

            # Exempt workloads that rely on native Anthropic server-side tools.
            workload = get_workload(data)
            if workload in EXEMPTED_WORKLOADS:
                _log.info(
                    "eros bedrock tool guard: skipping strip for exempted workload %r "
                    "(server-side tools present but kept for this consumer).",
                    workload,
                )
                return None

            model = data.get("model")
            if not any(targets_bedrock(model, ml) for ml in candidate_model_lists()):
                return None

            kept, dropped = split_tools(tools)
            if not dropped:
                return None

            dropped_types = sorted({str(t.get("type")) for t in dropped})
            _log.warning(
                "eros bedrock tool guard: dropped %d unsupported server-side tool(s) "
                "(%s) from a %s request to %r. Bedrock rejects provider-executed "
                "tools; use the MCP gateway's search tools instead.",
                len(dropped),
                ", ".join(dropped_types),
                call_type,
                data.get("model"),
            )

            patched = dict(data)
            if kept:
                patched["tools"] = kept
            else:
                patched.pop("tools", None)
                patched.pop("tool_choice", None)
            return patched
        except Exception as exc:  # fail open, always
            _log.warning("eros bedrock tool guard: passing request through unchanged (%s)", exc)
            return None


guard = BedrockServerSideToolGuard()


def _self_check() -> None:
    web_search = {"type": "web_search_20250305", "name": "web_search"}
    code_exec = {"type": "code_execution_20250522", "name": "code_execution"}
    normal = {"name": "get_weather", "input_schema": {"type": "object"}}
    bash = {"type": "bash_20250124", "name": "bash"}

    assert is_server_side_tool(web_search)
    assert is_server_side_tool(code_exec)
    assert not is_server_side_tool(normal)
    assert not is_server_side_tool(bash), "client-executed tools must survive"
    assert not is_server_side_tool("not-a-dict")
    assert not is_server_side_tool({"type": None})

    assert split_tools([web_search, normal, code_exec]) == ([normal], [web_search, code_exec])
    assert split_tools([normal, bash]) == ([normal, bash], [])
    assert split_tools([]) == ([], [])

    bedrock_list = [
        {"model_name": "claude-opus-5", "litellm_params": {"model": "bedrock/us.anthropic.claude-opus-5"}},
        {"model_name": "quality", "litellm_params": {"model": "openai/gpt-5.4"}},
    ]
    assert targets_bedrock("claude-opus-5", bedrock_list)
    assert not targets_bedrock("quality", bedrock_list)
    assert not targets_bedrock("nonexistent", bedrock_list)
    assert not targets_bedrock(None, bedrock_list)
    assert not targets_bedrock("claude-opus-5", None)
    # a mixed group still counts, because the router may pick the Bedrock leg
    mixed = bedrock_list + [{"model_name": "quality", "litellm_params": {"model": "bedrock/us.anthropic.x"}}]
    assert targets_bedrock("quality", mixed)

    # workload exemption
    assert get_workload({"metadata": {"headers": {"x-eros-workload": "claude-desktop"}}}) == "claude-desktop"
    assert get_workload({"metadata": {"headers": {"x-eros-workload": "claude-code"}}}) == "claude-code"
    assert get_workload({}) == ""
    assert "claude-desktop" in EXEMPTED_WORKLOADS
    assert "claude-code" not in EXEMPTED_WORKLOADS

    print("bedrock_tool_guard self-check ok")


if __name__ == "__main__":
    _self_check()
