{ pkgs }:

pkgs.writeShellScriptBin "openclawd-attach" ''
  set -euo pipefail

  url="''${OPENCLAWD_GATEWAY_URL:-ws://100.80.58.4:18789}"
  token_file="''${OPENCLAWD_GATEWAY_TOKEN_FILE:-$HOME/.config/openclaw/gateway.token}"

  if [ ! -r "$token_file" ]; then
    echo "openclawd-attach: token file not readable: $token_file" >&2
    exit 1
  fi

  sessions_json="$(${pkgs.openclaw-gateway}/bin/openclaw sessions --json --url "$url" --token-file "$token_file")"
  if [ -z "$sessions_json" ]; then
    echo "openclawd-attach: no sessions found" >&2
    exit 1
  fi

  lines="$(${pkgs.jq}/bin/jq -r '
    (.sessions // .items // [])
    | map({
        key: (.key // .sessionKey // .id // ""),
        label: (.label // ""),
        agent: (.agentId // ""),
        model: (.model // ""),
        updated: (.updatedAt // .lastActive // "")
      })
    | map(select(.key != ""))
    | map([.key, .label, .agent, .model, .updated] | @tsv)
    | .[]' <<<"$sessions_json")"

  if [ -z "$lines" ]; then
    echo "openclawd-attach: no sessions found" >&2
    exit 1
  fi

  if command -v ${pkgs.fzf}/bin/fzf >/dev/null 2>&1; then
    picked=$(printf '%s\n' "$lines" | ${pkgs.fzf}/bin/fzf --delimiter=$'\t' --with-nth=1,2,3,4 --prompt='session> ')
  else
    i=1
    while IFS= read -r line; do
      printf '%2d. %s\n' "$i" "$line"
      i=$((i + 1))
    done <<<"$lines"
    printf 'Select a session number: '
    read -r choice
    if ! [[ "$choice" =~ ^[0-9]+$ ]]; then
      exit 1
    fi
    picked=$(printf '%s\n' "$lines" | sed -n "''${choice}p")
  fi

  if [ -z "''${picked:-}" ]; then
    exit 1
  fi

  session_key=$(printf '%s' "$picked" | cut -f1)
  if [ -z "$session_key" ]; then
    echo "openclawd-attach: failed to parse session key" >&2
    exit 1
  fi

  exec ${pkgs.openclaw-gateway}/bin/openclaw acp client --server-args --url "$url" --token-file "$token_file" --session "$session_key"
''
