#!/usr/bin/env python3
import argparse
import copy
import sys

from hermes_cli import config as hermes_config  # pyright: ignore[reportMissingImports]
from hermes_cli.oneshot import run_oneshot  # pyright: ignore[reportMissingImports]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--reasoning", required=True)
    parser.add_argument("--toolsets")
    parser.add_argument("--usage-file")
    args = parser.parse_args()

    load_config = hermes_config.load_config

    def load_config_with_reasoning() -> dict:
        config = copy.deepcopy(load_config())
        agent = config.setdefault("agent", {})
        if not isinstance(agent, dict):
            raise ValueError("Hermes agent config must be an object")
        agent["reasoning_effort"] = args.reasoning
        return config

    hermes_config.load_config = load_config_with_reasoning
    return run_oneshot(
        sys.stdin.read(),
        model=args.model,
        provider=args.provider,
        toolsets=args.toolsets,
        usage_file=args.usage_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
