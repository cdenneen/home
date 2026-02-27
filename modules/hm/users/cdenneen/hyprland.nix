{
  config,
  lib,
  pkgs,
  ...
}:
let
  terminal = "alacritty";
  theme = "catppuccin";
  palettes = {
    catppuccin = {
      # Mocha palette
      bg = "1e1e2e";
      bg_alt = "181825";
      fg = "cdd6f4";
      fg_dim = "a6adc8";
      accent = "89b4fa";
      accent_alt = "f5c2e7";
      border = "313244";
    };
    tokyonight = {
      bg = "1a1b26";
      bg_alt = "16161e";
      fg = "c0caf5";
      fg_dim = "9aa5ce";
      accent = "7aa2f7";
      accent_alt = "bb9af7";
      border = "2a2e3f";
    };
  };
  colors = palettes.${theme};
in
{
  config = lib.mkIf (pkgs.stdenv.isLinux && config.profiles.gui.enable) {
    home.packages = with pkgs; [
      cliphist
      fuzzel
      networkmanagerapplet
      networkmanager-openconnect
      openconnect
      polkit_gnome
      swaynotificationcenter
      waybar
      wl-clipboard
    ];

    programs.fuzzel.enable = true;
    xdg.configFile."fuzzel/fuzzel.ini".text = ''
      font=JetBrainsMono Nerd Font:style=Medium:size=12
      icon-theme=Papirus-Dark
      lines=10
      width=50
      horizontal-pad=24
      vertical-pad=14
      inner-pad=8
      prompt="> "
      background=${colors.bg}e6
      text-color=${colors.fg}ff
      selection-color=${colors.accent}ff
      border-color=${colors.border}ff
      border-width=2
    '';
    programs.waybar.enable = true;
    xdg.configFile."waybar/config".text = ''
      {
        "layer": "top",
        "position": "top",
        "height": 30,
        "spacing": 8,
        "modules-left": ["hyprland/workspaces", "hyprland/window"],
        "modules-center": ["clock"],
        "modules-right": ["network", "pulseaudio", "battery", "tray"],
        "clock": {
          "format": "{:%a %b %d  %I:%M %p}"
        },
        "network": {
          "format-wifi": "  {essid} {signalStrength}%",
          "format-ethernet": "󰈁  {ipaddr}",
          "format-disconnected": "󰤮  Disconnected"
        },
        "pulseaudio": {
          "format": "󰕾  {volume}%",
          "format-muted": "󰝟  Muted"
        },
        "battery": {
          "format": "{icon}  {capacity}%",
          "format-charging": "󰂄  {capacity}%",
          "format-plugged": "󰂄  {capacity}%",
          "format-alt": "{time}  {capacity}%",
          "format-icons": ["󰁺", "󰁻", "󰁼", "󰁽", "󰁾", "󰁿", "󰂀", "󰂁", "󰂂", "󰁹"]
        }
      }
    '';
    xdg.configFile."waybar/style.css".text = ''
      * {
        font-family: "Monaspace Neon", "JetBrainsMono Nerd Font";
        font-size: 12px;
      }
      window#waybar {
        background: #${colors.bg}d9;
        color: #${colors.fg};
        border-bottom: 1px solid #${colors.border};
      }
      #workspaces button {
        padding: 0 6px;
        margin: 2px 2px;
        border-radius: 6px;
        color: #${colors.fg_dim};
      }
      #workspaces button.active {
        background: #${colors.accent}55;
        color: #${colors.fg};
      }
      #clock, #network, #pulseaudio, #battery, #tray, #window {
        padding: 0 8px;
        margin: 2px 0;
      }
    '';

    services.swaync.enable = true;
    catppuccin.swaync.enable = false;
    xdg.configFile."swaync/config.json".text = ''
      {
        "positionX": "right",
        "positionY": "top",
        "layer": "overlay",
        "control-center-margin-top": 10,
        "control-center-margin-right": 10,
        "control-center-margin-left": 0,
        "control-center-margin-bottom": 0,
        "fit-to-screen": true,
        "timeout": 6,
        "timeout-low": 4,
        "timeout-critical": 0
      }
    '';
    xdg.configFile."swaync/style.css".text = ''
      * {
        font-family: "Monaspace Neon", "JetBrainsMono Nerd Font";
        font-size: 12px;
      }
      .control-center, .notification {
        background: #${colors.bg_alt}f2;
        color: #${colors.fg};
        border: 1px solid #${colors.border};
        border-radius: 10px;
      }
      .notification {
        padding: 10px;
      }
    '';

    wayland.windowManager.hyprland = {
      enable = true;
      settings = {
        "$mod" = "SUPER";
        monitor = [ ",preferred,auto,1" ];
        exec-once = [
          "dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP=Hyprland"
          "${pkgs.polkit_gnome}/libexec/polkit-gnome-authentication-agent-1"
          "${pkgs.networkmanagerapplet}/bin/nm-applet --indicator"
          "${pkgs.waybar}/bin/waybar"
          "${pkgs.swaynotificationcenter}/bin/swaync"
          "${pkgs.wl-clipboard}/bin/wl-paste --watch ${pkgs.cliphist}/bin/cliphist store"
        ];
        env = [
          "XDG_CURRENT_DESKTOP,Hyprland"
          "XDG_SESSION_TYPE,wayland"
          "XCURSOR_SIZE,24"
          "HYPRCURSOR_SIZE,24"
        ];
        general = {
          gaps_in = 4;
          gaps_out = 8;
          border_size = 2;
          "col.active_border" = "rgba(${colors.accent}ee)";
          "col.inactive_border" = "rgba(${colors.border}aa)";
        };
        decoration = {
          rounding = 6;
          blur = {
            enabled = true;
            size = 6;
            passes = 2;
          };
        };
        windowrule = [
          "match:class nm-connection-editor, float = on"
          "match:class nm-applet, float = on"
          "match:class blueman-manager, float = on"
          "match:class pavucontrol, float = on"
          "match:class org.gnome.Settings, float = on"
          "match:class nm-connection-editor, size 900 600"
        ];
        animations = {
          enabled = true;
        };
        input = {
          kb_layout = "us";
          follow_mouse = 1;
          touchpad = {
            natural_scroll = true;
            tap-to-click = true;
          };
        };
        misc = {
          disable_splash_rendering = true;
          vrr = 2;
        };
        bind = [
          "$mod, Return, exec, ${terminal}"
          "$mod SHIFT, Return, exec, ghostty"
          "$mod, D, exec, fuzzel"
          "$mod, V, exec, ${pkgs.cliphist}/bin/cliphist list | ${pkgs.fuzzel}/bin/fuzzel --dmenu | ${pkgs.cliphist}/bin/cliphist decode | ${pkgs.wl-clipboard}/bin/wl-copy"
          "$mod, N, exec, ${pkgs.swaynotificationcenter}/bin/swaync-client -t"
          "$mod, Q, killactive"
          "$mod, M, exit"
          "$mod, F, fullscreen"
          "$mod, Space, togglefloating"
          "$mod, H, movefocus, l"
          "$mod, J, movefocus, d"
          "$mod, K, movefocus, u"
          "$mod, L, movefocus, r"
          "$mod ALT, left, movewindow, l"
          "$mod ALT, down, movewindow, d"
          "$mod ALT, up, movewindow, u"
          "$mod ALT, right, movewindow, r"
          "$mod ALT SHIFT, left, swapwindow, l"
          "$mod ALT SHIFT, down, swapwindow, d"
          "$mod ALT SHIFT, up, swapwindow, u"
          "$mod ALT SHIFT, right, swapwindow, r"
          "$mod, T, workspaceopt, orientation:toggle"
        ];
      };
    };
  };
}
