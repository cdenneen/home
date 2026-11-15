# Paperclip — AI agent orchestration server
# Runs as a systemd service under the cdenneen user account.
# Exposes the web UI + API on port 3100, reachable over Tailscale.
#
# Requires system PostgreSQL with a 'paperclip' database.
# embedded-postgres npm binaries don't work on NixOS (glibc path mismatch);
# use services.postgresql + DATABASE_URL instead.
#
# Required sops secret: paperclip_api_key (owner: cdenneen, mode: 0400)
#
# Usage in a host file:
#   profiles.paperclip.enable = true;
#   services.postgresql.enable = true;
#   services.postgresql.ensureDatabases = [ "paperclip" ];

{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.profiles.paperclip;
  paperclip = pkgs.callPackage ../../../../pkgs/paperclip.nix { };
  # Managed install shim — paperclipai install writes here.
  # The shim delegates to ~/.paperclip/cli/installs/npm/<version>/node_modules/paperclipai/dist/index.js
  shimPath = "/home/cdenneen/.local/bin/paperclipai";
in
{
  options.profiles.paperclip = {
    enable = lib.mkEnableOption "Paperclip AI agent orchestration server";

    port = lib.mkOption {
      type = lib.types.port;
      default = 3100;
      description = "HTTP port Paperclip listens on.";
    };

    dataDir = lib.mkOption {
      type = lib.types.str;
      default = "/home/cdenneen/.paperclip";
      description = "Paperclip data root (logs, uploads, secrets).";
    };

    publicUrl = lib.mkOption {
      type = lib.types.str;
      default = "http://100.107.59.106:3100";
      description = "Public URL agents and browsers use to reach Paperclip.";
    };

    apiKeySecretPath = lib.mkOption {
      type = lib.types.str;
      default = config.sops.secrets.paperclip_api_key.path;
      description = "Path to the sops-decrypted API key secret file.";
    };

    memoryHigh = lib.mkOption {
      type = lib.types.str;
      default = "768M";
      description = "systemd MemoryHigh for paperclipai (soft memory pressure threshold).";
    };

    memoryMax = lib.mkOption {
      type = lib.types.str;
      default = "1G";
      description = "systemd MemoryMax for paperclipai (hard memory limit).";
    };
  };

  config = lib.mkIf cfg.enable {
    # sops secret — decrypted at boot, readable only by cdenneen
    sops.secrets.paperclip_api_key = {
      owner = "cdenneen";
      mode = "0400";
    };

    # Open the Paperclip port — Tailscale traffic passes through host firewall.
    networking.firewall.allowedTCPPorts = [ cfg.port ];

    # paperclipai CLI available system-wide for manual ops / onboarding
    environment.systemPackages = [ paperclip ];

    # One-shot activation: run `paperclipai install -y` to create the managed
    # install under ~/.paperclip/cli/ if it doesn't exist yet. This writes the
    # shim at ~/.local/bin/paperclipai which the service ExecStart uses.
    # ExecCondition: exit 0 → run; exit 1–254 → skip unit (shim already present).
    systemd.user.services.paperclip-install = {
      description = "Paperclip managed CLI install";
      wantedBy = [ "default.target" ];
      before = [ "paperclipai.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecCondition = "${pkgs.bash}/bin/bash -c '! test -x %h/.local/bin/paperclipai'";
        ExecStart = "${paperclip}/bin/paperclipai install -y";
        Environment = "HOME=/home/cdenneen";
      };
    };

    # Paperclip server — started via the managed install shim.
    # paperclipai onboard must have been run once to initialise the instance.
    systemd.user.services.paperclipai = {
      description = "Paperclip AI (default)";
      wantedBy = [ "default.target" ];
      after = [
        "network-online.target"
        "paperclip-install.service"
      ];
      requires = [ "paperclip-install.service" ];

      serviceConfig = {
        Type = "notify";
        NotifyAccess = "all";
        Restart = "on-failure";
        RestartSec = "10s";
        TimeoutStartSec = "120";
        ExecStart = ''${shimPath} run --instance "default"'';
        Environment = [
          "HOME=/home/cdenneen"
          "PAPERCLIP_SERVICE_MANAGED=1"
          "PAPERCLIP_INSTANCE_ID=default"
          "PAPERCLIP_HOME=${cfg.dataDir}"
        ];
        # Resource limits — observed peak on Talon ~500.5 MiB
        MemoryMax = cfg.memoryMax;
        MemoryHigh = cfg.memoryHigh;
      };

      # DATABASE_URL overrides embedded-postgres — system Postgres on 127.0.0.1
      # (localhost resolves to ::1 on Talon, where pg_hba requires md5; 127.0.0.1 is trust)
      environment = {
        DATABASE_URL = "postgres://cdenneen@127.0.0.1:5432/paperclip";
        PORT = toString cfg.port;
        PAPERCLIP_PUBLIC_URL = cfg.publicUrl;
        AUTH_DISABLE_SIGNUP = "true";
        PAPERCLIP_TELEMETRY_DISABLED = "1";
      };
    };
  };
}
