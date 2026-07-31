#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import TypedDict


class CliConfig(TypedDict):
    repo: str
    file: str
    tag_prefix: str
    assets: dict[str, str]


CONFIG: dict[str, CliConfig] = {
    "codex": {
        "repo": "openai/codex",
        "file": "pkgs/codex-cli.nix",
        "tag_prefix": "rust-v",
        "assets": {
            "aarch64-darwin": "codex-aarch64-apple-darwin.tar.gz",
            "x86_64-darwin": "codex-x86_64-apple-darwin.tar.gz",
            "x86_64-linux": "codex-x86_64-unknown-linux-musl.tar.gz",
            "aarch64-linux": "codex-aarch64-unknown-linux-musl.tar.gz",
        },
    },
    "opencode": {
        "repo": "anomalyco/opencode",
        "file": "pkgs/opencode-cli.nix",
        "tag_prefix": "v",
        "assets": {
            "aarch64-darwin": "opencode-darwin-arm64.zip",
            "x86_64-darwin": "opencode-darwin-x64.zip",
            "x86_64-linux": "opencode-linux-x64.tar.gz",
            "aarch64-linux": "opencode-linux-arm64.tar.gz",
        },
    },
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_json(cmd: list[str]) -> dict:
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def latest_release(repo: str) -> dict:
    return run_json(["gh", "api", f"repos/{repo}/releases/latest"])


def normalize_version(tag_name: str, tag_prefix: str) -> str:
    if not tag_name.startswith(tag_prefix):
        raise ValueError(f"tag {tag_name!r} does not start with expected prefix {tag_prefix!r}")
    return tag_name[len(tag_prefix) :]


def sri_hash_for_url(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "cdenneen-home-updater"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request) as response:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return "sha256-" + base64.b64encode(digest.digest()).decode("ascii")


def update_nix_file(path: Path, version: str, asset_hashes: dict[str, str]) -> tuple[str, str]:
    original = path.read_text()
    lines = original.splitlines(keepends=True)
    updated_lines: list[str] = []

    version_updated = False
    current_asset: str | None = None
    updated_assets: set[str] = set()

    for line in lines:
        if not version_updated and re.search(r'^\s*version = "[^"]+";', line):
            line = re.sub(r'(^\s*version = ")[^"]+(";\s*$)', rf"\g<1>{version}\2", line)
            version_updated = True

        asset_match = re.search(r'asset = "([^"]+)";', line)
        if asset_match:
            current_asset = asset_match.group(1)

        if current_asset in asset_hashes and re.search(r'^\s*hash = "[^"]+";', line):
            line = re.sub(
                r'(^\s*hash = ")[^"]+(";\s*$)',
                rf'\g<1>{asset_hashes[current_asset]}\2',
                line,
            )
            updated_assets.add(current_asset)
            current_asset = None

        updated_lines.append(line)

    if not version_updated:
        raise ValueError(f"failed to update version in {path}")

    missing_assets = set(asset_hashes) - updated_assets
    if missing_assets:
        missing = ", ".join(sorted(missing_assets))
        raise ValueError(f"failed to update hashes for assets in {path}: {missing}")

    return original, "".join(updated_lines)


def current_version(text: str) -> str:
    match = re.search(r'^\s*version = "([^"]+)";', text, flags=re.MULTILINE)
    if not match:
        raise ValueError("could not determine current version")
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Update custom pinned agent CLI package versions and hashes.")
    parser.add_argument("cli", choices=sorted(CONFIG))
    parser.add_argument("--check", action="store_true", help="Check for a newer version without writing files.")
    args = parser.parse_args()

    cfg = CONFIG[args.cli]
    release = latest_release(cfg["repo"])
    version = normalize_version(str(release["tag_name"]), cfg["tag_prefix"])
    assets_by_name = {asset["name"]: asset["browser_download_url"] for asset in release.get("assets", [])}

    asset_hashes: dict[str, str] = {}
    for asset_name in cfg["assets"].values():
        url = assets_by_name.get(asset_name)
        if not url:
            raise ValueError(f"release {release['tag_name']} missing asset {asset_name}")
        print(f"hashing {args.cli} asset {asset_name}", file=sys.stderr)
        asset_hashes[asset_name] = sri_hash_for_url(url)

    nix_file = repo_root() / cfg["file"]
    original, updated = update_nix_file(nix_file, version, asset_hashes)
    previous = current_version(original)

    if original == updated:
        print(f"{args.cli}: already up to date at {previous}")
        return 0

    if args.check:
        print(f"{args.cli}: would update {previous} -> {version}")
        return 0

    nix_file.write_text(updated)
    print(f"{args.cli}: updated {previous} -> {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
