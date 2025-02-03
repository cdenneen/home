{ pkgs, ... }:
{
  home = {
    username = "hmuser";
    homeDirectory = "/home/hmuser";
  };
  profiles = {
    cdenneen.enable = true;
    defaults.enable = true;
    gui.enable = true;
  };
}
