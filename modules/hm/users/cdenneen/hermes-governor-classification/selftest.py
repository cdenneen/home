"""
Functional (not merely attribute-identity) self-test for the G-CONT
governor-denial classification patch. Run as ExecStartPre before the
gateway starts - if Hermes 0.20.0 upstream changes classify_api_error's
or _gateway_provider_error_reply's shape/behavior, this raises and the
gateway fails to start (fail closed) rather than silently reverting to
the retry-amplification / misleading-message bug this patch exists to
fix.

Exercises both patched call sites end-to-end with synthetic inputs, and
explicitly proves the patch is narrowly scoped: an ORDINARY 429 (no
budget-exceeded signature) must classify exactly as it did before this
patch existed.
"""

import agent.error_classifier as error_classifier
import gateway.run as gateway_run


class _FakeBudgetExceededError(Exception):
    status_code = 429

    def __str__(self):
        return (
            "litellm.BudgetExceededError: Budget has been exceeded! "
            "Key=ghost-alpha0 (sk-...ewhw) Current cost: 20.21, Max budget: 20.0"
        )


class _FakeOrdinaryRateLimitError(Exception):
    status_code = 429

    def __str__(self):
        return "RateLimitError: You have hit the rate limit, please retry later."


def main() -> None:
    budget_result = error_classifier.classify_api_error(_FakeBudgetExceededError())
    assert budget_result.retryable is False, (
        f"expected retryable=False for a governor budget denial, got {budget_result.retryable!r} "
        f"- G-CONT governor-classification patch is not taking effect as expected"
    )
    assert budget_result.should_rotate_credential is False, (
        "expected should_rotate_credential=False for a governor budget denial"
    )
    assert budget_result.should_fallback is False, (
        "expected should_fallback=False for a governor budget denial"
    )
    assert budget_result.error_context.get("gcont_governor_denial") is True, (
        "expected error_context['gcont_governor_denial']=True for a governor budget denial"
    )

    # Narrow-scope proof: an ordinary 429 (no budget-exceeded signature)
    # must be unaffected by this patch.
    ordinary_result = error_classifier.classify_api_error(_FakeOrdinaryRateLimitError())
    assert ordinary_result.retryable is True, (
        f"expected retryable=True for an ORDINARY rate-limit error (patch must not widen "
        f"beyond the budget-exceeded shape), got {ordinary_result.retryable!r}"
    )
    assert not ordinary_result.error_context.get("gcont_governor_denial"), (
        "ordinary rate-limit error must not be tagged as a governor denial"
    )

    budget_reply = gateway_run._gateway_provider_error_reply(str(_FakeBudgetExceededError()))
    assert "spend/governor policy" in budget_reply, (
        f"expected a governor-policy-specific reply for a budget denial, got {budget_reply!r} "
        f"- the misleading 'model provider failed after retries' message is still being used"
    )

    generic_reply = gateway_run._gateway_provider_error_reply("some unrelated transport error")
    assert "model provider failed after retries" in generic_reply, (
        f"expected the ORIGINAL generic fallback reply for unrelated errors (patch must not "
        f"widen beyond the budget-exceeded shape), got {generic_reply!r}"
    )

    print("gcont-governor-classification selftest: OK")


if __name__ == "__main__":
    main()
