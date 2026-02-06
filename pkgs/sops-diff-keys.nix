{ pkgs, ... }:
pkgs.writeShellApplication {
  name = "sops-diff-keys";
  runtimeInputs = with pkgs; [
    sops
    diffutils
    gnugrep
  ];
  text = ''
    set -euo pipefail

    tmp_current=$(mktemp)
    tmp_known=$(mktemp)
    trap 'rm -f "$tmp_current" "$tmp_known"' EXIT

    # Current recipients parsed directly from secrets file
    grep -E 'recipient:[[:space:]]*age1' secrets/secrets.yaml \
      | sed 's/.*recipient:[[:space:]]*//' \
      | sort -u > "$tmp_current"

    # Documented recipients
    awk '{print $1}' pub/age-recipients.txt | sort -u > "$tmp_known"

    echo "Diff of AGE recipients (secrets.yaml -> pub/age-recipients.txt):"
    diff -u "$tmp_current" "$tmp_known" || true

    if [ -f pub/age-recipients.txt ]; then
      echo ""
      echo "Reference (pub/age-recipients.txt):"
      sed 's/^/  /' pub/age-recipients.txt
    fi

    # cleanup handled by trap
  '';
}
