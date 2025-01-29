{ pkgs, ... }:
{
  profiles = {
    defaults.enable = true;
    dev.enable = true;
    gui.enable = true;
  };
  userPresets.cdenneen.enable = true;
  environment.systemPackages = with pkgs; [
    coreutils
    itsycal
    keycastr
    maccy
    mas
    mkalias
    rectangle
    synology-drive-client
  ];
  homebrew.brews = [
  ];
  homebrew.casks = [
    "1password"
    "amazon-chime"
    "amazon-photos"
    "cleanshot"
    "evernote"
    "firefox@developer-edition"
    "fliqlo"
    "ghostty"
    "hiddenbar"
    "sketchybar"
    "jumpcut"
    "megasync"
    "microsoft-edge"
    "spotify"
    {
      name = "discord";
    }
    {
      name = "keybase";
      greedy = true;
    }
    {
      name = "slack";
      greedy = true;
    }
    {
      name = "docker";
      greedy = true;
    }
  ];
}
