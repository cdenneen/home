"""
Bootstrap deterministic economic-state governor.

Fixed seed thresholds only - no adaptive learning, no hysteresis beyond
what's needed to avoid pure threshold-boundary flapping on the burn
windows themselves. Real numbers here are BOOTSTRAP-SCALE PLACEHOLDERS
(matching the $20/30d test budgets already provisioned on the Eros
virtual keys) pending Product-Owner-approved production values from
Phase 1 baseline forecasting - see 00-program-spec.md "Decisions
required" / execution-contract.md 14.

Capability floors are look-up only here: this module NEVER lowers a
route below the workload's declared minimum_capability_tier. It only
ever decides whether/how often a request at its already-declared tier
may be admitted right now.

Budget scope: `compute_state` operates per-actor (one Eros virtual key,
one PolicyEndpoint instance, one local SQLite ledger) - by construction,
not per trust_domain. There is one global monthly objective spanning
Work and Personal; trust_domain provides isolation and attribution, not
an independent top-level budget. Enforcement targets the lowest
responsible actor/workstream before degrading unrelated domains -
per-actor scoping here already does that; it does not itself need a
trust_domain concept. Rolling every actor's local spend_events into one
global ledger/forecast against the overall monthly objective is not
implemented by this host-local module - Eros's LiteLLM_SpendLogs is
already the authoritative aggregate (confirmed in Phase 1 attribution
work) and is the natural home for that rollup. Shared economic
governance must never weaken Work/Personal isolation elsewhere: this
endpoint only ever sees route/model/tool-name/cost/token metadata, never
prompt content, memory, retrieved knowledge, or another actor's
credential - each actor's Eros virtual key and local state DB are
already fully separate per instance.
"""

import calendar
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class EconomicState(str, Enum):
    NORMAL = "NORMAL"
    CONSERVE = "CONSERVE"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    BREAK_GLASS = "BREAK_GLASS"


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class Admission(str, Enum):
    ADMIT = "admit"
    QUEUE = "queue"
    DENY = "deny"


# --- Bootstrap seed thresholds (provisional - see module docstring) --------

@dataclass(frozen=True)
class BootstrapThresholds:
    # Fraction of the actor's monthly budget at which projected-EOM burn
    # triggers each state. Deliberately conservative/small for a
    # bootstrap-scale $20/30d test budget; NOT a production recommendation.
    conserve_at_projected_fraction: float = 0.60
    degraded_at_projected_fraction: float = 0.85
    critical_at_projected_fraction: float = 1.00
    break_glass_at_projected_fraction: float = 1.50
    # Also escalate on a burn-rate spike even if projected-EOM still looks
    # healthy (00-program-spec.md governor inputs: "1h/6h/24h burn").
    spike_multiple_of_expected_1h: float = 4.0


DEFAULT_THRESHOLDS = BootstrapThresholds()


def days_in_month(ts: float) -> int:
    dt = datetime.fromtimestamp(ts, timezone.utc)
    return calendar.monthrange(dt.year, dt.month)[1]


def day_of_month(ts: float) -> int:
    return datetime.fromtimestamp(ts, timezone.utc).day


def project_eom(mtd_cost: float, now_ts: float) -> float:
    """Simplest deterministic projection: linear extrapolation of MTD
    spend across the remaining days. Intentionally not adaptive."""
    d = day_of_month(now_ts)
    dim = days_in_month(now_ts)
    if d <= 0:
        return mtd_cost
    return mtd_cost * (dim / d)


def compute_state(
    *,
    monthly_budget: float,
    mtd_cost: float,
    burn_1h: float,
    expected_burn_1h: float,
    now_ts: float | None = None,
    thresholds: BootstrapThresholds = DEFAULT_THRESHOLDS,
) -> tuple[EconomicState, str]:
    now_ts = now_ts or time.time()
    projected = project_eom(mtd_cost, now_ts)
    fraction = (projected / monthly_budget) if monthly_budget > 0 else float("inf")

    if expected_burn_1h > 0 and burn_1h >= expected_burn_1h * thresholds.spike_multiple_of_expected_1h:
        return (
            EconomicState.CRITICAL,
            f"1h burn {burn_1h:.4f} >= {thresholds.spike_multiple_of_expected_1h}x "
            f"expected {expected_burn_1h:.4f} (anomaly, independent of projected EOM)",
        )

    if fraction >= thresholds.break_glass_at_projected_fraction:
        return EconomicState.BREAK_GLASS, f"projected EOM {projected:.2f} >= {thresholds.break_glass_at_projected_fraction}x budget {monthly_budget:.2f}"
    if fraction >= thresholds.critical_at_projected_fraction:
        return EconomicState.CRITICAL, f"projected EOM {projected:.2f} >= budget {monthly_budget:.2f}"
    if fraction >= thresholds.degraded_at_projected_fraction:
        return EconomicState.DEGRADED, f"projected EOM {projected:.2f} >= {thresholds.degraded_at_projected_fraction}x budget {monthly_budget:.2f}"
    if fraction >= thresholds.conserve_at_projected_fraction:
        return EconomicState.CONSERVE, f"projected EOM {projected:.2f} >= {thresholds.conserve_at_projected_fraction}x budget {monthly_budget:.2f}"
    return EconomicState.NORMAL, f"projected EOM {projected:.2f} within budget {monthly_budget:.2f}"


# --- Admission rule: state x priority -> decision --------------------------
# 00-program-spec.md "Operating-state contract" / execution-contract.md 8:
# state changes frequency/concurrency/queueing, never the capability floor.

_ADMISSION_TABLE: dict[tuple[EconomicState, Priority], Admission] = {
    (EconomicState.NORMAL, Priority.P0): Admission.ADMIT,
    (EconomicState.NORMAL, Priority.P1): Admission.ADMIT,
    (EconomicState.NORMAL, Priority.P2): Admission.ADMIT,
    (EconomicState.NORMAL, Priority.P3): Admission.ADMIT,

    (EconomicState.CONSERVE, Priority.P0): Admission.ADMIT,
    (EconomicState.CONSERVE, Priority.P1): Admission.ADMIT,
    (EconomicState.CONSERVE, Priority.P2): Admission.ADMIT,   # throttled by caller cadence, not denied here
    (EconomicState.CONSERVE, Priority.P3): Admission.ADMIT,

    (EconomicState.DEGRADED, Priority.P0): Admission.ADMIT,
    (EconomicState.DEGRADED, Priority.P1): Admission.ADMIT,
    (EconomicState.DEGRADED, Priority.P2): Admission.QUEUE,
    (EconomicState.DEGRADED, Priority.P3): Admission.DENY,

    (EconomicState.CRITICAL, Priority.P0): Admission.ADMIT,
    (EconomicState.CRITICAL, Priority.P1): Admission.ADMIT,   # "essential" P1 only in the fuller contract; bootstrap treats all P1 as essential absent finer signal
    (EconomicState.CRITICAL, Priority.P2): Admission.DENY,
    (EconomicState.CRITICAL, Priority.P3): Admission.DENY,

    (EconomicState.BREAK_GLASS, Priority.P0): Admission.ADMIT,  # via continuity path only, not normal keys - enforced by caller
    (EconomicState.BREAK_GLASS, Priority.P1): Admission.DENY,
    (EconomicState.BREAK_GLASS, Priority.P2): Admission.DENY,
    (EconomicState.BREAK_GLASS, Priority.P3): Admission.DENY,
}


def admission_decision(state: EconomicState, priority: Priority) -> Admission:
    return _ADMISSION_TABLE[(state, priority)]


# --- Unknown-workload least-privilege default -------------------------------
# execution-contract.md 7 "Unknown-workload default": unknown means least
# privilege, not merely lower priority.

@dataclass(frozen=True)
class WorkloadAuthority:
    priority: Priority
    mutation_allowed: bool
    continuity_class: str
    t4_allowed: bool
    minimum_capability: str


# continuity_class="automatic-read-only" here does NOT conflict with
# action_classification.py's unknown-TOOL ceiling (manual-break-glass) -
# PO decision, roadmap-amendment-unified-topology-workstreams-global-
# economics-developer-clients.md #37, 2026-08-25: unknown *purpose*
# (an uncharacterized workload in the abstract) does not imply dangerous
# *effect*; unknown effect (a specific tool call whose mutation status is
# unverified) does. This default answers the former question; that
# module's EFFECT_CLASS_CEILING[UNKNOWN] answers the latter. Do not
# unify these into one value - see action_classification.py's module
# docstring and test_endpoint.py's TestUnknownPurposeVsUnknownEffect.
UNKNOWN_WORKLOAD_DEFAULT = WorkloadAuthority(
    priority=Priority.P2,
    mutation_allowed=False,
    continuity_class="automatic-read-only",
    t4_allowed=False,
    minimum_capability="tier2-general",  # conservative floor, never tier0
)
