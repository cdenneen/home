{ config, lib, ... }:

# User-scoped Homebrew packages for cdenneen.
# GUI and macOS-specific tools belong here (not in Nixpkgs / Home Manager).

lib.mkIf config.userPresets.cdenneen.enable {
  homebrew = {
    taps = [
      "felixkratz/formulae"
      "nikitabobko/tap"
    ];

    brews = [
      # "sketchybar"
      # 1Password CLI (op) for macOS
      # "1password-cli"
    ];

    casks = [
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
