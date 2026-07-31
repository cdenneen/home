{ lib, ... }:
{
  programs.opencode.enable = lib.mkForce false;
  profiles.gui.ghostty.softwareRenderer = true;
  xdg.desktopEntries."ghostty-safe" = {
    name = "Ghostty (Safe)";
    genericName = "Terminal";
    categories = [
      "System"
      "TerminalEmulator"
    ];
    icon = "ghostty";
    exec = "env GDK_BACKEND=wayland,x11 LIBGL_ALWAYS_SOFTWARE=1 ghostty";
    terminal = false;
  };
  wayland.windowManager.hyprland.enable = lib.mkForce false;
}
