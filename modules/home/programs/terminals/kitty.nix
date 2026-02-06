{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.kitty;
in
{
  config = lib.mkIf cfg.enable {
    catppuccin.kitty = {
      enable = true;
      flavor = config.catppuccin.flavor;
    };
    programs.kitty = {
      font = {
        name = "JetBrainsMono Nerd Font Mono";
        size = 14;
      };
      settings = {
        shell = "${lib.getExe pkgs.zsh}";
      };
      extraConfig = "";
    };
  };
}
