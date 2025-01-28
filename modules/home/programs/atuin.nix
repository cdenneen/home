{ config, lib, ... }:
let
  cfg = config.programs.atuin;
in
{
  config = lib.mkIf cfg.enable {
    programs.atuin = {
      enableBashIntegration = config.programs.bash.enable;
      enableFishIntegration = config.programs.fish.enable;
      enableZshIntegration = config.programs.zsh.enable;
      flags = [ "--disable-up-arrow" ];
      settings = {
        auto_sync = true;
      };
    };
  };
}
