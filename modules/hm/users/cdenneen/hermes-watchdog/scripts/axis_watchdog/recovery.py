import os
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .records import atomic_write, timestamp


class RecoveryExecutor:
    def __init__(
        self,
        root: Path,
        *,
        runner: Any | None = None,
        self_repair_command: str | None = None,
        runtime_repair_command: str | None = None,
    ):
        self.root = root
        self.runner = runner or subprocess.run
        self.self_repair_command = self_repair_command or os.environ.get(
            "AXIS_WATCHDOG_SELF_REPAIR_COMMAND",
            "axis-development-watchdog-self-repair",
        )
        self.runtime_repair_command = runtime_repair_command or os.environ.get(
            "AXIS_WATCHDOG_RUNTIME_REPAIR_COMMAND",
            "axis-development-watchdog-runtime-repair",
        )

    def _run(self, command: str, timeout: int) -> str:
        result = self.runner(
            shlex.split(command),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            output = (result.stdout or "") + (result.stderr or "")
            raise RuntimeError(
                f"recovery command exited {result.returncode}: {output[-1000:]}"
            )
        return (result.stdout or "").strip()[-1000:] or "command completed"

    def execute(
        self,
        incident: dict[str, Any],
        diagnosis: str | None,
        control: dict[str, Any],
        now: int,
    ) -> tuple[str, str]:
        level = int(incident["recovery_level"])
        if level == 0:
            return "completed", "current watchdog cycle supplied the missed heartbeat catch-up"
        if level == 1:
            return "in-progress", "canonical Slack projection retry is scheduled in this cycle"
        if level == 2:
            return "completed", self._run(self.self_repair_command, timeout=60)
        if level == 3:
            return "completed", self._run(self.runtime_repair_command, timeout=60)
        if level == 4:
            if incident.get("repair_repository") != "cdenneen/home":
                raise ValueError("level-4 repair escalation escaped cdenneen/home")
            escalation = {
                "schema": "axis.development-watchdog.repair-escalation",
                "schema_version": "1.0.0",
                "escalation_id": uuid.uuid4().hex,
                "incident_id": incident["incident_id"],
                "repository": "cdenneen/home",
                "authority": "bounded-watchdog-repair",
                "diagnostic_mode": "pinned-hermes-no-tools-read-only",
                "diagnosis": diagnosis or "bounded diagnostic unavailable",
                "requested_at": timestamp(now),
                "allowed_scope": [
                    "modules/hm/users/cdenneen/hermes-supervisor",
                    "modules/hm/users/cdenneen/hermes-watchdog",
                ],
                "product_dispatch_allowed": False,
            }
            path = self.root / "repair-escalations" / f"{incident['incident_id']}.json"
            atomic_write(path, escalation)
            return "completed", f"persisted bounded repair escalation {escalation['escalation_id']}"
        if level == 5:
            return "waiting-human", "Product Owner action is required"
        raise ValueError(f"unsupported recovery level: {level}")
