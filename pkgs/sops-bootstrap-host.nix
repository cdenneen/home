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

     pubkey=$(sudo age-keygen -y "$keyfile" | sed 's/^# public key: //')

     echo "" >&2
     echo "Public AGE key:" >&2
     echo "$pubkey" >&2

     echo "" >&2
     echo "Add to pub/age-recipients.txt:" >&2
     echo "$pubkey  # $(hostname) (host)" >&2

     echo "" >&2
     echo "Add to .sops.yaml:" >&2
     echo "  - &server_$(hostname | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '_') $pubkey" >&2
     echo "  - *server_$(hostname | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '_')" >&2
  '';
}
