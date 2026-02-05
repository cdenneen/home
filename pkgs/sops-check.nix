{ pkgs, ... }:
pkgs.writeShellApplication {
  name = "sops-check";
  runtimeInputs = with pkgs; [ sops ];
  text = ''
    set -euo pipefail
    echo "AGE recipients:" >&2
    sops -d --extract '["sops"]["age"]' secrets/secrets.yaml
  '';
}
