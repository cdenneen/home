{ pkgs, lib, ... }:
{
  networking.hostName = "eros";
  ec2.efi = true;

  services.udisks2.enable = lib.mkForce false;
  services.openssh.settings.PermitRootLogin = lib.mkForce "prohibit-password";

  # boot.kernelPackages = pkgs.linuxPackages_latest;
  boot.loader.grub.splashImage = lib.mkForce null;

  # home-manager.options.programs.zsh.shellAliases.swnix = "sudo nixos-rebuild switch --flake ~/src/personal/home#eros";

  profiles = {
    defaults.enable = true;
    gui.enable = false;
    printing.enable = false;
  };
  userPresets.cdenneen.enable = true;
  services.desktopManager.cosmic.enable = true;
}
