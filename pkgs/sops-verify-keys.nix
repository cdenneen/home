{ pkgs, ... }:
pkgs.writeShellApplication {
  name = "sops-verify-keys";
  runtimeInputs = with pkgs; [ sops gnugrep coreutils ];
  text = ''
    set -euo pipefail

    if [ ! -f pub/age-recipients.txt ]; then
      echo "pub/age-recipients.txt not found" >&2
      exit 1
    fi

    tmp_current=$(mktemp)
    tmp_known=$(mktemp)
    trap 'rm -f "$tmp_current" "$tmp_known"' EXIT

    grep -E 'recipient:[[:space:]]*age1' secrets/secrets.yaml \
      | sed 's/.*recipient:[[:space:]]*//' \
      | sort -u > "$tmp_current"

    awk '{print $1}' pub/age-recipients.txt | sort -u > "$tmp_known"

    undocumented=$(comm -23 "$tmp_current" "$tmp_known" || true)

    if [ -n "$undocumented" ]; then
      echo "One or more AGE recipients are not documented in pub/age-recipients.txt" >&2
      printf '%s\n' "$undocumented" | while read -r line; do
        printf '  %s\n' "$line" >&2
      done
      exit 1
    fi

    echo "All AGE recipients are documented."
  '';
}
