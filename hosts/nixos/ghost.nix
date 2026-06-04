{
  config,
  lib,
  pkgs,
  happier,
  ...
}:
let
  pepsRepoDir = "/var/lib/peps/repo";
  pepsRuntimeDir = "/var/lib/peps";
  pepsGitRemote = "chris@100.125.246.107:/volume1/Git/peptide_tracker.git";
  pepsGitBranch = "main";
  pepsApiPort = "8787";
  pepsCloudflareTokenFile = "${pepsRuntimeDir}/cloudflare_tunnel_token";
  pepsHealthImportTokenFile = "${pepsRuntimeDir}/health_import_token";
in
{
  imports = [
    happier.nixosModules.happier-server
  ];

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
    hostName = "ghost";
    firewall.trustedInterfaces = lib.mkAfter [ "tailscale0" ];
    firewall = {
      allowedTCPPorts = [ 22 ];
      allowedUDPPorts = [ ];
    };
  };

  users.users.cdenneen.openssh.authorizedKeys.keyFiles = [
    ../../pub/ssh/cdenneen_ed25519_2024.pub
  ];

  profiles.defaults.enable = false;
  profiles.hmIntegrated.enable = false;
  environment.systemPackages = lib.mkForce [
    pkgs.bashInteractive
    pkgs.cloudflared
    pkgs.curl
    pkgs.git
    pkgs.nodejs_24
    pkgs.openssh
    pkgs.util-linux
  ];

  services = {
    openssh = {
      enable = true;
      settings.PasswordAuthentication = false;
    };

    tailscale = {
      enable = true;
      openFirewall = true;
    };

    happier-server = {
      enable = true;
      package = happier.packages.${pkgs.stdenv.hostPlatform.system}.happier-server;
      mode = "light";
      port = 3005;
      environmentFile = "/var/lib/happier-server/happier.env";
    };
  };

  sops.secrets.ghost_cloudflare_tunnel_token = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
    path = pepsCloudflareTokenFile;
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/happier-server 0700 root root -"
    "d ${pepsRuntimeDir} 0750 cdenneen users -"
    "d ${pepsRepoDir} 0750 cdenneen users -"
  ];

  systemd.services.happier-env-bootstrap = {
    description = "Bootstrap HANDY_MASTER_SECRET for happier-server";
    before = [ "happier-server.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [
      pkgs.coreutils
      pkgs.gnugrep
      pkgs.openssl
    ];
    script = ''
      set -euo pipefail

      env_file="/var/lib/happier-server/happier.env"
      ${pkgs.coreutils}/bin/mkdir -p /var/lib/happier-server

      if [ ! -s "$env_file" ] || ! ${pkgs.gnugrep}/bin/grep -q '^HANDY_MASTER_SECRET=' "$env_file"; then
        secret="$(${pkgs.openssl}/bin/openssl rand -base64 48 | ${pkgs.coreutils}/bin/tr -d '\n\r')"
        ${pkgs.coreutils}/bin/install -m 600 /dev/null "$env_file"
        printf 'HANDY_MASTER_SECRET=%s\n' "$secret" > "$env_file"
      fi

      ${pkgs.coreutils}/bin/chmod 600 "$env_file"
    '';
  };

  systemd.services.happier-server = {
    requires = [ "happier-env-bootstrap.service" ];
    after = [ "happier-env-bootstrap.service" ];
  };

  systemd.services.peps-sync = {
    description = "Sync peps repo from NAS over Tailscale";
    after = [
      "network-online.target"
      "tailscaled.service"
    ];
    wants = [
      "network-online.target"
      "tailscaled.service"
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = pepsRuntimeDir;
    };
    path = [
      pkgs.coreutils
      pkgs.git
      pkgs.openssh
    ];
    script = ''
      set -euo pipefail

      if [ ! -r /home/cdenneen/.ssh/id_ed25519 ]; then
        echo "Missing /home/cdenneen/.ssh/id_ed25519 for NAS clone auth" >&2
        exit 1
      fi

      export GIT_SSH_COMMAND="ssh -i /home/cdenneen/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

      if [ ! -d "${pepsRepoDir}/.git" ]; then
        rm -rf "${pepsRepoDir}"
        git clone --branch "${pepsGitBranch}" "${pepsGitRemote}" "${pepsRepoDir}"
      fi

      cd "${pepsRepoDir}"
      git remote set-url origin "${pepsGitRemote}"
      git fetch --prune origin "${pepsGitBranch}"
      git checkout -B "${pepsGitBranch}" "origin/${pepsGitBranch}"
      git reset --hard "origin/${pepsGitBranch}"
    '';
  };

  systemd.timers.peps-sync = {
    description = "Periodic peps repo sync";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2m";
      OnUnitActiveSec = "10m";
      Unit = "peps-sync.service";
    };
  };

  systemd.services.peps-api = {
    description = "Peps API/web runtime";
    after = [
      "network-online.target"
      "tailscaled.service"
      "peps-sync.service"
    ];
    wants = [
      "network-online.target"
      "tailscaled.service"
    ];
    requires = [ "peps-sync.service" ];
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = pepsRepoDir;
      Restart = "always";
      RestartSec = "10s";
      Environment = [
        "HOME=/home/cdenneen"
        "API_PORT=${pepsApiPort}"
        "AUTH_REQUIRED=true"
        "AUTH_ADMIN_EMAILS=cdenneen@gmail.com,c.denneen@gmail.com"
      ];
    };
    path = [
      pkgs.coreutils
      pkgs.curl
      pkgs.nodejs_24
    ];
    script = ''
      set -euo pipefail

      cd "${pepsRepoDir}"

      if [ -r "${pepsHealthImportTokenFile}" ]; then
        export HEALTH_IMPORT_TOKEN="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${pepsHealthImportTokenFile}")"
      fi

      npm ci --no-audit --no-fund
      npm run web:build
      exec npm run api:start
    '';
  };

  systemd.services.peps-cloudflared = {
    description = "Cloudflare Tunnel for peps.denneen.net and peps-api.denneen.net";
    after = [
      "network-online.target"
      "tailscaled.service"
      "peps-api.service"
    ];
    wants = [
      "network-online.target"
      "tailscaled.service"
      "peps-api.service"
    ];
    requires = [ "peps-api.service" ];
    unitConfig = {
      ConditionPathExists = pepsCloudflareTokenFile;
    };
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      Restart = "always";
      RestartSec = "5s";
    };
    path = [
      pkgs.cloudflared
      pkgs.coreutils
    ];
    script = ''
      set -euo pipefail

      token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${pepsCloudflareTokenFile}")"
      if [ -z "$token" ]; then
        echo "${pepsCloudflareTokenFile} is empty" >&2
        exit 1
      fi

      exec cloudflared tunnel --no-autoupdate run --token "$token"
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
