{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.hyper;
  hostSystem = pkgs.stdenv.hostPlatform.system;
in
{
  options.programs.hyper = {
    enable = lib.mkEnableOption "Hyper terminal emulator";
    package = lib.mkOption {
      type = lib.types.package;
      default = if (hostSystem == "x86_64-linux") then pkgs.hyper else pkgs.emptyDirectory;
    };
    config_file = lib.mkOption {
      type = lib.types.lines;
      default = ''
        module.exports = {
          config: {
            shell: '${lib.getExe pkgs.zsh}',
            shellArgs: [],
            bell: false,
          },
        };
      '';
    };
  };
  config = lib.mkIf cfg.enable {
    home.packages = [ cfg.package ];
    home.file.".hyper.js" = lib.mkIf pkgs.stdenv.hostPlatform.isDarwin { text = cfg.config_file; };
    xdg.configFile."Hyper/.hyper.js" = lib.mkIf pkgs.stdenv.hostPlatform.isLinux { text = cfg.config_file; };
  };
}
