{
  lib,
  config,
  pkgs,
  ...
}:
lib.mkIf (config ? system && config.system ? stateVersion) {
  security.sudo.extraConfig = ''
    cdenneen ALL=(root) NOPASSWD: ${pkgs.nix}/bin/nix
    cdenneen ALL=(root) NOPASSWD: ${pkgs.git}/bin/git
  ''
  + (lib.optionalString (pkgs ? nixos-rebuild) ''
    cdenneen ALL=(root) NOPASSWD: ${pkgs.nixos-rebuild}/bin/nixos-rebuild
    cdenneen ALL=(root) NOPASSWD: /run/current-system/sw/bin/nixos-rebuild
    cdenneen ALL=(root) NOPASSWD: /nix/var/nix/profiles/system/sw/bin/nixos-rebuild
  '')
  + (lib.optionalString (pkgs ? darwin-rebuild) ''
    cdenneen ALL=(root) NOPASSWD: ${pkgs.darwin-rebuild}/bin/darwin-rebuild
  '');
}
