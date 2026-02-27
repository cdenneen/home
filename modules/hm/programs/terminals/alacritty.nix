{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.alacritty;
in
{
  config = lib.mkIf cfg.enable {
    catppuccin.alacritty = {
      enable = true;
      flavor = config.catppuccin.flavor;
    };
    programs.alacritty = {
      settings = {
        font.size = 11.0;
        key_bindings = [
          {
            key = "Equals";
            mods = "Super";
            action = "IncreaseFontSize";
          }
          {
            key = "Plus";
            mods = "Super";
            action = "IncreaseFontSize";
          }
          {
            key = "Minus";
            mods = "Super";
            action = "DecreaseFontSize";
          }
          {
            key = "Key0";
            mods = "Super";
            action = "ResetFontSize";
          }
        ];
        shell = {
          program = "${lib.getExe pkgs.bashInteractive}";
          args = [ "-l" ];
        };
      };
    };
  };
}
