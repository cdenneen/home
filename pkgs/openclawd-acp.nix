{ pkgs }:

pkgs.writeShellScriptBin "openclawd-acp" ''
  set -euo pipefail

  url="''${OPENCLAWD_GATEWAY_URL:-ws://100.80.58.4:18789}"
  token_file="''${OPENCLAWD_GATEWAY_TOKEN_FILE:-$HOME/.config/openclaw/gateway.token}"

  if [ ! -r "$token_file" ]; then
    echo "openclawd-acp: token file not readable: $token_file" >&2
    exit 1
  fi

  exec ${pkgs.openclaw-gateway}/bin/openclaw acp --url "$url" --token-file "$token_file" "$@"
''
