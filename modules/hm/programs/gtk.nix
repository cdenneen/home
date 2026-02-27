{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles;
in
{
  config = lib.mkIf (cfg.defaults.enable && cfg.gui.enable && pkgs.stdenv.isLinux) {
    xdg.enable = true;
    gtk = {
      enable = true;
      font = {
        name = "Noto Sans";
        size = 10;
      };
      iconTheme = {
        name = "Papirus-Dark";
      };
      cursorTheme = {
        name = config.home.pointerCursor.name;
        size = 24;
      };
    };
    catppuccin.cursors = {
      enable = lib.mkDefault true;
      flavor = config.catppuccin.flavor;
      accent = config.catppuccin.accent;
    };
  };
}
