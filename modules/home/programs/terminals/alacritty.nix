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
        shell = {
          program = "${lib.getExe pkgs.bashInteractive}";
          args = [ "-l" ];
        };
        key_bindings = [
          { key = "K"; mods = "Command"; chars = "ClearHistory"; }
          { key = "V"; mods = "Command"; action = "Paste"; }
          { key = "C"; mods = "Command"; action = "Copy"; }
          { key = "Key0"; mods = "Command"; action = "ResetFontSize"; }
          { key = "Equals"; mods = "Command"; action = "IncreaseFontSize"; }
          { key = "Subtract"; mods = "Command"; action = "DecreaseFontSize"; }
        ];
      };
    };
  };
}
