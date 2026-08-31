"""
G-CONT item 1: classify LiteLLM/OmniRoute virtual-key economic-governor
denials (BudgetExceededError, surfaced as HTTP 429 by LiteLLM's own
pre-dispatch auth check) as a deterministic, non-retryable policy
decision - never as a provider/infrastructure failure.

Root cause (2026-08-29 incident, traced via LiteLLM_SpendLogs.metadata):
Hermes's agent/error_classifier.py has no special case for this shape, so
it falls into the generic 429 -> FailoverReason.rate_limit branch with
retryable=True, should_rotate_credential=True, should_fallback=True. That
causes three real problems: (1) pointless retries against a fixed budget
ceiling that can never succeed, (2) credential rotation that can't help
either, (3) the final user-facing message becomes the generic "The model
provider failed after retries" (gateway/run.py's
_gateway_provider_error_reply catch-all) - actively misleading, since this
was never a provider or infrastructure failure at all.

This module patches two call sites, using the exact wrapped-function
pattern already established by hermes-workload-metadata/sitecustomize.py
for the same reason: Hermes is NousResearch/hermes-agent (third-party,
pinned by revision) - it is patched via a fail-open wrapper, never
forked/edited in place. If either target function's shape changes in a
future Hermes upgrade, the wrapped call falls through to the untouched
original behavior (never breaks the gateway) - selftest.py's functional
check is what actually catches that at service-start time via
ExecStartPre, per the existing precedent.

Explicitly bounded (per instruction, not a retry-framework rewrite): this
only recognizes ONE specific, deterministic, LiteLLM-authored error
shape (BudgetExceededError). It does not touch classification for any
other error class (auth, invalid-request, content-policy, etc.) - those
already have dedicated, correct handling in error_classifier.py.
"""

try:
    import re

    import agent.error_classifier as error_classifier
    import gateway.run as gateway_run

    _BUDGET_EXCEEDED_RE = re.compile(r"budget has been exceeded", re.IGNORECASE)

    _orig_classify_api_error = error_classifier.classify_api_error

    def _is_governor_budget_denial(error) -> bool:
        try:
            status_code = error_classifier._extract_status_code(error)
        except Exception:
            status_code = getattr(error, "status_code", None)
        if status_code != 429:
            return False
        try:
            body = error_classifier._extract_error_body(error)
        except Exception:
            body = None
        combined = str(error)
        if isinstance(body, dict):
            err_obj = body.get("error", {})
            if isinstance(err_obj, dict):
                combined += " " + str(err_obj.get("message") or "")
                combined += " " + str(err_obj.get("error_class") or "")
        try:
            error_code = error_classifier._extract_error_code(body)
        except Exception:
            error_code = ""
        return bool(
            _BUDGET_EXCEEDED_RE.search(combined)
            or "budgetexceedederror" in str(error_code).lower()
            or "budgetexceedederror" in combined.lower()
        )

    def _patched_classify_api_error(error, **kwargs):
        result = _orig_classify_api_error(error, **kwargs)
        try:
            if _is_governor_budget_denial(error):
                result.retryable = False
                result.should_rotate_credential = False
                result.should_fallback = False
                result.error_context = dict(result.error_context)
                result.error_context["gcont_governor_denial"] = True
        except Exception:
            # Never let this classification enhancement itself break
            # classification - fall through to the original result.
            pass
        return result

    error_classifier.classify_api_error = _patched_classify_api_error

    _orig_gateway_provider_error_reply = gateway_run._gateway_provider_error_reply

    def _patched_gateway_provider_error_reply(text: str) -> str:
        if _BUDGET_EXCEEDED_RE.search(text):
            return (
                "⚠️ This request was denied by spend/governor policy, "
                "not a provider or infrastructure failure. The configured "
                "budget for this route has been reached; raw details are in "
                "the gateway logs."
            )
        return _orig_gateway_provider_error_reply(text)

    gateway_run._gateway_provider_error_reply = _patched_gateway_provider_error_reply
except Exception:
    pass
