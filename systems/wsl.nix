{ ... }:
{
  networking.hostName = "wsl";
  networking.firewall.enable = false;
  profiles = {
    defaults.enable = true;
  };
  userPresets.cdenneen.enable = true;
  wsl.enable = true;
  wsl.defaultUser = "cdenneen";
}
