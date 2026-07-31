{
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

  homebrew = {
    onActivation = {
      autoUpdate = lib.mkForce false;
      upgrade = lib.mkForce false;
      cleanup = lib.mkForce "none";
    };
    taps = lib.mkForce [ "cdenneen/taps" ];
    casks = lib.mkForce [ "cdenneen/taps/tailscale-app@1.70.0" ];
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
  ];
}
