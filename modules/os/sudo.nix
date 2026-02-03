{
  lib,
  config,
  pkgs,
  ...
}:

# This module is NixOS-only. nix-darwin does not provide compatible sudo options
# in this context, so it must evaluate to an empty attrset there.
lib.mkIf (config ? system && config.system ? stateVersion) {
  security.sudo.extraConfig = ''
    cdenneen ALL=(root) NOPASSWD: ${pkgs.nixos-rebuild}/bin/nixos-rebuild
    cdenneen ALL=(root) NOPASSWD: ${pkgs.nix}/bin/nix
    cdenneen ALL=(root) NOPASSWD: ${pkgs.git}/bin/git
  '';
}
