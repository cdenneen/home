{ pkgs, lib, ... }:
{
  networking.hostName = "eros";
  ec2.efi = true;

  services.udisks2.enable = lib.mkForce false;
  services.openssh.settings.PermitRootLogin = lib.mkForce "prohibit-password";

  # boot.kernelPackages = pkgs.linuxPackages_latest;
  boot.loader.grub.splashImage = lib.mkForce null;

  profiles = {
    defaults.enable = true;
    gui.enable = false;
    printing.enable = false;
  };
  userPresets.cdenneen.enable = true;
  # services.desktopManager.gnome.enable = true;
}
