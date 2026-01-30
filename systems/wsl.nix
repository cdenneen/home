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

  # Required when any `boot.binfmt.*` is configured, otherwise WSL's `.exe`
  # interop registration can get replaced and break running Windows binaries.
  wsl.interop.register = true;
}
