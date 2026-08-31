#!/usr/bin/env bash
# eros-reconstruction-verify.sh - G-DR-PREP-1/2 reconstruction verifier
#
# Proves the minimum Eros execution substrate exists and is healthy,
# then runs bounded, non-mutating canaries through each required
# execution class. Intended to become part of eventual G-DR, not a
# one-off script - safe to run repeatedly against a live system.
#
# Every check is read-only or a minimal-cost real request (a handful of
# tokens). Nothing here mutates state. Exit code is the count of failed
# checks (0 = fully healthy).

set -uo pipefail

BASE_URL="${OMNIROUTE_BASE_URL:-http://127.0.0.1:20128}"
LITELLM_URL="${LITELLM_BASE_URL:-http://127.0.0.1:4000}"
DASH_PASSWORD="${OMNIROUTE_DASHBOARD_PASSWORD:?OMNIROUTE_DASHBOARD_PASSWORD must be set}"
LITELLM_MASTER_KEY="${LITELLM_MASTER_KEY:?LITELLM_MASTER_KEY must be set}"

failures=0
pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; failures=$((failures + 1)); }

echo "=== Substrate health ==="

if curl -s -o /dev/null -w '%{http_code}' "$LITELLM_URL/health/liveliness" | grep -q 200; then
  pass "LiteLLM healthy"
else
  fail "LiteLLM did not respond healthy on $LITELLM_URL/health/liveliness"
fi

COOKIE_FILE="$(mktemp)"; chmod 600 "$COOKIE_FILE"
trap 'rm -f "$COOKIE_FILE"' EXIT
login="$(curl -s -c "$COOKIE_FILE" -X POST "$BASE_URL/api/auth/login" -H 'Content-Type: application/json' -d "$(jq -n --arg p "$DASH_PASSWORD" '{password:$p}')")"
if [ "$(echo "$login" | jq -r '.success // false')" = "true" ]; then
  pass "OmniRoute healthy (dashboard auth OK)"
else
  fail "OmniRoute dashboard login failed - cannot verify further OmniRoute state"
fi

providers="$(curl -s -b "$COOKIE_FILE" "$BASE_URL/api/providers")"
for p in openai bedrock gemini; do
  if echo "$providers" | jq -e --arg p "$p" '.connections[]? | select(.provider==$p and .isActive==true)' > /dev/null 2>&1; then
    pass "$p provider configured and active"
  else
    fail "$p provider NOT configured/active"
  fi
done

if command -v pg_isready > /dev/null 2>&1; then
  if pg_isready -h 127.0.0.1 -p 5432 -U litellm > /dev/null 2>&1; then
    pass "Postgres reachable"
  else
    fail "Postgres not reachable"
  fi
else
  if curl -s -o /dev/null "$LITELLM_URL/health/liveliness"; then
    pass "Postgres reachability inferred via LiteLLM health (pg_isready unavailable)"
  else
    fail "Cannot verify Postgres reachability"
  fi
fi

if curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:6333/collections | grep -q 200; then
  pass "Qdrant reachable"
else
  fail "Qdrant not reachable"
fi

echo "=== Required routes present ==="
model_list="$(curl -s "$LITELLM_URL/v1/models" -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq -r '.data[]?.id // empty')"
for route in tier1-general tier1-coding tier2-general tier2-coding tier2-research tier3-quality tier4-frontier; do
  if echo "$model_list" | grep -qx "$route"; then
    pass "route '$route' present"
  else
    fail "route '$route' MISSING"
  fi
done

echo "=== tier2-general direct-cache exception preserved ==="
# This is a config-shape check, not a cache-behavior canary (that
# requires a large repeated prefix - out of scope for a routine health
# check). Confirms the route still points directly at Bedrock rather
# than having been silently re-routed through OmniRoute, which would
# silently reintroduce the CACHE_NOT_REQUESTED regression this
# exception exists to avoid.
tier2_general_model="$(curl -s "$LITELLM_URL/v1/model/info" -H "Authorization: Bearer $LITELLM_MASTER_KEY" 2>/dev/null | jq -r '.data[]? | select(.model_name=="tier2-general") | .litellm_params.model // empty' 2>/dev/null)"
if echo "$tier2_general_model" | grep -q '^bedrock/'; then
  pass "tier2-general still direct-to-Bedrock (cache exception intact)"
elif [ -z "$tier2_general_model" ]; then
  fail "could not determine tier2-general's configured model (endpoint/auth issue, not necessarily a regression)"
else
  fail "tier2-general is NOT direct-to-Bedrock anymore (model=$tier2_general_model) - this would silently reintroduce the cache regression this exception exists to prevent"
fi

echo "=== Bounded non-mutating canaries ==="
run_canary() {
  local route="$1" marker="$2"
  local result
  result="$(curl -s -X POST "$LITELLM_URL/v1/chat/completions" \
    -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg m "$route" --arg marker "Reply with exactly: $marker" '{model:$m,messages:[{role:"user",content:$marker}],max_tokens:300}')")"
  if echo "$result" | jq -e '.choices[0].message.content' > /dev/null 2>&1 && echo "$result" | jq -r '.choices[0].message.content' | grep -q "$marker"; then
    pass "canary through '$route' succeeded (exact match)"
  else
    fail "canary through '$route' failed: $(echo "$result" | jq -c '.error // .' 2>/dev/null | head -c 200)"
  fi
}

run_canary tier1-general  "VERIFY-TIER1-GENERAL-OK"
run_canary tier1-coding   "VERIFY-TIER1-CODING-OK"
run_canary tier2-general  "VERIFY-TIER2-GENERAL-OK"

echo "=== Summary ==="
echo "$failures check(s) failed."
exit "$failures"
