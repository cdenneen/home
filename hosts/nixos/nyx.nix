{
  lib,
  pkgs,
  config,
  happier,
  jarvis,
  ...
}:
let
  convexHost = "nyx.tail0e55.ts.net";
  convexCloudOrigin = "http://${convexHost}:3210";
  convexSiteOrigin = "http://${convexHost}:3211";
  opensyncPublicHost = "opensync.denneen.net";
  convexBackendImage = "ghcr.io/get-convex/convex-backend@sha256:ed7ad78d762042f99dcaaf0d9e3d54394bc9c57f49db9a086022e6812c0fe2e5";
  convexDashboardImage = "ghcr.io/get-convex/convex-dashboard@sha256:5130ab98244b8e9900c05603cfce8cf5a806c769af716a512e662545a9332db2";
  opensyncRevision = "80005262fed8dac894fe618352a5e4b94c53813d";
  recalliumHost = "nyx.tail0e55.ts.net";
  recalliumApiPort = 18001;
  recalliumUiPort = 19001;
  recalliumMcpUrl = "http://${recalliumHost}:${toString recalliumApiPort}/mcp";
  recalliumUiBaseUrl = "http://${recalliumHost}:${toString recalliumUiPort}";
  recalliumImage = "docker.io/recalliumai/recallium@sha256:306b43857aa712bb0f8e63d1830776c621c0220131856d3be88a0429d22f907d";
  terraformMcpImage = "hashicorp/terraform-mcp-server:0.4.0";
  nyxMcpWarmPackages = [
    "supergateway"
    "@zereight/mcp-gitlab"
    "@strowk/mcp-k8s"
    "aws-mcp-readonly-lite"
    "terraform-mcp-server"
    "ddg-mcp-search"
    "@upstash/context7-mcp"
    "@playwright/mcp"
  ];
  nyxSharedMcpServers = {
    gitlab = {
      port = 18101;
      script = ''
        set -euo pipefail

        export GITLAB_API_URL="https://git.ap.org/api/v4"
        export GITLAB_READ_ONLY_MODE="true"

        token_file="$HOME/.config/opnix/gitlab_token"
        if [ -z "''${GITLAB_PERSONAL_ACCESS_TOKEN:-}" ] && [ -r "$token_file" ]; then
          token="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$token_file")"
          if [ -n "$token" ]; then
            export GITLAB_PERSONAL_ACCESS_TOKEN="$token"
          fi
        fi

        if [ -z "''${GITLAB_PERSONAL_ACCESS_TOKEN:-}" ] && command -v glab >/dev/null 2>&1; then
          token="$(glab auth token -h git.ap.org 2>/dev/null || true)"
          if [ -z "$token" ]; then
            token="$(glab auth token 2>/dev/null || true)"
          fi
          if [ -n "$token" ]; then
            export GITLAB_PERSONAL_ACCESS_TOKEN="$token"
          fi
        fi

        exec ${pkgs.nodejs_24}/bin/npx -y @zereight/mcp-gitlab
      '';
    };
    kubernetes = {
      port = 18102;
      script = ''
        set -euo pipefail

        kubeconfig="''${KUBECONFIG:-$HOME/.kube/config}"
        if [ -r "$kubeconfig" ]; then
          sanitized="''${TMPDIR:-/tmp}/nyx-mcp-kubeconfig.$$"
          ${pkgs.gnused}/bin/sed -E 's/^([[:space:]]*-[[:space:]]+)no([[:space:]]*)$/\1"no"\2/' "$kubeconfig" > "$sanitized"
          export KUBECONFIG="$sanitized"
        fi

        exec ${pkgs.nodejs_24}/bin/npx -y @strowk/mcp-k8s
      '';
    };
    aws = {
      port = 18103;
      script = ''
        set -euo pipefail
        export LOG_LEVEL="error"
        exec ${pkgs.nodejs_24}/bin/npx -y aws-mcp-readonly-lite
      '';
    };
    terraform = {
      port = 18104;
      script = ''
        set -euo pipefail

        if ${pkgs.podman}/bin/podman info >/dev/null 2>&1; then
          exec ${pkgs.podman}/bin/podman run -i --rm ${lib.escapeShellArg terraformMcpImage}
        fi

        exec ${pkgs.nodejs_24}/bin/npx -y terraform-mcp-server
      '';
    };
    duckduckgo = {
      port = 18105;
      stateful = false;
      script = ''
        set -euo pipefail
        exec ${pkgs.nodejs_24}/bin/npx -y ddg-mcp-search
      '';
    };
    context7 = {
      port = 18106;
      script = ''
        set -euo pipefail
        exec ${pkgs.nodejs_24}/bin/npx -y @upstash/context7-mcp
      '';
    };
    playwright = {
      port = 18107;
      memoryHigh = "1536M";
      memoryMax = "2G";
      script = ''
        set -euo pipefail
        exec ${pkgs.nodejs_24}/bin/npx -y @playwright/mcp --headless
      '';
    };
  };
  mkNyxMcpGatewayService =
    name: spec:
    let
      stdioScript = pkgs.writeShellScript "nyx-mcp-${name}-stdio" spec.script;
      stateful = spec.stateful or true;
      run = pkgs.writeShellScript "nyx-mcp-${name}-gateway" ''
        set -euo pipefail

        export PATH="${
          lib.makeBinPath [
            pkgs.bash
            pkgs.coreutils
            pkgs.glab
            pkgs.gnused
            pkgs.nodejs_24
            pkgs.podman
          ]
        }:/run/current-system/sw/bin:/etc/profiles/per-user/cdenneen/bin:$PATH"

        cmd=(
          ${pkgs.nodejs_24}/bin/npx -y supergateway
          --stdio ${lib.escapeShellArg "${stdioScript}"}
          --outputTransport streamableHttp
          --port ${toString spec.port}
          --streamableHttpPath /mcp
          --healthEndpoint /healthz
          --logLevel info
        )

        if [ "${if stateful then "1" else "0"}" = "1" ]; then
          cmd+=(--stateful --sessionTimeout 3600000)
        fi

        exec "''${cmd[@]}"
      '';
    in
    {
      description = "Shared MCP ${name} gateway";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "default.target" ];
      restartIfChanged = true;
      restartTriggers = [
        stdioScript
        run
      ];
      serviceConfig = {
        Type = "simple";
        ExecStart = run;
        Restart = "always";
        RestartSec = 10;
        MemoryAccounting = true;
        MemoryHigh = spec.memoryHigh or "768M";
        MemoryMax = spec.memoryMax or "1G";
      };
    };
  pepsApiHost = "peps-api.denneen.net";
  pepsWebHost = "peps.denneen.net";
  pepsApiPort = 8787;
  pepsRepoDir = "/home/cdenneen/src/workspace/personal/peps";
  pepsRuntimeDir = "/var/lib/peps";
  pepsEnvFile = "${pepsRuntimeDir}/backend.env";
  pepsEnvLocalFile = "${pepsRuntimeDir}/backend.env.local";
  pepsHealthImportTokenFile = "${pepsRuntimeDir}/health_import_token";
  pepsStateFilePath = "/home/cdenneen/.local/state/peps-api/web-state.json";
  pepsAdminEmails = "cdenneen@gmail.com,c.denneen@gmail.com";
  wellnessApiHost = "wellness-api.denneen.net";
  wellnessApiPort = 8797;
  wellnessRepoDir = "/home/cdenneen/src/workspace/personal/wellness";
  wellnessSupabaseUrl = "https://kefpmmjhtdxhhhcndrnx.supabase.co";
  wellnessOpenAiKeyFile = "/home/cdenneen/.config/sops-nix/secrets/openai_api_key";
  wellnessGeminiKeyFile = config.sops.secrets.gemini_api_key.path;
  wellnessSupabasePublishableKeyFile = config.sops.secrets.wellness_supabase_publishable_key.path;
  wellnessSupabaseSecretKeyFile = config.sops.secrets.wellness_supabase_secret_key.path;
  wellnessSupabaseDbUrlFile = config.sops.secrets.wellness_supabase_db_url.path;
  supabaseAccessTokenFile = config.sops.secrets.supabase_access_token.path;
in
{
  imports = [
    happier.nixosModules.happier-server
    jarvis.nixosModules.jarvis-node
  ];

  networking.hostName = "nyx";
  networking.extraHosts = ''
    100.80.58.4 nyx.tail0e55.ts.net
  '';
  ec2.efi = true;

  services.tailscale = {
    enable = true;
    openFirewall = true;
  };

  services.jarvis-node = {
    enable = true;
    mode = "oci";
    image = "registry.gitlab.com/cdenneen/my-jarvis/jarvis-node";
    imageTag = "0.1.0a3";
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

  services.happier-server = {
    enable = true;
    package = happier.packages.${pkgs.stdenv.hostPlatform.system}.happier-server;
    mode = "full";
    port = 3005;
    environmentFile = config.sops.secrets.happier-env.path;
    minio.rootCredentialsFile = config.sops.secrets.minio-credentials.path;
  };

  systemd.services.redis-happier =
    let
      recoverIncompatibleRdb = pkgs.writeShellScript "redis-happier-recover-incompatible-rdb" ''
        set -euo pipefail

        db_dir="/var/lib/redis-happier"
        dump_file="$db_dir/dump.rdb"

        if [ ! -s "$dump_file" ]; then
          exit 0
        fi

        if ${pkgs.redis}/bin/redis-check-rdb "$dump_file" >/dev/null 2>&1; then
          exit 0
        fi

        ts="$(${pkgs.coreutils}/bin/date -u +%Y%m%dT%H%M%SZ)"
        backup_file="$db_dir/dump.rdb.incompatible.$ts"

        echo "redis-happier: incompatible dump.rdb detected, moving to $backup_file" >&2
        ${pkgs.coreutils}/bin/mv "$dump_file" "$backup_file"
      '';
    in
    {
      serviceConfig.ExecStartPre = lib.mkBefore [ recoverIncompatibleRdb ];
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
  services.tailscale.permitCertUid = "caddy";

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

  systemd.services.home-manager-cdenneen = {
    environment = {
      OP_NIX_TOKEN_FILE = "/home/cdenneen/.config/opnix/token";
    };
  };
  home-manager.backupFileExtension = lib.mkForce "bak";
  users.users.cdenneen.extraGroups = lib.mkAfter [ "tailscale" ];

  services.dnsmasq = {
    enable = true;
    settings = {
      # Answer DNS queries from Tailscale clients for split DNS.
      interface = "tailscale0";
      bind-dynamic = true;
      domain-needed = true;
      bogus-priv = true;
      no-resolv = true;
      # Route AP internal split-DNS zones to the VPC resolver.
      server = [
        "/git.ap.org/10.224.0.2"
        "/associatedpress.com/10.224.0.2"
        "/apsharedservices.com/10.224.0.2"
      ];
    };
  };

  services.caddy = {
    enable = true;
    virtualHosts = {
      "nyx.tail0e55.ts.net".extraConfig = ''
        reverse_proxy 127.0.0.1:3005
      '';
      "${opensyncPublicHost}".extraConfig = ''
        reverse_proxy 127.0.0.1:5173
      '';
    };
  };

  services.cloudflared = {
    enable = true;
    tunnels = {
      "d1d49353-ddca-4c9c-bc8a-3bbb1885aa98" = {
        credentialsFile = "/var/lib/cloudflared/opencode.json";
        ingress = {
          "chat.denneen.net" = "http://127.0.0.1:4096";
          "${opensyncPublicHost}" = "http://127.0.0.1:5173";
        };
        default = "http_status:404";
        originRequest = {
          connectTimeout = "30s";
          noTLSVerify = false;
        };
      };
    };
  };

  virtualisation.oci-containers.backend = "podman";
  virtualisation.oci-containers.containers = {
    convex-backend = {
      image = convexBackendImage;
      ports = [
        "3210:3210"
        "3211:3211"
      ];
      volumes = [
        "/var/lib/convex/data:/convex/data"
      ];
      environment = {
        CONVEX_CLOUD_ORIGIN = convexCloudOrigin;
        CONVEX_SITE_ORIGIN = convexSiteOrigin;
        DISABLE_METRICS_ENDPOINT = "true";
        DOCUMENT_RETENTION_DELAY = "172800";
        RUST_LOG = "info";
      };
      autoStart = true;
    };

    convex-dashboard = {
      image = convexDashboardImage;
      ports = [ "6791:6791" ];
      environment = {
        NEXT_PUBLIC_DEPLOYMENT_URL = convexCloudOrigin;
      };
      dependsOn = [ "convex-backend" ];
      autoStart = true;
    };

    recallium = {
      image = recalliumImage;
      ports = [
        "${toString recalliumUiPort}:9000"
        "${toString recalliumApiPort}:8000"
        "5433:5432"
      ];
      volumes = [
        "/var/lib/recallium/data:/data"
        "/var/lib/recallium/wal:/wal"
        "/var/lib/recallium/docs:/documents"
        "/var/lib/recallium/secrets:/secrets"
      ];
      environment = {
        TZ = "America/Chicago";
        LOG_LEVEL = "INFO";
        RECALLIUM_EDITION = "community";
        ENVIRONMENT = "production";
        UI_BASE_URL = recalliumUiBaseUrl;
        HOST_API_PORT = toString recalliumApiPort;
        HOST_UI_PORT = toString recalliumUiPort;
        HOST_POSTGRES_PORT = "5433";
        DB_HOST = "localhost";
        DB_PORT = "5432";
        DB_USER = "recallium";
        DB_PASSWORD = "recallium_password";
        DB_NAME = "recallium_memories";
        LOAD_SAMPLE_DATA = "false";
      };
      extraOptions = [
        "--add-host=host.docker.internal:host-gateway"
      ];
      autoStart = true;
    };
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/cloudflared 0700 root root -"
    "d /run/caddy 0750 caddy caddy -"
    "d /var/lib/convex 0700 root root -"
    "d /var/lib/convex/data 0700 root root -"
    "d /var/lib/opensync 0750 cdenneen users -"
    "d /var/lib/opensync/repo 0750 cdenneen users -"
    "d /var/lib/opensync/state 0750 cdenneen users -"
    "d /var/lib/recallium 0750 cdenneen users -"
    "d /var/lib/recallium/data 0750 cdenneen users -"
    "d /var/lib/recallium/wal 0750 cdenneen users -"
    "d /var/lib/recallium/docs 0750 cdenneen users -"
    "d /var/lib/recallium/secrets 0750 cdenneen users -"
  ];

  systemd.services.convex-generate-admin-key = {
    description = "Generate self-hosted Convex admin key";
    wantedBy = [ "multi-user.target" ];
    after = [ "podman-convex-backend.service" ];
    requires = [ "podman-convex-backend.service" ];
    path = [
      pkgs.coreutils
      pkgs.curl
      pkgs.gnugrep
      pkgs.podman
    ];
    script = ''
      set -euo pipefail

      out="/var/lib/convex/admin.key"
      out_opensync="/var/lib/opensync/state/convex-admin.key"
      if [ -s "$out" ] && [ -s "$out_opensync" ]; then
        exit 0
      fi

      for _ in $(seq 1 60); do
        if ${pkgs.curl}/bin/curl -fsS http://127.0.0.1:3210/version >/dev/null; then
          break
        fi
        sleep 2
      done

      key="$(${pkgs.podman}/bin/podman exec convex-backend ./generate_admin_key.sh | ${pkgs.coreutils}/bin/tail -n 1 | ${pkgs.coreutils}/bin/tr -d '\n\r')"
      if [ -z "$key" ]; then
        echo "convex-generate-admin-key: failed to generate key" >&2
        exit 1
      fi

      ${pkgs.coreutils}/bin/install -m 600 /dev/null "$out"
      printf '%s\n' "$key" > "$out"

      ${pkgs.coreutils}/bin/install -m 640 -o cdenneen -g users /dev/null "$out_opensync"
      printf '%s\n' "$key" > "$out_opensync"
    '';
    serviceConfig = {
      Type = "oneshot";
    };
  };

  systemd.services.opensync-web = {
    description = "OpenSync web on self-hosted Convex";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "podman-convex-backend.service"
      "convex-generate-admin-key.service"
    ];
    wants = [ "network-online.target" ];
    requires = [
      "podman-convex-backend.service"
      "convex-generate-admin-key.service"
    ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.curl
      pkgs.git
      pkgs.gnugrep
      pkgs.nodejs_24
    ];
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = "/var/lib/opensync";
      Restart = "always";
      RestartSec = "15s";
      TimeoutStartSec = "15min";
      Environment = [
        "HOME=/home/cdenneen"
      ];
    };
    script = ''
            set -euo pipefail

            repo_dir="/var/lib/opensync/repo"
            branch="main"
            revision="${opensyncRevision}"
            remote="https://github.com/waynesutton/opensync.git"
            admin_key_file="/var/lib/opensync/state/convex-admin.key"
            workos_client_id_file="${config.sops.secrets.opensync_workos_client_id.path}"
            workos_api_key_file="${config.sops.secrets.opensync_workos_api_key.path}"
            workos_cookie_password_file="${config.sops.secrets.opensync_workos_cookie_password.path}"

            if [ ! -s "$admin_key_file" ]; then
              echo "opensync-web: missing Convex admin key at $admin_key_file" >&2
              exit 1
            fi

            if [ ! -d "$repo_dir/.git" ]; then
              rm -rf "$repo_dir"
              git clone --branch "$branch" "$remote" "$repo_dir"
            fi

            cd "$repo_dir"
            git fetch --prune origin "$branch"
            if ! git cat-file -e "$revision^{commit}" 2>/dev/null; then
              git fetch --depth 1 origin "$revision"
            fi
            git checkout --detach "$revision"
            git reset --hard "$revision"

            npm ci --no-audit --no-fund

            workos_client_id="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$workos_client_id_file")"
            workos_api_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$workos_api_key_file")"
            workos_cookie_password="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$workos_cookie_password_file")"
            convex_admin_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$admin_key_file")"

            if [ -z "$workos_client_id" ] || [ -z "$workos_api_key" ] || [ -z "$workos_cookie_password" ] || [ -z "$convex_admin_key" ]; then
              echo "opensync-web: one or more required secrets are empty" >&2
              exit 1
            fi

            export CONVEX_SELF_HOSTED_URL="http://127.0.0.1:3210"
            export CONVEX_SELF_HOSTED_ADMIN_KEY="$convex_admin_key"

            npx convex env set WORKOS_API_KEY "$workos_api_key"
            npx convex env set WORKOS_CLIENT_ID "$workos_client_id"
            npx convex deploy

            cat > "$repo_dir/.env.local" <<EOF
      VITE_CONVEX_URL=${convexCloudOrigin}
      VITE_WORKOS_CLIENT_ID=$workos_client_id
      VITE_REDIRECT_URI=http://localhost:5173/callback
      WORKOS_COOKIE_PASSWORD=$workos_cookie_password
      EOF

            exec npm run dev -- --host 127.0.0.1 --port 5173
    '';
  };

  systemd.services.cloudflared-credentials-opencode =
    let
      script = pkgs.writeShellScript "cloudflared-credentials-opencode" ''
        set -euo pipefail

        token_file="${config.sops.secrets.nyx_cloudflare_tunnel_token.path}"
        cred_dir="/var/lib/cloudflared"
        cred_file="$cred_dir/opencode.json"

        if [ ! -r "$token_file" ]; then
          echo "cloudflared-credentials-opencode: token file not readable" >&2
          exit 1
        fi

        token_json="$(${pkgs.coreutils}/bin/cat "$token_file" | ${pkgs.coreutils}/bin/tr -d '\n\r' | ${pkgs.coreutils}/bin/base64 -d)"
        account_tag="$(${pkgs.jq}/bin/jq -r '.a // empty' <<<"$token_json")"
        tunnel_id="$(${pkgs.jq}/bin/jq -r '.t // empty' <<<"$token_json")"
        tunnel_secret="$(${pkgs.jq}/bin/jq -r '.s // empty' <<<"$token_json")"

        if [ -z "$account_tag" ] || [ -z "$tunnel_id" ] || [ -z "$tunnel_secret" ]; then
          echo "cloudflared-credentials-opencode: invalid token contents" >&2
          exit 1
        fi

        ${pkgs.coreutils}/bin/mkdir -p "$cred_dir"
        ${pkgs.jq}/bin/jq -n \
          --arg account_tag "$account_tag" \
          --arg tunnel_id "$tunnel_id" \
          --arg tunnel_secret "$tunnel_secret" \
          --arg tunnel_name "opencode" \
          '{
            AccountTag: $account_tag,
            TunnelID: $tunnel_id,
            TunnelName: $tunnel_name,
            TunnelSecret: $tunnel_secret
          }' >"$cred_file"
        ${pkgs.coreutils}/bin/chmod 0400 "$cred_file"
      '';
    in
    {
      description = "Generate Cloudflared credentials from token";
      before = [
        "cloudflared-tunnel-d1d49353-ddca-4c9c-bc8a-3bbb1885aa98.service"
      ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = script;
        RemainAfterExit = true;
      };
    };

  systemd.services."cloudflared-tunnel-d1d49353-ddca-4c9c-bc8a-3bbb1885aa98" = {
    requires = [ "cloudflared-credentials-opencode.service" ];
    after = [ "cloudflared-credentials-opencode.service" ];
  };

  sops.secrets.happier-env.owner = "root";
  sops.secrets.minio-credentials.owner = "root";
  sops.secrets.wellness_supabase_publishable_key = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.wellness_supabase_secret_key = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.wellness_supabase_db_url = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.supabase_access_token = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.gemini_api_key = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };

  # Cloudflare Tunnel for Telegram webhook.
  sops.secrets.nyx_cloudflare_tunnel_token = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.opencode_server_password = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.opensync_workos_client_id = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.opensync_workos_api_key = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.opensync_workos_cookie_password = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };

  systemd.user.services.nyx-mcp-warm-cache =
    let
      run = pkgs.writeShellScript "nyx-mcp-warm-cache" ''
        set -euo pipefail

        export PATH="${pkgs.bash}/bin:${pkgs.nodejs_24}/bin:$PATH"

        state_dir="$HOME/.local/state/nyx-mcp"
        log_file="$state_dir/warm-cache.log"
        ${pkgs.coreutils}/bin/mkdir -p "$state_dir"

        echo "$(${pkgs.coreutils}/bin/date -Is) start" >> "$log_file"

        warm_pkg() {
          local pkg="$1"
          if ${pkgs.nodejs_24}/bin/npm exec --yes --package "$pkg" -- ${pkgs.nodejs_24}/bin/node -e "process.exit(0)" >/dev/null 2>&1; then
            echo "$(${pkgs.coreutils}/bin/date -Is) ok package=$pkg" >> "$log_file"
          else
            echo "$(${pkgs.coreutils}/bin/date -Is) warn package=$pkg failed" >> "$log_file"
          fi
        }

        ${lib.concatMapStringsSep "\n" (pkg: "warm_pkg ${lib.escapeShellArg pkg}") nyxMcpWarmPackages}

        if ${pkgs.podman}/bin/podman info >/dev/null 2>&1; then
          if ${pkgs.podman}/bin/podman image exists ${lib.escapeShellArg terraformMcpImage}; then
            echo "$(${pkgs.coreutils}/bin/date -Is) ok image=${terraformMcpImage} exists" >> "$log_file"
          elif ${pkgs.podman}/bin/podman pull ${lib.escapeShellArg terraformMcpImage} >/dev/null 2>&1; then
            echo "$(${pkgs.coreutils}/bin/date -Is) ok image=${terraformMcpImage} pulled" >> "$log_file"
          else
            echo "$(${pkgs.coreutils}/bin/date -Is) warn image=${terraformMcpImage} pull failed" >> "$log_file"
          fi
        else
          echo "$(${pkgs.coreutils}/bin/date -Is) warn podman info unavailable" >> "$log_file"
        fi

        echo "$(${pkgs.coreutils}/bin/date -Is) done" >> "$log_file"
      '';
    in
    {
      description = "Warm nyx MCP package caches";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        Type = "oneshot";
      };
      script = "exec ${run}";
    };

  systemd.user.services.nyx-mcp-gitlab = mkNyxMcpGatewayService "gitlab" nyxSharedMcpServers.gitlab;
  systemd.user.services.nyx-mcp-kubernetes = mkNyxMcpGatewayService "kubernetes" nyxSharedMcpServers.kubernetes;
  systemd.user.services.nyx-mcp-aws = mkNyxMcpGatewayService "aws" nyxSharedMcpServers.aws;
  systemd.user.services.nyx-mcp-terraform = mkNyxMcpGatewayService "terraform" nyxSharedMcpServers.terraform;
  systemd.user.services.nyx-mcp-duckduckgo = mkNyxMcpGatewayService "duckduckgo" nyxSharedMcpServers.duckduckgo;
  systemd.user.services.nyx-mcp-context7 = mkNyxMcpGatewayService "context7" nyxSharedMcpServers.context7;
  systemd.user.services.nyx-mcp-playwright = mkNyxMcpGatewayService "playwright" nyxSharedMcpServers.playwright;

  systemd.user.timers.nyx-mcp-warm-cache = {
    description = "Run nyx MCP cache warmup after boot";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "3min";
      OnUnitActiveSec = "12h";
      RandomizedDelaySec = "2min";
      Persistent = true;
      Unit = "nyx-mcp-warm-cache.service";
    };
  };

  systemd.user.services.opencode-serve =
    let
      run = pkgs.writeShellScript "opencode-web" ''
        set -euo pipefail
        gitlab_file="${config.users.users.cdenneen.home}/.config/opnix/gitlab_token"
        password_file="${config.sops.secrets.opencode_server_password.path}"
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

        if [ -r "$password_file" ]; then
          opencode_password="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$password_file")"
          if [ -z "$opencode_password" ]; then
            echo "opencode-web: password file empty" >&2
          else
            export OPENCODE_SERVER_PASSWORD="$opencode_password"
          fi
        else
          echo "opencode-web: password file not readable" >&2
        fi

        exec /etc/profiles/per-user/cdenneen/bin/opencode serve --hostname 127.0.0.1 --port 4097
      '';
    in
    {
      description = "OpenCode web (chat)";
      after = [
        "network-online.target"
        "nyx-mcp-playwright.service"
      ];
      wants = [
        "network-online.target"
        "nyx-mcp-playwright.service"
      ];
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
        stale_age_ms=$((90 * 60 * 1000))
        max_compactions=12
        overflow_limit=48
        delete_age_ms=$((7 * 24 * 60 * 60 * 1000))
        delete_limit=16
        keep_per_directory=2
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
        sessions_json="$(${pkgs.curl}/bin/curl --connect-timeout 5 --max-time 15 -sS -u "$auth" http://127.0.0.1:4097/session || printf '[]')"
        total_sessions="$(${pkgs.jq}/bin/jq -r 'length' <<<"$sessions_json" 2>/dev/null || printf '0')"

        if [ "$total_sessions" = "0" ]; then
          echo "$timestamp no sessions to compact total_sessions=0" >> "$log_file"
          exit 0
        fi

        now_ms="$(${pkgs.coreutils}/bin/date +%s%3N)"
        sessions="$(${pkgs.jq}/bin/jq -r \
          --argjson now "$now_ms" \
          --argjson stale "$stale_age_ms" \
          --argjson overflow "$overflow_limit" \
          --argjson max "$max_compactions" '
            (sort_by(.time.updated // 0)) as $sorted
            | [ $sorted[] | select(($now - (.time.updated // 0)) >= $stale) | .id ] as $stale_ids
            | if ($stale_ids | length) > 0 then
                $stale_ids[:$max]
              elif ($sorted | length) > $overflow then
                [ $sorted[] | .id ][:$max]
              else
                []
              end
            | .[]
          ' <<<"$sessions_json" 2>/dev/null || true)"

        if [ -z "$sessions" ]; then
          echo "$timestamp no compaction candidates total_sessions=$total_sessions stale_age_ms=$stale_age_ms overflow_limit=$overflow_limit" >> "$log_file"
        else
          payload='{"command":"compact","arguments":""}'
          compacted=0
          for sid in $sessions; do
            if ! ${pkgs.curl}/bin/curl --connect-timeout 5 --max-time 15 -sS -u "$auth" \
              -H "content-type: application/json" \
              -X POST "http://127.0.0.1:4097/session/$sid/command" \
              -d "$payload" >/dev/null; then
              echo "$timestamp compact failed session=$sid" >> "$log_file"
            else
              compacted=$((compacted + 1))
            fi
          done

          echo "$timestamp compacted sessions=$compacted total_sessions=$total_sessions stale_age_ms=$stale_age_ms overflow_limit=$overflow_limit" >> "$log_file"
        fi

        delete_candidates="$(${pkgs.jq}/bin/jq -r \
          --argjson now "$now_ms" \
          --argjson stale "$delete_age_ms" \
          --argjson keep_per_dir "$keep_per_directory" \
          --argjson max "$delete_limit" '
            [
              (
                sort_by([(.directory // "__global__"), (.time.updated // 0)])
                | group_by(.directory // "__global__")[]
                | sort_by(.time.updated // 0)
                | reverse
                | .[$keep_per_dir:][]
                | select(($now - (.time.updated // 0)) > $stale)
              )
            ]
            | sort_by(.time.updated // 0)
            | .[:$max]
            | .[]
            | .id
          ' <<<"$sessions_json" 2>/dev/null || true)"

        if [ -z "$delete_candidates" ]; then
          echo "$timestamp no stale deletions total_sessions=$total_sessions delete_age_ms=$delete_age_ms keep_per_directory=$keep_per_directory" >> "$log_file"
          exit 0
        fi

        deleted=0
        for sid in $delete_candidates; do
          if ! ${pkgs.curl}/bin/curl --connect-timeout 5 --max-time 15 -sS -u "$auth" \
            -X DELETE "http://127.0.0.1:4097/session/$sid" >/dev/null; then
            echo "$timestamp delete failed session=$sid" >> "$log_file"
          else
            deleted=$((deleted + 1))
          fi
        done

        echo "$timestamp deleted stale_sessions=$deleted total_sessions=$total_sessions delete_age_ms=$delete_age_ms keep_per_directory=$keep_per_directory" >> "$log_file"
      '';
    in
    {
      description = "Compact OpenCode sessions via HTTP API";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = run;
        TimeoutStartSec = "3min";
      };
    };

  systemd.user.timers.opencode-serve-compact = {
    description = "Periodic OpenCode session compaction";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "4m";
      OnUnitActiveSec = "30min";
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

  environment.variables = {
    VITE_CONVEX_URL = convexCloudOrigin;
    RECALLIUM_MCP_URL = recalliumMcpUrl;
  };

  home-manager.users.cdenneen.imports = [ ./nyx-home.nix ];

  home-manager.users.cdenneen.programs.opencode.package = lib.mkForce (
    pkgs.callPackage ../../pkgs/opencode-cli.nix { }
  );

  # Starship palette is configured in nyx-home.nix.
}
