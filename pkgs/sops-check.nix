{ pkgs, ... }:
pkgs.writeShellApplication {
  name = "sops-check";
  runtimeInputs = with pkgs; [ sops ];
  text = ''
    set -euo pipefail
    echo "Current AGE recipients (from secrets file):" >&2
    # Extract AGE recipients directly from sops metadata
    # Handle both formats:
    #  - recipient: age1...
    #  - - age1...
    grep -E 'recipient:[[:space:]]*age1' secrets/secrets.yaml \
      | sed 's/.*recipient:[[:space:]]*//' \
      | sort -u >&2

    if [ -f pub/age-recipients.txt ]; then
      echo "" >&2
      echo "Known recipients (from pub/age-recipients.txt):" >&2
      sed 's/^/  /' pub/age-recipients.txt >&2
    fi
  '';
}
