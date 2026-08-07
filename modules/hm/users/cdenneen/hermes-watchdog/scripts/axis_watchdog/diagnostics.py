import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any


class SubprocessDiagnostic:
    def __init__(self, command: str | None = None):
        self.command = command or os.environ.get(
            "AXIS_WATCHDOG_DIAGNOSTIC_COMMAND",
            "axis-development-watchdog-diagnose",
        )

    def __call__(
        self,
        anomalies: list[dict[str, Any]],
        evidence: dict[str, Any],
        control: dict[str, Any],
    ) -> str:
        prompt = (
            "You are the bounded, read-only AXIS Development Watchdog diagnostician. "
            "Explain the deterministic anomalies and recommend only watchdog recovery or "
            "a cdenneen/home supervisor repair. Do not dispatch product work, mutate a "
            "repository, call tools, or reinterpret healthy waits as failures. Return at "
            "most 1200 characters.\n"
            + json.dumps(
                {"anomalies": anomalies, "evidence": evidence},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        maximum = int(control["diagnostic_max_prompt_bytes"])
        encoded = prompt.encode("utf-8")
        if len(encoded) > maximum:
            raise ValueError(f"diagnostic prompt exceeds {maximum} bytes")
        result = subprocess.run(
            shlex.split(self.command),
            input=prompt,
            text=True,
            capture_output=True,
            timeout=int(control["diagnostic_timeout_seconds"]),
            check=False,
            cwd=Path.home(),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"diagnostic exited {result.returncode}: {(result.stdout + result.stderr)[-1000:]}"
            )
        output = result.stdout.strip()
        if not output:
            raise RuntimeError("diagnostic returned empty output")
        return output[:1200]
