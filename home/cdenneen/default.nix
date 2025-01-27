{inputs, ...}: {
  imports = [
    ../modules/default.nix
    inputs.catppuccin.homeManagerModules.catppuccin
  ];

  # Enable home-manager
  programs.home-manager.enable = true;

  # Nicely reload system units when changing configs
  systemd.user.startServices = "sd-switch";

  home.stateVersion = "24.11";
}
