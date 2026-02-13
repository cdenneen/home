{
  config,
  lib,
  pkgs,
  unstablePkgs ? pkgs,
  self,
  ...
}@inputs:
let
  cfg = config.profiles;
in
{
  imports = [
    ./users
    ./podman.nix
    ./gui.nix
    ./dev.nix
    ./console.nix
    ./sudo.nix
  ];

  options.profiles.defaults.enable = lib.mkEnableOption "Enable Defaults";
  options.profiles.hmIntegrated.enable = lib.mkEnableOption "Enable Home Manager integration";

  config = lib.mkMerge [
    {
      # Enable cdenneen user preset globally so Home Manager activates everywhere
      userPresets.cdenneen.enable = true;

      # Home Manager integration is enabled by default, but can be disabled
      # for hosts that prefer standalone home-manager switch.
      profiles.hmIntegrated.enable = lib.mkDefault true;

      # Ensure new Nix CLI is enabled for this user/host
      # Note: On macOS with Determinate Nix this is advisory and must still be
      # present in trusted settings, but keeping it here documents intent and
      # works on NixOS.
      nix.settings.experimental-features = [
        "nix-command"
        "flakes"
      ];

      # Always enable Home Manager backups to avoid clobbering existing files
      # on first activation when HM manages an existing home directory.
      home-manager.backupFileExtension = "${self.shortRev or self.dirtyShortRev}.old";

      # Ensure security wrappers (sudo, ping, etc.) are found before system binaries
      # This must apply to SSH, TTY, and interactive shells
      environment.shellInit = ''
        export PATH="/run/wrappers/bin:$PATH"
      '';
    }
    # NOTE: Do NOT set security.sudo.enable here.
    # sudo enablement is handled by NixOS defaults and by modules/system/sudo.nix,
    # and must not be referenced at all on nix-darwin.

    (lib.mkIf (cfg.defaults.enable && config ? system && config.system ? stateVersion) {
      home-manager = lib.mkIf config.profiles.hmIntegrated.enable {
        backupFileExtension = "${self.shortRev or self.dirtyShortRev}.old";
        useUserPackages = true;
        useGlobalPkgs = false;
        sharedModules = [
          {
            nix.package = lib.mkForce config.nix.package;
            home.sessionVariables.NIXPKGS_ALLOW_UNFREE = 1;
          }
        ];
      };
      nix = {
        settings = {
          # Allow local user to use substituters (avoid source builds for nix shell/profile)
          trusted-users = [
            "root"
            config.userPresets.cdenneen.name
          ];
          experimental-features = [
            "nix-command"
            "flakes"
            "pipe-operators"
          ];
          substituters = config.nix.settings.trusted-substituters;
          trusted-substituters = [
            "https://cache.nixos.org"
            "https://nix-community.cachix.org"
            "https://cdenneen.cachix.org"
          ];
          trusted-public-keys = [
            "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
            "nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs="
            "cdenneen.cachix.org-1:EUognwSf1y0FAzDOPmUuYtz6aOxCWyNbcMi8PjHV8gU="
          ];

          auto-optimise-store = true;
        };
        nixPath = [
          "nixpkgs=${inputs.nixpkgs-unstable}"
        ];
      };

      # Containers: make Podman available everywhere by default.
      virtualisation.podman = {
        enable = lib.mkDefault true;
        dockerCompat = lib.mkDefault true;
      };

      # Periodic maintenance to keep /nix/store tidy.
      nix.gc.automatic = true;
      nix.gc.options = "--delete-older-than 14d";
      sops = {
        defaultSopsFile = ../../secrets/secrets.yaml;
        age.keyFile = "/var/sops/age/keys.txt";
      };
    })

    (lib.mkIf
      (cfg.defaults.enable && config ? system && config.system ? stateVersion && pkgs.stdenv.isLinux)
      {
        nix.gc.dates = "weekly";

        # Docker socket compatibility on Linux.
        virtualisation.podman.dockerSocket.enable = lib.mkDefault true;
        virtualisation.podman.defaultNetwork.settings.dns_enabled = lib.mkDefault true;
      }
    )

    # NOTE: nix-darwin-specific GC scheduling lives in modules/system/darwin/default.nix
  ];
}
