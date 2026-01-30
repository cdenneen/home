{ pkgs, lib, ... }:
{
  networking.hostName = "eros";
  ec2.efi = true;

  services.udisks2.enable = lib.mkForce false;
  services.openssh.settings.PermitRootLogin = lib.mkForce "prohibit-password";

  # Prefer per-user gpg-agent (Home Manager) on eros.
  programs.gnupg.agent.enable = lib.mkForce false;

  # boot.kernelPackages = pkgs.linuxPackages_latest;
  boot.loader.grub.splashImage = lib.mkForce null;

  # home-manager.options.programs.zsh.shellAliases.swnix = "sudo nixos-rebuild switch --flake ~/src/personal/home#eros";

  profiles = {
    defaults.enable = true;
    dev.enable = true;
    gui.enable = true;
    printing.enable = false;
  };
  userPresets.cdenneen.enable = true;
  services.xserver = {
    enable = true;
    windowManager.qtile.enable = true;
    desktopManager.xfce.enable = true;
    displayManager.sessionCommands = ''
      xset r rate 200 35 &
    '';
  };
  services.picom = {
    enable = true;
    backend = "glx";
    fade = true;
  };
  services.xrdp.enable = true;
  # services.xrdp.defaultWindowManager = "xfce4-session";
  services.xrdp.defaultWindowManager = "${pkgs.xfce.xfce4-session}/bin/startxfce4";
  networking.firewall.allowedTCPPorts = [ 3389 ];
  environment.systemPackages = with pkgs; [
    _1password-gui
    teams-for-linux
    vim
    wget
    neovim
    alacritty
    btop
    gedit
    xwallpaper
    #pcmanfm
    rofi
    git
    pfetch
  ];
  fonts.packages = with pkgs; [
    jetbrains-mono
  ];
}
