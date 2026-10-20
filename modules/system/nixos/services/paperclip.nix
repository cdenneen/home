# Paperclip — AI agent orchestration server
# Runs as a systemd service under the cdenneen user account.
# Exposes the web UI + API on port 3100, reachable over Tailscale.
#
# Required sops secret:  paperclip_api_key  (owner: cdenneen, mode: 0400)
# Set via PAPERCLIP_API_KEY env var — used as the initial admin API key and
# to configure Hermes gateway adapter connections from other hosts.
#
# Data directory: ~/.paperclip/instances/default/
# Embedded Postgres: ~/.paperclip/instances/default/db/ (port 54329 localhost-only)
#
# Usage in a host file:
#   profiles.paperclip.enable = true;

{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.profiles.paperclip;
  paperclip = pkgs.callPackage ../../../../pkgs/paperclip.nix { };
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
      description = "Paperclip data root (embedded Postgres, logs, uploads).";
    };

    publicUrl = lib.mkOption {
      type = lib.types.str;
      default = "http://100.107.59.106:3100";
      description = "Public URL agents and browsers use to reach Paperclip. Defaults to Tailscale IP.";
    };

    apiKeySecretPath = lib.mkOption {
      type = lib.types.str;
      default = config.sops.secrets.paperclip_api_key.path;
      description = "Path to the sops-decrypted API key secret file.";
    };
  };

  config = lib.mkIf cfg.enable {
    # sops secret — decrypted at boot, readable only by cdenneen
    sops.secrets.paperclip_api_key = {
      owner = "cdenneen";
      mode = "0400";
    };

    # Open the Paperclip port in the firewall — Tailscale traffic passes
    # through the host firewall, so this allows Tailscale peers to connect.
    networking.firewall.allowedTCPPorts = [ cfg.port ];

    environment.systemPackages = [ paperclip ];

    # Run as a systemd user service under cdenneen
    systemd.user.services.paperclip = {
      description = "Paperclip AI agent orchestration server";
      wantedBy = [ "default.target" ];
      after = [ "network-online.target" ];

      serviceConfig = {
        Type = "simple";
        Restart = "on-failure";
        RestartSec = "10s";

        # Load the API key from the sops secret file into the environment
        EnvironmentFile = cfg.apiKeySecretPath;

        ExecStart = "${paperclip}/bin/paperclipai start";

        # Resource limits appropriate for a 1GB host
        MemoryMax = "512M";
        MemoryHigh = "400M";
      };

      environment = {
        NODE_ENV = "production";
        PORT = toString cfg.port;
        PAPERCLIP_PUBLIC_URL = cfg.publicUrl;
        PAPERCLIP_DATA_DIR = cfg.dataDir;
        # Bind on all interfaces so Tailscale traffic reaches it
        HOST = "0.0.0.0";
        # Disable public sign-up — private internal instance
        AUTH_DISABLE_SIGNUP = "true";
        # Embedded Postgres — no external DB needed
        DATABASE_MODE = "embedded-postgres";
        PAPERCLIP_EMBEDDED_POSTGRES_DATA_DIR = "${cfg.dataDir}/instances/default/db";
      };
    };
  };
}
