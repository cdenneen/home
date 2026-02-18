{
  config,
  pkgs,
  lib,
  ...
}:
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

  services.amazon-cloudwatch-agent = {
    enable = true;
    mode = "ec2";
    user = "root";
    commonConfiguration = {
      credentials = {
        imds_version = 2;
      };
    };
    configuration = {
      agent = {
        metrics_collection_interval = 60;
        region = "us-east-1";
        logfile = "/var/log/amazon-cloudwatch-agent/amazon-cloudwatch-agent.log";
      };
      metrics = {
        namespace = "CWAgent";
        append_dimensions = {
          ImageId = "\${aws:ImageId}";
          InstanceId = "\${aws:InstanceId}";
          InstanceType = "\${aws:InstanceType}";
          AutoScalingGroupName = "\${aws:AutoScalingGroupName}";
        };
        aggregation_dimensions = [ [ "InstanceId" ] ];
        metrics_collected = {
          cpu = {
            measurement = [
              "cpu_usage_idle"
              "cpu_usage_iowait"
              "cpu_usage_user"
              "cpu_usage_system"
            ];
            totalcpu = true;
            metrics_collection_interval = 60;
          };
          mem = {
            measurement = [
              "mem_used_percent"
              "mem_available"
              "mem_available_percent"
            ];
            metrics_collection_interval = 60;
          };
          disk = {
            measurement = [ "used_percent" ];
            resources = [ "/" ];
            drop_device = true;
            metrics_collection_interval = 60;
          };
          diskio = {
            measurement = [
              "reads"
              "writes"
              "read_bytes"
              "write_bytes"
              "io_time"
            ];
            resources = [ "*" ];
            metrics_collection_interval = 60;
          };
          net = {
            measurement = [
              "bytes_sent"
              "bytes_recv"
            ];
            resources = [ "*" ];
            metrics_collection_interval = 60;
          };
          swap = {
            measurement = [ "used_percent" ];
            metrics_collection_interval = 60;
          };
          processes = {
            measurement = [
              "running"
              "sleeping"
              "zombies"
              "total"
            ];
            metrics_collection_interval = 60;
          };
        };
      };
    };
  };

  services.amazon-ssm-agent.enable = true;

  home-manager.users.cdenneen.programs.starship.settings.palette = lib.mkForce "eros";

  # Matches running system (do not change after initial install)
  # Match global default; do not downgrade
  system.stateVersion = lib.mkForce "26.05";

  # Filesystems.
  # NOTE: Some upstream EC2/EFI modules also declare an ESP at /boot.
  # We force the full fileSystems attrset here so the ESP is only mounted
  # at /boot/efi; /boot must remain on the root filesystem for NixOS kernels.
  fileSystems = lib.mkForce {
    "/" = {
      device = "/dev/disk/by-uuid/f222513b-ded1-49fa-b591-20ce86a2fe7f";
      fsType = "ext4";
    };

    "/boot/efi" = {
      device = "/dev/disk/by-uuid/12CE-A600";
      fsType = "vfat";
    };
  };

  # Leave /boot on the root filesystem; only mount the ESP at /boot/efi.
  # This avoids running out of space on the ESP when storing kernels/initrd.

  # UEFI + GRUB (current system uses GRUB on EFI)
  boot.loader = {
    efi = {
      # EC2 UEFI typically does not provide persistent EFI variables.
      canTouchEfiVariables = false;
      efiSysMountPoint = "/boot/efi";
    };
    grub = {
      splashImage = lib.mkForce null;
      enable = true;
      configurationLimit = 3;
      efiSupport = true;
      # Install via the UEFI removable-media fallback path (EFI/BOOT).
      efiInstallAsRemovable = true;
      device = "nodev";
    };
  };

  # On EC2 we install GRUB as "removable" (EFI/BOOT/BOOTAA64.EFI). In that mode
  # GRUB tends to use the ESP for configuration, which is too small for storing
  # NixOS kernels/initrds across generations.
  #
  # We generate a small ESP grub.cfg that:
  # - First entry chainloads the real GRUB menu from the root filesystem.
  # - Second entry boots the currently selected system profile directly.
  #
  # Important: avoid referencing config.system.build.* here; it can create module
  # evaluation recursion. Use stable on-disk paths instead.
  boot.loader.grub.extraInstallCommands = ''
        ${pkgs.coreutils}/bin/mkdir -p "${config.boot.loader.efi.efiSysMountPoint}/grub"
        ${pkgs.coreutils}/bin/cat > "${config.boot.loader.efi.efiSysMountPoint}/grub/grub.cfg" <<'EOF'
        # Autogenerated (NixOS): ESP GRUB config for EC2.
        set timeout=1
        set timeout_style=menu
        set default=0

        function chainload_rootfs_menu {
          insmod part_gpt
          insmod ext2
          insmod search_fs_file
          if search --no-floppy --file /boot/grub/grub.cfg --set=root; then
            set prefix=($root)/boot/grub
            configfile ($root)/boot/grub/grub.cfg
          fi
        }

        function boot_current_profile {
          insmod part_gpt
          insmod ext2
          insmod search_fs_file
          insmod linux
          if search --no-floppy --file /nix/var/nix/profiles/system/init --set=root; then
            linux ($root)/nix/var/nix/profiles/system/kernel init=/nix/var/nix/profiles/system/init console=ttyS0,115200n8
            initrd ($root)/nix/var/nix/profiles/system/initrd
            boot
          fi
        }

        menuentry "NixOS (full menu)" --class nixos --unrestricted {
          chainload_rootfs_menu
          echo "GRUB: failed to chainload /boot/grub/grub.cfg"
          sleep 5
        }

        menuentry "NixOS (current system profile)" --class nixos --unrestricted {
          boot_current_profile
          echo "GRUB: failed to boot /nix/var/nix/profiles/system"
          sleep 5
        }
    EOF
  '';

  # Networking (DHCP on ens5)
  networking.useDHCP = false;
  networking.interfaces.ens5.useDHCP = true;

  profiles.defaults.enable = true;
}
