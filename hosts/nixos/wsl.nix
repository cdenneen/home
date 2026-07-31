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
}
