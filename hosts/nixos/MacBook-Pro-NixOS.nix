{
  config,
  lib,
  pkgs,
  ...
}:
{
  networking.hostName = "MacBook-Pro-NixOS";
  networking.networkmanager.enable = true;
  networking.useDHCP = lib.mkDefault true;
  boot = {
    loader.systemd-boot.enable = true;
    loader.systemd-boot.editor = true;
    loader.efi.canTouchEfiVariables = true;
    kernelParams = [ "boot.shell_on_fail" ];
    loader.systemd-boot.memtest86.enable = true;
    initrd.availableKernelModules = [
      "xhci_pci"
      "ehci_pci"
      "ahci"
      "firewire_ohci"
      "usbhid"
      "usb_storage"
      "sd_mod"
      "sr_mod"
      "sdhci_pci"
    ];
    initrd.kernelModules = [ ];
    kernelModules = [
      "kvm-intel"
    ];
    # If b43 does not work, switch back to the Broadcom STA driver:
    # kernelModules = [ "kvm-intel" "wl" ];
    # extraModulePackages = [ config.boot.kernelPackages.broadcom_sta ];
  };
  boot.kernelPackages = pkgs.linuxPackages_6_6;
  boot.kernel.sysctl = {
    "kernel.sysrq" = 1;
    "net.core.netdev_max_backlog" = 30000;
    "net.core.rmem_default" = 262144;
    "net.core.rmem_max" = 33554432;
    "net.core.wmem_default" = 262144;
    "net.core.wmem_max" = 33554432;
    "net.ipv4.ipfrag_high_threshold" = 5242880;
    "net.ipv4.tcp_keepalive_intvl" = 30;
    "net.ipv4.tcp_keepalive_probes" = 5;
    "net.ipv4.tcp_keepalive_time" = 300;
    "vm.dirty_background_bytes" = 134217728;
    "vm.dirty_bytes" = 402653184;
    "vm.min_free_kbytes" = 131072;
    "vm.swappiness" = 10;
    "vm.vfs_cache_pressure" = 90;
    "fs.aio-max-nr" = 1000000;
    "fs.inotify.max_user_watches" = 65536;
    "kernel.panic" = 5;
    "kernel.pid_max" = 131072;
  };
  boot.initrd.postDeviceCommands = lib.mkBefore ''
    if grep -qw fsckroot=1 /proc/cmdline; then
      echo "fsckroot=1 set; running fsck on root device..."
      udevadm settle
      root_dev="${config.fileSystems."/".device}"
      if [ ! -e "$root_dev" ]; then
        echo "Root device $root_dev not found; falling back to /dev/sda2"
        root_dev="/dev/sda2"
      fi
      echo "fsck target: $root_dev"
      fsck.ext4 -fy "$root_dev" || true
    fi
  '';
  hardware.cpu.intel.updateMicrocode = lib.mkDefault config.hardware.enableRedistributableFirmware;
  # Mitigate MDS by disabling SMT (trade-off: lower peak throughput).
  security.allowSimultaneousMultithreading = false;
  # Provide swap to improve memory pressure behavior.
  zramSwap.enable = true;
  zramSwap.memoryPercent = 35;
  services.fstrim.enable = true;
  # Prefer the in-kernel b43 driver + firmware for BCM4331.
  networking.enableB43Firmware = true;
  # If you must use the Broadcom STA driver again, allow it explicitly:
  # nixpkgs.config.permittedInsecurePackages = [
  #   "broadcom-sta-6.30.223.271-59-6.12.74"
  # ];
  # Pin Nix to a current release to avoid daemon/evaluator crashes.
  nix.package = pkgs.nixVersions.latest;

  # Increase the default DPI size
  services.xserver.resolutions = [
    {
      x = 1680;
      y = 1050;
    }
    {
      x = 1440;
      y = 900;
    }
  ];
  services.xserver.dpi = lib.mkForce 120;

  # Fix default power governor to run at a lower frequency and boost as needed
  powerManagement.cpuFreqGovernor = "schedutil";
  services.upower = {
    enable = true;
    usePercentageForPolicy = true;
    percentageLow = 15;
    percentageCritical = 5;
    percentageAction = 2;
    criticalPowerAction = "PowerOff";
  };
  services.thermald.enable = true;
  services.logind.settings.Login = {
    HandleLidSwitch = "suspend";
  };
  services.udisks2.enable = true;
  services.tlp.enable = true;
  services.power-profiles-daemon.enable = false;
  services.smartd.enable = true;
  environment.systemPackages = with pkgs; [
    smartmontools
    efivar
    _1password-gui
    system-config-printer
  ];

  # Avoid "too many open files" warnings from NetworkManager.
  security.pam.loginLimits = [
    {
      domain = "*";
      type = "soft";
      item = "nofile";
      value = "1048576";
    }
    {
      domain = "*";
      type = "hard";
      item = "nofile";
      value = "1048576";
    }
  ];

  # Enable mDNS .local resolution (addresses Avahi NSS warning).
  services.avahi = {
    enable = true;
    nssmdns4 = true;
  };

  # Limit substituters to cache.nixos.org for stability.
  nix.settings.substituters = [ "https://cache.nixos.org" ];
  nix.settings.trusted-substituters = [ "https://cache.nixos.org" ];
  nix.settings.trusted-public-keys = [
    "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
  ];

  profiles = {
    defaults.enable = true;
    dev.enable = false;
    gui.enable = true;
  };
  userPresets.cdenneen.enable = true;
  home-manager.users.cdenneen.programs.opencode.enable = lib.mkForce false;
  home-manager.users.cdenneen.profiles.gui.ghostty.softwareRenderer = true;
  home-manager.users.cdenneen.xdg.desktopEntries."ghostty-safe" = {
    name = "Ghostty (Safe)";
    genericName = "Terminal";
    categories = [
      "System"
      "TerminalEmulator"
    ];
    icon = "ghostty";
    exec = "env GDK_BACKEND=wayland,x11 LIBGL_ALWAYS_SOFTWARE=1 ghostty";
    terminal = false;
  };
  home-manager.users.cdenneen.wayland.windowManager.hyprland.enable = lib.mkForce false;
  services.xserver.enable = true;
  services.displayManager.sddm.enable = true;
  services.desktopManager.plasma6.enable = true;
  security.polkit.enable = true;
  services.gnome.gnome-keyring.enable = true;
  networking.networkmanager.plugins = with pkgs; [
    networkmanager-openconnect
  ];
  xdg.portal = {
    enable = true;
  };
  systemd.services.disable-mac-boot-chime = {
    description = "Disable Mac boot chime (set EFI audio volume to 0)";
    wantedBy = [ "multi-user.target" ];
    after = [ "sysinit.target" ];
    serviceConfig = {
      Type = "oneshot";
    };
    script = ''
      if [ -d /sys/firmware/efi/efivars ]; then
        ${pkgs.efivar}/bin/efivar \
          --name 7c436110-ab2a-4bbb-a880-fe41995c9f82:SystemAudioVolume \
          --write --data 00 || true
        ${pkgs.efivar}/bin/efivar \
          --name 7c436110-ab2a-4bbb-a880-fe41995c9f82:SystemAudioVolumeDB \
          --write --data 00 || true
      fi
    '';
  };
  fileSystems."/" = {
    device = "/dev/disk/by-uuid/7a1a6b2c-519e-460e-b93f-815ef741d19a";
    fsType = "ext4";
  };

  fileSystems."/boot" = {
    device = "/dev/disk/by-uuid/5E2E-847A";
    fsType = "vfat";
    options = [
      "fmask=0077"
      "dmask=0077"
    ];
  };
  swapDevices = [ ];
}
