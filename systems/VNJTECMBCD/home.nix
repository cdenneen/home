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
    synology-drive-client
  ];
  homebrew.brews = [
    "sketchybar"
  ];
  homebrew.taps = [
    "felixkratz/formulae"
    "nikitabobko/tap"
  ];
  homebrew.casks = [
    "1password"
    "amazon-chime"
    "amazon-photos"
    "betterdisplay"
    "cleanshot"
    "evernote"
    "firefox@developer-edition"
    "fliqlo"
    "ghostty"
    "hiddenbar"
    "jumpcut"
    "megasync"
    "microsoft-edge"
    "nikitabobko/tap/aerospace"
    "spotify"
    {
      name = "discord";
    }
    {
      name = "keybase";
    }
    {
      name = "slack";
    }
    {
      name = "docker";
    }
  ];
}
