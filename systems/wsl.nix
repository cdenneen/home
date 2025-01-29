{ ... }:
{
  networking.hostName = "wsl";
  profiles = {
    defaults.enable = true;
  };
  userPresets.cdenneen.enable = true;
  wsl.enable = true;
  wsl.defaultUser = "toyvo";
}
