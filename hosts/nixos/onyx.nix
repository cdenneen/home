{ lib, pkgs, config, ... }:
{
  boot = {
    # ponytail: OCI's VM.Standard.E2.1.Micro boots via UEFI firmware
    # (confirmed via `oci compute instance get` -> launch-options.firmware =
    # "UEFI_64"), unlike GCE's e2-micro which boots BIOS/SeaBIOS -- a
    # BIOS-style grub install (bios_grub partition) is invisible to UEFI
    # firmware, so it silently falls through to the original base image's
    # EFI boot entry on every real reboot. systemd-boot + an ESP partition
    # is the UEFI-native equivalent.
    loader.systemd-boot.enable = true;
    loader.efi.canTouchEfiVariables = true;
    loader.grub.enable = false;
    initrd.availableKernelModules = [
      "virtio_pci"
      "virtio_blk"
      "virtio_scsi"
      "virtio_net"
      "sd_mod"
    ];
    # ponytail: GCE's documented serial console baud rate; unverified until
    # first boot, adjust here if the serial console shows garbled output.
    kernelParams = [ "console=ttyS0,38400n8" ];
  };

  # ponytail: NOT disko. nixos-anywhere/kexec repeatedly OOM'd on this 1GB
  # OCI shape (the generic kexec installer floor is ~400MB+, independent of
  # target config size -- see cloud-architecture-plan.md). onyx was instead
  # converted in-place from its running Ubuntu image via NixOS's official
  # lustrate mechanism (/etc/NIXOS_LUSTRATE), reusing the existing GPT
  # partitions as-is. These UUIDs are Ubuntu's own cloud-image partition
  # UUIDs (identical across onyx/talon since both came from the same base
  # image) -- fileSystems here must match reality, not a fresh disko layout.
  fileSystems."/" = {
    device = "/dev/disk/by-uuid/fdf980dc-4811-4282-88df-3c217e5c2fdb";
    fsType = "ext4";
  };
  fileSystems."/boot" = {
    device = "/dev/disk/by-uuid/40C7-C0D6";
    fsType = "vfat";
  };

  networking = {
    hostName = "onyx";
    firewall.allowedTCPPorts = [ 22 ];
    firewall.allowedUDPPorts = [ ];
  };

  profiles.minimalVm.enable = true;
  system.stateVersion = lib.mkForce "26.05";

  # modules/system/default.nix declares this secret gated on
  # profiles.defaults.enable, but its consuming activation script isn't
  # gated the same way -- minimalVm skips profiles.defaults, so declare it
  # here directly.
  sops.secrets.github-token = {
    owner = "cdenneen";
    mode = "0400";
  };
  sops.secrets.tailscale_auth_key = {
    owner = "root";
    mode = "0400";
  };

  users.users.cdenneen.openssh.authorizedKeys.keyFiles = [
    ../../pub/ssh/cdenneen_ed25519_2024.pub
  ];

  services.openssh = {
    enable = true;
    settings = {
      PasswordAuthentication = false;
      PermitRootLogin = lib.mkDefault "prohibit-password";
    };
  };

  environment.systemPackages = lib.mkAfter [
    pkgs.bashInteractive
    pkgs.curl
    pkgs.git
    pkgs.openssh
    pkgs.util-linux
  ];

  systemd.services.sops-age-keygen = {
    description = "Generate host AGE key for sops-nix";
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      set -euo pipefail
      keydir=/var/sops/age
      keyfile=$keydir/keys.txt

      mkdir -p "$keydir"
      chown root:sops "$keydir" || true
      chmod 0750 "$keydir" || true

      if [ -f "$keyfile" ]; then
        chown root:sops "$keyfile" || true
        chmod 0440 "$keyfile" || true
        exit 0
      fi

      ${pkgs.age}/bin/age-keygen -o "$keyfile"
      chown root:sops "$keyfile"
      chmod 0440 "$keyfile"

      pubkey=$(${pkgs.age}/bin/age-keygen -y "$keyfile" | sed 's/^# public key: //')
      echo "sops-nix host AGE public key: $pubkey"
    '';
  };

  # zram cushion on a 1GB box; small deliberately, revisit if OOM shows up.
  zramSwap = {
    enable = true;
    algorithm = "zstd";
    memoryPercent = 50;
    priority = 100;
  };

  services.tailscale = {
    enable = true;
    openFirewall = true;
    useRoutingFeatures = "client";
    authKeyFile = config.sops.secrets.tailscale_auth_key.path;
    extraSetFlags = [ "--accept-dns=true" ];
  };

  # ponytail: axis + cloudflared deliberately not included yet -- this
  # host's sops age key doesn't exist until after first boot, so it can't
  # decrypt axis/cloudflared secrets on the very first nixos-anywhere
  # install. Add both once the generated key is registered in .sops.yaml
  # and secrets are re-encrypted (see cdenneen/work, cloud-architecture-plan.md).
  systemd.user.services.herdr = {
    description = "Herdr persistent terminal workspace server";
    wantedBy = [ "default.target" ];
    serviceConfig = {
      Type = "simple";
      ExecStart = "${config.users.users.cdenneen.home}/.local/bin/herdr server";
      Restart = "on-failure";
      RestartSec = "5s";
    };
  };
}
