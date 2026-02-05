{ pkgs, ... }:
pkgs.writeShellApplication {
  name = "sops-update-keys";
  runtimeInputs = with pkgs; [ sops age ];
  text = ''
    set -euo pipefail
    exec sops updatekeys secrets/secrets.yaml
  '';
}
