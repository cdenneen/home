{ pkgs, ... }:
pkgs.writeShellApplication {
  name = "sops-edit";
  runtimeInputs = with pkgs; [
    sops
    age
  ];
  text = ''
    set -euo pipefail
    exec sops secrets/secrets.yaml
  '';
}
