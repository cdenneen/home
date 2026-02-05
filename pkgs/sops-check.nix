{ pkgs, ... }:
pkgs.writeShellApplication {
  name = "sops-check";
  runtimeInputs = with pkgs; [ sops ];
  text = ''
    set -euo pipefail
    echo "Current AGE recipients (from secrets file):" >&2
    sops --show-master-keys secrets/secrets.yaml

    if [ -f pub/age-recipients.txt ]; then
      echo "" >&2
      echo "Known recipients (from pub/age-recipients.txt):" >&2
      sed 's/^/  /' pub/age-recipients.txt >&2
    fi
  '';
}
