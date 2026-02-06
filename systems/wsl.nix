{ lib, ... }:
{
  networking.hostName = "wsl";
  profiles = {
    defaults.enable = true;
  };
  # Use cdenneen as the sole user for WSL
  userPresets.cdenneen.enable = true;
  wsl.enable = true;
  wsl.defaultUser = "cdenneen";

  # WSL does not manage Wi-Fi; networking is provided by Windows
  # Force-disable wireless on WSL; NetworkManager module may set defaults
  networking.wireless.enable = lib.mkForce false;

  # WSL has no real input devices; disable kanata keyboard service
  services.kanata.enable = lib.mkForce false;

  # opencode currently isn't confirmed to work on x86_64-linux WSL; disable by
  # default to avoid host builds breaking. Re-enable once verified.
  home-manager.users.cdenneen.programs.opencode.enable = lib.mkForce false;
}
