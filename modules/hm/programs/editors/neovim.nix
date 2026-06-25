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
    # vimnix bootstraps lazy.nvim from init.lua, so Home Manager plugin wiring
    # is unnecessary here and currently triggers a plugin schema mismatch with
    # recent nixpkgs/home-manager combinations.
    programs.neovim.plugins = lib.mkForce [ ];

    home.shellAliases.vi = "nvim";
    home.shellAliases.vim = "nvim";

    home.sessionVariables.EDITOR = "nvim";
    home.sessionVariables.VISUAL = "nvim";
  };
}
