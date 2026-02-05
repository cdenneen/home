{ pkgs, ... }:
pkgs.writeShellApplication {
  name = "sops-bootstrap-host";
  runtimeInputs = with pkgs; [ age coreutils ];
  text = ''
    set -euo pipefail

    keydir="/var/lib/sops-nix"
    keyfile="$keydir/key.txt"

    if [ -f "$keyfile" ]; then
      echo "Host AGE key already exists at $keyfile" >&2
      exit 0
    fi

    echo "Generating host AGE key at $keyfile" >&2
    sudo mkdir -p "$keydir"
    sudo age-keygen -o "$keyfile"

    echo "Public AGE key (add to pub/age-recipients.txt):" >&2
    sudo age-keygen -y "$keyfile"
  '';
}
