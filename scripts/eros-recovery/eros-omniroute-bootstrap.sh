#!/usr/bin/env bash
# eros-omniroute-bootstrap.sh - G-DR-PREP-1/2
#
# Reconstructs ONLY the OmniRoute state Eros actually depends on today:
# the governed provider connections and the LiteLLM-facing client key.
# storage.sqlite is NEVER the restoration mechanism - this script is.
#
# Idempotent: running it twice produces no functional change on the
# second run. Inspects current state, creates what's missing, verifies
# what already exists, and fails loudly (never silently overwrites) on
# any conflicting/destructive difference.
#
# Inputs are secret REFERENCES (environment variables), never embedded
# values. No provider is hardcoded as mandatory - DESIRED_CONNECTIONS
# below is the only place the current candidate set is named; everything
# else in this script operates generically over that table. Consumers
# never see provider identity - they request logical routes (tier1-coding,
# tier2-general, ...) whose provider mapping lives in LiteLLM config, not
# here. Swapping a candidate (e.g. Bedrock -> another Claude host) means
# editing one row below, not this script's logic.
#
# Authenticates itself (does not depend on a pre-existing dashboard
# session, which would itself be undocumented runtime state) - manages
# its own short-lived cookie jar in a private temp file, removed on exit.
#
# Required env vars:
#   OMNIROUTE_BASE_URL            (default http://127.0.0.1:20128)
#   OMNIROUTE_DASHBOARD_PASSWORD  (required)
#   OPENAI_API_KEY, GEMINI_API_KEY, BEDROCK_API_KEY  (only for providers
#     actually being bootstrapped - a missing var is a SKIP if the
#     connection already exists, or a reported gap if it doesn't)
#
# Output: prints the LiteLLM client key ONCE on first creation, to stdout
# only - caller is responsible for piping it directly into sops. Never
# writes it to a file. On subsequent runs it does not re-print it (an
# existing key's value cannot be read back from OmniRoute - by design
# this script verifies only its existence, never its value).

set -euo pipefail

BASE_URL="${OMNIROUTE_BASE_URL:-http://127.0.0.1:20128}"
DASH_PASSWORD="${OMNIROUTE_DASHBOARD_PASSWORD:?OMNIROUTE_DASHBOARD_PASSWORD must be set}"

COOKIE_FILE="$(mktemp)"
chmod 600 "$COOKIE_FILE"
cleanup() { rm -f "$COOKIE_FILE"; }
trap cleanup EXIT

log() { echo "[eros-omniroute-bootstrap] $*" >&2; }

login_result="$(curl -s -c "$COOKIE_FILE" -X POST "$BASE_URL/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "$(jq -n --arg p "$DASH_PASSWORD" '{password:$p}')")"
if [ "$(echo "$login_result" | jq -r '.success // false')" != "true" ]; then
  log "FATAL: dashboard login failed - cannot proceed without a valid session."
  exit 2
fi

# --- Desired configuration: logical capability -> current candidate provider ---
# This table is the ONLY place provider identity is named. It is NOT
# read by consumers; LiteLLM's own model_list owns that mapping. It
# exists purely to document intent alongside the connections it
# provisions, so a future G-PI substitution edits one line, not a
# script's control flow.
#
#   logical capability      current candidate
#   ---------------------   -----------------
#   tier1-general            gemini
#   tier1-coding              openai
#   tier2-coding/research     bedrock
#   tier3-quality             bedrock

# --- Desired provider connections: name | provider | secret env var ---
DESIRED_CONNECTIONS=(
  "eros-openai|openai|OPENAI_API_KEY"
  "eros-bedrock|bedrock|BEDROCK_API_KEY"
  "eros-gemini|gemini|GEMINI_API_KEY"
)

DESIRED_CLIENT_KEY_NAME="eros-litellm-sops-backed"

status=0

api_get() { curl -s -b "$COOKIE_FILE" "$BASE_URL$1"; }
api_post() { curl -s -b "$COOKIE_FILE" -X POST "$BASE_URL$1" -H 'Content-Type: application/json' -d "$2"; }

# Fails clearly (not a raw jq crash) if a response is an OmniRoute error
# envelope rather than the expected shape.
require_ok_response() {
  local response="$1" context="$2"
  local err
  err="$(echo "$response" | jq -r '.error // empty' 2>/dev/null || true)"
  if [ -n "$err" ]; then
    log "FATAL: $context returned an error: $err"
    exit 3
  fi
}

existing_connections="$(api_get /api/providers)"
require_ok_response "$existing_connections" "GET /api/providers"

for entry in "${DESIRED_CONNECTIONS[@]}"; do
  IFS='|' read -r name provider secret_var <<< "$entry"

  secret_value="${!secret_var:-}"
  existing_id="$(echo "$existing_connections" | jq -r --arg name "$name" '.connections[]? | select(.name==$name) | .id' | head -1)"

  if [ -z "$secret_value" ]; then
    if [ -n "$existing_id" ]; then
      log "SKIP: $name ($provider) already exists (id=$existing_id) and no $secret_var supplied - leaving unchanged."
      continue
    fi
    log "MISSING INPUT: $secret_var not set and no existing connection named '$name' ($provider) - cannot create. This capability's provider candidate is not bootstrapped in this environment."
    status=1
    continue
  fi

  if [ -n "$existing_id" ]; then
    test_result="$(curl -s -b "$COOKIE_FILE" -X POST "$BASE_URL/api/providers/$existing_id/test")"
    valid="$(echo "$test_result" | jq -r '.valid // false')"
    if [ "$valid" = "true" ]; then
      log "OK: $name ($provider) exists and passes its own connection test - unchanged."
    else
      log "CONFLICT: $name ($provider) exists (id=$existing_id) but fails its connection test. Refusing to silently overwrite a real credential - this needs a human decision (rotate, or confirm the connection should be deleted and recreated)."
      status=1
    fi
    continue
  fi

  log "CREATING: $name ($provider) - did not exist."
  payload="$(jq -n --arg provider "$provider" --arg name "$name" --arg key "$secret_value" \
    '{provider:$provider, authType:"apikey", apiKey:$key, name:$name}')"
  create_result="$(api_post /api/providers "$payload")"
  new_id="$(echo "$create_result" | jq -r '.connection.id // empty')"
  if [ -z "$new_id" ]; then
    log "FAILED to create $name ($provider): $(echo "$create_result" | jq -c 'del(.apiKey)')"
    status=1
    continue
  fi
  log "Created $name ($provider) id=$new_id. Verifying..."
  test_result="$(curl -s -b "$COOKIE_FILE" -X POST "$BASE_URL/api/providers/$new_id/test")"
  valid="$(echo "$test_result" | jq -r '.valid // false')"
  if [ "$valid" != "true" ]; then
    log "CREATED BUT UNHEALTHY: $name ($provider) id=$new_id did not pass its own connection test immediately after creation. Check the supplied $secret_var."
    status=1
  else
    log "OK: $name ($provider) created and verified healthy."
  fi
done

# --- LiteLLM-facing client key ---
existing_keys="$(api_get /api/keys)"
require_ok_response "$existing_keys" "GET /api/keys"
existing_key_id="$(echo "$existing_keys" | jq -r --arg name "$DESIRED_CLIENT_KEY_NAME" '.keys[]? | select(.name==$name) | .id' | head -1)"

if [ -n "$existing_key_id" ]; then
  log "OK: client key '$DESIRED_CLIENT_KEY_NAME' already exists (id=$existing_key_id) - unchanged. (Its value cannot be re-verified by this script; if LiteLLM calls are failing with 401s, that is a CONFLICT requiring rotation, not something this script can silently fix.)"
else
  log "CREATING: client key '$DESIRED_CLIENT_KEY_NAME' - did not exist."
  create_result="$(api_post /api/keys "$(jq -n --arg name "$DESIRED_CLIENT_KEY_NAME" '{name:$name, scopes:["self:usage"]}')")"
  new_key="$(echo "$create_result" | jq -r '.key // empty')"
  if [ -z "$new_key" ]; then
    log "FAILED to create client key: $(echo "$create_result" | jq -c 'del(.key)')"
    status=1
  else
    log "Created client key '$DESIRED_CLIENT_KEY_NAME'. Printing to stdout ONCE - pipe this directly into sops, do not save to a file:"
    echo "$new_key"
  fi
fi

exit $status
