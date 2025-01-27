{
  lib,
  pkgs,
  outputs,
  userConfig,
  ...
}: {
  # Add nix-homebrew configuration
  nix-homebrew = {
    enable = true;
    enableRosetta = if (pkgs.system == "aarch64-darwin") then true else false;
    user = "${userConfig.name}";
    autoMigrate = true;
    };

  # Nixpkgs configuration
  nixpkgs = {
    overlays = [
      outputs.overlays.stable-packages
    ];

    config = {
      allowUnfree = true;
    };
  };

  # Nix settings
  nix.settings = {
    experimental-features = "nix-command flakes";
  };

  nix = {
   optimise.automatic = true;
   package = pkgs.nix;
  };

  # Enable Nix daemon
  services.nix-daemon.enable = true;

  # User configuration
  users.users.${userConfig.name} = {
    name = "${userConfig.name}";
    home = "/Users/${userConfig.name}";
  };

  # Add ability to use TouchID for sudo
  security.pam.enableSudoTouchIdAuth = true;

  # System settings
  system = {
    defaults = {
      ".GlobalPreferences" = {
        "com.apple.mouse.scaling" = -1.0;
      };
      NSGlobalDomain = {
        AppleInterfaceStyle = "Dark";
        ApplePressAndHoldEnabled = false;
        AppleShowAllExtensions = true;
        KeyRepeat = 2;
        NSAutomaticCapitalizationEnabled = false;
        NSAutomaticDashSubstitutionEnabled = false;
        NSAutomaticQuoteSubstitutionEnabled = false;
        NSAutomaticSpellingCorrectionEnabled = false;
        NSAutomaticWindowAnimationsEnabled = false;
        NSDocumentSaveNewDocumentsToCloud = false;
        NSNavPanelExpandedStateForSaveMode = true;
        PMPrintingExpandedStateForPrint = true;
      };
      LaunchServices = {
        LSQuarantine = false;
      };
      trackpad = {
        TrackpadRightClick = true;
        TrackpadThreeFingerDrag = true;
        Clicking = true;
      };
      finder = {
        AppleShowAllFiles = true;
        CreateDesktop = false;
        FXDefaultSearchScope = "SCcf";
        FXEnableExtensionChangeWarning = false;
        FXPreferredViewStyle = "Nlsv";
        QuitMenuItem = true;
        ShowPathbar = true;
        ShowStatusBar = true;
        _FXShowPosixPathInTitle = true;
        _FXSortFoldersFirst = true;
      };
      dock = {
        autohide = true;
        expose-animation-duration = 0.15;
        show-recents = false;
        showhidden = true;
        # persistent-apps = [
        #   "/Applications/Brave Browser.app"
        #   "${pkgs.alacritty}/Applications/Alacritty.app"
        # ];
        tilesize = 30;
        wvous-bl-corner = 1;
        wvous-br-corner = 1;
        wvous-tl-corner = 1;
        wvous-tr-corner = 1;
      };
      screencapture = {
        # location = "/Users/${userConfig.name}/Downloads/temp";
        type = "png";
        disable-shadow = true;
      };
    };
    keyboard = {
      enableKeyMapping = true;
      # swapLeftCtrlAndFn = true;
      # Remap §± to ~
      # userKeyMapping = [
      #   {
      #     HIDKeyboardModifierMappingDst = 30064771125;
      #     HIDKeyboardModifierMappingSrc = 30064771172;
      #   }
      # ];
    };
  };

  # Zsh configuration
  programs.zsh.enable = true;

  # Fonts configuration
  fonts.packages = with pkgs; [
    nerd-fonts.jetbrains-mono
    nerd-fonts.meslo-lg
  ];

  homebrew = {
    enable = true;
    casks = [
    ];
    taps = [
    ];
    onActivation.cleanup = "zap";
  };

  # Used for backwards compatibility, please read the changelog before changing.
  system.stateVersion = 5;
}
