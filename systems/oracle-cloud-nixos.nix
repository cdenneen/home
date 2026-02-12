{
  pkgs,
  config,
  ...
}:
{
  boot = {
    loader.systemd-boot.enable = true;
    loader.efi.canTouchEfiVariables = true;
    initrd.availableKernelModules = [
      "xhci_pci"
      "virtio_scsi"
    ];
    binfmt.emulatedSystems = [ "x86_64-linux" ];
  };
  containerPresets = {
    podman.enable = true;
  };
  networking = {
    hostName = "oracle-cloud-nixos";
    firewall = {
      allowedTCPPorts = [
        443
        # gmod
        27015
        # minecraft java
        25565
        25566
        # terraria
        7777
        # vintage story
        42420
      ];
      allowedUDPPorts = [
        53
        443
        # gmod
        27015
        27005
        # minecraft bedrock
        19132
        # mincraft voice mod
        24454
        # terraria
        7777
        # vintage story
        42420
      ];
    };
  };
  profiles.defaults.enable = true;
  environment.systemPackages = with pkgs; [
    packwiz
  ];
  services = {
    openssh = {
      enable = true;
      settings.PasswordAuthentication = false;
    };
    minecraft-server = {
      declarative = true;
      enable = true;
      eula = true;
      lazymc = {
        enable = true;
        config = {
          public = {
            protocol = 771;
            version = "1.21.6";
          };
          advanced.rewrite_server_properties = false;
        };
      };
      openFirewall = true;
      package = pkgs.papermcServers.papermc-1_21_6;
      serverProperties = {
        allow-flight = true;
        difficulty = 3;
        enable-query = true;
        max-world-size = 50000;
        "query.port" = 25566;
        server-port = 25566;
        spawn-protection = 0;
      };
    };
  };
  containerPresets.portainer = {
    enable = true;
  };
  sops.secrets = {
    "rclone.conf" = { };
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
