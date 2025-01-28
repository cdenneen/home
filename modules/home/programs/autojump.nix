{ config, lib, ... }:
let
  cfg = config.programs.autojump;
in
{
  config = lib.mkIf cfg.enable {
    programs.autojump = {
      enableBashIntegration = config.programs.bash.enable;
      enableFishIntegration = config.programs.fish.enable;
      enableZshIntegration = config.programs.zsh.enable;
    };
  };
}
