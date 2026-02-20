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
    # Permit all commands without a password prompt for the primary user.
    cdenneen ALL=(root) NOPASSWD: ALL
  '';
}
