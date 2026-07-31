{ lib, ... }:
let
  flavorType = lib.types.enum [
    "latte"
    "frappe"
    "macchiato"
    "mocha"
  ];
  accentType = lib.types.enum [
    "rosewater"
    "flamingo"
    "pink"
    "mauve"
    "red"
    "maroon"
    "peach"
    "yellow"
    "green"
    "teal"
    "sky"
    "sapphire"
    "blue"
    "lavender"
  ];
  integrationOption = {
    enable = lib.mkEnableOption "the no-op Catppuccin compatibility integration";
    flavor = lib.mkOption {
      type = flavorType;
      default = "mocha";
      description = "Catppuccin flavor retained for configuration compatibility.";
    };
    accent = lib.mkOption {
      type = accentType;
      default = "red";
      description = "Catppuccin accent retained for configuration compatibility.";
    };
  };
in
{
  # Current Catppuccin assets pull build tools that are not safe on Big Sur.
  # Define only the option surface used by this repository; all integrations
  # intentionally produce no configuration or packages.
  options.catppuccin = {
    enable = lib.mkEnableOption "the no-op Catppuccin compatibility module";
    flavor = lib.mkOption {
      type = flavorType;
      default = "mocha";
      description = "Catppuccin flavor retained for configuration compatibility.";
    };
    accent = lib.mkOption {
      type = accentType;
      default = "red";
      description = "Catppuccin accent retained for configuration compatibility.";
    };
  }
  // lib.genAttrs [
    "alacritty"
    "bat"
    "cursors"
    "delta"
    "fzf"
    "helix"
    "kitty"
    "lazygit"
    "nvim"
    "rio"
    "starship"
    "swaync"
    "tmux"
  ] (_: integrationOption);

  # Home Manager 25.05 configures Delta under programs.git.
  config.programs.git.delta.enable = true;
}
