{outputs, pkgs, userConfig, ...}: {
  imports = [
  ];

  # Nixpkgs configuration
  nixpkgs = {
    overlays = [
      outputs.overlays.stable-packages
    ];

    config = {
      allowUnfree = true;
    };
  };

  # Home-Manager configuration for the user's home environment
  home = {
    username = "${userConfig.name}";
    homeDirectory =
      if pkgs.stdenv.isDarwin
      then "/Users/${userConfig.name}"
      else "/home/${userConfig.name}";
    packages = with pkgs; [
      tree
    ];
  };

  # Catpuccin flavor and accent
  catppuccin = {
    flavor = "macchiato";
    accent = "lavender";
  };
}
