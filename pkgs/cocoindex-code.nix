{ pkgs }:

pkgs.writeShellApplication {
  name = "ccc";

  runtimeInputs = [ pkgs.uv ];

  text = ''
    set -euo pipefail

    export UV_CACHE_DIR="''${XDG_CACHE_HOME:-$HOME/.cache}/uv"

    exec ${pkgs.uv}/bin/uv tool run --from 'cocoindex-code[full]==0.2.39' --with 'mcp==1.29.0' ccc "$@"
  '';
}
