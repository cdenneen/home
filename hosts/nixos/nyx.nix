{
  lib,
  pkgs,
  config,
  opencode ? null,
  ...
}:
{
  networking.hostName = "nyx";
  ec2.efi = true;

  services.tailscale = {
    enable = true;
    openFirewall = true;
  };

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

  # Allow inbound services over the private tailnet without opening ports to the public internet.
  networking.firewall.trustedInterfaces = lib.mkAfter [ "tailscale0" ];

  # Convenience for ad-hoc HTTP services during debugging.
  networking.firewall.interfaces.tailscale0.allowedTCPPorts = [ 8080 ];

  programs.mosh.enable = true;
  networking.firewall.allowedUDPPortRanges = lib.mkAfter [
    {
      from = 60000;
      to = 61000;
    }
  ];

  fileSystems."/home/cdenneen/src" = {
    device = "UUID=48a9e4a3-252f-4676-afd9-f2ed39ac8e90";
    fsType = "ext4";
    options = [ "noatime" ];
  };

  # Keep the system bootable on EC2 (UEFI GRUB on ESP at /boot).
  boot.loader.grub.configurationLimit = 3;

  # Avoid conflicts with the EC2 headless profile's GRUB defaults.
  catppuccin.grub.enable = lib.mkForce false;

  # Switch display manager from Plasma to XFCE
  services.desktopManager.plasma6.enable = false;
  services.xserver.desktopManager.xfce.enable = true;
  services.xserver.displayManager.lightdm.enable = true;
  services.displayManager.sddm.enable = false;

  services.udisks2.enable = lib.mkForce false;
  services.openssh.settings.PermitRootLogin = lib.mkForce "prohibit-password";

  # Matches running system (do not change after initial install)
  system.stateVersion = lib.mkForce "26.05";

  profiles.defaults.enable = true;

  # Keep rebuilds from saturating this host.
  # A too-parallel build can cause memory pressure and make the box unresponsive.
  nix.settings = {
    max-jobs = 1;
    cores = 1;

    # Keep memory usage predictable during large downloads/unpacks.
    download-buffer-size = 104857600; # 100MB
  };

  # Safety net for low-memory builds.
  zramSwap = {
    enable = true;
    memoryPercent = 50;
  };

  # Let user systemd services start at boot (no login needed).
  users.users.cdenneen.linger = true;
  users.users.cdenneen.extraGroups = lib.mkAfter [ "tailscale" ];

  systemd.user.services.tailscale-up =
    let
      run = pkgs.writeShellScript "tailscale-up" ''
        set -euo pipefail
        exec ${pkgs.tailscale}/bin/tailscale up --accept-dns=false
      '';
    in
    {
      description = "Tailscale up";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "default.target" ];

      serviceConfig = {
        Type = "oneshot";
        ExecStart = run;
        RemainAfterExit = true;
      };
    };

  # Cloudflare Tunnel for Telegram webhook.
  environment.systemPackages = lib.mkAfter [ pkgs.cloudflared ];
  sops.secrets.cloudflare_tunnel_token = {
    mode = "0400";
    restartUnits = [ "cloudflared-telegram-bridge.service" ];
  };
  sops.secrets.opencode_server_password = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  systemd.services.cloudflared-telegram-bridge =
    let
      configFile = pkgs.writeText "cloudflared-telegram-bridge.yml" ''
        ingress:
          - hostname: nyx.denneen.net
            path: /telegram
            service: http://127.0.0.1:18080
          - hostname: chat.denneen.net
            service: http://127.0.0.1:4096
          - service: http_status:404
      '';
      run = pkgs.writeShellScript "cloudflared-telegram-bridge" ''
        set -euo pipefail
        token_file="${config.sops.secrets.cloudflare_tunnel_token.path}"
        exec ${pkgs.cloudflared}/bin/cloudflared \
          --config "${configFile}" \
          tunnel run \
          --token "$(cat "$token_file")"
      '';
    in
    {
      description = "Cloudflare Tunnel (Telegram bridge + chat)";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        ExecStart = run;
        Restart = "always";
        RestartSec = 2;
      };
    };

  systemd.user.services.opencode-serve =
    let
      run = pkgs.writeShellScript "opencode-web" ''
         set -euo pipefail
         pw_file="${config.sops.secrets.opencode_server_password.path}"
        if [ ! -r "$pw_file" ]; then
          echo "opencode-web: password file not readable" >&2
          exit 1
        fi
        pw="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$pw_file")"
        if [ -z "$pw" ]; then
          echo "opencode-web: password file empty" >&2
          exit 1
        fi
        echo "opencode-web: loaded password length ''${#pw}" >&2
        export OPENCODE_SERVER_USERNAME="opencode"
        export OPENCODE_SERVER_PASSWORD="$pw"
        exec /etc/profiles/per-user/cdenneen/bin/opencode serve --hostname 127.0.0.1 --port 4096
      '';
    in
    {
      description = "OpenCode web (chat)";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "default.target" ];
      unitConfig = {
        StartLimitIntervalSec = "5min";
        StartLimitBurst = 3;
      };

      serviceConfig = {
        Type = "simple";
        Environment = [
          "PATH=${lib.makeBinPath [ pkgs.curl ]}:/run/current-system/sw/bin:/etc/profiles/per-user/cdenneen/bin"
        ];
        ExecStart = run;
        MemoryAccounting = true;
        MemoryHigh = "5G";
        MemoryMax = "6G";
        MemorySwapMax = "0";
        RuntimeMaxSec = "4h";
        OOMPolicy = "stop";
        Restart = "always";
        RestartSec = 15;
      };
    };

  systemd.user.services.opencode-serve-watchdog =
    let
      limitMb = 5200;
      peakLimitMb = 5400;
      run = pkgs.writeShellScript "opencode-serve-watchdog" ''
        set -euo pipefail
        limit_bytes=$(( ${toString limitMb} * 1024 * 1024 ))
        peak_limit_bytes=$(( ${toString peakLimitMb} * 1024 * 1024 ))
        log_dir="${config.users.users.cdenneen.home}/.local/state"
        log_file="$log_dir/opencode-serve-watchdog.log"
        ${pkgs.coreutils}/bin/mkdir -p "$log_dir"

        timestamp="$(${pkgs.coreutils}/bin/date -Is)"
        cg="$(${pkgs.systemd}/bin/systemctl --user show -p ControlGroup --value opencode-serve.service || true)"
        cgroup_bytes=""
        cgroup_peak=""
        if [ -n "$cg" ] && [ -r "/sys/fs/cgroup''${cg}/memory.current" ]; then
          cgroup_bytes="$(${pkgs.coreutils}/bin/cat "/sys/fs/cgroup''${cg}/memory.current" || true)"
          if [ -r "/sys/fs/cgroup''${cg}/memory.peak" ]; then
            cgroup_peak="$(${pkgs.coreutils}/bin/cat "/sys/fs/cgroup''${cg}/memory.peak" || true)"
          fi
        fi

        pid="$(${pkgs.systemd}/bin/systemctl --user show -p MainPID --value opencode-serve.service || true)"
        rss_kb=""
        if [ -n "$pid" ] && [ "$pid" != "0" ]; then
          rss_kb="$(${pkgs.gawk}/bin/awk '/^VmRSS:/ {print $2}' "/proc/$pid/status" || true)"
        fi

        echo "$timestamp pid=$pid rss_kb=$rss_kb cgroup_bytes=$cgroup_bytes cgroup_peak=$cgroup_peak limit_bytes=$limit_bytes peak_limit_bytes=$peak_limit_bytes" >> "$log_file"

        if [ -n "$cgroup_peak" ] && [ "$cgroup_peak" -gt "$peak_limit_bytes" ]; then
          echo "$timestamp restart reason=cgroup_peak cgroup_peak=$cgroup_peak peak_limit_bytes=$peak_limit_bytes" >> "$log_file"
          exec ${pkgs.systemd}/bin/systemctl --user restart opencode-serve.service
        fi

        if [ -n "$cgroup_bytes" ] && [ "$cgroup_bytes" -gt "$limit_bytes" ]; then
          echo "$timestamp restart reason=cgroup_bytes cgroup_bytes=$cgroup_bytes limit_bytes=$limit_bytes" >> "$log_file"
          exec ${pkgs.systemd}/bin/systemctl --user restart opencode-serve.service
        fi

        if [ -n "$rss_kb" ]; then
          limit_kb=$(( ${toString limitMb} * 1024 ))
          if [ "$rss_kb" -gt "$limit_kb" ]; then
            echo "$timestamp restart reason=rss_kb rss_kb=$rss_kb limit_kb=$limit_kb" >> "$log_file"
            exec ${pkgs.systemd}/bin/systemctl --user restart opencode-serve.service
          fi
        fi
      '';
    in
    {
      description = "Restart OpenCode web if cgroup memory too high";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = run;
      };
    };

  systemd.user.timers.opencode-serve-watchdog = {
    description = "Monitor OpenCode web memory usage";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2m";
      OnUnitActiveSec = "30s";
      Persistent = true;
      Unit = "opencode-serve-watchdog.service";
    };
  };

  systemd.services.opencode-user-restart =
    let
      run = pkgs.writeShellScript "opencode-user-restart" ''
        set -euo pipefail
        if [ -z "''${XDG_RUNTIME_DIR:-}" ] || [ ! -S "''${XDG_RUNTIME_DIR}/bus" ]; then
          echo "opencode-user-restart: user bus not available, skipping" >&2
          exit 0
        fi
        exec ${pkgs.systemd}/bin/systemctl --user restart opencode-serve.service
      '';
    in
    {
      description = "Restart OpenCode user services after secret update";
      serviceConfig = {
        Type = "oneshot";
        User = "cdenneen";
        Environment = [
          "XDG_RUNTIME_DIR=/run/user/%U"
          "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/%U/bus"
        ];
        ExecStart = run;
      };
    };

  systemd.paths.opencode-user-restart = {
    description = "Watch OpenCode server password secret";
    wantedBy = [ "multi-user.target" ];
    pathConfig = {
      PathChanged = config.sops.secrets.opencode_server_password.path;
      PathModified = config.sops.secrets.opencode_server_password.path;
    };
    unitConfig = {
      Unit = "opencode-user-restart.service";
    };
  };

  home-manager.users.cdenneen.programs.telegram-bridge = {
    enable = true;

    telegram.botTokenFile = config.home-manager.users.cdenneen.sops.secrets.telegram_bot_token.path;
    telegram.ownerChatIdFile = config.home-manager.users.cdenneen.sops.secrets.telegram_chat_id.path;
    telegram.updatesMode = "webhook";
    telegram.webhook.publicUrl = "https://nyx.denneen.net";

    opencode.workspaceRoot = "/home/cdenneen/src/workspace";
    opencode.useSharedServer = true;
    opencode.serverUrl = "http://127.0.0.1:4096";
    opencode.serverUsername = "opencode";
    opencode.serverPasswordFile = config.sops.secrets.opencode_server_password.path;

    web = {
      enable = true;
      baseUrl = "http://127.0.0.1:4096";
      username = "opencode";
      passwordFile = config.sops.secrets.opencode_server_password.path;
      syncIntervalSec = 10;
      forwardUserPrompts = true;
      forwardAgentSteps = true;
    };

    chat.allowedGithubUsers = [ "cdenneen" ];
    chat.announceStartup = true;
    chat.announceMessage = "Bridge connected.";
  };

  home-manager.users.cdenneen.programs.opencode.package = lib.mkForce (
    if opencode != null then
      opencode.packages.${pkgs.stdenv.hostPlatform.system}.default
    else
      pkgs.opencode
  );

  home-manager.users.cdenneen.programs.starship.settings.palette = lib.mkForce "nyx";
}
