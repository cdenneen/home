{
  config,
  pkgs,
  lib,
  ...
}:
let
  cfg = config.programs.ghostty;
in
{
  config = lib.mkIf cfg.enable {
    catppuccin.ghostty = {
      enable = true;
      flavor = config.catppuccin.flavor;
    };
    home.packages =
      with pkgs;
      lib.mkIf (stdenv.isLinux && config.profiles.gui.enable) [
        ghostty
      ];
    xdg.configFile."ghostty/config".text = ''
      background-opacity = 0.8
      font-family = MonaspiceAr Nerd Font Mono
      font-feature = calt
      font-feature = ss01
      font-feature = ss02
      font-feature = ss03
      font-feature = ss04
      font-feature = ss05
      font-feature = ss06
      font-feature = ss07
      font-feature = ss08
      font-feature = ss09
      font-feature = liga
      theme = catppuccin-frappe
      command = ${lib.getExe pkgs.fish}
      confirm-close-surface = false
      quit-after-last-window-closed = true

      macos-non-native-fullscreen = visible-menu
      macos-option-as-alt = left
      mouse-hide-while-typing = true

      # Keybinds to match macOS since this is a VM
      keybind = super+c=copy_to_clipboard
      keybind = super+v=paste_from_clipboard
      keybind = super+shift+c=copy_to_clipboard
      keybind = super+shift+v=paste_from_clipboard
      keybind = super+equal=increase_font_size:1
      keybind = super+minus=decrease_font_size:1
      keybind = super+zero=reset_font_size
      keybind = super+q=quit
      keybind = super+shift+comma=reload_config
      keybind = super+k=clear_screen
      keybind = super+n=new_window
      keybind = super+w=close_surface
      keybind = super+shift+w=close_window
      keybind = super+t=new_tab
      keybind = super+shift+left_bracket=previous_tab
      keybind = super+shift+right_bracket=next_tab
      keybind = super+d=new_split:right
      keybind = super+shift+d=new_split:down
      keybind = super+right_bracket=goto_split:next
      keybind = super+left_bracket=goto_split:previous
    '';
  };
}
