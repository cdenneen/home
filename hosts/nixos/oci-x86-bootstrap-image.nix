{ lib, pkgs, ... }:
{
  # Generic, hostname-agnostic bootstrap image for OCI x86_64 free-tier
  # VMs (VM.Standard.E2.1.Micro). Built via nixos-generators' qcow-efi
  # format, which manages its own filesystem/bootloader layout -- do not
  # add disko or boot.loader config here, they'd conflict with the
  # format's own definitions (that's the whole point: qcow-efi already
  # gets UEFI + virtio right, avoiding the disko/bios-vs-uefi/virtio_rng
  # issues hit hand-rolling this for nixos-anywhere).
  #
  # Intentionally minimal: just enough to boot and be reachable over SSH.
  # Real host-specific config (hostname, full minimalVm profile, sops
  # secrets, tailscale authkey) is applied afterwards via a normal
  # `nixos-rebuild switch --target-host` once the instance is up --
  # no kexec ever needed for hosts launched from this image.
  profiles.minimalVm.enable = true;

  services.openssh = {
    enable = true;
    settings = {
      PasswordAuthentication = false;
      PermitRootLogin = lib.mkForce "prohibit-password";
    };
  };

  users.users.cdenneen = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    openssh.authorizedKeys.keyFiles = [ ../../pub/ssh/cdenneen_ed25519_2024.pub ];
  };

  security.sudo.wheelNeedsPassword = false;

  networking.firewall.allowedTCPPorts = [ 22 ];

  environment.systemPackages = [
    pkgs.curl
    pkgs.git
  ];

  system.stateVersion = lib.mkForce "26.05";
}
