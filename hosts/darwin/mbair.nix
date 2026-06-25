{ lib, pkgs, ... }:
{
  networking.hostName = "mbair";

  # Intel MacBook Air capped at macOS Big Sur. Keep this host conservative:
  # command-line defaults only, no Homebrew GUI casks, no Podman/vfkit stack.
  profiles = {
    defaults.enable = true;
    dev.enable = false;
    gui.enable = lib.mkForce false;
  };

  virtualisation.podman.enable = lib.mkForce false;
  nix.gc.automatic = lib.mkForce false;

  homebrew = {
    onActivation = {
      autoUpdate = lib.mkForce false;
      upgrade = lib.mkForce false;
      cleanup = lib.mkForce "none";
    };
    taps = lib.mkForce [ "cdenneen/taps" ];
    # cdenneen/taps carries a legacy Tailscale app cask pinned for Big Sur.
    casks = lib.mkForce [ "cdenneen/taps/tailscale-app@1.70.0" ];
    brews = lib.mkForce [ ];
    masApps = lib.mkForce { };
  };

  environment.systemPackages = [
    pkgs.bash
    pkgs.git
    pkgs.jq
    pkgs.ripgrep
    pkgs.sops
    pkgs.age
  ];

  home-manager.users.cdenneen = {
    programs = {
      alacritty.enable = true;
      kitty.enable = true;
      rio.enable = false;
      wezterm.enable = false;
      opencode.enable = true;
    };

    home.sessionVariables = {
      # Darwin sops-nix reads this key file. Restore the personal age key here
      # before first switch, or add a dedicated mbair host recipient later.
      SOPS_AGE_KEY_FILE = "$HOME/.config/sops/age/keys.txt";
    };
  };
}
