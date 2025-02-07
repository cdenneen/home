{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.hyprland;
in
{
  config = lib.mkIf cfg.enable {
    services = {
      xserver = {
        enable = false;
      };
      displayManager.defaultSession = "hyprland";
      displayManager.sddm.wayland.enable = true;
      libinput.enable = true;
    };
    programs.hyprland = {
      xwayland.enable = true;
    };
    programs.waybar = {
      enable = true;
    };
    environment.sessionVariables = {
      NIXOS_OZONE_WL = 1;
      MOZ_ENABLE_WAYLAND = 1;
      XDG_CURRENT_DESKTOP = "Hyprland";
      XDG_SESSION_DESKTOP = "Hyprland";
      XDG_SESSION_TYPE = "wayland";
      GDK_BACKEND = "wayland,x11";
      QT_QPA_PLATFORM = "wayland;xcb";
    };
    environment.systemPackages = with pkgs; [
      foot
      waybar
      rofi-wayland
      wl-clipboard
      hyprland
      vesktop
      yazi
      xfce.thunar
      xfce.thunar-archive-plugin
      xfce.thunar-volman
      xfce.tumbler
      ffmpegthumbnailer
      gvfs
      dunst # notifications
      grim # screenshots
      slurp
      swaybg
      swaylock-effects
      hyprpaper
      zathura
    ];
    services.gvfs.enable = true;
    services.udisks2.enable = true;
    services.devmon.enable = true;
    xdg.portal = {
      enable = true;
      config = {
        common = {
          default = [ "hyprland" ];
        };
        hyprland = {
          default = [
            "gtk"
            "hyprland"
          ];
        };
      };
      xdgOpenUsePortal = true;
      extraPortals = with pkgs; [
        xdg-desktop-portal-gtk
        xdg-desktop-portal-hyprland
      ];
    };
    programs.thunar = {
      enable = true;
      plugins = with pkgs.xfce; [
        thunar-archive-plugin
        thunar-volman
      ];
    };
  };
}
