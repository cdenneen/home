{ lib, pkgs, ... }:
{
  networking.hostName = "VNJTECMBCD";

  system.stateVersion = 6;

  home-manager.users.cdenneen = {
    # Tokyo Night (TUI focus) for Ghostty + Neovim.
    xdg.configFile."ghostty/themes/tokyonight-night".text = ''
      background = 1a1b26
      foreground = c0caf5
      cursor-color = c0caf5
      selection-background = 33467c
      selection-foreground = c0caf5

      # ANSI colors
      palette = 0=15161e
      palette = 1=f7768e
      palette = 2=9ece6a
      palette = 3=e0af68
      palette = 4=7aa2f7
      palette = 5=bb9af7
      palette = 6=7dcfff
      palette = 7=a9b1d6
      palette = 8=414868
      palette = 9=f7768e
      palette = 10=9ece6a
      palette = 11=e0af68
      palette = 12=7aa2f7
      palette = 13=bb9af7
      palette = 14=7dcfff
      palette = 15=c0caf5
    '';

    # Override the shared Ghostty config to use Tokyo Night.
    xdg.configFile."ghostty/config".text = lib.mkForce ''
      background-opacity = 0.8
      font-family = JetBrainsMono Nerd Font Mono
      theme = tokyonight-night
      command = ${lib.getExe pkgs.zsh}
      confirm-close-surface = false
      quit-after-last-window-closed = true
    '';

  };
}
