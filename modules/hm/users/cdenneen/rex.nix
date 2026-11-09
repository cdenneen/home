{ lib, pkgs, ... }:
{
  programs.rex.enable = true;
  services.rex.enable = lib.mkIf pkgs.stdenv.hostPlatform.isLinux true;
}
