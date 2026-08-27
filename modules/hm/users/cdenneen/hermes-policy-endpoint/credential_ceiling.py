"""
Credential-bound route/tier ceilings (#41, pre-Phase-2 Bootstrap Gate
requirement).

Every production Eros virtual key already carries an administratively
configured model allowlist, enforced by LiteLLM itself (confirmed live,
BOOT-004: an ordinary key requesting tier4-frontier gets HTTP 403
key_model_access_denied before any provider call). This module mirrors
that same allowlist at the hermes-policy-endpoint admission layer too -
defense in depth, not a replacement - so a caller cannot widen its
effective route/tier ceiling merely by what it puts in the request body,
even in a hypothetical future where this endpoint's own key to Eros were
broader than the actor's intended ceiling.

ATTESTED, not ASSERTED: allowed_routes comes only from this instance's
static Nix-rendered config (mirroring the real LiteLLM key), never from
the request body - the same attestation boundary as trust_domain/agent/
workstream in endpoint.py.
"""

import re

_TIER_RE = re.compile(r"^tier(\d+)-")


def parse_tier(route: str) -> int | None:
    m = _TIER_RE.match(route)
    return int(m.group(1)) if m else None


def max_tier_of(allowed_routes) -> int | None:
    tiers = [t for t in (parse_tier(r) for r in allowed_routes) if t is not None]
    return max(tiers) if tiers else None


class RouteDenied(Exception):
    """Raised when a requested route is outside this actor's
    administratively bound ceiling. Callers must deny outright - there is
    no substitute/fallback route to silently use instead."""


def check_route_allowed(requested_route: str, allowed_routes) -> None:
    """Deny-by-default: an empty/missing/malformed/unrecognized route is
    denied exactly the same way a route that's merely outside the
    allowlist is - there is no permissive default for "couldn't tell what
    tier this is." Raises RouteDenied; never returns a substitute route."""
    if not requested_route or requested_route not in allowed_routes:
        raise RouteDenied(
            f"route {requested_route!r} is not in this credential's administratively "
            f"bound allowlist {sorted(allowed_routes)!r}"
        )
