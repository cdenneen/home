"""Assert host-key consumers use one aggregate Eros MCP registration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import tomllib

AGGREGATE_URL = "http://eros.tail0e55.ts.net:4000/mcp/"
COMMON_DIRECT_NAMES = {
    "aws",
    "context7",
    "duckduckgo",
    "gitlab",
    "graphify",
    "kubernetes",
    "playwright",
    "recallium",
    "terraform",
}
DOMAINS = {
    "ghost": "personal",
    "nyx": "work",
    "VNJTECMBCD": "personal",
    "mbair": "personal",
}


def assert_mcp_map(
    label: str, servers: dict[str, Any], workload: str, host: str
) -> None:
    aggregate = [
        name for name, value in servers.items() if value.get("url") == AGGREGATE_URL
    ]
    if aggregate != ["eros"]:
        raise AssertionError(
            f"{label}: expected one eros aggregate server, got {aggregate}"
        )
    direct = sorted(COMMON_DIRECT_NAMES.intersection(servers))
    if direct:
        raise AssertionError(f"{label}: eager common MCP servers remain: {direct}")
    headers = servers["eros"].get("http_headers", servers["eros"].get("headers", {}))
    expected_headers = {
        "x-eros-consumer": host,
        "x-eros-trust-domain": DOMAINS[host],
        "x-eros-workload": workload,
    }
    missing = {
        name: value
        for name, value in expected_headers.items()
        if headers.get(name) != value
    }
    if missing:
        raise AssertionError(
            f"{label}: invalid attribution headers {missing}: {headers}"
        )


def validate_host(directory: Path, host: str) -> None:
    with (directory / f"{host}-codex.toml").open("rb") as handle:
        codex = tomllib.load(handle)
    assert_mcp_map(f"{host}/codex", codex["mcp_servers"], "codex", host)

    with (directory / f"{host}-codex-eros.toml").open("rb") as handle:
        codex_eros = tomllib.load(handle)
    if codex_eros.get("model") != "coding-openai":
        raise AssertionError(
            f"{host}/codex: Responses API requires coding-openai, "
            f"got {codex_eros.get('model')!r}"
        )
    if codex_eros.get("model_providers", {}).get("eros", {}).get("wire_api") != "responses":
        raise AssertionError(f"{host}/codex: Eros provider must use Responses API")

    claude = json.loads((directory / f"{host}-claude.json").read_text())
    assert_mcp_map(f"{host}/claude", claude["mcpServers"], "claude-code", host)

    opencode_path = directory / f"{host}-opencode.json"
    if opencode_path.exists():
        opencode = json.loads(opencode_path.read_text())
        assert_mcp_map(f"{host}/opencode", opencode["mcp"], "opencode", host)

    pi_activation = (directory / f"{host}-pi-activation.sh").read_text()
    if (
        pi_activation.count(".mcpServers.eros =") != 1
        or AGGREGATE_URL not in pi_activation
    ):
        raise AssertionError(f"{host}/pi: aggregate registration is not singular")
    for name in COMMON_DIRECT_NAMES:
        if (
            f".mcpServers.{name}," not in pi_activation
            and f".mcpServers.{name}\n" not in pi_activation
        ):
            raise AssertionError(f"{host}/pi: stale {name} cleanup is missing")
    for expected in (host, DOMAINS[host], '"x-eros-workload": "pi"'):
        if expected not in pi_activation:
            raise AssertionError(
                f"{host}/pi: missing rendered attribution value {expected}"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args()
    hosts = sorted(
        path.name.removesuffix("-codex.toml")
        for path in arguments.directory.glob("*-codex.toml")
    )
    for host in hosts:
        validate_host(arguments.directory, host)

    desktop_path = arguments.directory / "VNJTECMBCD-claude-desktop.json"
    if desktop_path.exists():
        desktop = json.loads(desktop_path.read_text())
        servers = desktop["mcpServers"]
        if (
            sorted(servers) != ["eros"]
            or servers["eros"]["args"].count(AGGREGATE_URL) != 1
        ):
            raise AssertionError(
                f"Claude Desktop aggregate registration is invalid: {servers}"
            )
        args = servers["eros"]["args"]
        for expected in (
            "x-eros-consumer: VNJTECMBCD",
            "x-eros-trust-domain: personal",
            "x-eros-workload: claude-desktop",
        ):
            if expected not in args:
                raise AssertionError(
                    f"Claude Desktop missing attribution argument {expected}: {args}"
                )
    arguments.directory.joinpath("validated").touch()


if __name__ == "__main__":
    main()
