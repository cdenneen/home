{
  config,
  lib,
  pkgs,
  system,
  osConfig,
  unstablePkgs ? pkgs,
  ...
}:
let
  cfg = config.profiles;
in
{
  imports = [
    ./chloe.nix
    ./cdenneen/default.nix
  ];

  options.profiles = {
    defaults.enable = lib.mkEnableOption "Enable default profile";
    gui.enable = lib.mkEnableOption "Enable GUI applications";
  };

  config = lib.mkIf cfg.defaults.enable {
    home = {
      stateVersion = "25.11";
      enableNixpkgsReleaseCheck = false;
      sessionPath =
        lib.optionals config.programs.volta.enable [
          "${config.programs.volta.voltaHome}/bin"
        ]
        ++ [
          "${config.home.homeDirectory}/.cargo/bin"
          "${config.home.homeDirectory}/.local/bin"
          "${config.home.homeDirectory}/.bin"
          "${config.home.homeDirectory}/bin"
          "/run/wrappers/bin"
          "${config.home.homeDirectory}/.nix-profile/bin"
          "/nix/profile/bin"
          "${config.home.homeDirectory}/.local/state/nix/profile/bin"
          "/etc/profiles/per-user/${config.home.username}/bin"
          "/run/current-system/sw/bin"
          "/nix/var/nix/profiles/default/bin"
        ]
        ++ lib.optionals (system == "aarch64-darwin") [
          "/opt/homebrew/bin"
          "/opt/homebrew/sbin"
        ]
        ++ lib.optionals pkgs.stdenv.isDarwin [
          "/System/Cryptexes/App/usr/bin"
        ]
        ++ [
          "/usr/local/bin"
          "/usr/local/sbin"
          "/usr/bin"
          "/usr/sbin"
          "/bin"
          "/sbin"
          "/usr/local/games"
          "/usr/games"
        ]
        ++ lib.optionals pkgs.stdenv.isDarwin [
          "/var/run/com.apple.security.cryptexd/codex.system/bootstrap/usr/local/bin"
          "/var/run/com.apple.security.cryptexd/codex.system/bootstrap/usr/bin"
          "/var/run/com.apple.security.cryptexd/codex.system/bootstrap/usr/appleinternal/bin"
          "/Library/Apple/usr/bin"
        ];
    };
    # Do not manage nixpkgs or nix.conf from Home Manager.
    # With home-manager.useGlobalPkgs = true, nixpkgs configuration
    # must live at the system level (NixOS / nix-darwin).
    # XDG config files managed by Home Manager
    xdg.configFile = {
      # Restore user zsh functions and helpers
      "zsh".source = ./cdenneen/zsh;
    };
    programs = {
      home-manager.enable = true;
      starship = {
        enable = lib.mkDefault true;
        enableTransience = true;
        settings = {
          add_newline = false;
          format = "$directory$character";
          right_format = "$all";
          command_timeout = 1000;

          character = {
            vicmd_symbol = "[N] >>>";
            success_symbol = "[➜](bold green)";
          };

          directory.substitutions = {
            "~/tests/starship-custom" = "work-project";
          };

          git_branch.format = "[$symbol$branch(:$remote_branch)]($style)";

          aws = {
            disabled = false;
            format = "[$symbol(profile: \"$profile\" )(\\(region: $region\\) )]($style)";
            style = "bold blue";
            symbol = " ";
          };

          golang.format = "[ ](bold cyan)";

          kubernetes = {
            disabled = true;
            symbol = "☸ ";
            detect_files = [ "Dockerfile" ];
            format = "[$symbol$context( \\($namespace\\))]($style) ";
            contexts = [
              {
                context_pattern = "arn:aws:eks:us-west-2:577926974532:cluster/zd-pvc-omer";
                context_alias = "omerxx";
                style = "green";
                symbol = " ";
              }
            ];
          };

          docker_context.disabled = true;

          # Colors are provided dynamically by catppuccin
        };
      };
      zoxide.enable = lib.mkDefault true;
      bat.enable = lib.mkDefault true;
      eza.enable = lib.mkDefault true;
      zsh.enable = lib.mkDefault true;
      bash.enable = lib.mkDefault true;
      nvim.enable = lib.mkDefault true;
      nix-index-database.comma.enable = lib.mkDefault true;

      atuin = {
        enable = lib.mkDefault true;
        settings.keymap_mode = lib.mkDefault "emacs";
      };

      fzf.enable = lib.mkDefault true;

      direnv.enable = lib.mkDefault true;
      direnv.nix-direnv.enable = lib.mkDefault true;

      opencode.enable = lib.mkDefault true;
      opencode.package = unstablePkgs.opencode;
    };
    # Catppuccin program integrations (top-level module, not under programs)
    # Catppuccin program integrations (supported by the flake)
    # Catppuccin integrations supported by the flake
    catppuccin.starship.enable = true;
    catppuccin.bat.enable = true;
    catppuccin.fzf.enable = true;
    catppuccin.tmux.enable = true;
    # Neovim is configured via vimnix; avoid duplicate nvim theming.
    catppuccin.nvim.enable = false;
    # Opportunistically refresh secrets on HM switch
    home.activation.updateSecrets = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      if command -v update-secrets >/dev/null; then
        update-secrets --quiet || true
      fi
    '';
    # User-level tools available on all systems
    home.packages = [
      pkgs.cachix
      pkgs.coreutils
      pkgs.nerd-fonts.jetbrains-mono
    ];
    services.easyeffects = lib.mkIf (pkgs.stdenv.isLinux && cfg.gui.enable) {
      enable = true;
    };
    catppuccin = {
      enable = true;
      flavor = lib.mkDefault "mocha";
      accent = lib.mkDefault "red";
    };
  };
}
