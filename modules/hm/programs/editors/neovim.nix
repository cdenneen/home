{
  config,
  lib,
  vimnix,
  system,
  pkgs,
  ...
}:
let
  cfg = config.programs.nvim;

in
{
  imports = [ vimnix.homeManagerModules.default ];
  options.programs.nvim = {
    enable = lib.mkEnableOption "Enable neovim";
  };

  config = lib.mkIf cfg.enable {
    home.shellAliases.vi = "nvim";
    home.shellAliases.vim = "nvim";

    home.sessionVariables.EDITOR = "nvim";
    home.sessionVariables.VISUAL = "nvim";
  };
}
