{
  pkgs,
  lib,
  config,
  ...
}:
{
  home.packages =
    with pkgs;
    lib.mkIf (stdenv.isLinux && config.profiles.gui.enable) [
      ghostty
    ];
  xdg.configFile."ghostty/config".text = ''
    background-opacity = 0.8
    font-family = JetBrainsMono Nerd Font Mono
    theme = light:Catppuccin Latte,dark:Catppuccin Mocha
    command = ${lib.getExe pkgs.zsh}
    confirm-close-surface = false
    quit-after-last-window-closed = true
  '';
}
