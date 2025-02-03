{ pkgs, ... }:
{
  home = {
    username = "hmuser";
    homeDirectory = "/home/hmuser";
  };
  profiles = {
    defaults.enable = true;
    gui.enable = true;
    dev.enable = true;
  };
  userPresets.cdenneen.enable = true;
}
