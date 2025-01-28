{
  config,
  lib,
  pkgs,
  nh,
  ...
}:
let
  cfg = config.profiles;
in
{
  config = lib.mkIf cfg.defaults.enable {
    programs = {
      zsh.enable = true;
      nh = {
        enable = true;
        flake = "${config.users.users.${config.userPresets.cdenneen.name}.home}/nixcfg";
        package = nh.packages.${pkgs.stdenv.hostPlatform.system}.default;
        clean.enable = true;
      };
    };
    environment =
      let
        shells = with pkgs; [
          bashInteractive
          powershell
          zsh
        ];
      in
      {
        inherit shells;
        systemPackages =
          with pkgs;
          shells
          ++ [
            age
            broot
            cachix
            curl
            dig
            fd
            gnutar
            gzip
            jq
            kubectl
            lsof
            netcat
            nix-output-monitor
            nixfmt-rfc-style
            nmap
            nvd
            openssh
            openssl
            rclone
            ripgrep
            rsync
            sops
            unzip
            uutils-coreutils-noprefix
            wget
            zip
          ]
          ++ lib.optionals stdenv.isLinux [
            clinfo
            fwupd
            glxinfo
            pciutils
            vulkan-tools
            wayland-utils
          ];
      };

    #services.netbird.enable = true;
  };
}
