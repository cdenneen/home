{ pkgs, ... }:
pkgs.writeShellApplication {
  name = "sops-diff-keys";
  runtimeInputs = with pkgs; [ sops diffutils gnugrep ];
  text = ''
    set -euo pipefail

    tmp_old=$(mktemp)
    tmp_new=$(mktemp)

    sops --show-master-keys secrets/secrets.yaml | sort > "$tmp_old"

    # Trigger updatekeys calculation without modifying the file
    if sops updatekeys secrets/secrets.yaml </dev/null 2>/dev/null; then
      true
    fi

    sops --show-master-keys secrets/secrets.yaml | sort > "$tmp_new"

    echo "Diff of AGE recipients (current -> computed):"
    diff -u "$tmp_old" "$tmp_new" || true

    if [ -f pub/age-recipients.txt ]; then
      echo ""
      echo "Reference (pub/age-recipients.txt):"
      sed 's/^/  /' pub/age-recipients.txt
    fi

    rm -f "$tmp_old" "$tmp_new"
  '';
}
