{ lib, pkgs, ... }:
{
  programs.rex.enable = true;
}
// lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux {
  services.rex.enable = true;
}
