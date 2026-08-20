#!/usr/bin/env bash
set -euo pipefail
root=$(git rev-parse --show-toplevel)
chmod +x "$root/.githooks/pre-push"
git config --local core.hooksPath .githooks
printf 'installed core.hooksPath=.githooks\n'
