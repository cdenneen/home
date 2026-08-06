{
  axis,
  homebrew-taps-mbair,
  lib,
  pkgs,
  ...
}:
{
  networking.hostName = "mbair";

  # This Intel MacBook Air is capped at macOS 11. Keep system activation
  # intentionally small and avoid current GUI/development toolchains.
  profiles = {
    aiTools.enable = lib.mkForce false;
    defaults.enable = false;
    dev.enable = lib.mkForce false;
    gui.enable = lib.mkForce false;
  };

  services.netbird.enable = lib.mkForce false;
  virtualisation.podman.enable = lib.mkForce false;
  nix.gc.automatic = lib.mkForce false;

  # Match Home Manager and the existing Darwin recovery helper.
  sops.age.keyFile = lib.mkForce "/Users/cdenneen/Library/Application Support/sops/age/keys.txt";

  nix-homebrew = {
    enable = true;
    user = "cdenneen";
    mutableTaps = false;
    taps."cdenneen/taps" = homebrew-taps-mbair;
    extraEnv = {
      HOMEBREW_NO_INSTALL_CLEANUP = "1";
      HOMEBREW_NO_ENV_HINTS = "1";
    };
  };

  system.activationScripts.homebrew.text = lib.mkMerge [
    (lib.mkOrder 400 ''
      taps_dir="/usr/local/Homebrew/Library/Taps"
      taps_backup="$taps_dir.pre-nix-homebrew"

      if [ -d "$taps_dir" ] && [ ! -L "$taps_dir" ]; then
        if [ -e "$taps_backup" ]; then
          echo "error: cannot migrate mutable Homebrew taps; backup already exists at $taps_backup" >&2
          exit 1
        fi

        echo "Migrating mutable Homebrew taps to $taps_backup..." >&2
        mv "$taps_dir" "$taps_backup"
      fi
    '')
    (lib.mkOrder 750 ''
      old_cask_dir="/usr/local/Caskroom/tailscale-app@1.70.0"
      if [ -d "$old_cask_dir" ]; then
        brew="/usr/local/bin/brew"
        if [ ! -x "$brew" ]; then
          echo "error: cannot migrate Tailscale 1.70; $brew is unavailable" >&2
          exit 1
        fi

        echo "Removing obsolete Tailscale 1.70 cask before declarative upgrade..." >&2
        sudo --user=cdenneen --set-home "$brew" uninstall --cask --force "tailscale-app@1.70.0"
      fi
    '')
  ];

  homebrew = {
    onActivation = {
      autoUpdate = lib.mkForce false;
      upgrade = lib.mkForce false;
      cleanup = lib.mkForce "none";
    };
    taps = lib.mkForce [ "cdenneen/taps" ];
    casks = lib.mkForce [
      "cdenneen/taps/1password8-big-sur"
      "cdenneen/taps/brave-browser-big-sur"
      "cdenneen/taps/google-chrome-for-testing-big-sur"
      "cdenneen/taps/tailscale-app@1.86.4"
      "cdenneen/taps/vivaldi-big-sur"
    ];
    brews = lib.mkForce [ ];
    masApps = lib.mkForce { };
  };

  environment.systemPackages = [
    pkgs.age
    pkgs.bash
    pkgs.git
    pkgs.jq
    pkgs.ripgrep
    pkgs.sops
    axis.packages.${pkgs.system}.axis
    axis.packages.${pkgs.system}.axis-desktop
  ];

  sops.secrets.axis_remote_client_token = {
    sopsFile = ../../secrets/axis.yaml;
    owner = "cdenneen";
    mode = "0400";
  };

  system.activationScripts.axisDeploymentIdentity.text = ''
    /bin/mkdir -p /Users/cdenneen/.local/share/axis
    deployed_at="$(/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
    cat > /Users/cdenneen/.local/share/axis/deployment-identity.json <<EOF
    {"runtime":"mbair","runtime_kind":"axis-desktop","ring":1,"runtime_revision":"${axis.rev or "unknown"}","supervisor_revision":"unknown","deployment_time":"$deployed_at","service_url":"https://ai.denneen.net/api","verification_status":"deployment-recorded","health":"pending-runtime-verification"}
    EOF
    /usr/sbin/chown cdenneen:staff /Users/cdenneen/.local/share/axis/deployment-identity.json
    /bin/chmod 0640 /Users/cdenneen/.local/share/axis/deployment-identity.json
  '';
}
