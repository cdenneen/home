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

    mapfile -t current < <(sops --show-master-keys secrets/secrets.yaml | sed 's/^age: //' | sort -u)
    mapfile -t known < <(grep -E '^age1' pub/age-recipients.txt | awk '{print $1}' | sort -u)

    missing=0

    for k in "${current[@]}"; do
      if ! printf '%s\n' "${known[@]}" | grep -qx "$k"; then
        echo "Missing annotation for key: $k" >&2
        missing=1
      fi
    done

    if [ "$missing" -ne 0 ]; then
      echo "One or more AGE recipients are not documented in pub/age-recipients.txt" >&2
      exit 1
    fi

    echo "All AGE recipients are documented."
  '';
}
