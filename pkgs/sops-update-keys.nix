{ pkgs, ... }:
pkgs.writeShellApplication {
  name = "sops-update-keys";
  runtimeInputs = with pkgs; [ sops age ];
  text = ''
    set -euo pipefail
    exec env SOPS_NO_EDITOR=1 sops updatekeys secrets/secrets.yaml
  '';
}
