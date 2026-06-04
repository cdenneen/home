#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: oci-ghost-build-notify.sh [options]

Monitors an OCI ghost build log and sends desktop notifications for key events.

Desktop notifications are always attempted first.
Optional fallback channels:
  - Telegram Bot API (token + chat id)
  - Slack incoming webhook URL

Options:
  --log-file <path>      Log file to monitor (default: ~/oci-ghost-build.log)
  --pid <pid>            Optional builder PID; exits when PID is gone
  --poll-sec <seconds>   Poll interval in seconds (default: 15)
  --telegram-token <tok> Telegram bot token (or TELEGRAM_BOT_TOKEN)
  --telegram-token-file <path>
                         Telegram bot token file
                         (default: ~/.config/sops-nix/secrets/telegram_bot_token)
  --telegram-chat-id <id> Telegram chat id (or TELEGRAM_CHAT_ID)
  --telegram-chat-id-file <path>
                         Telegram chat id file
                         (default: ~/.config/sops-nix/secrets/telegram_chat_id)
  --slack-webhook-url <url>
                         Slack incoming webhook URL (or SLACK_WEBHOOK_URL)
  --once                 Exit after first success notification
  --help                 Show help
USAGE
}

log_file="$HOME/oci-ghost-build.log"
builder_pid=""
poll_sec=15
exit_on_success="0"

telegram_token="${TELEGRAM_BOT_TOKEN:-}"
telegram_chat_id="${TELEGRAM_CHAT_ID:-}"
telegram_token_file="${TELEGRAM_BOT_TOKEN_FILE:-$HOME/.config/sops-nix/secrets/telegram_bot_token}"
telegram_chat_id_file="${TELEGRAM_CHAT_ID_FILE:-$HOME/.config/sops-nix/secrets/telegram_chat_id}"
slack_webhook_url="${SLACK_WEBHOOK_URL:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --log-file)
      log_file="$2"
      shift 2
      ;;
    --pid)
      builder_pid="$2"
      shift 2
      ;;
    --poll-sec)
      poll_sec="$2"
      shift 2
      ;;
    --telegram-token)
      telegram_token="$2"
      shift 2
      ;;
    --telegram-token-file)
      telegram_token_file="$2"
      shift 2
      ;;
    --telegram-chat-id)
      telegram_chat_id="$2"
      shift 2
      ;;
    --telegram-chat-id-file)
      telegram_chat_id_file="$2"
      shift 2
      ;;
    --slack-webhook-url)
      slack_webhook_url="$2"
      shift 2
      ;;
    --once)
      exit_on_success="1"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if ! [[ "$poll_sec" =~ ^[0-9]+$ ]] || [ "$poll_sec" -lt 1 ]; then
  echo "--poll-sec must be an integer >= 1" >&2
  exit 1
fi

read_secret_file() {
  local file="$1"
  if [ -z "$file" ] || [ ! -r "$file" ]; then
    return 0
  fi
  tr -d '\n\r' < "$file"
}

marker_field() {
  local line="$1"
  local key="$2"
  local token

  for token in $line; do
    case "$token" in
      "$key"=*)
        printf '%s\n' "${token#*=}"
        return 0
        ;;
    esac
  done

  return 1
}

if [ -z "$telegram_token" ]; then
  telegram_token="$(read_secret_file "$telegram_token_file")"
fi

if [ -z "$telegram_chat_id" ]; then
  telegram_chat_id="$(read_secret_file "$telegram_chat_id_file")"
fi

send_telegram() {
  local title="$1"
  local body="$2"

  if [ -z "$telegram_token" ] || [ -z "$telegram_chat_id" ]; then
    return 0
  fi

  local text
  text="$title: $body"

  curl -fsS --max-time 10 \
    -X POST "https://api.telegram.org/bot${telegram_token}/sendMessage" \
    --data-urlencode "chat_id=${telegram_chat_id}" \
    --data-urlencode "text=${text}" \
    >/dev/null 2>&1 || true
}

send_slack() {
  local title="$1"
  local body="$2"

  if [ -z "$slack_webhook_url" ]; then
    return 0
  fi

  local payload
  if command -v jq >/dev/null 2>&1; then
    payload="$(jq -cn --arg text "$title: $body" '{text:$text}')"
  else
    payload="{\"text\":\"${title}: ${body}\"}"
  fi

  curl -fsS --max-time 10 \
    -X POST "$slack_webhook_url" \
    -H 'Content-Type: application/json' \
    --data "$payload" \
    >/dev/null 2>&1 || true
}

notify() {
  local title="$1"
  local body="$2"
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S')"

  echo "[$ts] $title: $body"

  if [ "$(uname -s)" = "Darwin" ] && command -v osascript >/dev/null 2>&1; then
    local safe_title safe_body
    safe_title="${title//\"/\\\"}"
    safe_body="${body//\"/\\\"}"
    osascript -e "display notification \"$safe_body\" with title \"$safe_title\"" >/dev/null 2>&1 || true
  fi

  if command -v notify-send >/dev/null 2>&1; then
    notify-send "$title" "$body" || true
  fi

  # Audible fallback
  printf '\a' || true

  send_telegram "$title" "$body"
  send_slack "$title" "$body"
}

wait_for_log() {
  local waited=0
  while [ ! -f "$log_file" ]; do
    if [ -n "$builder_pid" ] && ! kill -0 "$builder_pid" 2>/dev/null; then
      notify "OCI ghost monitor" "Builder PID $builder_pid is not running and log file does not exist."
      exit 1
    fi

    if [ $waited -eq 0 ]; then
      notify "OCI ghost monitor" "Waiting for log file: $log_file"
      waited=1
    fi

    sleep "$poll_sec"
  done
}

wait_for_log

processed_lines=0
notified_launch="0"
notified_install_start="0"
notified_resize="0"
notified_done="0"
notified_ready="0"

notify "OCI ghost monitor" "Started monitoring $log_file"

while :; do
  total_lines=$(wc -l < "$log_file" 2>/dev/null || echo 0)

  if [ "$total_lines" -gt "$processed_lines" ]; then
    while IFS= read -r line; do
      case "$line" in
        *"Launched instance:"*|*"GHOST_LAUNCHED "*)
          if [ "$notified_launch" = "0" ]; then
            notified_launch="1"
            region="$(marker_field "$line" region 2>/dev/null || true)"
            if [ -n "$region" ]; then
              notify "OCI ghost" "Capacity found and instance launched in $region."
            else
              notify "OCI ghost" "Capacity found and instance launched."
            fi
          fi
          ;;
        *"Resize succeeded;"*|*"GHOST_RESIZE_OK "*)
          if [ "$notified_resize" = "0" ]; then
            notified_resize="1"
            ocpus="$(marker_field "$line" ocpus 2>/dev/null || true)"
            memory_gb="$(marker_field "$line" memory_gb 2>/dev/null || true)"
            if [ -n "$ocpus" ] && [ -n "$memory_gb" ]; then
              notify "OCI ghost" "Post-provision resize succeeded at ${ocpus}/${memory_gb}."
            else
              notify "OCI ghost" "Post-provision resize succeeded."
            fi
          fi
          ;;
        *"Running nixos-anywhere"*)
          if [ "$notified_install_start" = "0" ]; then
            notified_install_start="1"
            notify "OCI ghost" "nixos-anywhere install started."
          fi
          ;;
        *"GHOST_READY "*)
          if [ "$notified_ready" = "0" ]; then
            notified_ready="1"
            notified_done="1"
            name="$(marker_field "$line" name 2>/dev/null || true)"
            public_ip="$(marker_field "$line" public_ip 2>/dev/null || true)"
            region="$(marker_field "$line" region 2>/dev/null || true)"
            ocpus="$(marker_field "$line" ocpus 2>/dev/null || true)"
            memory_gb="$(marker_field "$line" memory_gb 2>/dev/null || true)"
            details=""
            if [ -n "$public_ip" ]; then
              details="IP $public_ip"
            fi
            if [ -n "$region" ]; then
              details="${details:+$details, }region $region"
            fi
            if [ -n "$ocpus" ] && [ -n "$memory_gb" ]; then
              details="${details:+$details, }shape ${ocpus}/${memory_gb}"
            fi
            if [ -n "$details" ]; then
              notify "OCI ghost" "${name:-ghost} is ready (${details})."
            else
              notify "OCI ghost" "${name:-ghost} is ready."
            fi
            if [ "$exit_on_success" = "1" ]; then
              exit 0
            fi
          fi
          ;;
        *"installation finished!"*|*"### Done! ###"*)
          if [ "$notified_done" = "0" ]; then
            notified_done="1"
            notify "OCI ghost" "Install completed successfully."
            if [ "$exit_on_success" = "1" ]; then
              exit 0
            fi
          fi
          ;;
        *"SSH did not become ready in time"*)
          notify "OCI ghost" "Instance launched but SSH readiness timed out."
          ;;
        *"All launch attempts failed:"*)
          notify "OCI ghost" "Capacity attempt failed; retrying." 
          ;;
      esac
    done < <(sed -n "$((processed_lines + 1)),$total_lines p" "$log_file")

    processed_lines="$total_lines"
  fi

  if [ -n "$builder_pid" ] && ! kill -0 "$builder_pid" 2>/dev/null; then
    if [ "$notified_ready" = "1" ] || [ "$notified_done" = "1" ]; then
      notify "OCI ghost monitor" "Builder exited after successful install."
    else
      notify "OCI ghost monitor" "Builder PID $builder_pid exited. Check $log_file."
    fi
    exit 0
  fi

  sleep "$poll_sec"
done
