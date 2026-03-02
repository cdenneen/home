{ pkgs }:

pkgs.writeShellScriptBin "openclawd-acp" ''
  set -euo pipefail

  url="''${OPENCLAWD_GATEWAY_URL:-wss://nyx.tail0e55.ts.net}"
  token_file="''${OPENCLAWD_GATEWAY_TOKEN_FILE:-$HOME/.config/openclaw/gateway.token}"

  if [ ! -r "$token_file" ]; then
    echo "openclawd-acp: token file not readable: $token_file" >&2
    exit 1
  fi

  export OPENCLAW_GATEWAY_URL="$url"
  export OPENCLAW_GATEWAY_TOKEN_FILE="$token_file"
  export OPENCLAW_GATEWAY_TOKEN="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$token_file")"

  exec ${pkgs.openclaw-gateway}/bin/openclaw acp "$@"
''
