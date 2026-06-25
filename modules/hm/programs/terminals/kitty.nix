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
        size = 11;
      };
      settings = {
        shell = "${lib.getExe pkgs.zsh}";
      };
      extraConfig = ''
        map super+plus change_font_size all +1.0
        map super+equal change_font_size all +1.0
        map super+minus change_font_size all -1.0
        map super+0 change_font_size all 0
      '';
    };
  };
}
