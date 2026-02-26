{
  lib,
  pkgs,
  config,
  happyNix,
  unstablePkgs,
  opencode ? null,
  ...
}:
{
  imports = [
    happyNix.nixosModules.happy-server
    happyNix.nixosModules.happy-codex-agent
  ];

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

  services.happy-server = {
    enable = true;
    envFile = "/var/lib/happy/env";
    workspaceRoot = "/home/cdenneen/src/workspace";
    bindAddress = "127.0.0.1";
    publicUrl = "https://happy.denneen.net";
    port = 3000;
    storage = {
      mode = "pglite";
      local.bundle.enable = false;
    };
  };

  services.happy-codex-agent = {
    enable = true;
    mode = "user";
    pathPackages = [
      unstablePkgs.happy-coder
      unstablePkgs.codex
      unstablePkgs.coreutils
    ];
    instances = [
      {
        name = "nix";
        workspace = "/home/cdenneen/src/workspace/nix";
        happyServerUrl = "https://happy.denneen.net";
      }
      {
        name = "gitlab";
        workspace = "/home/cdenneen/src/workspace/gitlab";
        happyServerUrl = "https://happy.denneen.net";
      }
      {
        name = "infra";
        workspace = "/home/cdenneen/src/workspace/infra";
        happyServerUrl = "https://happy.denneen.net";
      }
      {
        name = "eks";
        workspace = "/home/cdenneen/src/workspace/eks";
        happyServerUrl = "https://happy.denneen.net";
      }
      {
        name = "backstage";
        workspace = "/home/cdenneen/src/workspace/backstage";
        happyServerUrl = "https://happy.denneen.net";
      }
      {
        name = "work";
        workspace = "/home/cdenneen/src/workspace/work";
        happyServerUrl = "https://happy.denneen.net";
      }
    ];
  };

  virtualisation.docker.enable = lib.mkForce false;

  services.amazon-ssm-agent.enable = true;

  # Allow inbound services over the private tailnet without opening ports to the public internet.
  networking.firewall.trustedInterfaces = lib.mkAfter [ "tailscale0" ];

  # Convenience for ad-hoc HTTP services during debugging.
  networking.firewall.interfaces.tailscale0.allowedTCPPorts = [
    53
    8080
  ];
  networking.firewall.interfaces.tailscale0.allowedUDPPorts = [ 53 ];

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

  # Allow subnet routing for Tailscale (routing features manage sysctls).
  services.tailscale.useRoutingFeatures = "server";
  services.tailscale.extraSetFlags = [
    "--accept-dns=false"
    "--advertise-routes=10.208.0.0/16"
  ];

  # Keep resolvconf from injecting localhost DNS servers.
  # dnsmasq is only for Tailscale split DNS on tailscale0.
  networking.resolvconf.useLocalResolver = false;
  networking.resolvconf.enable = false;
  networking.nameservers = [ "10.224.0.2" ];
  environment.etc."resolv.conf".text = ''
    search ec2.internal
    nameserver 10.224.0.2
    options edns0
  '';

  services.opencode-telegram-bridge = {
    enable = true;
    user = "cdenneen";
    systemdMode = "user";
    enableLinger = true;
  };

  systemd.services.home-manager-cdenneen = {
    environment = {
      OP_NIX_TOKEN_FILE = "/home/cdenneen/.config/opnix/token";
    };
  };
  users.users.cdenneen.extraGroups = lib.mkAfter [ "tailscale" ];

  services.dnsmasq = {
    enable = true;
    settings = {
      # Answer DNS queries from Tailscale clients for split DNS.
      interface = "tailscale0";
      bind-interfaces = true;
      domain-needed = true;
      bogus-priv = true;
      no-resolv = true;
      # Route git.ap.org lookups to the VPC resolver.
      server = [ "/git.ap.org/10.224.0.2" ];
    };
  };

  # Cloudflare Tunnel for Telegram webhook.
  sops.secrets.cloudflare_tunnel_token = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.opencode_server_password = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  systemd.user.services.opencode-serve =
    let
      run = pkgs.writeShellScript "opencode-web" ''
         set -euo pipefail
         gitlab_file="${config.users.users.cdenneen.home}/.config/opnix/gitlab_token"
        if [ -r "$gitlab_file" ]; then
          gitlab_token="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$gitlab_file")"
          if [ -z "$gitlab_token" ]; then
            echo "opencode-web: gitlab token file empty" >&2
          else
            export GITLAB_TOKEN="$gitlab_token"
          fi
        else
          echo "opencode-web: gitlab token file not readable" >&2
        fi
        exec /etc/profiles/per-user/cdenneen/bin/opencode serve --hostname 127.0.0.1 --port 4097
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
        EnvironmentFile = [ ];
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

  systemd.user.services.opencode-oauth2-proxy =
    let
      run = pkgs.writeShellScript "opencode-oauth2-proxy" ''
        set -euo pipefail
        client_secret_file="${config.users.users.cdenneen.home}/.config/opnix/chat_oauth_client_secret"
        cookie_secret_file="${config.users.users.cdenneen.home}/.config/opnix/chat_oauth_cookie_secret"

        if [ ! -r "$client_secret_file" ]; then
          echo "opencode-oauth2-proxy: client secret file not readable" >&2
          exit 1
        fi
        if [ ! -r "$cookie_secret_file" ]; then
          echo "opencode-oauth2-proxy: cookie secret file not readable" >&2
          exit 1
        fi

        export OAUTH2_PROXY_PROVIDER="github"
        export OAUTH2_PROXY_CLIENT_ID="Ov23liTeufmUc2bOkNIM"
        export OAUTH2_PROXY_CLIENT_SECRET
        OAUTH2_PROXY_CLIENT_SECRET="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$client_secret_file")"
        export OAUTH2_PROXY_COOKIE_SECRET
        OAUTH2_PROXY_COOKIE_SECRET="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$cookie_secret_file")"

        exec ${pkgs.oauth2-proxy}/bin/oauth2-proxy \
          --http-address=127.0.0.1:4096 \
          --upstream=http://127.0.0.1:4097 \
          --redirect-url=https://chat.denneen.net/oauth2/callback \
          --email-domain=* \
          --github-user=cdenneen \
          --cookie-domain=chat.denneen.net \
          --cookie-secure=true \
          --cookie-samesite=lax \
          --set-authorization-header=true \
          --pass-access-token=true
      '';
    in
    {
      description = "OpenCode GitHub OAuth proxy";
      after = [
        "network-online.target"
        "opencode-serve.service"
      ];
      wants = [
        "network-online.target"
        "opencode-serve.service"
      ];
      wantedBy = [ "default.target" ];
      serviceConfig = {
        Type = "simple";
        ExecStart = run;
        Restart = "always";
        RestartSec = 5;
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

  systemd.user.services.opencode-serve-compact =
    let
      run = pkgs.writeShellScript "opencode-serve-compact" ''
        set -euo pipefail
        pw_file="${config.sops.secrets.opencode_server_password.path}"
        log_dir="${config.users.users.cdenneen.home}/.local/state"
        log_file="$log_dir/opencode-serve-compact.log"
        ${pkgs.coreutils}/bin/mkdir -p "$log_dir"

        timestamp="$(${pkgs.coreutils}/bin/date -Is)"
        if [ ! -r "$pw_file" ]; then
          echo "$timestamp missing password file" >> "$log_file"
          exit 0
        fi

        pw="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$pw_file")"
        if [ -z "$pw" ]; then
          echo "$timestamp empty password file" >> "$log_file"
          exit 0
        fi

        auth="opencode:$pw"
        sessions="$(${pkgs.curl}/bin/curl -sS -u "$auth" http://127.0.0.1:4096/session \
          | ${pkgs.jq}/bin/jq -r '.[].id' || true)"

        if [ -z "$sessions" ]; then
          echo "$timestamp no sessions to compact" >> "$log_file"
          exit 0
        fi

        payload='{"command":"compact","arguments":""}'
        for sid in $sessions; do
          if ! ${pkgs.curl}/bin/curl -sS -u "$auth" \
            -H "content-type: application/json" \
            -X POST "http://127.0.0.1:4096/session/$sid/command" \
            -d "$payload" >/dev/null; then
            echo "$timestamp compact failed session=$sid" >> "$log_file"
          fi
        done

        count="$(${pkgs.coreutils}/bin/printf "%s" "$sessions" | ${pkgs.coreutils}/bin/wc -w | ${pkgs.coreutils}/bin/tr -d ' ')"
        echo "$timestamp compacted sessions=$count" >> "$log_file"
      '';
    in
    {
      description = "Compact OpenCode sessions via HTTP API";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = run;
      };
    };

  systemd.user.timers.opencode-serve-compact = {
    description = "Periodic OpenCode session compaction";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "5m";
      OnUnitActiveSec = "6h";
      Persistent = true;
      Unit = "opencode-serve-compact.service";
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

  home-manager.users.cdenneen.imports = [ ./nyx-home.nix ];

  home-manager.users.cdenneen.programs.opencode.package = lib.mkForce (
    if opencode != null then
      opencode.packages.${pkgs.stdenv.hostPlatform.system}.default
    else
      pkgs.opencode
  );

  # Starship palette is configured in nyx-home.nix.
}
