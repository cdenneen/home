{
  config,
  lib,
  nixvim,
  system,
  pkgs,
  ...
}:
let
  cfg = config.programs.nvim;

  nixvimUpstream = nixvim.inputs.nixvim;
  nixvimConfigModule = import "${nixvim.outPath}/config";

  nvim = nixvimUpstream.legacyPackages.${system}.makeNixvimWithModule {
    inherit pkgs;
    extraSpecialArgs = import "${nixvim.outPath}/lib" { inherit pkgs; };
    module =
      {
        lib,
        pkgs,
        ...
      }:
      {
        imports = [ nixvimConfigModule ];

        # nixpkgs currently ships nvim-treesitter-refactor with a dependency on
        # nvim-treesitter-legacy, which conflicts with the normal nvim-treesitter.
        plugins.treesitter-refactor = lib.mkForce { enable = false; };

        # Keep nixvim buildable without unfree Copilot LSP.
        plugins.copilot-lsp.enable = lib.mkForce false;
        plugins.sidekick.enable = lib.mkForce false;

        # Avoid Linux-only dependencies in PATH.
        extraPackages = lib.mkForce (with pkgs; [
          ripgrep
          lazygit
          fzf
          fd
        ]);
      };
  };
in
{
  options.programs.nvim.enable = lib.mkEnableOption "Enable neovim";

  config = lib.mkIf cfg.enable {
    home.packages = [
      nvim
    ];

    home.shellAliases.vi = "nvim";
    home.shellAliases.vim = "nvim";

    home.sessionVariables.EDITOR = "nvim";
    home.sessionVariables.VISUAL = "nvim";
  };
}
