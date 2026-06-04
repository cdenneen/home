{ lib, pkgs, ... }:
{
  boot = {
    loader.systemd-boot.enable = true;
    loader.efi.canTouchEfiVariables = true;
    initrd.availableKernelModules = [
      "xhci_pci"
      "virtio_scsi"
    ];
    kernelParams = [
      "console=ttyAMA0,115200n8"
      "console=tty1"
    ];
    binfmt.emulatedSystems = [ "x86_64-linux" ];
  };

  networking = {
    hostName = "ghost";
    firewall.allowedTCPPorts = [ 22 ];
    firewall.allowedUDPPorts = [ ];
  };

  profiles.defaults.enable = true;
  profiles.hmIntegrated.enable = false;
  system.stateVersion = lib.mkForce "26.05";

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

  environment.systemPackages = lib.mkAfter (
    [
      pkgs.bashInteractive
      pkgs.curl
      pkgs.git
      pkgs.openssh
      pkgs.util-linux
    ]
    ++ lib.optionals (pkgs ? oci-cli) [ pkgs.oci-cli ]
  );

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

  disko.devices.disk.sda = {
    type = "disk";
    device = "/dev/sda";
    content = {
      type = "gpt";
      partitions = {
        ESP = {
          name = "ESP";
          size = "500M";
          type = "EF00";
          content = {
            type = "filesystem";
            format = "vfat";
            mountpoint = "/boot";
            extraArgs = [
              "-n"
              "BOOT"
            ];
          };
        };
        root = {
          size = "100%";
          content = {
            type = "btrfs";
            extraArgs = [
              "-f"
              "-L"
              "NIXOS"
            ];
            subvolumes = {
              "@" = {
                mountpoint = "/";
              };
              "@home" = {
                mountOptions = [ "compress=zstd" ];
                mountpoint = "/home";
              };
              "@nix" = {
                mountOptions = [
                  "compress=zstd"
                  "noatime"
                ];
                mountpoint = "/nix";
              };
            };
          };
        };
      };
    };
  };
}
