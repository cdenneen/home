{
  config,
  lib,
  vimnix,
  pkgs,
  ...
}:
let
  cfg = config.programs.nvim;
  vimnixRoot = vimnix.outPath;
  mkIfExists = path: if builtins.pathExists path then path else pkgs.emptyFile;
in
{
  options.programs.nvim = {
    enable = lib.mkEnableOption "Enable neovim";
  };

  config = lib.mkIf cfg.enable {
    home.packages = with pkgs; [
      neovim-unwrapped
      biome
      clang-tools
      gh
      go
      gofumpt
      gotools
      gopls
      jq
      luajitPackages.jsregexp
      luajitPackages.luarocks
      mdformat
      nixd
      pyright
      ripgrep
      rust-analyzer
      ruff
      stylua
      taplo
      tree-sitter
      typescript
      typescript-language-server
      yamlfmt
    ];

    home.file = {
      ".config/nvim/init.lua".source = "${vimnixRoot}/init.lua";
      ".config/nvim/lua".source = mkIfExists "${vimnixRoot}/lua";
    };

    home.shellAliases.vi = "nvim";
    home.shellAliases.vim = "nvim";

    home.sessionVariables.EDITOR = "nvim";
    home.sessionVariables.VISUAL = "nvim";
  };
}
