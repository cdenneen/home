{ pkgs, lib, ... }:
let
  isDarwin = pkgs.stdenv.hostPlatform.isDarwin;
  isLinux = pkgs.stdenv.hostPlatform.isLinux;
in
{
  programs.zsh.enable = true;

  # User creation is handled centrally in modules/system/users/cdenneen.nix
}
