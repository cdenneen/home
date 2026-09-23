{
  config,
  pkgs,
  lib,
  ...
}:
let
  litellmPort = 4000;
  # Real HTTPS termination (not the --tcp passthrough below) for clients
  # that hard-require TLS and can't be pointed at a plain-HTTP base URL -
  # e.g. Claude Desktop's Bring-your-own-Bedrock "Gateway" connection type
  # (2026-09-11): it attempts a TLS handshake against whatever URL/port is
  # configured regardless of scheme, so litellmPort's plain-HTTP --tcp
  # passthrough 400s with ERR_SSL_PROTOCOL_ERROR for that specific client.
  # Tailscale itself terminates TLS here (real, publicly-trusted Let's
  # Encrypt cert via Tailscale's ACME integration - not self-signed) and
  # reverse-proxies to the same plain-HTTP litellmPort backend. Every
  # existing consumer keeps using plain HTTP on litellmPort unchanged.
  litellmHttpsPort = 8443;
  litellmEnvFile = "/run/eros-litellm/env";
  litellmConfigFile = "/run/eros-litellm/config.yaml";
  bedrockToolGuardFile = ../../pkgs/eros-litellm-hooks/bedrock_tool_guard.py;
  omniroutePort = 20128;
  qdrantPort = 6333;
  contextPorts = {
    shared = 18120;
    personal = 18121;
    work = 18122;
  };
  contextBroker = pkgs.python313.withPackages (ps: [
    ps.mcp
    ps.psycopg
  ]);
  contextBrokerSource = ../../pkgs/eros-context-broker;
  contextModelRoutes = [
    "auto"
    "axis-claude-sonnet-4-6"
    "claude-haiku-4-5"
    "claude-sonnet-4-6"
    "claude-sonnet-5"
    "claude-opus-5"
    "claude-opus-5-5"
    "coding"
    "coding-core"
    "coding-gemini"
    "coding-haiku"
    "coding-openai"
    "coding-strong"
    "deepseek-v3.2"
    "embedding-core"
    "g2-omniroute-openai-gpt4o-mini"
    "g5-omniroute-bedrock-haiku"
    "general-core"
    "glm-5"
    "gpt-5.4"
    "gpt-5.6-terra"
    "kimi-k2.5"
    "local-embed"
    "mini"
    "multimodal-long"
    "nova-2-lite"
    "openai/*"
    "personal"
    "quality"
    "qwen3-coder-next"
    "qwen3-next-80b-a3b"
    "reasoning-candidate"
    "research-candidate"
    "review-strong"
    "tier0-local"
    "tier1-coding"
    "tier1-general"
    "tier2-coding"
    "tier2-general"
    "tier2-research"
    "tier3-quality"
    "tier4-frontier"
    "titan-embed-text-v2"
    "work"
  ];
  contextMcpCatalog = {
    recallium = "http://nyx.tail0e55.ts.net:18001/mcp";
    graphify = "http://nyx.tail0e55.ts.net:18108/mcp";
    context7 = "http://nyx.tail0e55.ts.net:18106/mcp";
    playwright = "http://nyx.tail0e55.ts.net:18107/mcp";
    kubernetes = "http://nyx.tail0e55.ts.net:18102/mcp";
    aws = "http://nyx.tail0e55.ts.net:18103/mcp";
    terraform = "http://nyx.tail0e55.ts.net:18104/mcp";
    # No duckduckgo entry: the ddg-mcp-search server on nyx:18105 was removed
    # (Chinese-only tool descriptions made it undiscoverable, and it was an
    # unpinned npx dependency). Web search reaches consumers through the
    # gateway's `web_search` mcp_servers entry, which the context brokers see
    # via the gateway rather than this direct catalog.
    gitlab = "http://nyx.tail0e55.ts.net:18101/mcp";
  };
  contextSkillRoots = lib.concatStringsSep ":" [
    "${../../modules/hm/users/cdenneen/ai/skills}"
    "${../../modules/hm/users/cdenneen/hermes-supervisor}"
  ];
  mkContextBrokerService = domain: port: {
    description = "Eros ${domain} context broker";
    after = [
      "eros-context-schema.service"
      "network-online.target"
    ];
    requires = [ "eros-context-schema.service" ];
    wants = [
      "network-online.target"
      "ollama.service"
      "podman-qdrant.service"
    ];
    wantedBy = [ "multi-user.target" ];
    environment = {
      EROS_TRUST_DOMAIN = domain;
      EROS_CONTEXT_PORT = toString port;
      EROS_KNOWLEDGE_DSN = "postgresql:///eros_context?host=/run/postgresql";
      EROS_LITELLM_DSN = "postgresql:///litellm?host=/run/postgresql";
      EROS_QDRANT_URL = "http://127.0.0.1:${toString qdrantPort}";
      EROS_OLLAMA_URL = "http://127.0.0.1:11434";
      EROS_SKILL_ROOTS = contextSkillRoots;
      EROS_MODEL_ROUTES = lib.concatStringsSep "," contextModelRoutes;
      EROS_MCP_CATALOG = builtins.toJSON contextMcpCatalog;
      EROS_EXTERNAL_PROJECTS = "[]";
    };
    serviceConfig = {
      Type = "simple";
      User = "eros_context";
      Group = "eros_context";
      ExecStart = "${contextBroker}/bin/python ${contextBrokerSource}/server.py serve";
      Restart = "on-failure";
      RestartSec = "5s";
      StateDirectory = "eros-context";
      NoNewPrivileges = true;
      PrivateTmp = true;
    };
  };
in
{
  networking.hostName = "eros";
  ec2.efi = true;

  # CPU-only LLM gateway: retain the host tooling but do not run a desktop.
  profiles.gui.enable = false;
  services.desktopManager.plasma6.enable = false;
  services.xserver.desktopManager.xfce.enable = false;
  services.xserver.displayManager.lightdm.enable = false;
  services.displayManager.sddm.enable = false;

  services.tailscale = {
    enable = true;
    openFirewall = false;
  };

  # Keep the raw Ollama API local. Clients use the authenticated LiteLLM
  # gateway on the tailnet; do not expose port 11434.
  services.ollama = {
    enable = true;
    host = "127.0.0.1";
    openFirewall = false;
    loadModels = [
      "qwen2.5-coder:7b"
      "qwen3-embedding:0.6b"
    ];
    environmentVariables = {
      OLLAMA_MAX_LOADED_MODELS = "2";
      OLLAMA_CONTEXT_LENGTH = "32768";
      OLLAMA_NUM_PARALLEL = "1";
    };
  };

  virtualisation.oci-containers = {
    backend = "podman";
    containers = {
      qdrant = {
        image = "qdrant/qdrant:v1.18.3@sha256:0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286";
        ports = [ "127.0.0.1:${toString qdrantPort}:6333" ];
        volumes = [ "/var/lib/qdrant:/qdrant/storage:U" ];
        autoStart = true;
      };
      litellm = {
        # v1.101.0 (2026-09-18). Multi-arch index digest, same convention as the
        # v1.94.0 pin it replaces - eros is aarch64, and pinning the index keeps
        # the expression arch-independent.
        #
        # Validated before cutover on a v1.101.0 container against a
        # schema-complete clone of the prod database (litellm_staging, spend-log
        # rows excluded), on a separate port, with prod untouched throughout.
        # The Prisma migration on that clone succeeded and the proxy was ready in
        # ~20s. All seven checks passed:
        #   1. the Bedrock tool guard still fires on /v1/messages with
        #      call_type=anthropic_messages - this was the main risk, since
        #      v1.95.0 introduced a Rust /v1/messages path that could have
        #      bypassed the Python pre_call_hook. It does not.
        #   2. timeout: 600 preserved on all four claude-* routes
        #   3. cache_control_injection_points still applied
        #   4. x_hermes_source still dropped
        #   5. object_permission survived v1.96's MCP entitlements rework
        #   6. semantic MCP tool search works (see mcp_tool_search below)
        #   7. 219,333 input tokens to claude-opus-5 returned 200
        #
        # Rollback is the previous pin:
        # v1.94.0@sha256:65d84a2282137b4dc73bbe184650a7c807177c533e4223b3bfbc87963fe3fabe
        image = "ghcr.io/berriai/litellm:v1.101.0@sha256:d295634e09c648dcdb72c4cc2dd226f5fb87823a73e88cbbed6f205e4deb044b";
        volumes = [
          "${litellmConfigFile}:/app/config.yaml:ro"
          # Mounted at /app because the container's WorkingDir is /app and
          # sys.path[0] is "", so litellm_settings.callbacks can reference it as
          # the bare module `eros_bedrock_tool_guard`. See the callbacks entry.
          "${bedrockToolGuardFile}:/app/eros_bedrock_tool_guard.py:ro"
        ];
        extraOptions = [
          "--env-file=${litellmEnvFile}"
          "--network=host"
        ];
        cmd = [
          "--config"
          "/app/config.yaml"
          "--host"
          "127.0.0.1"
          "--port"
          (toString litellmPort)
        ];
        autoStart = true;
      };
    };
  };

  systemd.tmpfiles.rules = [ "d /var/lib/qdrant 0750 root root -" ];

  users.users.eros_context = {
    isSystemUser = true;
    group = "eros_context";
  };
  users.groups.eros_context = { };

  services.postgresql = {
    enable = true;
    ensureDatabases = [
      "litellm"
      "eros_context"
    ];
    ensureUsers = [
      {
        name = "litellm";
        ensureDBOwnership = true;
      }
      {
        name = "eros_context";
        ensureDBOwnership = true;
      }
    ];
    authentication = lib.mkAfter ''
      host litellm litellm 127.0.0.1/32 scram-sha-256
    '';
    settings.password_encryption = "scram-sha-256";
  };

  systemd.services.eros-postgresql-collation-refresh = {
    description = "Refresh PostgreSQL template collation metadata";
    after = [ "postgresql.service" ];
    requires = [ "postgresql.service" ];
    before = [ "postgresql-setup.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "postgres";
    };
    script = ''
      set -euo pipefail
      psql=${config.services.postgresql.package}/bin/psql

      for database in postgres template1; do
        versions="$($psql --dbname=postgres --tuples-only --no-align --field-separator='|' \
          --command="SELECT datcollversion, pg_database_collation_actual_version(oid) FROM pg_database WHERE datname = '$database'")"
        stored="''${versions%%|*}"
        actual="''${versions#*|}"
        if [ -n "$stored" ] && [ "$stored" != "$actual" ]; then
          $psql --dbname="$database" --command="REINDEX DATABASE \"$database\""
          $psql --dbname=postgres --command="ALTER DATABASE \"$database\" REFRESH COLLATION VERSION"
        fi
      done
    '';
  };

  systemd.services.postgresql-setup = {
    after = [ "eros-postgresql-collation-refresh.service" ];
    requires = [ "eros-postgresql-collation-refresh.service" ];
  };

  sops.secrets = {
    eros_litellm_master_key = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    eros_litellm_db_password = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    eros_litellm_salt_key = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    openai_api_key = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    gemini_api_key = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    omniroute_client_key = {
      owner = "root";
      group = "root";
      mode = "0400";
      # NOT a pre-existing external secret - see G-DR-PREP-1 notes below.
      # Bootstrap once via: POST /api/keys {scopes:["self:usage"]} against
      # a running omniroute.service, then sops-encrypt the returned key into
      # this path (requires local age identity - see recovery manifest).
    };
    # personal/work (2026-09-03): same bootstrap process as omniroute_client_key
    # above, one key per trust domain - see the `personal`/`work` model_list
    # entries' comment for why this isn't one shared key.
    omniroute_client_key_personal = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
    omniroute_client_key_work = {
      owner = "root";
      group = "root";
      mode = "0400";
    };
  };

  systemd.services.eros-litellm-env = {
    description = "Render the LiteLLM secret environment";
    before = [ "podman-litellm.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail

      read_secret() {
        local secret_file="$1"
        local secret_name="$2"
        if [ ! -r "$secret_file" ]; then
          echo "Missing $secret_name at $secret_file" >&2
          exit 1
        fi
        ${pkgs.coreutils}/bin/tr -d '\n\r' < "$secret_file"
      }

      read_secret_optional() {
        local secret_file="$1"
        local secret_name="$2"
        if [ ! -r "$secret_file" ]; then
          echo "Missing $secret_name at $secret_file - OmniRoute-backed routes will fail auth until this is bootstrapped (see scripts/eros-recovery/); other LiteLLM routes are unaffected." >&2
          echo ""
          return 0
        fi
        ${pkgs.coreutils}/bin/tr -d '\n\r' < "$secret_file"
      }

      ${pkgs.coreutils}/bin/install -d -m 0700 /run/eros-litellm
      ${pkgs.coreutils}/bin/install -m 0600 /dev/null "${litellmEnvFile}"
      {
        printf 'LITELLM_MASTER_KEY=%s\n' "$(read_secret "${config.sops.secrets.eros_litellm_master_key.path}" "LiteLLM master key")"
        printf 'LITELLM_SALT_KEY=%s\n' "$(read_secret "${config.sops.secrets.eros_litellm_salt_key.path}" "LiteLLM salt key")"
        printf 'DATABASE_URL=postgresql://litellm:%s@127.0.0.1:5432/litellm\n' "$(read_secret "${config.sops.secrets.eros_litellm_db_password.path}" "LiteLLM database password")"
        printf 'OPENAI_API_KEY=%s\n' "$(read_secret "${config.sops.secrets.openai_api_key.path}" "OpenAI key")"
        printf 'GEMINI_API_KEY=%s\n' "$(read_secret "${config.sops.secrets.gemini_api_key.path}" "Gemini key")"
        printf 'OMNIROUTE_CLIENT_KEY=%s\n' "$(read_secret_optional "${config.sops.secrets.omniroute_client_key.path}" "OmniRoute client key")"
        printf 'OMNIROUTE_CLIENT_KEY_PERSONAL=%s\n' "$(read_secret_optional "${config.sops.secrets.omniroute_client_key_personal.path}" "OmniRoute personal client key")"
        printf 'OMNIROUTE_CLIENT_KEY_WORK=%s\n' "$(read_secret_optional "${config.sops.secrets.omniroute_client_key_work.path}" "OmniRoute work client key")"
        printf 'QDRANT_API_BASE=http://127.0.0.1:%s\n' "${toString qdrantPort}"
        printf 'QDRANT_VECTOR_SIZE=1024\n'
      } > "${litellmEnvFile}"
    '';
  };

  systemd.services.eros-litellm-config = {
    description = "Render the LiteLLM configuration";
    before = [ "podman-litellm.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail
      ${pkgs.coreutils}/bin/install -d -m 0700 /run/eros-litellm
      cat > "${litellmConfigFile}" <<'EOF'
      model_list:
        # --- Legacy aliases (existing live consumers; kept for compatibility) ---
        - model_name: coding
          litellm_params:
            model: ollama/qwen2.5-coder:7b
            api_base: http://127.0.0.1:11434
          model_info:
            max_input_tokens: 28672
            max_output_tokens: 4096
        - model_name: local-embed
          litellm_params:
            model: ollama/qwen3-embedding:0.6b
            api_base: http://127.0.0.1:11434
        - model_name: coding-openai
          litellm_params:
            model: openai/gpt-5-mini
            api_key: os.environ/OPENAI_API_KEY
        - model_name: openai/*
          litellm_params:
            model: openai/*
            api_key: os.environ/OPENAI_API_KEY
        - model_name: coding-haiku
          litellm_params:
            model: bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
            cache_control_injection_points: &eros_cache_points_with_tools
              - location: tool_config
                control:
                  type: ephemeral
              - location: message
                role: system
                control:
                  type: ephemeral
              - location: message
                index: -1
                control:
                  type: ephemeral
        - model_name: coding-gemini
          litellm_params:
            model: gemini/gemini-2.5-flash
            api_key: os.environ/GEMINI_API_KEY
        # Fixed 2026-08-27: was bedrock/us.anthropic.claude-sonnet-4-6 ($3/$15 per
        # Mtok), which is *more expensive* than Sonnet 5 ($2/$10 per Mtok) despite
        # being the older model. Live traffic was silently overpaying since this
        # route went into use. See phase1-attribution-and-counterfactual.md.
        # cache_control_injection_points added 2026-08-29: this route (bedrock/
        # claude-sonnet-5, called by the eros-nyx-all-routing / eros-VNJTECMBCD-
        # all-routing generic keys - a legacy coding CLI worker, not a Hermes
        # gateway) showed the same sustained-tool-loop shape as the already-proven
        # Nyx EKS tier2-general fix: real traffic on 2026-08-27 02:17-02:23 grew
        # 110,864 -> 133,689 prompt tokens across dozens of turns in ~6 minutes,
        # $0.37-0.45/call at the uncached rate, zero cache_read/cache_creation
        # ever recorded. Validated on a bounded test route before applying here,
        # INCLUDING a tool-calling check this workload needed that tier2-general's
        # validation didn't: a `tools`-bearing request only cached correctly once
        # a `location: tool_config` breakpoint was added alongside system/trailing-
        # message - system+trailing-message alone (tier2-general's exact config)
        # silently produced a 100% cache MISS on every call once `tools` was
        # present, with no error, same cost as no caching at all. Confirmed the
        # 3-point form below handles tools-present AND tools-absent turns in the
        # same conversation correctly (cache write once cache-read 143,757/143,757
        # across 3 real turns of that shape). tier2-general is deliberately left
        # untouched - its 2-point config has proven correct for its own real
        # traffic and is out of scope for alteration this slice.
        # Reusable for any future route with this same tools-capable, repeated-
        # prefix shape: alias *eros_cache_points_with_tools rather than retyping.
        - model_name: coding-strong
          litellm_params:
            model: bedrock/us.anthropic.claude-sonnet-5
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
            cache_control_injection_points: *eros_cache_points_with_tools

        # --- Claude Code / Claude Desktop client-facing aliases (2026-09-11) ---
        # Net-new, parallel to coding-strong/tier2-general (not a rename/reuse):
        # those routes' existing consumers (eros-*-all-routing keys, Nyx EKS
        # traffic) are tuned/validated separately, and Claude Code's gateway
        # model-discovery (CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1)
        # requires "claude"/"anthropic" literally in model_name, which no
        # existing alias has. Underlying Bedrock deployments are shared with
        # existing routes where the model is identical - this only adds
        # routing-layer aliases, not new Bedrock calls.
        #
        # timeout: 600 on each claude-* deployment (2026-09-17). router_settings
        # timeout: 90 leaks onto the streaming path and kills Claude Code turns
        # mid-stream. Chain: /v1/messages -> anthropic_messages ->
        # _ageneric_api_call_with_fallbacks, which router.py:3095-3110 routes to
        # resolve_llm_passthrough_timeout(); that ignores stream_timeout and
        # falls back to router_timeout (= self._explicit_timeout, router.py:571,
        # = 90). httpx read timeout becomes aiohttp sock_read
        # (aiohttp_transport.py:262), which is a PER-CHUNK gap timeout, not a
        # total - so any >90s pause in the Bedrock SSE stream (extended thinking
        # on a large prompt) raises SocketTimeoutError -> httpx.ReadTimeout
        # inside async_data_generator() and the turn just stops. num_retries
        # can't cover it: the 200 OK is already on the wire. Observed on eros:
        # 08:28:26 stream start -> 08:29:58 ReadTimeout = 92s, recurring Sep
        # 15-17. litellm_params.timeout outranks router_timeout in that chain
        # (and feeds _get_non_stream_timeout for the chat-completions path), so
        # it fixes both without loosening timeout: 90 for every other route.
        # 600 = LiteLLM's own DEFAULT_PASS_THROUGH_REQUEST_TIMEOUT_SECONDS.
        - model_name: claude-haiku-4-5
          litellm_params:
            model: bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0
            aws_region_name: us-east-1
            timeout: 600
            drop_params: true
            additional_drop_params:
              - x_hermes_source
            cache_control_injection_points: *eros_cache_points_with_tools
        - model_name: claude-sonnet-5
          litellm_params:
            model: bedrock/us.anthropic.claude-sonnet-5
            aws_region_name: us-east-1
            timeout: 600
            drop_params: true
            additional_drop_params:
              - x_hermes_source
            cache_control_injection_points: *eros_cache_points_with_tools
          model_info:
            # UNVERIFIED: max_input_tokens only changes what LiteLLM reports
            # to clients, it does not itself request extended context from
            # Bedrock. Confirm against the live Bedrock model card / a real
            # >200K-token test call before trusting this in practice.
            max_input_tokens: 1000000
        - model_name: claude-opus-5
          litellm_params:
            # REGIONAL (us.), not the global. cross-region profile (2026-09-14).
            # Bedrock prompt caches are region-scoped: under global. AWS may
            # route consecutive invocations to different regions, so a cache
            # written on call N is unreachable on call N+1. Observed live on
            # ghost: 3 Claude Code turns wrote 87393/127295/134318 cache tokens
            # with cache_read_input_tokens: 0 every time - ~$2.20 in 19s, ~99%
            # of it cache-write. Regional pins the cache so reads can land.
            # Do not revert to global. without re-proving cache reads.
            model: bedrock/us.anthropic.claude-opus-5
            aws_region_name: us-east-1
            timeout: 600
            drop_params: true
            additional_drop_params:
              - x_hermes_source
            cache_control_injection_points: *eros_cache_points_with_tools
          model_info:
            max_input_tokens: 1000000

        # Opus 5.5 (2026-09-23). Regional us. profile for the same cache-scoping
        # reason as claude-opus-5 above - do not switch to global. without
        # re-proving cache reads. Verified against Bedrock from eros before
        # adding: us.anthropic.claude-opus-5-5 is an ACTIVE inference profile,
        # invoke-model returns 200, streaming works (checked through litellm's
        # own SDK, 5 chunks), and both the context-1m-2025-08-07 beta and
        # cache_control on a system block are accepted.
        #
        # Single route with max_input_tokens: 1000000, matching claude-opus-5 and
        # claude-sonnet-5. There is deliberately no separate 200k/1M pair: the
        # normal-vs-1M choice is client-side in Claude Code via the [1m] suffix,
        # which is stripped before the request leaves the client, so both land on
        # this one route.
        #
        # Caveat for Claude Code specifically: CLI 2.1.220's model table stops at
        # claude-opus-4-7, so it has no capability entry for this slug yet - it
        # will be selectable via gateway discovery but without native_1m_3p or a
        # [1m] variant until the CLI ships an entry. API-shaped consumers
        # (Hermes, opencode, pi) are unaffected.
        - model_name: claude-opus-5-5
          litellm_params:
            model: bedrock/us.anthropic.claude-opus-5-5
            aws_region_name: us-east-1
            timeout: 600
            drop_params: true
            additional_drop_params:
              - x_hermes_source
            cache_control_injection_points: *eros_cache_points_with_tools
          model_info:
            max_input_tokens: 1000000

        - model_name: g2-omniroute-openai-gpt4o-mini
          litellm_params:
            model: openai/gpt-4o-mini
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
        - model_name: g5-omniroute-bedrock-haiku
          litellm_params:
            model: openai/anthropic.claude-3-haiku-20240307-v1:0
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
          model_info:
            input_cost_per_token: 0.00000025
            output_cost_per_token: 0.00000125
        # --- Stable capability-tier routes (00-program-spec.md route contract) ---
        - model_name: tier0-local
          litellm_params:
            model: ollama/qwen2.5-coder:7b
            api_base: http://127.0.0.1:11434
          model_info:
            max_input_tokens: 28672
            max_output_tokens: 4096
        - model_name: tier1-general
          litellm_params:
            model: openai/gemini-2.5-flash
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
          model_info:
            input_cost_per_token: 0.00000015
            output_cost_per_token: 0.0000006
        - model_name: tier1-coding
          litellm_params:
            model: openai/gpt-5-mini
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            api_base: http://127.0.0.1:20128/v1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        # Consolidated cheap/fast tier (2026-09-02): additive alongside
        # tier1-general/tier1-coding above, not a replacement yet. Once every
        # consumer requests `mini` instead of tier1-general/tier1-coding
        # directly, those two entries retire - see `mini`'s fallback below,
        # which reuses tier1-coding rather than duplicating it.
        - model_name: mini
          litellm_params:
            model: openai/gemini-2.5-flash
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        # cache_control_injection_points added 2026-08-28: validated on a bounded
        # test route (154.8K-token stable prefix, 3-turn A/B) before applying here -
        # cache write $0.4257/3655ms turn 1, cache read $0.0341/1716ms turn 2,
        # $0.0342/1699ms turn 3 (92% cost cut, 53% latency cut, sustained across
        # consecutive turns). Model/capability unchanged - this only changes how
        # Bedrock bills/serves an already-identical prefix. tier2-coding/
        # tier2-research/tier3-quality intentionally NOT touched yet - bounded to
        # the one route with proven real organic long-tool-loop usage (Nyx EKS).
        # ARCHITECTURE EXCEPTION (2026-08-31, G9/G10 investigation): this route
        # intentionally stays on direct LiteLLM->Bedrock rather than OmniRoute.
        # Root cause (confirmed via source inspection + live debug-log capture):
        # LiteLLM cache_control_injection_points is implemented ONLY in
        # llms/bedrock/chat/converse_transformation.py, gated to
        # custom_llm_provider="bedrock". Routing this model through OmniRoute
        # (custom_llm_provider="openai" + api_base=OmniRoute) means cache_control
        # is never attached to the request at all - confirmed empirically: a
        # repeated 6989-token prefix test via OmniRoute showed turn 2 SLOWER than
        # turn 1 (no cache read), vs this direct route real cache_creation/
        # cache_read tokens and a documented 92%% cost cut. Classified
        # CACHE_NOT_REQUESTED, not an OmniRoute defect - OmniRoute never receives
        # cache metadata to translate or drop.
        # This route remains governed by Eros and accounted by LiteLLM - NOT an
        # unmanaged bypass. Revisit only when: upstream LiteLLM supports cache
        # injection for the openai-compatible path; OmniRoute gains a native
        # equivalent; or another candidate/path proves cheaper per verified
        # outcome. See phase1-cache-root-cause.md (recovery/portability manifest).
        - model_name: tier2-general
          litellm_params:
            model: bedrock/us.anthropic.claude-sonnet-5
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
            cache_control_injection_points:
              - location: message
                role: system
                control:
                  type: ephemeral
              - location: message
                index: -1
                control:
                  type: ephemeral
        - model_name: tier2-coding
          litellm_params:
            model: openai/us.anthropic.claude-sonnet-5
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        # Consolidated quality-coding tier (2026-09-02): additive alongside
        # tier2-coding/tier2-research above. Same-tier redundancy across
        # paths, NOT the "generic 429/error fallback" this file's
        # router_settings comment below still correctly bans - see that
        # comment for the incident (coding-strong -> coding-gemini silently
        # collapsing Claude-tier onto Gemini Flash) this must never repeat.
        # Every candidate in `auto`'s own fallback chain is the same
        # Sonnet-5/GPT-5.4 quality class; nothing here ever drops to mini.
        #
        # Named `auto`, not `coding-strong` - `coding-strong` already exists
        # above (direct Bedrock, 3-point tool-aware cache, real legacy CLI
        # worker traffic) and reusing that name here would have registered a
        # second deployment under the same model_name, letting litellm's
        # router pick between them outside this fallback chain entirely and
        # sometimes bypass the existing tool-aware cache. Caught by review
        # before merge, not discovered live.
        #
        # Motivating evidence (2026-08-25..09-01, OmniRoute call_logs): Sonnet
        # traffic through OmniRoute (tier2-coding/tier2-research) ran ~57%
        # success over 7 days; 74%% of the failures were OmniRoute's own
        # local request-queue timeout (resilienceSettings.requestQueue.
        # maxWaitMs), not a Bedrock/upstream problem. This chain gives that
        # traffic somewhere real to go instead of failing outright - falling
        # through to the *existing* `coding-strong` (not a new duplicate) as
        # the final, most-reliable rung.
        - model_name: auto
          litellm_params:
            model: openai/bedrock/us.anthropic.claude-sonnet-5
            # co-located on eros today; if omniroute ever moves to its own
            # host, this becomes http://eros.tail0e55.ts.net:20128/v1
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: tier2-research
          litellm_params:
            model: openai/us.anthropic.claude-sonnet-5
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: tier3-quality
          litellm_params:
            model: openai/global.anthropic.claude-opus-5
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
        # `quality` (2026-09-02): additive alias for tier3-quality's exact
        # model. No fallback, same as tier3-quality itself - Opus is its own
        # capability class; failure must reject, never silently become
        # coding-strong's Sonnet-5.
        - model_name: quality
          litellm_params:
            model: openai/global.anthropic.claude-opus-5
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
        # tier4-frontier deliberately has no fallback and is not part of any
        # fallback chain below - explicit-only, separate key at the governor
        # layer (00-program-spec.md: "explicit only; no automatic fallback").
        - model_name: gpt-5.4
          litellm_params:
            model: openai/gpt-5.4
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: gpt-5.6-terra
          litellm_params:
            model: openai/gpt-5.6-terra
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: axis-claude-sonnet-4-6
          litellm_params:
            model: bedrock/us.anthropic.claude-sonnet-4-6
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
            cache_control_injection_points: *eros_cache_points_with_tools
        - model_name: tier4-frontier
          litellm_params:
            model: openai/gpt-5.6-sol
            api_key: os.environ/OPENAI_API_KEY
        # AXIS-only core models (2026-09-09): direct native Bedrock, instance
        # profile (no explicit api_key, same pattern as coding-strong above).
        # Deliberately NOT added to any consumer key's allowlist except axis -
        # see /key/update done alongside this PR. multimodal-long (nova-2-lite)
        # is NOT wired here: amazon.nova-2-lite-v1:0 (both the us. and global.
        # cross-region inference profiles) is blocked by an explicit deny in
        # an AWS Organizations SCP (arn:...policy/o-l5977bt4h1/.../p-znpv8ugv)
        # on this instance profile - confirmed via direct bedrock-runtime
        # converse calls, not a LiteLLM/OmniRoute-side issue. Needs an AWS
        # Organizations admin to adjust the SCP, or a different credential
        # path (e.g. a Bedrock Mantle API key under a different account) -
        # neither set up yet.
        - model_name: general-core
          litellm_params:
            model: bedrock/qwen.qwen3-next-80b-a3b
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: coding-core
          litellm_params:
            model: bedrock/qwen.qwen3-coder-next
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        # multimodal-long (2026-09-09): amazon.nova-2-lite is INFERENCE_PROFILE-
        # only (no ON_DEMAND support) - the us. cross-region inference
        # profile, not the bare foundation-model id. Required an AWS
        # Organizations SCP change (Allow-Listing-AWS-Bedrock-Models,
        # p-znpv8ugv) to add an inference-profile/us.amazon.nova-* allowlist
        # entry - the existing amazon.nova-* wildcard only covered the
        # foundation-model/ ARN pattern, not inference-profile/.
        - model_name: multimodal-long
          litellm_params:
            model: bedrock/us.amazon.nova-2-lite-v1:0
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: review-strong
          litellm_params:
            model: bedrock/zai.glm-5
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: research-candidate
          litellm_params:
            model: bedrock/moonshotai.kimi-k2.5
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: reasoning-candidate
          litellm_params:
            model: bedrock/deepseek.v3.2
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: embedding-core
          litellm_params:
            model: bedrock/amazon.titan-embed-text-v2:0
            aws_region_name: us-east-1

        # --- Real-model-name aliases for Hermes agents (2026-09-12) ---
        # Additive routing-layer aliases only: same Bedrock deployments as the
        # semantic routes above (coding-core, review-strong, ...), named after
        # the actual model so Hermes's model picker is self-describing. The
        # semantic names stay for their existing AXIS consumers - nothing is
        # renamed or repointed. claude-sonnet-5/-haiku-4-5/-opus-5 already have
        # explicit names above and are deliberately NOT duplicated here.
        # NOTE: this widens the AXIS-only boundary noted at the general-core
        # comment above - the eros-hermes-agents key is granted these aliases
        # per explicit user request for "all the bedrock models we wired up".
        - model_name: claude-sonnet-4-6
          litellm_params:
            model: bedrock/us.anthropic.claude-sonnet-4-6
            aws_region_name: us-east-1
            # See the timeout: 600 rationale on claude-haiku-4-5 above.
            timeout: 600
            drop_params: true
            additional_drop_params:
              - x_hermes_source
            cache_control_injection_points: *eros_cache_points_with_tools
        - model_name: qwen3-coder-next
          litellm_params:
            model: bedrock/qwen.qwen3-coder-next
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: qwen3-next-80b-a3b
          litellm_params:
            model: bedrock/qwen.qwen3-next-80b-a3b
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: deepseek-v3.2
          litellm_params:
            model: bedrock/deepseek.v3.2
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: kimi-k2.5
          litellm_params:
            model: bedrock/moonshotai.kimi-k2.5
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: glm-5
          litellm_params:
            model: bedrock/zai.glm-5
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: nova-2-lite
          litellm_params:
            model: bedrock/us.amazon.nova-2-lite-v1:0
            aws_region_name: us-east-1
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: titan-embed-text-v2
          litellm_params:
            model: bedrock/amazon.titan-embed-text-v2:0
            aws_region_name: us-east-1

        # personal/work (2026-09-03): single entry point per trust domain,
        # forwarding to OmniRoute's native combo/reasoning_routing_rules
        # engine (combo: ai-auto) - see hermes-profile-model migration.
        # Two OmniRoute client keys preserve personal/work cost attribution
        # and routing identity. Private context stays domain-scoped, while
        # shared ontology/capabilities are common and routing remains usable.
        # Old tier0-4/auto/mini/quality/coding-* entries above are left in
        # place until consumer traffic is proven flowing through these two
        # and they can be retired.
        - model_name: personal
          litellm_params:
            model: openai/ai-auto
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY_PERSONAL
            drop_params: true
            additional_drop_params:
              - x_hermes_source
        - model_name: work
          litellm_params:
            model: openai/ai-auto
            api_base: http://127.0.0.1:20128/v1
            api_key: os.environ/OMNIROUTE_CLIENT_KEY_WORK
            drop_params: true
            additional_drop_params:
              - x_hermes_source
      general_settings:
        master_key: os.environ/LITELLM_MASTER_KEY
        database_url: os.environ/DATABASE_URL
        # LiteLLM 1.94.0 leaks estimated reservation spend when a native
        # Anthropic stream disconnects. Operational keys have no hard budget;
        # test-key limits use reconciled spend rather than rejecting real work
        # against the leaked process-local estimate.
        disable_budget_reservation: true
        # Fallback targets must also be in the calling key'''s own allowlist,
        # not just the primary model - closes a gap where a key restricted
        # to model X could silently reach fallback Y via a fallback chain
        # without Y ever being explicitly granted. Same credential-bound-
        # ceiling principle as #41 (Bootstrap Gate), just applied to
        # fallback targets specifically. Every key with a fallback-bearing
        # primary model (auto/mini for ghost-alpha0-policy-endpoint/nyx-eks/
        # nyx-gitlab/axis) was additively updated to include its fallback
        # targets before this was enabled, so no consumer's fallback
        # behavior changes - this only prevents that gap from reopening.
        enforce_fallback_model_access: true
        # Enables Claude Code's context-compaction feature. Points at
        # claude-sonnet-5, not coding-strong, so this doesn't retroactively
        # change coding-strong's contract for its existing callers.
        context_management_summary_model: claude-sonnet-5
      litellm_settings:
        # Keep response caching disabled: an exact response can be stale or
        # unsafe to replay. Provider-side Anthropic prompt-prefix caching is
        # enabled separately and measured in LiteLLM's native spend tables.
        cache: false
        enable_anthropic_prompt_caching: true
        extra_spend_tag_headers:
          - x-eros-consumer
          - x-eros-trust-domain
          - x-eros-workload
          - x-eros-task
          - x-eros-session
          - x-eros-outcome
        default_key_generate_params:
          object_permission:
            mcp_tool_search_enabled: true
            mcp_servers:
              - recallium
              - graphify
              - context7
              - playwright
              # renamed from duckduckgo - see the mcp_servers entry for why
              - web_search
              - gitlab
              - kubernetes
              - aws
              - terraform
              - eros-context-shared
        # Direct request-layer semantic filtering remains disabled until a
        # pinned-v1.94 compatibility test proves nested/native tools fail open.
        # MCP-speaking clients use /mcp/ virtual tool search instead.
        mcp_semantic_tool_filter:
          enabled: false
        # Ranks the /mcp/ virtual mcp_tool_search results by meaning instead of
        # by keyword. v1.94.0's search_tools() scored a tool by counting query
        # tokens appearing as substrings of `name + " " + description`, which
        # made the catalog effectively undiscoverable by intent: "search the
        # internet for recent news" returned three recallium *memory* tools, and
        # "fetch a web page" returned playwright-browser_click. Unset keeps that
        # keyword behaviour, so this key is what turns the feature on.
        #
        # local-embed is the existing ollama qwen3-embedding:0.6b route, so this
        # adds no new dependency and no egress. Measured on v1.101.0 staging:
        # "fetch a web page" -> web_search-fetch_content (0.73) as the top hit.
        #
        # Note the key is mcp_tool_search.embedding_model, NOT the
        # skill_search_embedding_model named in the docs - that one configures
        # semantic search over LiteLLM-hosted *skills*, a different feature.
        # Verified against v1.101.0's own MCPToolSearchSettings model.
        #
        # This does not fix web-search discovery on its own: web_search-search's
        # upstream description is a single Chinese phrase, and even a
        # multilingual embedder ranks it below verbose English tools for
        # "search the internet" queries. That needs an English-described search
        # server, tracked separately.
        mcp_tool_search:
          embedding_model: local-embed
        # Drops Anthropic provider-executed tools (web_search_*, web_fetch_*,
        # code_execution_*) from requests whose model group resolves to a
        # bedrock/* deployment. Bedrock has no implementation for them and fails
        # the whole request with a non-retryable 400 - and because cross-tier
        # fallback is banned here, the turn just dies and the consumer reports a
        # bare "provider failed after retries". Measured 2026-09-17: 592 such
        # failures in two hours on claude-opus-5/claude-sonnet-5.
        #
        # Resolved via get_instance_fn, which splits on the last dot: module
        # `eros_bedrock_tool_guard`, instance `guard`. It reaches this path
        # because ProxyLogging.pre_call_hook's CustomLogger branch has no
        # call_type allowlist, so it fires for anthropic_messages (/v1/messages)
        # as well as chat completions. Source and self-check:
        # pkgs/eros-litellm-hooks/bedrock_tool_guard.py - it fails open on any
        # internal error.
        callbacks:
          - eros_bedrock_tool_guard.guard
        drop_params: true
        additional_drop_params:
          - x_hermes_source
      router_settings:
        cache_responses: false
        # Added for Claude Code/Desktop migration: multiple deployments of
        # bedrock/us.anthropic.claude-sonnet-5 now exist across aliases
        # (coding-strong, tier2-general, claude-sonnet-5) - this makes the
        # router prefer whichever deployment most recently served a matching
        # prompt prefix, for cache hits across alias boundaries. Independent
        # of litellm_settings.cache (response cache, stays false above).
        optional_pre_call_checks: ["prompt_caching"]
        # Cross-tier/generic fallback is still banned - 01-eros-inference-
        # fabric.md's rule stands, and the incident that produced it is real:
        # an earlier `coding-strong: [coding-gemini]` entry once silently
        # collapsed a Claude-tier request onto Gemini Flash on failure -
        # undetectable capability downgrade. That must never happen again.
        #
        # What's below is narrower and different in kind: same-tier
        # redundancy across paths for one named model, not a downgrade path.
        # `auto` only ever falls back to gpt-5.4 (same quality class,
        # different provider) or the *existing* `coding-strong` (the literal
        # same Sonnet-5, direct-Bedrock, already-proven tool-aware-cache
        # path) - see the comment on `auto` above for the OmniRoute-
        # reliability evidence motivating this, and why it isn't itself
        # named `coding-strong`. `mini` only falls back to tier1-coding,
        # itself a cheap/fast-tier model, not a downgrade from mini's own
        # tier. tier4-frontier/coding-strong/quality getting no entry at all
        # would already mean no fallback (litellm only fires a fallback for
        # a model that has one) - the explicit empty lists below are just
        # that intent written down, so a future generic/wildcard entry can't
        # silently start catching them without someone having to touch these
        # lines first.
        fallbacks:
          - auto: [gpt-5.4, coding-strong]
          - mini: [tier1-coding]
          - personal: [coding-strong]
          - work: [coding-strong]
          - tier4-frontier: []
          - coding-strong: []
          - quality: []
          # AXIS-only models (2026-09-09). general-core/coding-core get a
          # real fallback (same rationale as auto/mini above - redundancy,
          # not a downgrade path). The rest are explicit no-fallback by
          # design, per Chris: review-strong (preserve reviewer quality/
          # independence), multimodal-long (no equivalent video + 1M-context
          # route), embedding-core (never mix embedding spaces),
          # research-candidate/reasoning-candidate (keep candidate
          # measurements uncontaminated).
          - general-core: [multimodal-long]
          - coding-core: [review-strong]
          - review-strong: []
          - multimodal-long: []
          - embedding-core: []
          - research-candidate: []
          - reasoning-candidate: []
        num_retries: 1
        timeout: 90
      mcp_servers:
        # MVP set (2026-09-11): plain-HTTP servers only, no OAuth. Excludes
        # github/supabase/cloudflare (need LiteLLM oauth2 auth_type,
        # unverified against this version) and does not touch Hermes's
        # separate GitLab OAuth-refresh proxy on 127.0.0.1:8899. Auth header
        # for clients hitting these paths: verify x-litellm-api-key vs
        # Authorization against the live instance before wiring clients.
        recallium:
          url: "http://nyx.tail0e55.ts.net:18001/mcp"
          transport: "http"
          description: "Personal and work memory retrieval; results remain unverified until promoted with evidence"
          mcp_info: &eros_local_mcp_cost
            mcp_server_cost_info:
              default_cost_per_query: 0.0
        graphify:
          url: "http://nyx.tail0e55.ts.net:18108/mcp"
          transport: "http"
          description: "Graph projection and knowledge search"
          mcp_info: *eros_local_mcp_cost
        context7:
          url: "http://nyx.tail0e55.ts.net:18106/mcp"
          transport: "http"
          description: "Current library and framework documentation"
          mcp_info: *eros_local_mcp_cost
        playwright:
          url: "http://nyx.tail0e55.ts.net:18107/mcp"
          transport: "http"
          description: "Browser automation with external side effects"
          mcp_info: *eros_local_mcp_cost
        kubernetes:
          url: "http://nyx.tail0e55.ts.net:18102/mcp"
          transport: "http"
          description: "Kubernetes discovery and operations"
          mcp_info: *eros_local_mcp_cost
        aws:
          url: "http://nyx.tail0e55.ts.net:18103/mcp"
          transport: "http"
          description: "AWS discovery and operations"
          mcp_info: *eros_local_mcp_cost
        terraform:
          url: "http://nyx.tail0e55.ts.net:18104/mcp"
          transport: "http"
          description: "Terraform and OpenTofu discovery and operations"
          mcp_info: *eros_local_mcp_cost
        # Parallel's hosted Search MCP, replacing the ddg-mcp-search server on
        # nyx:18105 (2026-09-18). Free and anonymous - "The Search MCP is free to
        # use - no API key required" per docs.parallel.ai/integrations/mcp/search-mcp -
        # so this adds no secret and no cost. The /mcp endpoint is deliberately
        # the unauthenticated one; /mcp-oauth exists for higher rate limits and
        # 401s without a key, which we do not want here.
        #
        # Why replace a server that worked: tool discovery is only as good as the
        # upstream tool *descriptions*, and ddg-mcp-search's were a single
        # Chinese phrase each ("在DuckDuckGo上搜索并返回格式化结果"). That made web
        # search unfindable by intent no matter what the gateway did. Measured on
        # this deployment, before and after enabling semantic ranking:
        #   keyword  (v1.94.0): "search the internet for recent news" -> three
        #                       recallium *memory* tools
        #   semantic (v1.101.0): same query -> context_personal-* (0.70), still
        #                       not web search
        # Renaming the server key could not fix it (the key only prefixes the
        # name), and LiteLLM has no tool-description override. Parallel's tools
        # ship verbose English descriptions, which is what the ranker indexes.
        # Its output is English structured JSON too, where ddg-mcp-search
        # returned Chinese boilerplate ("找到 3 条搜索结果:") into model context.
        #
        # Verified from eros before switching: initialize + tools/list + a real
        # web_search call, all anonymous. Result quality spot-checked on three
        # queries - authoritative hits (freedesktop.org man pages, AWS Bedrock
        # model cards). Tool names stay web_search-* because the server key is
        # unchanged, so the object_permission grants in litellm-policy.sql and
        # every issued key keep working untouched.
        #
        # Rate limits are unpublished and keyed on session_id for anonymous use;
        # if that becomes a problem the options are an API key on /mcp-oauth, or
        # self-hosting (RivalSearchMCP is MIT, English, zero-auth, and was the
        # runner-up). LiteLLM still reserves "-" as its MCP tool-prefix
        # separator, so this key keeps using "_".
        web_search:
          url: "https://search.parallel.ai/mcp"
          transport: "http"
          description: "Public web search and page fetch"
          # Still the zero-cost anchor: the anonymous /mcp endpoint is free, so
          # 0.0 stays accurate, and keeping mcp_info preserves this server's
          # accounting attribution alongside every other entry.
          mcp_info: *eros_local_mcp_cost
        gitlab:
          url: "http://nyx.tail0e55.ts.net:18101/mcp"
          transport: "http"
          description: "GitLab discovery and operations"
          mcp_info: *eros_local_mcp_cost
        context_shared:
          server_id: "eros-context-shared"
          url: "http://127.0.0.1:${toString contextPorts.shared}/mcp"
          transport: "http"
          description: "Central ontology, capability discovery, reusable results, and shared accounting identity"
          mcp_info: *eros_local_mcp_cost
        context_personal:
          server_id: "eros-context-personal"
          url: "http://127.0.0.1:${toString contextPorts.personal}/mcp"
          transport: "http"
          description: "Central context fabric with personal accounting identity"
          mcp_info: *eros_local_mcp_cost
        context_work:
          server_id: "eros-context-work"
          url: "http://127.0.0.1:${toString contextPorts.work}/mcp"
          transport: "http"
          description: "Central context fabric with work accounting identity"
          mcp_info: *eros_local_mcp_cost
      EOF
      ${pkgs.yq-go}/bin/yq -e '
        [
          .model_list[]
          | select(.litellm_params.model | test("^bedrock/"))
          | select(.litellm_params.model | contains("embed") | not)
          | select(
              .litellm_params.drop_params != true
              or (.litellm_params.additional_drop_params | contains(["x_hermes_source"]) | not)
            )
        ]
        | length == 0
      ' "${litellmConfigFile}" > /dev/null
      # LiteLLM reserves "-" as its MCP tool-prefix separator and rejects it in
      # mcp_servers keys at startup, so a hyphen here is not a config smell but
      # an outage: on 2026-09-17 an `mcp_servers.web-search` key crashlooped the
      # proxy 601 times over ~20h until it was rolled back. The YAML parsed and
      # the container image was fine, so nothing upstream of here caught it.
      # Note this constrains the map key only - server_id may contain hyphens,
      # which is why the eros-context-* servers use underscore keys.
      ${pkgs.yq-go}/bin/yq -e '
        [ .mcp_servers | keys | .[] | select(test("-")) ] | length == 0
      ' "${litellmConfigFile}" > /dev/null
      # Every server named in the default key allowlist must actually exist, by
      # map key or by server_id. Without this, renaming an mcp_servers key while
      # leaving the allowlist stale silently drops the server's tools from every
      # key referencing it - the failure mode is an empty tool search rather than
      # an error, so it is invisible until someone notices a capability is gone.
      ${pkgs.yq-go}/bin/yq -e '
        (.mcp_servers | to_entries | map([.key, (.value.server_id // .key)]) | flatten) as $known
        | [
            .litellm_settings.default_key_generate_params.object_permission.mcp_servers[]
            | select([.] - $known | length > 0)
          ]
        | length == 0
      ' "${litellmConfigFile}" > /dev/null
      ${pkgs.coreutils}/bin/chmod 0600 "${litellmConfigFile}"
    '';
  };

  systemd.services.eros-litellm-db-user = {
    description = "Set the LiteLLM PostgreSQL role password";
    after = [ "postgresql.service" ];
    requires = [ "postgresql.service" ];
    before = [ "podman-litellm.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [
      config.services.postgresql.package
      pkgs.coreutils
      pkgs.util-linux
    ];
    script = ''
      set -euo pipefail

      password_file="${config.sops.secrets.eros_litellm_db_password.path}"
      if [ ! -r "$password_file" ]; then
        echo "Missing LiteLLM database password at $password_file" >&2
        exit 1
      fi

      password="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$password_file")"
      if [ -z "$password" ]; then
        echo "LiteLLM database password is empty" >&2
        exit 1
      fi
      case "$password" in
        *[!0-9a-f]*)
          echo "LiteLLM database password must be lowercase hexadecimal" >&2
          exit 1
          ;;
      esac

      printf "ALTER ROLE litellm PASSWORD '%s';\\n" "$password" \
        | ${pkgs.util-linux}/bin/runuser -u postgres -- ${config.services.postgresql.package}/bin/psql --dbname=postgres --set=ON_ERROR_STOP=1
    '';
  };

  systemd.services.eros-context-schema = {
    description = "Initialize the Eros ontology database";
    after = [ "postgresql.service" ];
    requires = [ "postgresql.service" ];
    before = [
      "eros-context-shared.service"
      "eros-context-personal.service"
      "eros-context-work.service"
    ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "eros_context";
    };
    script = ''
      set -euo pipefail
      ${config.services.postgresql.package}/bin/psql \
        --dbname=eros_context --set=ON_ERROR_STOP=1 \
        --file=${contextBrokerSource}/schema.sql
    '';
  };

  systemd.services.eros-litellm-policy = {
    description = "Apply fail-open LiteLLM accounting and Eros context permissions";
    after = [ "podman-litellm.service" ];
    wants = [ "podman-litellm.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [
      config.services.postgresql.package
      pkgs.coreutils
      pkgs.util-linux
    ];
    script = ''
      set -euo pipefail
      for attempt in $(${pkgs.coreutils}/bin/seq 1 60); do
        if ${pkgs.util-linux}/bin/runuser -u postgres -- \
          ${config.services.postgresql.package}/bin/psql --dbname=litellm --tuples-only --command \
          'SELECT 1 FROM "LiteLLM_VerificationToken" LIMIT 1' >/dev/null 2>&1; then
          exec ${pkgs.util-linux}/bin/runuser -u postgres -- \
            ${config.services.postgresql.package}/bin/psql --dbname=litellm \
            --set=ON_ERROR_STOP=1 --file=${contextBrokerSource}/litellm-policy.sql
        fi
        ${pkgs.coreutils}/bin/sleep 2
      done
      echo "LiteLLM schema did not become ready within 120 seconds" >&2
      exit 1
    '';
  };

  systemd.services.eros-context-shared = mkContextBrokerService "shared" contextPorts.shared;
  systemd.services.eros-context-personal = mkContextBrokerService "personal" contextPorts.personal;
  systemd.services.eros-context-work = mkContextBrokerService "work" contextPorts.work;

  systemd.services.eros-context-catalog = {
    description = "Refresh the Eros capability catalog";
    after = [ "eros-context-shared.service" ];
    wants = [ "eros-context-shared.service" ];
    serviceConfig = {
      Type = "oneshot";
      User = "eros_context";
      Group = "eros_context";
    };
    environment = {
      EROS_TRUST_DOMAIN = "shared";
      EROS_KNOWLEDGE_DSN = "postgresql:///eros_context?host=/run/postgresql";
      EROS_QDRANT_URL = "http://127.0.0.1:${toString qdrantPort}";
      EROS_OLLAMA_URL = "http://127.0.0.1:11434";
      EROS_SKILL_ROOTS = contextSkillRoots;
      EROS_MODEL_ROUTES = lib.concatStringsSep "," contextModelRoutes;
      EROS_MCP_CATALOG = builtins.toJSON contextMcpCatalog;
    };
    script = "${contextBroker}/bin/python ${contextBrokerSource}/server.py sync";
  };
  systemd.timers.eros-context-catalog = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "5m";
      OnUnitActiveSec = "6h";
      Persistent = true;
    };
  };

  systemd.services.eros-spend-report = {
    description = "Generate advisory Eros spend and cache report";
    after = [
      "eros-context-schema.service"
      "eros-litellm-policy.service"
    ];
    wants = [ "eros-litellm-policy.service" ];
    serviceConfig = {
      Type = "oneshot";
      User = "eros_context";
      Group = "eros_context";
      StateDirectory = "eros-context";
    };
    environment = {
      EROS_TRUST_DOMAIN = "shared";
      EROS_LITELLM_DSN = "postgresql:///litellm?host=/run/postgresql";
      EROS_REPORT_PATH = "/var/lib/eros-context/spend-report.json";
    };
    script = "${contextBroker}/bin/python ${contextBrokerSource}/server.py report";
  };
  systemd.timers.eros-spend-report = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "10m";
      OnUnitActiveSec = "1h";
      Persistent = true;
    };
  };

  systemd.services.podman-litellm = {
    requires = [
      "eros-litellm-db-user.service"
      "eros-litellm-env.service"
      "eros-litellm-config.service"
      "ollama.service"
      "podman-qdrant.service"
    ];
    after = [
      "eros-litellm-db-user.service"
      "eros-litellm-env.service"
      "eros-litellm-config.service"
      "ollama.service"
      "podman-qdrant.service"
    ];
  };

  systemd.services.omniroute = {
    description = "OmniRoute local AI gateway";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    wantedBy = [ "multi-user.target" ];
    path = [ pkgs.nodejs_24 ];
    environment = {
      HOME = config.users.users.cdenneen.home;
      HOSTNAME = "127.0.0.1";
      PORT = toString omniroutePort;
      DATA_DIR = "${config.users.users.cdenneen.home}/.omniroute";
      # Temporary diagnostic (2026-09-02): captures full request/response
      # pipeline bytes to request_detail_logs for the anthropic-compatible/
      # bedrock-runtime empty-response investigation. The dashboard's
      # call_log_pipeline_enabled setting cannot toggle this in practice -
      # this env var is the only thing isDetailedLoggingEnabled() honors.
      # Revert once that investigation concludes; verbose and not meant to
      # run long-term.
      ENABLE_REQUEST_LOGS = "true";
    };
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = config.users.users.cdenneen.home;
      ExecStart = "${config.users.users.cdenneen.home}/.local/bin/omniroute --no-open";
      Restart = "on-failure";
      RestartSec = "5s";
    };
  };

  systemd.services.tailscale-serve-eros = {
    description = "Expose LiteLLM, OmniRoute, and Qdrant over Tailscale";
    after = [
      "tailscaled.service"
      "podman-litellm.service"
      "omniroute.service"
      "podman-qdrant.service"
    ];
    requires = [
      "tailscaled.service"
      "podman-litellm.service"
      "omniroute.service"
      "podman-qdrant.service"
    ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig.Type = "oneshot";
    path = [ pkgs.tailscale ];
    script = ''
      set -euo pipefail
      if ! ${pkgs.tailscale}/bin/tailscale status >/dev/null 2>&1; then
        echo "Tailscale is not authenticated; skipping LiteLLM serve"
        exit 0
      fi
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString litellmPort} 127.0.0.1:${toString litellmPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --https=${toString litellmHttpsPort} http://127.0.0.1:${toString litellmPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString omniroutePort} 127.0.0.1:${toString omniroutePort}
      # Shared AI Services MVP: policy-endpoint instances on Ghost/Nyx need
      # to reach Qdrant for shared-reuse retrieval/promotion
      # (shared_intelligence.py) - previously loopback-only, undiscovered
      # until the first real cross-host retrieval attempt hung on
      # POST/PUT (GET happened to work locally-only in prior testing; this
      # is the first time it's been reached from another host at all).
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString qdrantPort} 127.0.0.1:${toString qdrantPort}
    '';
  };

  services.udisks2.enable = lib.mkForce false;
  services.openssh.settings.PermitRootLogin = lib.mkForce "prohibit-password";
  users.users.cdenneen.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAII1avpzyzr4rhp/LyD9JrcO+DJP+6pBMwbOglSBXHudF cdenneen_ed25519_2024"
  ];

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

  # Matches running system (do not change after initial install)
  # Match global default; do not downgrade
  system.stateVersion = lib.mkForce "26.05";

  # Filesystems.
  # NOTE: Some upstream EC2/EFI modules also declare an ESP at /boot.
  # We force the full fileSystems attrset here so the ESP is only mounted
  # at /boot/efi; /boot must remain on the root filesystem for NixOS kernels.
  fileSystems = lib.mkForce {
    "/" = {
      device = "/dev/disk/by-uuid/f222513b-ded1-49fa-b591-20ce86a2fe7f";
      fsType = "ext4";
    };

    "/boot/efi" = {
      device = "/dev/disk/by-uuid/12CE-A600";
      fsType = "vfat";
    };
  };

  # Leave /boot on the root filesystem; only mount the ESP at /boot/efi.
  # This avoids running out of space on the ESP when storing kernels/initrd.

  # UEFI + GRUB (current system uses GRUB on EFI)
  boot.loader = {
    efi = {
      # EC2 UEFI typically does not provide persistent EFI variables.
      canTouchEfiVariables = false;
      efiSysMountPoint = "/boot/efi";
    };
    grub = {
      splashImage = lib.mkForce null;
      enable = true;
      configurationLimit = 3;
      efiSupport = true;
      # Install via the UEFI removable-media fallback path (EFI/BOOT).
      efiInstallAsRemovable = true;
      device = "nodev";
    };
  };

  # On EC2 we install GRUB as "removable" (EFI/BOOT/BOOTAA64.EFI). In that mode
  # GRUB tends to use the ESP for configuration, which is too small for storing
  # NixOS kernels/initrds across generations.
  #
  # We generate a small ESP grub.cfg that:
  # - First entry chainloads the real GRUB menu from the root filesystem.
  # - Second entry boots the currently selected system profile directly.
  #
  # Important: avoid referencing config.system.build.* here; it can create module
  # evaluation recursion. Use stable on-disk paths instead.
  boot.loader.grub.extraInstallCommands = ''
        ${pkgs.coreutils}/bin/mkdir -p "${config.boot.loader.efi.efiSysMountPoint}/grub"
        ${pkgs.coreutils}/bin/cat > "${config.boot.loader.efi.efiSysMountPoint}/grub/grub.cfg" <<'EOF'
        # Autogenerated (NixOS): ESP GRUB config for EC2.
        set timeout=1
        set timeout_style=menu
        set default=0

        function chainload_rootfs_menu {
          insmod part_gpt
          insmod ext2
          insmod search_fs_file
          if search --no-floppy --file /boot/grub/grub.cfg --set=root; then
            set prefix=($root)/boot/grub
            configfile ($root)/boot/grub/grub.cfg
          fi
        }

        function boot_current_profile {
          insmod part_gpt
          insmod ext2
          insmod search_fs_file
          insmod linux
          if search --no-floppy --file /nix/var/nix/profiles/system/init --set=root; then
            linux ($root)/nix/var/nix/profiles/system/kernel init=/nix/var/nix/profiles/system/init console=ttyS0,115200n8
            initrd ($root)/nix/var/nix/profiles/system/initrd
            boot
          fi
        }

        menuentry "NixOS (full menu)" --class nixos --unrestricted {
          chainload_rootfs_menu
          echo "GRUB: failed to chainload /boot/grub/grub.cfg"
          sleep 5
        }

        menuentry "NixOS (current system profile)" --class nixos --unrestricted {
          boot_current_profile
          echo "GRUB: failed to boot /nix/var/nix/profiles/system"
          sleep 5
        }
    EOF
  '';

  # Networking (DHCP on ens5)
  networking.useDHCP = false;
  networking.interfaces.ens5.useDHCP = true;

  # Drift guard (G-DR-PREP-1): detects when the running eros-litellm
  # config.yaml has diverged from what the CURRENTLY DEPLOYED flake pin
  # (/etc/nixos's flake.lock) would generate. Reuses the exact oneshot
  # config-render logic already in this file rather than a new
  # controller/service - this is a read-only comparison, run on demand
  # or from a monitoring check, not a background daemon.
  environment.systemPackages = [
    (pkgs.writeShellScriptBin "eros-drift-check" ''
      set -euo pipefail
      echo "Evaluating declarative config from the deployed flake pin..." >&2
      generated="$(${pkgs.nix}/bin/nix eval --raw path:/etc/nixos#nixosConfigurations.eros.config.systemd.services.eros-litellm-config.script \
        | ${pkgs.gnused}/bin/sed -n '/^model_list:/,/^EOF$/p' | ${pkgs.gnused}/bin/sed '$d')"
      live="$(${pkgs.coreutils}/bin/cat /run/eros-litellm/config.yaml)"
      if [ "$generated" = "$live" ]; then
        echo "OK: /run/eros-litellm/config.yaml matches the deployed declarative source."
        exit 0
      fi
      echo "DRIFT DETECTED: /run/eros-litellm/config.yaml differs from what the deployed" >&2
      echo "flake pin would generate. A reboot/rebuild would silently discard the live" >&2
      echo "difference shown below. Persist it to hosts/nixos/eros.nix before relying on it." >&2
      ${pkgs.diffutils}/bin/diff <(echo "$generated") <(echo "$live") || true
      exit 1
    '')
  ];
  profiles.defaults.enable = true;
}
