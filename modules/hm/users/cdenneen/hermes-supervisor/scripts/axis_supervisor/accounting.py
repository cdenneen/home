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


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    role: str
    model: str
    provider: str
    run: str
    assignment: str
    attempt: int


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
        if usage is not None:
            record["usage"] = usage
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
