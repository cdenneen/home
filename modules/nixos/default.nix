{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles;
in
{
  imports = [
    ../os
    ./services
    ./containers
    ./filesystems.nix
    ./gaming.nix
  ];
  config = lib.mkIf cfg.defaults.enable {
    networking = {
      networkmanager.enable = true;
      nftables.enable = true;
    };
    time.timeZone = "America/New_York";
    i18n = {
      defaultLocale = "en_US.UTF-8";
      extraLocaleSettings = {
        LC_ADDRESS = "en_US.UTF-8";
        LC_IDENTIFICATION = "en_US.UTF-8";
        LC_MEASUREMENT = "C.UTF-8";
        LC_MONETARY = "en_US.UTF-8";
        LC_NAME = "en_US.UTF-8";
        LC_NUMERIC = "en_US.UTF-8";
        LC_PAPER = "en_US.UTF-8";
        LC_TELEPHONE = "en_US.UTF-8";
        LC_TIME = "C.UTF-8";
      };
    };
    catppuccin = rec {
      enable = true;
      flavor = "frappe";
      accent = "red";
      tty = {
        enable = true;
        flavor = flavor;
      };
      plymouth.enable = true;
    };
    console.useXkbConfig = true;
    programs = {
      bash.blesh.enable = true;
      ssh.startAgent = false;
      gnupg.agent = {
        enable = true;
        enableSSHSupport = true;
      };
      nix-ld = {
        enable = true;
        libraries = with pkgs; [
          nss
          pcre
          pcre2
          pcre16
          pcre-cpp
          libgcc
        ];
      };
      command-not-found.enable = false;
    };
    services = {
      xserver.xkb = {
        layout = "us";
        options = "ctrl:nocaps";
      };
      pcscd.enable = true;
      # udev.packages = with pkgs; [ yubikey-personalization ];
      printing.enable = cfg.printing.enable;
      pipewire = lib.mkIf cfg.gui.enable {
        enable = true;
        alsa.enable = true;
        alsa.support32Bit = true;
        pulse.enable = true;
      };
      flatpak.enable = cfg.gui.enable;
      fwupd.enable = true;
    };
    system = {
      stateVersion = "25.05";
      autoUpgrade = {
        enable = true;
        flake = "${config.users.users.${config.userPresets.cdenneen.name}.home}/nixcfg";
        persistent = true;
        allowReboot = true;
        rebootWindow = {
          lower = "01:00";
          upper = "05:00";
        };
        randomizedDelaySec = "45min";
        flags = [
          "--update-input"
          "nixpkgs"
        ];
      };
    };
    security.rtkit.enable = true;
    nix.optimise.automatic = true;
    boot = {
      loader.systemd-boot.configurationLimit = lib.mkIf config.boot.loader.systemd-boot.enable 3;
      binfmt.registrations.appimage = {
        wrapInterpreterInShell = false;
        interpreter = lib.getExe pkgs.appimage-run;
        recognitionType = "magic";
        offset = 0;
        mask = ''\xff\xff\xff\xff\x00\x00\x00\x00\xff\xff\xff'';
        magicOrExtension = ''\x7fELF....AI\x02'';
      };
      plymouth.enable = true;
    };
    sops = {
      defaultSopsFile = ../../secrets/secrets.yaml;
      age.keyFile = "/var/sops/age/keys.txt";
    };
    environment.systemPackages = lib.optionals (config.system.activationScripts ? setupSecrets) [
      (pkgs.writeShellScriptBin "sops-nix-system" "${config.system.activationScripts.setupSecrets.text}")
    ];
  };
}
