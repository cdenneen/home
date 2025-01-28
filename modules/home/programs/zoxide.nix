{ config, lib, ... }:
let
  cfg = config.programs.zoxide;
in
{
  config = lib.mkIf cfg.enable {
    programs.zoxide = {
      enableBashIntegration = config.programs.bash.enable;
      enableFishIntegration = config.programs.fish.enable;
      enableZshIntegration = config.programs.zsh.enable;
    };
  };
}
