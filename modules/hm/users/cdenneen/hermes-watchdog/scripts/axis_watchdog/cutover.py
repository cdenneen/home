import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .records import atomic_write, load_optional, timestamp

GENERATIONS = ("A", "B", "C", "D", "E")


class CutoverCoordinator:
    def __init__(
        self,
        root: Path,
        jobs_path: Path,
        *,
        clock: Any,
        reconcile_command: str | None = None,
        runner: Any | None = None,
    ):
        self.root = root
        self.jobs_path = jobs_path
        self.path = root / "slack-cutover.json"
        self.clock = clock
        self.reconcile_command = reconcile_command or os.environ.get(
            "AXIS_WATCHDOG_CUTOVER_RECONCILE_COMMAND",
            str(
                Path.home()
                / ".nix-profile/bin/axis-development-watchdog-cutover-reconcile"
            ),
        )
        self.runner = runner or subprocess.run

    def load(self) -> dict[str, Any]:
        value = load_optional(self.path)
        if value:
            if value.get("generation") not in GENERATIONS:
                raise ValueError("Slack cutover generation is invalid")
            return value
        now = int(self.clock())
        value = {
            "schema": "axis.development-watchdog.slack-cutover",
            "schema_version": "1.0.0",
            "generation": "A",
            "status": "shadowing",
            "shadow_fingerprint": None,
            "canonical_fingerprint": None,
            "parity_count": 0,
            "writer_verified_count": 0,
            "last_error": None,
            "history": [
                {
                    "generation": "A",
                    "event": "initialized",
                    "at": timestamp(now),
                }
            ],
            "updated_at": timestamp(now),
        }
        atomic_write(self.path, value)
        return value

    def _write(self, value: dict[str, Any]) -> dict[str, Any]:
        value["updated_at"] = timestamp(int(self.clock()))
        value["history"] = list(value.get("history") or [])[-100:]
        atomic_write(self.path, value)
        return value

    def _transition(
        self, value: dict[str, Any], generation: str, event: str
    ) -> dict[str, Any]:
        value = dict(value)
        value["generation"] = generation
        value["status"] = {
            "A": "shadowing",
            "B": "parity",
            "C": "watchdog-writer",
            "D": "reporter-removal",
            "E": "observing",
        }[generation]
        value.setdefault("history", []).append(
            {
                "generation": generation,
                "event": event,
                "at": timestamp(int(self.clock())),
            }
        )
        result = self._write(value)
        self.reconcile()
        return result

    def reconcile(self) -> None:
        result = self.runner(
            shlex.split(self.reconcile_command),
            text=True,
            capture_output=True,
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            output = (result.stdout or "") + (result.stderr or "")
            raise RuntimeError(f"Slack cutover reconcile failed: {output[-1200:]}")

    def mode(self) -> str:
        return "shadow" if self.load()["generation"] in {"A", "B"} else "writer"

    def record_shadow(
        self, shadow_fingerprint: str, canonical_fingerprint: str | None
    ) -> dict[str, Any]:
        value = self.load()
        value["shadow_fingerprint"] = shadow_fingerprint
        value["canonical_fingerprint"] = canonical_fingerprint
        value["last_error"] = None
        if value["generation"] == "A" and canonical_fingerprint:
            return self._transition(value, "B", "shadow-and-reporter-observed")
        if value["generation"] == "B":
            if canonical_fingerprint and shadow_fingerprint == canonical_fingerprint:
                value["parity_count"] = int(value.get("parity_count") or 0) + 1
                if value["parity_count"] >= 1:
                    return self._transition(value, "C", "canonical-parity-verified")
            else:
                value["parity_count"] = 0
                value["last_error"] = "shadow/canonical fingerprint mismatch"
        return self._write(value)

    def record_writer(self, success: bool, error: str | None = None) -> dict[str, Any]:
        value = self.load()
        generation = value["generation"]
        if not success:
            value["last_error"] = error or "watchdog writer failed"
            if generation in {"C", "D", "E"}:
                value["parity_count"] = 0
                value["writer_verified_count"] = 0
                return self._transition(value, "A", "automatic-writer-rollback")
            return self._write(value)
        value["last_error"] = None
        value["writer_verified_count"] = int(value.get("writer_verified_count") or 0) + 1
        if generation == "C":
            return self._transition(value, "D", "watchdog-writer-verified")
        if generation == "D":
            if not self.reporter_present():
                return self._transition(value, "E", "legacy-reporter-removed")
            self.reconcile()
        return self._write(value)

    def reporter_present(self) -> bool:
        jobs = load_optional(self.jobs_path).get("jobs") or []
        return any(job.get("name") == "axis-development-supervisor-report" for job in jobs)

    def rollback(self, reason: str) -> dict[str, Any]:
        value = self.load()
        value["last_error"] = reason[:1200]
        value["parity_count"] = 0
        value["writer_verified_count"] = 0
        return self._transition(value, "A", "operator-rollback")
