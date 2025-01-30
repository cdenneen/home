{
  config,
  lib,
  pkgs,
  system,
  ...
}:
let
  cfg = config.profiles;
in
{
  imports = [
    ./cdenneen.nix
  ];

  options.profiles = {
    defaults.enable = lib.mkEnableOption "Enable default profile";
    gui.enable = lib.mkEnableOption "Enable GUI applications";
  };

  config = lib.mkIf cfg.defaults.enable {
    home = {
      stateVersion = "25.05";
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
        ++ lib.optionals pkgs.stdenv.isDarwin [
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
    xdg.configFile = {
      "nix/nix.conf".text = ''
        experimental-features = nix-command flakes
        substituters = https://cache.nixos.org https://cosmic.cachix.org https://cdenneen.cachix.org
        trusted-public-keys = cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY= cosmic.cachix.org-1:Dya9IyXD4xdBehWjrkPv6rtxpmMdRel02smYzA85dPE= cdenneen.cachix.org-1:EUognwSf1y0FAzDOPmUuYtz6aOxCWyNbcMi8PjHV8gU=
      '';
      "nixpkgs/config.nix".text = ''
        {
          allowUnfree = true;
          allowBroken = true;
        }
      '';
    };
    programs = {
      home-manager.enable = true;
      starship = {
        enable = true;
        enableBashIntegration = config.programs.bash.enable;
        enableFishIntegration = config.programs.fish.enable;
        enableZshIntegration = config.programs.zsh.enable;
        enableTransience = true;
        settings = {
          right_format = "$time";
          time.disabled = false;
        };
      };
      zoxide.enable = true;
      bat.enable = true;
      awscli.enable = true;
      eza.enable = true;
      zsh.enable = true;
      bash.enable = true;
      fish.enable = false;
      ion.enable = false;
      nushell.enable = false;
      powershell.enable = false;
      nvim.enable = true;
      nix-index-database.comma.enable = true;
      tmux.enable = true;
    };
    services.easyeffects = lib.mkIf (pkgs.stdenv.isLinux && cfg.gui.enable) {
      enable = true;
    };
    catppuccin = {
      flavor = lib.mkDefault "frappe";
      accent = lib.mkDefault "red";
    };
  };
}
