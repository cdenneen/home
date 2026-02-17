{ pkgs }:
pkgs.writeShellScriptBin "hm-switch" ''
  set -euo pipefail

  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) arch="x86_64" ;;
    aarch64|arm64) arch="aarch64" ;;
  esac

  case "$os" in
    darwin) system="$arch-darwin" ;;
    linux) system="$arch-linux" ;;
    *)
      echo "hm-switch: unsupported OS: $os" >&2
      exit 2
      ;;
  esac

  export NIX_SYSTEM="$system"
  exec home-manager switch --impure --flake .#cdenneen
''
