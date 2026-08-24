"""Shared fail-closed production CLI bootstrap."""

from __future__ import annotations

import sys
from collections.abc import Callable

from .trust import TrustAnchor, TrustError, load_trust_anchor


def run_without_options(action: Callable[[TrustAnchor], None]) -> int:
    """Reject every option/operand and load the fixed anchor before the action."""
    if len(sys.argv) != 1:
        print(
            "phase-b tools accept no command, path, time, or trust options",
            file=sys.stderr,
        )
        return 64
    try:
        anchor = load_trust_anchor()
        action(anchor)
    except (OSError, RuntimeError, TrustError) as exc:
        print(f"phase-b: safe failure: {exc}", file=sys.stderr)
        return 1
    return 0
