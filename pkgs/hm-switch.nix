{ pkgs }:
pkgs.writeShellScriptBin "hm-switch" ''
  set -euo pipefail

  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  case "$os" in
    darwin)
      host="$(scutil --get HostName 2>/dev/null || hostname -s)"
      ;;
    linux)
      if [ -r /etc/hostname ]; then
        host="$(cat /etc/hostname | tr -d '\n\r')"
      else
        host="$(hostname -s)"
      fi
      ;;
    *)
      echo "hm-switch: unsupported OS: $os" >&2
      exit 2
      ;;
  esac

  if [ -z "${"host:-"}" ]; then
    echo "hm-switch: unable to determine hostname" >&2
    exit 2
  fi

  exec home-manager switch --flake .#cdenneen@"$host"
''
