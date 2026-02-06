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

        colorscheme = cfg.colorscheme;

        # Override the upstream nixvim config's default theme.
        colorschemes.catppuccin.enable = lib.mkForce (cfg.colorscheme == "catppuccin");
        colorschemes.tokyonight.enable = lib.mkForce (cfg.colorscheme == "tokyonight");

        colorschemes.catppuccin.settings.flavour = lib.mkIf (cfg.colorscheme == "catppuccin") (
          lib.mkForce config.catppuccin.flavor
        );

        colorschemes.catppuccin.settings.transparent_background = lib.mkIf (
          cfg.colorscheme == "catppuccin"
        ) (lib.mkForce cfg.transparent);

        colorschemes.tokyonight.settings.style = lib.mkIf (cfg.colorscheme == "tokyonight") (
          lib.mkForce cfg.tokyonightStyle
        );

        colorschemes.tokyonight.settings.transparent = lib.mkIf (cfg.colorscheme == "tokyonight") (
          lib.mkForce cfg.transparent
        );

        # nixpkgs currently ships nvim-treesitter-refactor with a dependency on
        # nvim-treesitter-legacy, which conflicts with the normal nvim-treesitter.
        plugins.treesitter-refactor = lib.mkForce { enable = false; };

        # Keep nixvim buildable without unfree Copilot LSP.
        plugins.copilot-lsp.enable = lib.mkForce false;
        plugins.sidekick.enable = lib.mkForce false;

        # Avoid Linux-only dependencies in PATH.
        extraPackages = lib.mkForce (
          with pkgs;
          [
            ripgrep
            lazygit
            fzf
            fd
          ]
        );
      };
  };
in
{
  options.programs.nvim = {
    enable = lib.mkEnableOption "Enable neovim";

    colorscheme = lib.mkOption {
      type = lib.types.enum [
        "catppuccin"
        "tokyonight"
      ];
      default = "catppuccin";
      description = "Neovim colorscheme (nixvim).";
    };

    tokyonightStyle = lib.mkOption {
      type = lib.types.enum [
        "night"
        "storm"
        "moon"
        "day"
      ];
      default = "night";
      description = "Tokyo Night style (when programs.nvim.colorscheme = tokyonight).";
    };

    transparent = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Whether the colorscheme uses a transparent background.";
    };
  };

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
