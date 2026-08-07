#!/usr/bin/env python3
import copy
import sys

from hermes_cli import config as hermes_config  # pyright: ignore[reportMissingImports]
from hermes_cli.oneshot import run_oneshot  # pyright: ignore[reportMissingImports]


def main() -> int:
    load_config = hermes_config.load_config

    def bounded_config() -> dict:
        value = copy.deepcopy(load_config())
        agent = value.setdefault("agent", {})
        if not isinstance(agent, dict):
            raise TypeError("Hermes agent config must be an object")
        agent["reasoning_effort"] = "medium"
        return value

    hermes_config.load_config = bounded_config
    return run_oneshot(
        sys.stdin.read(),
        model="gpt-5.4",
        provider="openai-api",
        toolsets="",
    )


if __name__ == "__main__":
    raise SystemExit(main())
