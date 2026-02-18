{
  lib,
  config,
  pkgs,
  ...
}:
lib.mkIf (config ? system && config.system ? stateVersion) {
  security.sudo.extraConfig = ''
    Defaults secure_path="/run/wrappers/bin:/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    # Allow non-interactive sudo for automation.
    Defaults:cdenneen !requiretty
    # Permit Nix and git operations without a password prompt.
    cdenneen ALL=(root) NOPASSWD: ${pkgs.nix}/bin/nix
    cdenneen ALL=(root) NOPASSWD: ${pkgs.git}/bin/git
  ''
  + (lib.optionalString (pkgs ? nixos-rebuild) ''
    # Allow system rebuilds without sudo password prompts.
    cdenneen ALL=(root) NOPASSWD: ${pkgs.nixos-rebuild}/bin/nixos-rebuild
    cdenneen ALL=(root) NOPASSWD: /run/current-system/sw/bin/nixos-rebuild
    cdenneen ALL=(root) NOPASSWD: /nix/var/nix/profiles/system/sw/bin/nixos-rebuild
  '')
  + (lib.optionalString (pkgs ? darwin-rebuild) ''
    # Allow darwin-rebuild in managed macOS hosts.
    cdenneen ALL=(root) NOPASSWD: ${pkgs.darwin-rebuild}/bin/darwin-rebuild
  '')
  + ''
    # Allow Home Manager CLI + wrapper without sudo password prompts.
    cdenneen ALL=(root) NOPASSWD: /run/current-system/sw/bin/home-manager
    cdenneen ALL=(root) NOPASSWD: /nix/var/nix/profiles/system/sw/bin/home-manager
    cdenneen ALL=(root) NOPASSWD: /etc/profiles/per-user/cdenneen/bin/home-manager
    cdenneen ALL=(root) NOPASSWD: /etc/profiles/per-user/cdenneen/bin/hm-switch
  '';
}
