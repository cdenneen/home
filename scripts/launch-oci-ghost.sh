#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: launch-oci-ghost.sh [build-script args...]

Starts a timestamped OCI ghost build + notifier run.

Defaults (when no args are provided):
  --name ghost --flake .#ghost --regions us-ashburn-1 --retry-forever --retry-sleep-sec 300

Wrapper options:
  --wait  Keep the wrapper alive until the builder exits
          (useful under launchd so child processes are not reaped)

Examples:
  launch-oci-ghost.sh
  launch-oci-ghost.sh --wait
  launch-oci-ghost.sh --regions us-ashburn-1 --retry-forever --retry-sleep-sec 300
USAGE
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

wait_for_builder="${OCI_GHOST_WAIT:-0}"
if [ "${1:-}" = "--wait" ]; then
  wait_for_builder="1"
  shift
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build_script="$script_dir/oci-build-oracle-cloud-nixos.sh"
notify_script="$script_dir/oci-ghost-build-notify.sh"

if [ ! -x "$build_script" ] || [ ! -x "$notify_script" ]; then
  echo "Required scripts are missing or not executable:" >&2
  echo "  $build_script" >&2
  echo "  $notify_script" >&2
  exit 1
fi

state_dir="${XDG_STATE_HOME:-$HOME/.local/state}"
lock_dir="$state_dir/oci-ghost-build.lock.d"
lock_pid_file="$lock_dir/pid"

pid_is_ghost_builder() {
  local pid="$1"
  local cmd=""

  cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
  [ -n "$cmd" ] && printf '%s' "$cmd" | grep -Fq "oci-build-oracle-cloud-nixos.sh"
}

if [ -d "$lock_dir" ]; then
  lock_pid=""
  if [ -r "$lock_pid_file" ]; then
    lock_pid="$(cat "$lock_pid_file" 2>/dev/null || true)"
  fi

  if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null && pid_is_ghost_builder "$lock_pid"; then
    echo "A ghost build is already running (lock pid: $lock_pid)."
    echo "Stop it first, or wait for it to finish."
    exit 1
  fi

  echo "Found stale lock at $lock_dir; removing and continuing."
  rm -rf "$lock_dir"
fi

run_dir="$state_dir/oci-ghost-runs"
mkdir -p "$run_dir"

run_id="$(date -u +%Y%m%dT%H%M%SZ)"
build_log="$run_dir/ghost-build-${run_id}.log"
notify_log="$run_dir/ghost-notify-${run_id}.log"

if [ "$#" -gt 0 ]; then
  build_args=("$@")
else
  build_args=(
    --name ghost
    --flake .#ghost
    --regions us-ashburn-1
    --retry-forever
    --retry-sleep-sec 300
  )
fi

if [ "${OCI_GHOST_FOREGROUND:-0}" = "1" ]; then
  echo "Running ghost build in foreground (OCI_GHOST_FOREGROUND=1)."
  echo "Command: $build_script ${build_args[*]}"
  exec "$build_script" "${build_args[@]}"
fi

nohup "$build_script" "${build_args[@]}" >>"$build_log" 2>&1 &
builder_pid=$!

nohup "$notify_script" \
  --log-file "$build_log" \
  --pid "$builder_pid" \
  --poll-sec 20 \
  --telegram-token-file "$HOME/.config/sops-nix/secrets/telegram_bot_token" \
  --telegram-chat-id-file "$HOME/.config/sops-nix/secrets/telegram_chat_id" \
  --once \
  >>"$notify_log" 2>&1 &
notify_pid=$!

ln -sfn "$build_log" "$run_dir/ghost-build-latest.log"
ln -sfn "$notify_log" "$run_dir/ghost-notify-latest.log"

cat <<INFO
Started ghost OCI run.
  run_id:      $run_id
  builder_pid: $builder_pid
  notify_pid:  $notify_pid
  build_log:   $build_log
  notify_log:  $notify_log

Quick checks:
  pgrep -fl "oci-build-oracle-cloud-nixos.sh|oci-ghost-build-notify.sh"
  tail -f "$build_log"
INFO

if [ "$wait_for_builder" = "1" ]; then
  set +e
  wait "$builder_pid"
  builder_rc=$?
  wait "$notify_pid" || true
  set -e
  exit "$builder_rc"
fi
