#!/usr/bin/env python3
"""
Eros weekly LLM cost alert.

Queries the LiteLLM postgres database for the current calendar-week spend
(Monday 00:00 CDT through now) and emits a structured alert if the spend
exceeds the WARNING or CRITICAL thresholds.

Exit codes:
  0  spend is under warning threshold (OK)
  1  spend is at or above warning threshold (WARN or CRITICAL)

Environment:
  EROS_LITELLM_DSN  libpq-style DSN for the litellm database
                    default: postgresql:///litellm?host=/run/postgresql
  EROS_COST_WEEKLY_WARN_USD    warning threshold in USD (default 1500)
  EROS_COST_WEEKLY_CRITICAL_USD  critical threshold in USD (default 2000)
  EROS_ALERT_STATE_PATH  path to persistent alert state JSON file
                          default: /var/lib/eros-context/cost-alert-state.json

The script is designed to run from a systemd oneshot service on a 1-hour
cadence (identical to eros-spend-report).  Alert deduplication: a CRITICAL
or WARN alert fires at most once per calendar day per level.  Clearing fires
once when spend drops below the warning threshold after a prior alert.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    print("ERROR: psycopg not available", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DSN = os.environ.get(
    "EROS_LITELLM_DSN", "postgresql:///litellm?host=/run/postgresql"
)
WARN_USD = float(os.environ.get("EROS_COST_WEEKLY_WARN_USD", "1500"))
CRITICAL_USD = float(os.environ.get("EROS_COST_WEEKLY_CRITICAL_USD", "2000"))
STATE_PATH = Path(
    os.environ.get(
        "EROS_ALERT_STATE_PATH",
        "/var/lib/eros-context/cost-alert-state.json",
    )
)
TZ_NAME = "America/Chicago"  # CDT/CST — week boundary is Monday 00:00 local

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def query_weekly_spend(dsn: str) -> tuple[float, int, float]:
    """
    Return (current_week_spend, request_count, last_week_spend).

    Uses the CDT week boundary (Monday 00:00 CDT / UTC-5 in winter, UTC-6 in
    summer; we use date_trunc with AT TIME ZONE rather than hard-coding the
    offset so daylight-saving transitions are handled correctly).
    """
    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                  SUM(spend)   AS total_spend,
                  COUNT(*)     AS num_requests
                FROM "LiteLLM_SpendLogs"
                WHERE "startTime" AT TIME ZONE 'America/Chicago'
                      >= DATE_TRUNC('week', NOW() AT TIME ZONE 'America/Chicago')
                """
            )
            row = cur.fetchone()
            current_spend = float(row["total_spend"] or 0)
            request_count = int(row["num_requests"] or 0)

            # Last full week (for context)
            cur.execute(
                """
                SELECT COALESCE(SUM(spend), 0) AS total_spend
                FROM "LiteLLM_SpendLogs"
                WHERE "startTime" AT TIME ZONE 'America/Chicago'
                      >= DATE_TRUNC('week', NOW() AT TIME ZONE 'America/Chicago')
                           - INTERVAL '7 days'
                  AND "startTime" AT TIME ZONE 'America/Chicago'
                      <  DATE_TRUNC('week', NOW() AT TIME ZONE 'America/Chicago')
                """
            )
            last_row = cur.fetchone()
            last_spend = float(last_row["total_spend"] or 0)

    return current_spend, request_count, last_spend


def determine_level(spend: float) -> str:
    if spend >= CRITICAL_USD:
        return "critical"
    if spend >= WARN_USD:
        return "warn"
    return "ok"


def should_alert(level: str, state: dict, today: str) -> bool:
    """
    Return True if we should emit an alert for this level today.
    Deduplicates: fires at most once per (level, day).
    Also fires when transitioning from a higher level back to ok.
    """
    if level == "ok":
        # Fire a clear notice only if the previous alert was warn/critical
        prev = state.get("last_alert_level", "ok")
        return prev in ("warn", "critical")
    # For warn/critical: fire if we haven't fired this level today
    last_fired_day = state.get(f"last_{level}_day")
    return last_fired_day != today


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    now_utc = datetime.now(timezone.utc)
    today = date.today().isoformat()

    try:
        current_spend, request_count, last_spend = query_weekly_spend(DSN)
    except Exception as exc:
        print(f"ERROR querying LiteLLM database: {exc}", file=sys.stderr)
        sys.exit(1)

    level = determine_level(current_spend)
    state = load_state()

    report = {
        "generated_at": now_utc.isoformat(),
        "current_week_spend_usd": round(current_spend, 4),
        "last_week_spend_usd": round(last_spend, 4),
        "request_count_this_week": request_count,
        "level": level,
        "warn_threshold_usd": WARN_USD,
        "critical_threshold_usd": CRITICAL_USD,
    }

    fire = should_alert(level, state, today)

    # Always print the status line to stdout (captured by journald)
    print(json.dumps(report))

    if fire:
        lines = [
            f"EROS COST ALERT [{level.upper()}]",
            f"  This-week spend : ${current_spend:,.2f}",
            f"  Last-week spend : ${last_spend:,.2f}",
            f"  Requests (week) : {request_count:,}",
            f"  Warn threshold  : ${WARN_USD:,.0f}/week",
            f"  Critical limit  : ${CRITICAL_USD:,.0f}/week",
            f"  Generated       : {now_utc.strftime('%Y-%m-%d %H:%M UTC')}",
        ]
        if level == "ok":
            lines[0] = "EROS COST ALERT [CLEARED]"
            lines.append("  Spend is now below the warning threshold.")
        print("\n".join(lines), file=sys.stderr)

        # Update state
        if level != "ok":
            state[f"last_{level}_day"] = today
        state["last_alert_level"] = level
        save_state(state)
        return 1

    state["last_alert_level"] = level
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
