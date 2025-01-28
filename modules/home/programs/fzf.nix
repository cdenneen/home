{ config, lib, ... }:
let
  cfg = config.programs.fzf;
in
{
  config = lib.mkIf cfg.enable {
    catppuccin.fzf = {
      enable = true;
      flavor = config.catppuccin.flavor;
    };
    programs.fzf = {
      enableBashIntegration = config.programs.bash.enable;
      enableZshIntegration = config.programs.zsh.enable;
      enableFishIntegration = config.programs.fish.enable;
      tmux.enableShellIntegration = config.programs.tmux.enable;
    };
  };
}
