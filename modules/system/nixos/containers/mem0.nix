{ config, lib, ... }:
let
  cfg = config.containerPresets.mem0;
in
{
  options.containerPresets.mem0 = {
    enable = lib.mkEnableOption "Mem0 self-hosted memory service (REST API + OpenMemory MCP + Redis)";

    port = lib.mkOption {
      type = lib.types.int;
      default = 8888;
      description = "Loopback port for the Mem0 REST API (used by Hermes via MEM0_BASE_URL).";
    };

    mcpPort = lib.mkOption {
      type = lib.types.int;
      default = 8889;
      description = "Loopback port for the OpenMemory MCP server (used by Claude Code, OpenCode, Cursor, etc.).";
    };

    dataDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/mem0";
      description = "Host path for the Redis persistence volume.";
    };

    userId = lib.mkOption {
      type = lib.types.str;
      description = ''
        Default user_id namespace for this host's memory pool.
        All agents on this host share this namespace; per-agent isolation
        is done via agent_id at the API/MCP call level, not here.
        Convention: use the hostname (e.g. "nyx", "ghost").
      '';
      example = "nyx";
    };

    databaseUrlFile = lib.mkOption {
      type = lib.types.path;
      description = ''
        Path to a file containing the DATABASE_URL for the shared
        PostgreSQL+pgvector backend (e.g. Supabase).  Must be readable
        by root.  Use a sops secret.
        File format: one line, DATABASE_URL=postgresql://user:pass@host/db
      '';
      example = "/run/secrets/mem0_database_url";
    };

    openaiApiKeyFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = ''
        Path to a file containing OPENAI_API_KEY used for embeddings.
        If null the containers must be configured with an alternative
        embedding provider.
        File format: one line, OPENAI_API_KEY=sk-...
      '';
    };

    openFirewall = lib.mkEnableOption "expose Mem0 ports on the firewall (cross-host access via tailnet)";
  };

  config = lib.mkIf cfg.enable {
    containerPresets.podman.enable = lib.mkDefault true;

    systemd.tmpfiles.rules = [
      "d ${cfg.dataDir} 0700 root root -"
      "d ${cfg.dataDir}/redis 0700 root root -"
    ];

    virtualisation.arion.projects.mem0.settings = {
      # Shared Redis: both the REST server and MCP server use the same cache.
      services.mem0-redis.service = {
        image = "redis:7-alpine";
        restart = "unless-stopped";
        volumes = [ "${cfg.dataDir}/redis:/data" ];
        command = [ "redis-server" "--appendonly" "yes" ];
      };

      # REST API server — Hermes connects here via MEM0_BASE_URL.
      # ⚠ Verify image before deploying:
      #   docker.io/mem0ai/mem0  or  ghcr.io/mem0ai/mem0
      # If no pre-built server image is published, build from
      # github.com/mem0ai/mem0 server/Dockerfile and pin to a digest.
      services.mem0-server.service = {
        image = "mem0ai/mem0:latest";
        ports = [ "127.0.0.1:${toString cfg.port}:8000" ];
        environment = {
          REDIS_URL = "redis://mem0-redis:6379";
        };
        env_file =
          lib.optional (cfg.openaiApiKeyFile != null) cfg.openaiApiKeyFile
          ++ [ cfg.databaseUrlFile ];
        depends_on = [ "mem0-redis" ];
        restart = "unless-stopped";
      };

      # OpenMemory MCP server — Claude Code, OpenCode, Cursor, Codex, and any
      # other MCP-capable agent connects here.  Backed by the same Supabase
      # pgvector DB as the REST server, so memories are shared across all agents.
      # ⚠ Verify image before deploying:
      #   docker.io/mem0ai/openmemory-mcp  or  ghcr.io/mem0ai/openmemory-mcp
      services.mem0-mcp.service = {
        image = "mem0ai/openmemory-mcp:latest";
        ports = [ "127.0.0.1:${toString cfg.mcpPort}:8765" ];
        environment = {
          REDIS_URL = "redis://mem0-redis:6379";
          # Default user_id for this host's memory pool.  Agents that send
          # their own user_id/agent_id in the MCP request override this.
          MEM0_DEFAULT_USER_ID = cfg.userId;
        };
        env_file =
          lib.optional (cfg.openaiApiKeyFile != null) cfg.openaiApiKeyFile
          ++ [ cfg.databaseUrlFile ];
        depends_on = [
          "mem0-redis"
          "mem0-server"
        ];
        restart = "unless-stopped";
      };
    };

    networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall [
      cfg.port
      cfg.mcpPort
    ];
  };
}
