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
  options.profiles.cdenneen.enable = lib.mkEnableOption "Enable cdenneen profile";

  config = lib.mkIf cfg.cdenneen.enable {
    home.packages =
      with pkgs;
      lib.optionals config.profiles.gui.enable [
        spotify
        discord
      ];
    catppuccin = {
      flavor = "latte";
      accent = "pink";
    };
  };
}
