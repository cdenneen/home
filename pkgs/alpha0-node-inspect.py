#!@python@
"""Deterministic read-only worker for the Alpha0 Nyx node."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

GIT = "@git@"


def git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    for attempt in range(3):
        try:
            return subprocess.run(
                [GIT, "-c", "core.hooksPath=/dev/null", "-C", str(repository), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except BlockingIOError:
            if attempt == 2:
                raise
            time.sleep(0.1 * (attempt + 1))
    raise AssertionError("bounded Git spawn retry exhausted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()

    package = json.loads(args.package.read_text(encoding="utf-8"))
    expected = package["repository"]["base_sha"]
    head = git(args.repository, "rev-parse", "HEAD")
    status = git(args.repository, "status", "--porcelain=v1", "--untracked-files=all")
    observed = head.stdout.strip()
    clean = head.returncode == 0 and status.returncode == 0 and not status.stdout
    matches = observed == expected
    response = {
        "schema": "alpha0.node-worker-observation.v1",
        "head_sha": observed,
        "clean": clean,
        "summary": (
            "exact detached base inspected; worktree clean"
            if clean and matches
            else "repository observation did not match the requested clean base"
        ),
    }
    print(json.dumps(response, sort_keys=True, separators=(",", ":")))
    return 0 if clean and matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
