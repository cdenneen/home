import fcntl
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema_registry import read_record, validate_record


ATTEMPT_SCHEMA_ID = "axis.external-development-supervisor.model-attempt"

# Worker accounting event contract (task AX-M4/#59): every model-attempt
# record's cost must be classified into exactly one of these states, never
# left to imply zero cost by omission.
#
# Hermes's own hermes_cli.oneshot usage report carries a coarser
# CostStatus of "actual" | "estimated" | "included" | "unknown" (see
# hermes_agent's agent/usage_pricing.py). The three states below that map
# from it are DERIVED per-attempt, straight from that report:
#   actual    -> KNOWN_COST                     (real, metered provider cost)
#   estimated -> RECONSTRUCTED_COST              (priced from a pricing table,
#                                                  not a direct provider meter)
#   included  -> SUBSCRIPTION_NO_INCREMENTAL_METER (bundled in a subscription;
#                                                  no incremental per-call cost)
#   unknown   -> UNKNOWN_COST                    (Hermes itself couldn't price it)
#
# UNAVAILABLE_USAGE is not a Hermes cost_status at all - it is what this
# ledger assigns when Hermes never got the chance to report ANYTHING (the
# subprocess was killed before writing --usage-file, or the report was
# unreadable). This is the fix for task #58's stale-ledger gap: previously
# this case just silently omitted `usage`, which is indistinguishable from
# "$0" to any downstream reader that doesn't know to check for absence.
#
# PROVIDER_BILLED_UNATTRIBUTED is intentionally never emitted here. It is
# reserved for the provider-bill reconciliation layer (roadmap: Provider
# Billing Reconciliation / Forward Accounting Cut Line, tasks #60/#64) -
# spend the provider's own invoice shows that no ledger record, at any
# per-attempt granularity, can be tied back to. That is a whole-system
# reconciliation finding, not a fact knowable from a single attempt.
COST_STATE_KNOWN_COST = "KNOWN_COST"
COST_STATE_RECONSTRUCTED_COST = "RECONSTRUCTED_COST"
COST_STATE_UNKNOWN_COST = "UNKNOWN_COST"
COST_STATE_SUBSCRIPTION_NO_INCREMENTAL_METER = "SUBSCRIPTION_NO_INCREMENTAL_METER"
COST_STATE_PROVIDER_BILLED_UNATTRIBUTED = "PROVIDER_BILLED_UNATTRIBUTED"
COST_STATE_UNAVAILABLE_USAGE = "UNAVAILABLE_USAGE"

_HERMES_COST_STATUS_TO_COST_STATE = {
    "actual": COST_STATE_KNOWN_COST,
    "estimated": COST_STATE_RECONSTRUCTED_COST,
    "included": COST_STATE_SUBSCRIPTION_NO_INCREMENTAL_METER,
    "unknown": COST_STATE_UNKNOWN_COST,
}


def cost_state_for(usage: dict[str, Any] | None) -> str:
    """Classify a model-attempt's cost into the worker accounting event
    contract's enum. Never returns anything implying zero cost by default -
    an unrecognized or missing Hermes cost_status maps to UNKNOWN_COST, not
    KNOWN_COST, and a wholly absent usage report maps to UNAVAILABLE_USAGE,
    never KNOWN_COST/$0."""
    if usage is None:
        return COST_STATE_UNAVAILABLE_USAGE
    return _HERMES_COST_STATUS_TO_COST_STATE.get(
        usage.get("cost_status"), COST_STATE_UNKNOWN_COST
    )


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    role: str
    model: str
    provider: str
    run: str
    assignment: str
    attempt: int
    prompt_digest: str | None


class AccountingLedger:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "accounting" / "model-attempts.jsonl"

    @staticmethod
    def _day(epoch: int) -> object:
        return datetime.fromtimestamp(epoch, timezone.utc).date()

    def _records(self, handle) -> list[dict[str, Any]]:
        handle.seek(0)
        records = []
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"corrupt accounting ledger line {line_number}: {exc}"
                ) from exc
            records.append(
                validate_record(
                    value,
                    ATTEMPT_SCHEMA_ID,
                )
            )
        return records

    @staticmethod
    def _started(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [record for record in records if record["result"] == "started"]

    def model_attempts_today(self, now: int | None = None) -> int:
        if not self.path.exists():
            return 0
        current = int(now or time.time())
        with self.path.open("r", encoding="utf-8") as handle:
            records = self._records(handle)
        return sum(
            self._day(int(record["recorded_at_epoch"])) == self._day(current)
            for record in self._started(records)
        )

    def model_attempts_for_assignment(self, assignment: str) -> int:
        if not self.path.exists():
            return 0
        with self.path.open("r", encoding="utf-8") as handle:
            records = self._records(handle)
        return sum(
            record["assignment"] == assignment for record in self._started(records)
        )

    def worker_cycles_today(self, now: int | None = None) -> int:
        current = int(now or time.time())
        count = 0
        for path in (self.root / "runs").glob("*.json"):
            try:
                record = read_record(
                    path, "axis.external-development-supervisor.run"
                )
            except Exception:
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
            if record.get("status") == "preflight-test":
                continue
            if self._day(int(record.get("started_at_epoch") or 0)) == self._day(current):
                count += 1
        return count

    def start(
        self,
        *,
        role: str,
        model: str,
        provider: str,
        run: str,
        assignment: str,
        limit: int,
        prompt_digest: str | None = None,
    ) -> Attempt:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        now = int(time.time())
        with self.path.open("a+", encoding="utf-8") as handle:
            os.chmod(self.path, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            records = self._records(handle)
            used = sum(
                self._day(int(record["recorded_at_epoch"])) == self._day(now)
                for record in self._started(records)
            )
            if used >= limit:
                raise RuntimeError(f"daily model call limit reached: {used}/{limit}")
            attempt_number = 1 + sum(
                record["assignment"] == assignment and record["role"] == role
                for record in self._started(records)
            )
            attempt = Attempt(
                attempt_id=uuid.uuid4().hex,
                role=role,
                model=model,
                provider=provider,
                run=run,
                assignment=assignment,
                attempt=attempt_number,
                prompt_digest=prompt_digest,
            )
            self._append(handle, attempt, "started", now)
            fcntl.flock(handle, fcntl.LOCK_UN)
        return attempt

    def finish(
        self,
        attempt: Attempt,
        result: str,
        *,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if result not in {"succeeded", "failed"}:
            raise ValueError(f"invalid attempt result: {result}")
        with self.path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self._append(handle, attempt, result, int(time.time()), usage, error)
            fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def _append(
        handle,
        attempt: Attempt,
        result: str,
        now: int,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        record = {
            "schema": ATTEMPT_SCHEMA_ID,
            "schema_version": "1.0.0",
            "attempt_id": attempt.attempt_id,
            "role": attempt.role,
            "model": attempt.model,
            "provider": attempt.provider,
            "run": attempt.run,
            "assignment": attempt.assignment,
            "attempt": attempt.attempt,
            "result": result,
            "recorded_at_epoch": now,
        }
        if result in {"succeeded", "failed"}:
            record["cost_state"] = cost_state_for(usage)
        if usage is not None:
            record["usage"] = usage
        if attempt.prompt_digest:
            record["prompt_digest"] = attempt.prompt_digest
        if error:
            record["error"] = error
        validate_record(
            record,
            ATTEMPT_SCHEMA_ID,
        )
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
