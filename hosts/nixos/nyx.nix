{
  lib,
  pkgs,
  config,
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
    configuration = {
      agent = {
        metrics_collection_interval = 60;
        logfile = "/var/log/amazon-cloudwatch-agent/amazon-cloudwatch-agent.log";
      };
      metrics = {
        namespace = "CWAgent";
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
            measurement = [ "mem_used_percent" ];
            metrics_collection_interval = 60;
          };
          disk = {
            measurement = [ "used_percent" ];
            resources = [ "/" ];
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

      serviceConfig = {
        Type = "simple";
        Environment = [
          "PATH=/run/current-system/sw/bin:/etc/profiles/per-user/cdenneen/bin"
        ];
        ExecStart = run;
        Restart = "always";
        RestartSec = 2;
      };
    };

  systemd.user.services.opencode-web-warm =
    let
      warm = pkgs.writeShellScript "opencode-web-warm" ''
        set -euo pipefail
        export HOME="/home/cdenneen"

        ${pkgs.python3}/bin/python - <<'PY'
        import json
        import base64
        import os
        import sqlite3
        import time
        import urllib.error
        import urllib.request

        db_path = os.path.expanduser("~/.local/share/opencode-telegram-bridge/state.sqlite")
        base_url = "http://127.0.0.1:4096"
        user = "opencode"
        pw_file = "${config.sops.secrets.opencode_server_password.path}"

        try:
            with open(pw_file, "r", encoding="utf-8") as fh:
                password = fh.read().strip()
        except OSError:
            print("opencode-web-warm: password file not readable, skipping", flush=True)
            raise SystemExit(0)

        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")

        if not os.path.exists(db_path):
            print("opencode-web-warm: bridge DB missing, skipping", flush=True)
            raise SystemExit(0)

        def http(method, path, body=None, timeout=2):
            url = f"{base_url}{path}"
            data = None
            headers = {"Authorization": f"Basic {token}"}
            if body is not None:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()

        healthy = False
        for _ in range(30):
            try:
                status, _ = http("GET", "/global/health")
                if status == 200:
                    healthy = True
                    break
            except Exception:
                time.sleep(1)

        if not healthy:
            print("opencode-web-warm: server not healthy, skipping", flush=True)
            raise SystemExit(0)

        existing_titles = set()
        try:
            status, body = http("GET", "/session")
            if status == 200:
                data = json.loads(body.decode("utf-8"))
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    items = data.get("items") or data.get("data") or []
                else:
                    items = []

                for item in items:
                    if isinstance(item, dict):
                        title = item.get("title")
                        if title:
                            existing_titles.add(str(title))
        except Exception:
            existing_titles = set()

        conn = sqlite3.connect(db_path)
        try:
            rows = list(
                conn.execute(
                    "SELECT chat_id, thread_id, workspace, topic_title FROM topics ORDER BY updated_at DESC"
                )
            )
        finally:
            conn.close()

        warmed = 0
        for chat_id, thread_id, workspace, topic_title in rows:
            title = f"tg:{chat_id}/{thread_id}"
            if workspace:
                title = f"{title} {os.path.basename(str(workspace))}"
            if topic_title:
                title = f"{title} {topic_title}"
            title = title.strip()
            if not title:
                continue
            if title in existing_titles:
                continue
            try:
                http("POST", "/session", {"title": title}, timeout=10)
                warmed += 1
            except urllib.error.HTTPError as exc:
                print(f"opencode-web-warm: create failed {exc}", flush=True)
            except Exception as exc:
                print(f"opencode-web-warm: create failed {exc}", flush=True)

        print(f"opencode-web-warm: warmed {warmed} sessions", flush=True)
        PY
      '';
    in
    {
      description = "OpenCode web warm from Telegram bridge DB";
      after = [ "opencode-serve.service" ];
      requires = [ "opencode-serve.service" ];
      wantedBy = [ "default.target" ];

      serviceConfig = {
        Type = "oneshot";
        ExecStart = warm;
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
        exec ${pkgs.systemd}/bin/systemctl --user restart opencode-serve.service opencode-web-warm.service
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
  };
}
