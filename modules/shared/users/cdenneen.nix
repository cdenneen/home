{ pkgs, lib, ... }:
let
  isDarwin = pkgs.stdenv.isDarwin;
  isLinux = pkgs.stdenv.isLinux;
in
{
  programs.zsh.enable = true;

  # User creation is handled centrally in modules/system/users/cdenneen.nix
}
