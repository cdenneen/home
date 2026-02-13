{ config, lib, ... }:

# User-scoped Homebrew packages for cdenneen.
# GUI and macOS-specific tools belong here (not in Nixpkgs / Home Manager).

lib.mkIf config.userPresets.cdenneen.enable {
  homebrew = {
    taps = [
      "felixkratz/formulae"
      "nikitabobko/tap"
      "1password/tap"
    ];

    brews = [
      # "sketchybar"
    ];

    casks = [
      "1password"
      "1password-cli"
      "amazon-chime"
      "amazon-photos"
      "betterdisplay"
      "cleanshot"
      "evernote"
      "firefox@developer-edition"
      "fliqlo"
      "ghostty"
      "hiddenbar"
      "itsycal"
      "jumpcut"
      "keycastr"
      "maccy"
      "megasync"
      "microsoft-edge"
      "nikitabobko/tap/aerospace"
      "spotify"
      # renamed upstream
      "synology-drive"

      { name = "discord"; }
      # keybase cask currently fails to download (403); disabled
      # { name = "keybase"; }
      { name = "slack"; }
    ];
  };
}
