#!/usr/bin/env python3

import argparse
import re
import subprocess
from functools import cache
from pathlib import Path


ACTION_PATTERN = re.compile(
    r"(?P<prefix>\buses:\s*)"
    r"(?P<path>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*)"
    r"@(?P<ref>v\d+|[0-9a-fA-F]{40})"
    r"(?:\s+#\s+(?P<tag>v\d+(?:\.\d+){0,2}))?"
)
VERSION_PATTERN = re.compile(
    r"^v(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?$"
)


def github_output(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


@cache
def latest_release(repository: str) -> tuple[str, str]:
    try:
        tags = [
            github_output(
                ["gh", "api", f"repos/{repository}/releases/latest", "--jq", ".tag_name"]
            )
        ]
    except RuntimeError:
        tags = github_output(
            ["gh", "api", f"repos/{repository}/tags?per_page=100", "--jq", ".[].name"]
        ).splitlines()
    versions = [
        (
            int(match.group("major")),
            int(match.group("minor") or 0),
            int(match.group("patch") or 0),
            tag,
        )
        for tag in tags
        if (match := VERSION_PATTERN.match(tag))
    ]
    if not versions:
        raise RuntimeError(f"No stable major release found for {repository}")
    tag = max(versions)[3]
    commit = github_output(
        ["gh", "api", f"repos/{repository}/commits/{tag}", "--jq", ".sha"]
    )
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise RuntimeError(f"Invalid commit returned for {repository} {tag}: {commit}")
    return tag, commit.lower()


def update_file(path: Path, dry_run: bool) -> list[str]:
    original = path.read_text()
    repositories = {
        "/".join(match.group("path").split("/")[:2])
        for match in ACTION_PATTERN.finditer(original)
    }
    latest = {repository: latest_release(repository) for repository in repositories}
    updates: list[str] = []

    def replace(match: re.Match[str]) -> str:
        action_path = match.group("path")
        repository = "/".join(action_path.split("/")[:2])
        current_ref = match.group("ref")
        current_tag = current_ref if current_ref.startswith("v") else match.group("tag")
        current_version = VERSION_PATTERN.match(current_tag or "")
        if not current_version:
            return match.group(0)
        target_tag, target_commit = latest[repository]
        target_version = VERSION_PATTERN.match(target_tag)
        if not target_version:
            return match.group(0)
        current_major = int(current_version.group("major"))
        target_major = int(target_version.group("major"))
        if target_major < current_major:
            return match.group(0)
        if current_ref.lower() == target_commit and current_tag == target_tag:
            return match.group(0)
        updates.append(f"{action_path}: {current_tag} -> {target_tag}")
        return f"{match.group('prefix')}{action_path}@{target_commit} # {target_tag}"

    updated = ACTION_PATTERN.sub(replace, original)
    if updates and not dry_run:
        path.write_text(updated)
    return updates


def main() -> None:
    parser = argparse.ArgumentParser(description="Update stable major GitHub Action references")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    updates: list[str] = []
    for path in sorted(Path(".github/workflows").glob("*.y*ml")):
        updates.extend(update_file(path, args.dry_run))
    if updates:
        print("\n".join(updates))
    else:
        print("GitHub Actions are current.")


if __name__ == "__main__":
    main()
