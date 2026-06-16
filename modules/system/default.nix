{
  config,
  lib,
  pkgs,
  unstablePkgs ? pkgs,
  self,
  happier,
  ...
}@inputs:
let
  cfg = config.profiles;
  happierPkg = happier.packages.${pkgs.stdenv.hostPlatform.system}.happier-cli;
  codexPkg = pkgs.callPackage ../../pkgs/codex-cli.nix { };
  hmBackupSuffix =
    if self ? shortRev then
      self.shortRev
    else if self ? dirtyShortRev then
      self.dirtyShortRev
    else
      "local";
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
      home-manager.backupFileExtension = "${hmBackupSuffix}.old";

      # Ensure security wrappers (sudo, ping, etc.) are found before system binaries
      # This must apply to SSH, TTY, and interactive shells
      environment.shellInit = ''
        export PATH="/run/wrappers/bin:$PATH"
      '';

      # Ensure home-manager CLI is available even before HM activation.
      environment.systemPackages = [
        pkgs.home-manager
        happierPkg
        codexPkg
      ];
    }
    # NOTE: Do NOT set security.sudo.enable here.
    # sudo enablement is handled by NixOS defaults and by modules/system/sudo.nix,
    # and must not be referenced at all on nix-darwin.

    (lib.mkIf (cfg.defaults.enable && config ? system && config.system ? stateVersion) {
      sops.secrets.github-token = {
        owner = "cdenneen";
        mode = "0400";
      };

      home-manager = lib.mkIf config.profiles.hmIntegrated.enable {
        backupFileExtension = "${hmBackupSuffix}.old";
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
    })

    (lib.mkIf (config ? system && config.system ? stateVersion) {
      sops = {
        defaultSopsFile = ../../secrets/secrets.yaml;
        age.keyFile =
          if pkgs.stdenv.isDarwin then
            "${config.users.users.${config.userPresets.cdenneen.name}.home}/.config/sops/age/keys.txt"
          else
            "/var/sops/age/keys.txt";
      };
      sops.secrets.gitlab_com_flake_token = {
        owner = "root";
        mode = "0400";
      };
      nix.extraOptions = lib.mkAfter (lib.optionalString pkgs.stdenv.isLinux ''
        !include /etc/nix/nix.conf.d/90-access-tokens.conf
      '');
      system.activationScripts.nixAccessTokens = lib.mkAfter ''
        token_file="${config.sops.secrets.gitlab_com_flake_token.path}"
        token=""

        if [ -s "$token_file" ]; then
          token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$token_file")"
        fi

        ${lib.optionalString pkgs.stdenv.isDarwin ''
        if [ -z "$token" ]; then
          sops_file="${config.sops.defaultSopsFile}"
          age_key="${config.sops.age.keyFile}"
          if [ -r "$sops_file" ] && [ -r "$age_key" ]; then
            token="$(SOPS_AGE_KEY_FILE="$age_key" ${pkgs.sops}/bin/sops --extract '["gitlab_com_flake_token"]' --decrypt "$sops_file" 2>/dev/null | ${pkgs.coreutils}/bin/tr -d '\n\r')"
          fi
        fi
        ''}

        ${lib.optionalString pkgs.stdenv.isDarwin ''
        conf_file="/etc/nix/nix.custom.conf"
        tmp_file="$conf_file.tmp"
        ${pkgs.coreutils}/bin/install -d -m 0755 /etc/nix

        if [ -f "$conf_file" ]; then
          ${pkgs.gnugrep}/bin/grep -v '^access-tokens = gitlab.com=' "$conf_file" > "$tmp_file" || true
        else
          : > "$tmp_file"
        fi

        if [ -n "$token" ]; then
          printf 'access-tokens = gitlab.com=%s\n' "$token" >> "$tmp_file"
        fi

        ${pkgs.coreutils}/bin/install -m 0644 "$tmp_file" "$conf_file"
        ${pkgs.coreutils}/bin/rm -f "$tmp_file"
        ''}
        ${lib.optionalString pkgs.stdenv.isLinux ''
        conf_dir="/etc/nix/nix.conf.d"
        conf_file="$conf_dir/90-access-tokens.conf"

        ${pkgs.coreutils}/bin/install -d -m 0755 "$conf_dir"

        if [ -n "$token" ]; then
          printf 'access-tokens = gitlab.com=%s\n' "$token" > "$conf_file"
          ${pkgs.coreutils}/bin/chmod 0400 "$conf_file"
        else
          ${pkgs.coreutils}/bin/rm -f "$conf_file"
        fi
        ''}
      '';
    })

    (lib.mkIf
      (cfg.defaults.enable && config ? system && config.system ? stateVersion && pkgs.stdenv.isLinux)
      {
        nix.gc.dates = "weekly";
      }
    )

    # NOTE: nix-darwin-specific GC scheduling lives in modules/system/darwin/default.nix
  ];
}
