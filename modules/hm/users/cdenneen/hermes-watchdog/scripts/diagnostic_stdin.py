#!/usr/bin/env python3
import os
import sys

from agent.auxiliary_client import (  # pyright: ignore[reportMissingImports]
    call_llm,
    extract_content_or_reasoning,
)

PINNED_HERMES_REVISION = "f5be9236e00ddf2f2a412697f267078fc4ee068e"


def main() -> int:
    revision = os.environ.get("AXIS_WATCHDOG_HERMES_REVISION")
    if revision != PINNED_HERMES_REVISION:
        raise RuntimeError("watchdog diagnostic must run under the pinned Hermes revision")
    prompt = sys.stdin.read()
    if not prompt.strip():
        raise ValueError("watchdog diagnostic prompt is empty")
    response = call_llm(
        task="axis_watchdog_diagnostic",
        model="gpt-5.4",
        provider="openai-api",
        messages=[
            {
                "role": "system",
                "content": (
                    "Read-only AXIS watchdog diagnosis. No tools are available. "
                    "Do not propose product dispatch or repository mutation."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        tools=[],
        max_tokens=700,
        timeout=90,
        reasoning_config={"effort": "medium"},
    )
    output = (extract_content_or_reasoning(response) or "").strip()
    if not output:
        raise RuntimeError("watchdog diagnostic returned empty output")
    sys.stdout.write(output[:1200] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
