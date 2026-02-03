{ pkgs, lib, ... }:
{
  networking.hostName = "eros";
  ec2.efi = true;

  # Switch display manager from Plasma to XFCE
  services.desktopManager.plasma6.enable = false;
  services.xserver.desktopManager.xfce.enable = true;
  services.xserver.displayManager.lightdm.enable = true;
  services.displayManager.sddm.enable = false;

  services.udisks2.enable = lib.mkForce false;
  services.openssh.settings.PermitRootLogin = lib.mkForce "prohibit-password";

  # Matches running system (do not change after initial install)
  # Match global default; do not downgrade
  system.stateVersion = lib.mkForce "26.05";

  # Root filesystem
  fileSystems."/" = {
    device = "/dev/disk/by-uuid/f222513b-ded1-49fa-b591-20ce86a2fe7f";
    fsType = "ext4";
  };

  # EFI system partition
  fileSystems."/boot" = {
    device = "/dev/disk/by-uuid/12CE-A600";
    fsType = "vfat";
  };

  # UEFI + GRUB (current system uses GRUB on EFI)
  boot.loader = {
    grub = {
      splashImage = lib.mkForce null;
      enable = true;
      efiSupport = true;
      device = "nodev";
    };
  };

  # Networking (DHCP on ens5)
  networking.useDHCP = false;
  networking.interfaces.ens5.useDHCP = true;

  # User definition is shared via commonModules.users.cdenneen
  profiles.defaults.enable = true;
}
