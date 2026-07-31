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
    ../default.nix
    ./compat/display-manager-generic.nix
    ./services
    ./containers
    ./filesystems.nix
    ./gaming.nix
  ];
  config = lib.mkMerge [
    (lib.mkIf cfg.defaults.enable {
      networking = {
        networkmanager.enable = true;
        nftables.enable = true;
      };
      time.timeZone = "America/Chicago";
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
        gtk.icon.enable = lib.mkDefault false;
        tty = {
          enable = true;
          flavor = flavor;
        };
        plymouth.enable = true;
      };
      console.useXkbConfig = true;
      programs = {
        ssh.startAgent = false;
        # gpg-agent is managed via Home Manager for the primary user.
        gnupg.agent = lib.mkIf (!((config ? home-manager) && (config.home-manager.users ? cdenneen))) {
          enable = true;
          enableSSHSupport = true;
        };
        nix-ld = {
          enable = true;
          libraries =
            with pkgs;
            (appimageTools.defaultFhsEnvArgs.multiPkgs pkgs)
            ++ (appimageTools.defaultFhsEnvArgs.targetPkgs pkgs)
            ++ [
              SDL
              SDL_image
              SDL_mixer
              SDL_ttf
              freeglut
              fuse
              fuse3
              icu
              libclang.lib
              libdbusmenu
              libgcc
              libxcrypt-legacy
              libxml2
              mesa
              pcre
              pcre-cpp
              pcre2
              python3
              stdenv.cc.cc
              xz
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
        udev.packages = with pkgs; [ yubikey-personalization ];
        printing.enable = cfg.gui.enable;
        pipewire = lib.mkIf cfg.gui.enable {
          enable = true;
          alsa.enable = true;
          alsa.support32Bit = true;
          pulse.enable = true;
        };
        flatpak.enable = cfg.gui.enable;
        fwupd.enable = true;
        kanata = {
          enable = true;
          keyboards.usbKeyboard = {
            devices = [
              "/dev/input/by-path/pci-0000:27:00.3-usb-0:3:1.0-event-kbd"
              "/dev/input/by-path/pci-0000:27:00.3-usbv2-0:3:1.0-event-kbd"
            ];
            extraDefCfg = "process-unmapped-keys yes";
            config = ''
              (defsrc
                caps
              )

              (defalias
                caps (tap-hold 100 100 esc lctl)
              )

              (deflayer base
                @caps
              )
            '';
          };
        };
      };
      system = {
        stateVersion = "26.05";
        autoUpgrade = {
          enable = true;
          flake = "github:cdenneen/home";
          persistent = true;
          allowReboot = true;
          rebootWindow = {
            lower = "01:00";
            upper = "05:00";
          };
          randomizedDelaySec = "45min";
        };
      };
      systemd.services.nixos-upgrade =
        let
          rebuildExe = lib.getExe pkgs.nixos-rebuild-ng;
          flakeRef = config.system.autoUpgrade.flake;
          rebootLower = config.system.autoUpgrade.rebootWindow.lower;
          rebootUpper = config.system.autoUpgrade.rebootWindow.upper;
        in
        {
          script = lib.mkForce ''
            set -euo pipefail

            token_file="/run/secrets/github-token"
            access_flags=()
            if [ -r "$token_file" ] && [ -s "$token_file" ]; then
              token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$token_file")"
              access_flags=(--option access-tokens "github.com=$token")
            fi

            ${rebuildExe} boot --refresh --flake ${lib.escapeShellArg flakeRef} --upgrade "''${access_flags[@]}"

            booted="$(${pkgs.coreutils}/bin/readlink /run/booted-system/{initrd,kernel,kernel-modules})"
            built="$(${pkgs.coreutils}/bin/readlink /nix/var/nix/profiles/system/{initrd,kernel,kernel-modules})"

            current_time="$(${pkgs.coreutils}/bin/date +%H:%M)"
            lower=${lib.escapeShellArg rebootLower}
            upper=${lib.escapeShellArg rebootUpper}

            if [[ "$lower" < "$upper" ]]; then
              if [[ "$current_time" > "$lower" ]] && [[ "$current_time" < "$upper" ]]; then
                do_reboot="true"
              else
                do_reboot="false"
              fi
            else
              if [[ "$current_time" < "$upper" ]] || [[ "$current_time" > "$lower" ]]; then
                do_reboot="true"
              else
                do_reboot="false"
              fi
            fi

            if [ "$booted" = "$built" ]; then
              ${rebuildExe} switch --refresh --flake ${lib.escapeShellArg flakeRef} "''${access_flags[@]}"
            elif [ "$do_reboot" != "true" ]; then
              echo "Outside of configured reboot window, skipping."
            else
              ${pkgs.systemd}/bin/shutdown -r +1
            fi
          '';
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
      environment.systemPackages =
        with pkgs;
        [
          cifs-utils
          kitty
          alacritty
          ghostty
        ]
        ++ lib.optionals (config.system.activationScripts ? setupSecrets) [
          (writeShellScriptBin "sops-nix-system" "${config.system.activationScripts.setupSecrets.text}")
        ];
    })
    {
      # Nix performance tuning
      nix.settings = {
        # Use all available CPU cores
        cores = lib.mkDefault 0;
        # Let Nix decide optimal parallelism
        max-jobs = lib.mkDefault "auto";
        # Prefer binary caches when available
        builders-use-substitutes = true;
        # Increase download buffering for large artifacts
        download-buffer-size = lib.mkDefault 524288000; # 500MB
      };

      # Podman Linux-specific defaults (not available on nix-darwin).
      virtualisation.podman.dockerSocket.enable = lib.mkDefault true;
      virtualisation.podman.defaultNetwork.settings.dns_enabled = lib.mkDefault true;
    }
  ];
}
