#!/usr/bin/env bash
set -euo pipefail

sudo_cmd=()
if [[ "${EUID}" -ne 0 ]]; then
  sudo_cmd=(sudo)
fi

images=(
  "registry.gitlab.com/cdenneen/my-jarvis/jarvis:latest"
  "registry.gitlab.com/cdenneen/my-jarvis/jarvis-web:latest"
)

units=(
  "podman-jarvis-api.service"
  "podman-jarvis-harness.service"
  "podman-jarvis-slack-gateway.service"
  "podman-jarvis-web.service"
)

for image in "${images[@]}"; do
  "${sudo_cmd[@]}" podman pull "$image"
done

"${sudo_cmd[@]}" systemctl reset-failed "${units[@]}" || true
"${sudo_cmd[@]}" systemctl restart "${units[@]}"
"${sudo_cmd[@]}" systemctl status "${units[@]}" --no-pager -n 20
