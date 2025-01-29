{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.nvim;
in
{
  options.programs.nvim.enable = lib.mkEnableOption "Enable neovim";

  config = lib.mkIf cfg.enable {
    programs.neovim = {
      enable = true;
      extraPackages = with pkgs; [
        # LazyVim
        lua-language-server
        stylua
        # Telescope
        ripgrep
        # compiler
        gcc
        # tools
        fzf
        ruby
        python3
        unzip
      ];
    };
    # programs.neovim = {
    #   enable = true;
    #   extraPackages = with pkgs; [
    #     # LazyVim
    #     lua-language-server
    #     stylua
    #     # Telescope
    #     ripgrep
    #   ];
    #
    #   plugins = with pkgs.vimPlugins; [
    #     lazy-nvim
    #   ];
    #
    #   extraLuaConfig =
    #     let
    #       plugins = with pkgs.vimPlugins; [
    #         # LazyVim
    #         LazyVim
    #         bufferline-nvim
    #         cmp-buffer
    #         cmp-nvim-lsp
    #         cmp-path
    #         cmp_luasnip
    #         conform-nvim
    #         dashboard-nvim
    #         dressing-nvim
    #         flash-nvim
    #         friendly-snippets
    #         gitsigns-nvim
    #         indent-blankline-nvim
    #         lualine-nvim
    #         neo-tree-nvim
    #         neoconf-nvim
    #         neodev-nvim
    #         noice-nvim
    #         nui-nvim
    #         nvim-cmp
    #         nvim-lint
    #         nvim-lspconfig
    #         nvim-notify
    #         nvim-spectre
    #         nvim-treesitter
    #         nvim-treesitter-context
    #         nvim-treesitter-textobjects
    #         nvim-ts-autotag
    #         nvim-ts-context-commentstring
    #         nvim-web-devicons
    #         persistence-nvim
    #         plenary-nvim
    #         telescope-fzf-native-nvim
    #         telescope-nvim
    #         todo-comments-nvim
    #         tokyonight-nvim
    #         trouble-nvim
    #         vim-illuminate
    #         vim-startuptime
    #         which-key-nvim
    #         { name = "LuaSnip"; path = luasnip; }
    #         { name = "catppuccin"; path = catppuccin-nvim; }
    #         { name = "mini.ai"; path = mini-nvim; }
    #         { name = "mini.bufremove"; path = mini-nvim; }
    #         { name = "mini.comment"; path = mini-nvim; }
    #         { name = "mini.indentscope"; path = mini-nvim; }
    #         { name = "mini.pairs"; path = mini-nvim; }
    #         { name = "mini.surround"; path = mini-nvim; }
    #       ];
    #       mkEntryFromDrv = drv:
    #         if lib.isDerivation drv then
    #           { name = "${lib.getName drv}"; path = drv; }
    #         else
    #           drv;
    #       lazyPath = pkgs.linkFarm "lazy-plugins" (builtins.map mkEntryFromDrv plugins);
    #     in
    #     ''
    #       require("lazy").setup({
    #         defaults = {
    #           lazy = true,
    #         },
    #         dev = {
    #           -- reuse files from pkgs.vimPlugins.*
    #           path = "${lazyPath}",
    #           patterns = { "" },
    #           -- fallback to download
    #           fallback = true,
    #         },
    #         spec = {
    #           { "LazyVim/LazyVim", import = "lazyvim.plugins" },
    #           -- The following configs are needed for fixing lazyvim on nix
    #           -- force enable telescope-fzf-native.nvim
    #           { "nvim-telescope/telescope-fzf-native.nvim", enabled = true },
    #           -- disable mason.nvim, use programs.neovim.extraPackages
    #           { "williamboman/mason-lspconfig.nvim", enabled = false },
    #           { "williamboman/mason.nvim", enabled = false },
    #           -- import/override with your plugins
    #           { import = "plugins" },
    #           -- treesitter handled by xdg.configFile."nvim/parser", put this line at the end of spec to clear ensure_installed
    #           { "nvim-treesitter/nvim-treesitter", opts = { ensure_installed = {} } },
    #         },
    #       })
    #     '';
    # };
    #
    # # https://github.com/nvim-treesitter/nvim-treesitter#i-get-query-error-invalid-node-type-at-position
    # xdg.configFile."nvim/parser".source =
    #   let
    #     parsers = pkgs.symlinkJoin {
    #       name = "treesitter-parsers";
    #       paths = (pkgs.vimPlugins.nvim-treesitter.withPlugins (plugins: with plugins; [
    #         c
    #         lua
    #       ])).dependencies;
    #     };
    #   in
    #   "${parsers}/parser";

    # Normal LazyVim config here, see https://github.com/LazyVim/starter/tree/main/lua
    # xdg.configFile."nvim/lua".source = ./lua;

    # programs.neovim.enable = true;
    # programs.nvf = {
    #   enable = true;
    #   defaultEditor = true;
    #   settings.vim = {
    #     viAlias = true;
    #     vimAlias = true;
    #     debugMode = {
    #       enable = false;
    #       level = 16;
    #       logFile = "/tmp/nvim.log";
    #     };
    #     spellcheck.enable = true;
    #     lsp = {
    #       enable = true;
    #       formatOnSave = true;
    #       lspkind.enable = true;
    #       lightbulb.enable = true;
    #       lspsaga.enable = false;
    #       trouble.enable = true;
    #       lspSignature.enable = true;
    #       otter-nvim.enable = true;
    #       lsplines.enable = true;
    #       nvim-docs-view.enable = true;
    #     };
    #     debugger = {
    #       nvim-dap = {
    #         enable = true;
    #         ui.enable = true;
    #       };
    #     };
    #     theme = {
    #       enable = true;
    #       name = "catppuccin";
    #       style = config.catppuccin.flavor;
    #       transparent = true;
    #     };
    #     languages = {
    #       enableLSP = true;
    #       enableFormat = true;
    #       enableTreesitter = true;
    #       enableExtraDiagnostics = true;
    #       nix.enable = true;
    #       markdown.enable = true;
    #       bash.enable = true;
    #       rust.enable = false;
    #       python.enable = true;
    #       css.enable = true;
    #       ts.enable = true;
    #       html.enable = true;
    #       lua.enable = true;
    #       # Nim LSP is broken on Darwin and therefore
    #       # should be disabled by default. Users may still enable
    #       # `vim.languages.vim` to enable it, this does not restrict
    #       # that.
    #       # See: <https://github.com/PMunch/nimlsp/issues/178#issue-2128106096>
    #       nim.enable = false;
    #     };
    #     visuals = {
    #       nvim-scrollbar.enable = true;
    #       nvim-web-devicons.enable = true;
    #       nvim-cursorline.enable = true;
    #       cinnamon-nvim.enable = true;
    #       fidget-nvim.enable = true;
    #
    #       highlight-undo.enable = true;
    #       indent-blankline.enable = true;
    #
    #       # Fun
    #       cellular-automaton.enable = false;
    #     };
    #     statusline = {
    #       lualine = {
    #         enable = true;
    #         theme = "catppuccin";
    #       };
    #     };
    #     telescope.enable = true;
    #     autopairs.nvim-autopairs.enable = true;
    #     autocomplete.nvim-cmp.enable = true;
    #     snippets.luasnip.enable = true;
    #     filetree = {
    #       neo-tree = {
    #         enable = true;
    #       };
    #     };
    #
    #     tabline = {
    #       nvimBufferline.enable = true;
    #     };
    #
    #     treesitter.context.enable = true;
    #
    #     binds = {
    #       whichKey.enable = true;
    #       cheatsheet.enable = true;
    #     };
    #     git = {
    #       enable = true;
    #       gitsigns.enable = true;
    #       gitsigns.codeActions.enable = false; # throws an annoying debug message
    #     };
    #
    #     minimap = {
    #       minimap-vim.enable = false;
    #       codewindow.enable = true; # lighter, faster, and uses lua for configuration
    #     };
    #
    #     dashboard = {
    #       dashboard-nvim.enable = false;
    #       alpha.enable = true;
    #     };
    #
    #     notify = {
    #       nvim-notify.enable = true;
    #     };
    #
    #     projects = {
    #       project-nvim.enable = true;
    #     };
    #
    #     utility = {
    #       ccc.enable = false;
    #       vim-wakatime.enable = false;
    #       icon-picker.enable = true;
    #       surround.enable = true;
    #       diffview-nvim.enable = true;
    #       yanky-nvim.enable = true;
    #       motion = {
    #         hop.enable = true;
    #         leap.enable = true;
    #         precognition.enable = false;
    #       };
    #
    #       images = {
    #         image-nvim.enable = false;
    #       };
    #     };
    #
    #     notes = {
    #       obsidian.enable = false; # FIXME: neovim fails to build if obsidian is enabled
    #       neorg.enable = false;
    #       orgmode.enable = false;
    #       mind-nvim.enable = true;
    #       todo-comments.enable = true;
    #     };
    #
    #     terminal = {
    #       toggleterm = {
    #         enable = true;
    #         lazygit.enable = true;
    #       };
    #     };
    #
    #     ui = {
    #       borders.enable = true;
    #       noice.enable = true;
    #       colorizer.enable = true;
    #       modes-nvim.enable = false; # the theme looks terrible with catppuccin
    #       illuminate.enable = true;
    #       breadcrumbs = {
    #         enable = true;
    #         navbuddy.enable = true;
    #       };
    #       smartcolumn = {
    #         enable = true;
    #         setupOpts.custom_colorcolumn = {
    #           # this is a freeform module, it's `buftype = int;` for configuring column position
    #           nix = "110";
    #           ruby = "120";
    #           java = "130";
    #           go = ["90" "130"];
    #         };
    #       };
    #       fastaction.enable = true;
    #     };
    #
    #     assistant = {
    #       chatgpt.enable = false;
    #       copilot = {
    #         enable = false;
    #         cmp.enable = true;
    #       };
    #     };
    #
    #     session = {
    #       nvim-session-manager.enable = false;
    #     };
    #
    #     gestures = {
    #       gesture-nvim.enable = false;
    #     };
    #
    #     comments = {
    #       comment-nvim.enable = true;
    #     };
    #
    #     presence = {
    #       neocord.enable = false;
    #     };
  };
}
