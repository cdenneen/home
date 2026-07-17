{
  config,
  lib,
  pkgs,
  happier,
  ...
}:
let
  ghostTunnelId = "1481e71c-a53f-4fe0-8983-468a3e0fffdf";
  ghostCloudflareCredFile = "/var/lib/cloudflared/ghost.json";
  pepsApiHost = "peps-api.denneen.net";
  pepsWebHost = "peps.denneen.net";
  pepsRepoDir = "/var/lib/peps/repo";
  pepsRuntimeDir = "/var/lib/peps";
  pepsGitRemote = "https://github.com/cdenneen/peps.git";
  pepsGitBranch = "main";
  pepsApiPort = 8787;
  pepsEnvFile = "${pepsRuntimeDir}/backend.env";
  pepsEnvLocalFile = "${pepsRuntimeDir}/backend.env.local";
  pepsHealthImportTokenFile = "${pepsRuntimeDir}/health_import_token";
  pepsStateFilePath = "/home/cdenneen/.local/state/peps-api/web-state.json";
  pepsAdminEmails = "cdenneen@gmail.com,c.denneen@gmail.com";
  wellnessApiHost = "wellness-api.denneen.net";
  wellnessRuntimeDir = "/var/lib/wellness";
  wellnessRepoDir = "${wellnessRuntimeDir}/repo";
  wellnessGitRemote = "https://github.com/cdenneen/wellness-tracker.git";
  wellnessGitBranch = "main";
  wellnessApiPort = 8797;
  wellnessSupabaseUrl = "https://kefpmmjhtdxhhhcndrnx.supabase.co";
  githubTokenFile = config.sops.secrets.github-token.path;
  openAiKeyFile = config.sops.secrets.openai_api_key.path;
  openrouterKeyFile = config.sops.secrets.openrouter_api_key.path;
  geminiKeyFile = config.sops.secrets.gemini_api_key.path;
  jarvisSupabaseDbPasswordFile = config.sops.secrets.jarvis_supbabase_db_password.path;
  gitlabRunnerTokenFile = config.sops.secrets.gitlab_com_runner_token.path;
  gitlabRunnerSecondaryTokenFile = config.sops.secrets.gitlab_com_runner_token_2.path;
  qdrantApiKeyFile = config.sops.secrets.local_qdrant_api_key.path;
  litellmMasterKeyFile = config.sops.secrets.local_litellm_master_key.path;
  litellmSaltKeyFile = config.sops.secrets.local_litellm_salt_key.path;
  postgresPasswordFile = config.sops.secrets.local_postgres_password.path;
  neo4jPasswordFile = config.sops.secrets.local_neo4j_password.path;
  redisPasswordFile = config.sops.secrets.local_redis_password.path;
  wellnessSupabasePublishableKeyFile = config.sops.secrets.wellness_supabase_publishable_key.path;
  wellnessSupabaseSecretKeyFile = config.sops.secrets.wellness_supabase_secret_key.path;
  wellnessSupabaseDbUrlFile = config.sops.secrets.wellness_supabase_db_url.path;
  supabaseAccessTokenFile = config.sops.secrets.supabase_access_token.path;
  ollamaPort = 11434;
  qdrantHttpPort = 6333;
  qdrantGrpcPort = 6334;
  litellmPort = 4000;
  postgresPort = 5432;
  neo4jHttpPort = 7474;
  neo4jBoltPort = 7687;
  redisPort = 6379;
  minioApiPort = 9000;
  minioConsolePort = 9001;
  postgresUser = "postgres";
  postgresDb = "postgres";
  neo4jUser = "neo4j";
  ollamaDataDir = "/var/lib/ollama";
  qdrantDataDir = "/var/lib/qdrant";
  minioDataDir = "/var/lib/minio";
  ghostRuntimeDir = "/run/ghost-services";
  litellmConfigFile = "${ghostRuntimeDir}/litellm/config.yaml";
  litellmEnvFile = "${ghostRuntimeDir}/litellm/env";
  qdrantEnvFile = "${ghostRuntimeDir}/qdrant/env";
  neo4jEnvFile = "${ghostRuntimeDir}/neo4j/env";
  minioEnvFile = "${ghostRuntimeDir}/minio/env";
  gitlabRunnerEnvFile = "/var/lib/gitlab-runner/runner-auth.env";
  gitlabRunnerSecondaryEnvFile = "/var/lib/gitlab-runner/runner-auth-2.env";
  gitlabRunnerDockerConfig = "/var/lib/gitlab-runner/.docker/config.json";
  postgresDataDir = "/var/lib/postgres";
  neo4jDataDir = "/var/lib/neo4j/data";
  neo4jLogsDir = "/var/lib/neo4j/logs";
  redisDataDir = "/var/lib/redis";
  minioCredentialsFile = config.sops.secrets.minio-credentials.path;
in
{
  imports = [
    ./ghost-base.nix
    happier.nixosModules.happier-server
  ];

  profiles.hmIntegrated.enable = lib.mkForce true;

  profiles.aiTools.enable = true;

  containerPresets = {
    podman.enable = true;
  };
  virtualisation.docker.enable = lib.mkForce false;
  networking = {
    firewall.trustedInterfaces = lib.mkAfter [ "podman0" ];
    nftables.tables.jarvis-container-nat = {
      family = "ip";
      content = ''
        chain prerouting {
          type nat hook prerouting priority dstnat;
          ip saddr 10.88.0.0/16 ip daddr 127.0.0.1 tcp dport 5432 dnat ip to 127.0.0.1:5432
          ip saddr 10.88.0.0/16 ip daddr 127.0.0.1 tcp dport 6379 dnat ip to 127.0.0.1:6379
          ip saddr 10.88.0.0/16 ip daddr 127.0.0.1 tcp dport 4000 dnat ip to 127.0.0.1:4000
        }
      '';
    };
  };

  users.users.cdenneen.extraGroups = lib.mkAfter [ "tailscale" ];
  users.groups.gitlab-runner = { };
  users.groups.happier-server = { };
  users.users.happier-server = {
    isSystemUser = true;
    group = "happier-server";
    home = "/var/lib/happier-server";
    createHome = true;
  };
  users.users.gitlab-runner = {
    isSystemUser = true;
    group = "gitlab-runner";
    home = "/var/lib/gitlab-runner";
    extraGroups = [ "podman" ];
  };

  environment.systemPackages = lib.mkAfter [
    pkgs.caddy
    pkgs.cloudflared
    pkgs.crane
    pkgs.nodejs_24
    pkgs.pnpm
  ];

  services = {
    tailscale = {
      enable = true;
      openFirewall = true;
      useRoutingFeatures = "client";
      extraSetFlags = [ "--accept-dns=true" ];
    };

    happier-server = {
      enable = true;
      package = happier.packages.${pkgs.stdenv.hostPlatform.system}.happier-server;
      mode = "light";
      port = 3005;
      environmentFile = "/var/lib/happier-server/happier.env";
    };

    gitlab-runner = {
      enable = true;
      settings.concurrent = 3;
      extraPackages = [
        pkgs.git
        pkgs.openssh
      ];
      services = {
        ghost = {
          authenticationTokenConfigFile = gitlabRunnerEnvFile;
          executor = "docker";
          dockerImage = "alpine:3.20";
          requestConcurrency = 2;
        };
        "ghost-2" = {
          authenticationTokenConfigFile = gitlabRunnerSecondaryEnvFile;
          executor = "docker";
          dockerImage = "alpine:3.20";
          requestConcurrency = 2;
        };
      };
    };

    ollama = {
      enable = true;
      home = ollamaDataDir;
      models = "${ollamaDataDir}/models";
      host = "127.0.0.1";
      port = ollamaPort;
      user = "ollama";
      group = "ollama";
    };

    postgresql = {
      enable = true;
      package = pkgs.postgresql_16;
      dataDir = postgresDataDir;
      enableTCPIP = true;
      settings = {
        port = postgresPort;
        listen_addresses = lib.mkForce "127.0.0.1";
      };
      authentication = ''
        local all postgres peer
        local all all scram-sha-256
        host all all 127.0.0.1/32 scram-sha-256
        host all all 10.88.0.0/16 scram-sha-256
      '';
    };

    redis.servers."" = {
      enable = true;
      port = redisPort;
      bind = "127.0.0.1";
      requirePassFile = redisPasswordFile;
      settings = {
        dir = redisDataDir;
        "protected-mode" = "yes";
      };
    };

    cloudflared = {
      enable = true;
      tunnels."${ghostTunnelId}" = {
        credentialsFile = ghostCloudflareCredFile;
        ingress = {
          "${pepsApiHost}" = "http://127.0.0.1:${toString pepsApiPort}";
          "${pepsWebHost}" = "http://127.0.0.1:${toString pepsApiPort}";
          "${wellnessApiHost}" = "http://127.0.0.1:${toString wellnessApiPort}";
          "ai-dev.denneen.net" = "http://127.0.0.1:3000";
          "ai.denneen.net" = "http://127.0.0.1:3001";
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
    qdrant = {
      image = "qdrant/qdrant:latest";
      ports = [
        "127.0.0.1:${toString qdrantHttpPort}:6333"
        "127.0.0.1:${toString qdrantGrpcPort}:6334"
      ];
      volumes = [ "${qdrantDataDir}:/qdrant/storage:U" ];
      extraOptions = [ "--env-file=${qdrantEnvFile}" ];
      autoStart = true;
    };

    litellm = {
      image = "ghcr.io/berriai/litellm:latest";
      ports = [ ];
      volumes = [ "${litellmConfigFile}:/app/config.yaml:ro" ];
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
        "${toString litellmPort}"
      ];
      environment = {
        LITELLM_CONFIG = "/app/config.yaml";
        LITELLM_PORT = toString litellmPort;
      };
      autoStart = true;
    };

    neo4j = {
      image = "neo4j:5";
      ports = [
        "127.0.0.1:${toString neo4jHttpPort}:7474"
        "127.0.0.1:${toString neo4jBoltPort}:7687"
      ];
      volumes = [
        "${neo4jDataDir}:/data:U"
        "${neo4jLogsDir}:/logs:U"
      ];
      extraOptions = [ "--env-file=${neo4jEnvFile}" ];
      autoStart = true;
    };

    minio = {
      image = "minio/minio:latest";
      ports = [
        "127.0.0.1:${toString minioApiPort}:9000"
        "127.0.0.1:${toString minioConsolePort}:9001"
      ];
      volumes = [ "${minioDataDir}:/data:U" ];
      extraOptions = [ "--env-file=${minioEnvFile}" ];
      cmd = [
        "server"
        "/data"
        "--console-address"
        ":9001"
      ];
      autoStart = true;
    };

  };

  sops.secrets.ghost_cloudflare_tunnel_token = {
    owner = "root";
    group = "root";
    mode = "0400";
  };
  sops.secrets.cdenneen_ed25519_2024 = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.openai_api_key = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.gitlab_com_runner_token = {
    sopsFile = ../../secrets/ghost.yaml;
    key = "gitlab_com_runner_token";
    owner = "root";
    group = "root";
    mode = "0400";
  };
  sops.secrets.gitlab_com_runner_token_2 = {
    sopsFile = ../../secrets/ghost.yaml;
    key = "gitlab_com_runner_token_2";
    owner = "root";
    group = "root";
    mode = "0400";
  };
  sops.secrets.local_qdrant_api_key = {
    sopsFile = ../../secrets/ghost.yaml;
    key = "local_qdrant_api_key";
    owner = "root";
    group = "root";
    mode = "0400";
  };
  sops.secrets.local_litellm_master_key = {
    sopsFile = ../../secrets/ghost.yaml;
    key = "local_litellm_master_key";
    owner = "root";
    group = "root";
    mode = "0400";
  };
  sops.secrets.local_litellm_salt_key = {
    sopsFile = ../../secrets/ghost.yaml;
    key = "local_litellm_salt_key";
    owner = "root";
    group = "root";
    mode = "0400";
  };
  sops.secrets.openrouter_api_key = {
    sopsFile = ../../secrets/jarvis.yaml;
    key = "openrouter_api_key";
    owner = "root";
    group = "root";
    mode = "0400";
  };
  sops.secrets.jarvis_supbabase_db_password = {
    sopsFile = ../../secrets/jarvis.yaml;
    key = "jarvis_supbabase_db_password";
    owner = "root";
    group = "root";
    mode = "0400";
  };
  sops.secrets.local_postgres_password = {
    sopsFile = ../../secrets/ghost.yaml;
    key = "local_postgres_password";
    owner = "root";
    group = "root";
    mode = "0400";
  };
  sops.secrets.local_neo4j_password = {
    sopsFile = ../../secrets/ghost.yaml;
    key = "local_neo4j_password";
    owner = "root";
    group = "root";
    mode = "0400";
  };
  sops.secrets.local_redis_password = {
    sopsFile = ../../secrets/ghost.yaml;
    key = "local_redis_password";
    owner = "redis";
    group = "redis";
    mode = "0400";
  };
  sops.secrets.gemini_api_key = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
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
  sops.secrets.minio-credentials = {
    owner = "root";
    group = "root";
    mode = "0400";
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/cloudflared 0700 root root -"
    "d /var/lib/happier-server 0750 happier-server happier-server -"
    "d ${pepsRuntimeDir} 0750 cdenneen users -"
    "d ${pepsRepoDir} 0750 cdenneen users -"
    "d ${wellnessRuntimeDir} 0750 cdenneen users -"
    "d ${wellnessRepoDir} 0750 cdenneen users -"
    "d ${ollamaDataDir} 0750 ollama ollama -"
    "d ${qdrantDataDir} 0750 root root -"
    "d /var/lib/postgres 0700 postgres postgres -"
    "d /var/lib/neo4j 0750 root root -"
    "d ${neo4jDataDir} 0750 root root -"
    "d ${neo4jLogsDir} 0750 root root -"
    "d ${redisDataDir} 0750 redis redis -"
    "d ${minioDataDir} 0750 root root -"
  ];

  systemd.services.gitlab-runner = {
    after = [
      "gitlab-runner-env.service"
      "gitlab-runner-docker-auth.service"
    ];
    requires = [
      "gitlab-runner-env.service"
      "gitlab-runner-docker-auth.service"
    ];
    serviceConfig = {
      DynamicUser = lib.mkForce false;
      User = "gitlab-runner";
      Group = "gitlab-runner";
    };
  };

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
      ${pkgs.coreutils}/bin/install -d -m 0750 -o happier-server -g happier-server /var/lib/happier-server

      if [ ! -s "$env_file" ] || ! ${pkgs.gnugrep}/bin/grep -q '^HANDY_MASTER_SECRET=' "$env_file"; then
        secret="$(${pkgs.openssl}/bin/openssl rand -base64 48 | ${pkgs.coreutils}/bin/tr -d '\n\r')"
        ${pkgs.coreutils}/bin/install -m 600 /dev/null "$env_file"
        printf 'HANDY_MASTER_SECRET=%s\n' "$secret" > "$env_file"
      fi

      ${pkgs.coreutils}/bin/chmod 600 "$env_file"
    '';
  };

  systemd.services.litellm-env = {
    description = "Render litellm env file";
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

      env_dir="$(${pkgs.coreutils}/bin/dirname "${litellmEnvFile}")"
      ${pkgs.coreutils}/bin/mkdir -p "$env_dir"

      read_secret() {
        secret_file="$1"
        secret_name="$2"
        if [ ! -r "$secret_file" ]; then
          echo "Missing $secret_name at $secret_file" >&2
          exit 1
        fi
        value="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "$secret_file")"
        if [ -z "$value" ]; then
          echo "$secret_name at $secret_file is empty" >&2
          exit 1
        fi
        printf '%s' "$value"
      }

      openai_key="$(read_secret "${openAiKeyFile}" "OpenAI key")"
      openrouter_key="$(read_secret "${openrouterKeyFile}" "OpenRouter key")"
      gemini_key="$(read_secret "${geminiKeyFile}" "Gemini key")"
      db_password="$(read_secret "${jarvisSupabaseDbPasswordFile}" "Jarvis Supabase DB password")"
      master_key="$(read_secret "${litellmMasterKeyFile}" "LiteLLM master key")"
      salt_key="$(read_secret "${litellmSaltKeyFile}" "LiteLLM salt key")"
      qdrant_api_key="$(read_secret "${qdrantApiKeyFile}" "Qdrant API key")"
      db_url="postgresql://postgres.ysxipmxwfupqzywhevji:$db_password@aws-1-us-east-2.pooler.supabase.com:5432/postgres?options=-csearch_path%3Dlitellm"

      ${pkgs.coreutils}/bin/install -m 600 /dev/null "${litellmEnvFile}"
      {
        printf 'OPENAI_API_KEY=%s\n' "$openai_key"
        printf 'OPENROUTER_API_KEY=%s\n' "$openrouter_key"
        printf 'GEMINI_API_KEY=%s\n' "$gemini_key"
        printf 'LITELLM_DATABASE_URL=%s\n' "$db_url"
        printf 'LITELLM_MASTER_KEY=%s\n' "$master_key"
        printf 'LITELLM_SALT_KEY=%s\n' "$salt_key"
        printf 'OLLAMA_API_BASE=%s\n' "http://127.0.0.1:${toString ollamaPort}"
        printf 'QDRANT_API_BASE=%s\n' "http://127.0.0.1:${toString qdrantHttpPort}"
        printf 'QDRANT_API_KEY=%s\n' "$qdrant_api_key"
      } > "${litellmEnvFile}"
    '';
  };

  systemd.services.litellm-config = {
    description = "Render litellm config file";
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

      config_dir="$(${pkgs.coreutils}/bin/dirname "${litellmConfigFile}")"
      ${pkgs.coreutils}/bin/mkdir -p "$config_dir"
      ${pkgs.coreutils}/bin/install -m 600 /dev/null "${litellmConfigFile}"
      cat > "${litellmConfigFile}" <<'EOF'
      model_list:
        - model_name: jarvis-router
          litellm_params:
            model: openrouter/openrouter/free
            api_key: os.environ/OPENROUTER_API_KEY

        - model_name: jarvis-coder
          litellm_params:
            model: openrouter/openrouter/free
            api_key: os.environ/OPENROUTER_API_KEY

        - model_name: jarvis-coding-free
          litellm_params:
            model: openrouter/poolside/laguna-xs.2:free
            api_key: os.environ/OPENROUTER_API_KEY

        - model_name: jarvis-coding-deep-free
          litellm_params:
            model: openrouter/poolside/laguna-m.1:free
            api_key: os.environ/OPENROUTER_API_KEY

        - model_name: openrouter-free
          litellm_params:
            model: openrouter/openrouter/free
            api_key: os.environ/OPENROUTER_API_KEY

        - model_name: openrouter/*
          litellm_params:
            model: openrouter/*
            api_key: os.environ/OPENROUTER_API_KEY

        - model_name: openrouter-embed
          litellm_params:
            model: openrouter/nvidia/llama-nemotron-embed-vl-1b-v2:free
            api_key: os.environ/OPENROUTER_API_KEY

        - model_name: gemini/*
          litellm_params:
            model: gemini/*
            api_key: os.environ/GEMINI_API_KEY

        - model_name: openai/*
          litellm_params:
            model: openai/*
            api_key: os.environ/OPENAI_API_KEY

        - model_name: local-embed
          litellm_params:
            model: ollama/nomic-embed-text
            api_base: http://127.0.0.1:${toString ollamaPort}

      litellm_settings:
        cache: true
        check_provider_endpoint: true
        cache_params:
          type: qdrant-semantic
          cache_policy: semantic
          similarity_threshold: 0.85
          qdrant_semantic_cache_embedding_model: openrouter-embed
          qdrant_collection_name: litellm_semantic_cache
          qdrant_semantic_cache_vector_size: 2048

      router_settings:
        fallbacks:
          - jarvis-router:
              - openrouter/*
              - gemini/*
              - openai/*
          - jarvis-coder:
              - jarvis-coding-free
              - jarvis-coding-deep-free
              - openrouter/*
              - gemini/*
              - openai/*
        num_retries: 2
        timeout: 90

      general_settings:
        database_url: os.environ/LITELLM_DATABASE_URL
        allow_requests_on_db_unavailable: true

      jarvis_profiles:
        default: conversation
        profiles:
          - name: conversation
            primary:
              - jarvis-router
            fallback:
              - openrouter/*
              - gemini/*
              - openai/*
          - name: coding
            primary:
              - jarvis-coder
            fallback:
              - jarvis-coding-free
              - jarvis-coding-deep-free
              - openrouter/*
              - gemini/*
              - openai/*
          - name: architecture
            primary:
              - jarvis-coding-deep-free
            fallback:
              - jarvis-coding-free
              - jarvis-coder
              - openrouter/*
              - gemini/*
              - openai/*
        escalation:
          context_tokens_gt: 24000
          estimated_repo_files_gt: 100
          confidence_below: 0.75
          max_local_retries: 2
          local_timeout_seconds: 90
          local_failure:
            escalate: true
        cloud_routing:
          prefer_openrouter: true
          openrouter_free_only: true
          use_direct_vendor_only_if:
            - openrouter_unavailable
            - vendor_specific_feature_required
      EOF
    '';
  };

  systemd.services.qdrant-env = {
    description = "Render qdrant env file";
    before = [ "podman-qdrant.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail

      env_dir="$(${pkgs.coreutils}/bin/dirname "${qdrantEnvFile}")"
      ${pkgs.coreutils}/bin/mkdir -p "$env_dir"

      if [ ! -r "${qdrantApiKeyFile}" ]; then
        echo "Missing Qdrant API key at ${qdrantApiKeyFile}" >&2
        exit 1
      fi

      api_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${qdrantApiKeyFile}")"
      if [ -z "$api_key" ]; then
        echo "Qdrant API key at ${qdrantApiKeyFile} is empty" >&2
        exit 1
      fi

      ${pkgs.coreutils}/bin/install -m 600 /dev/null "${qdrantEnvFile}"
      printf 'QDRANT__SERVICE__API_KEY=%s\n' "$api_key" > "${qdrantEnvFile}"
    '';
  };

  systemd.services.postgresql-data-permissions = {
    description = "Ensure Postgres data directory ownership";
    before = [ "postgresql.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail

      ${pkgs.coreutils}/bin/install -d -m 0700 -o postgres -g postgres "${postgresDataDir}"
      ${pkgs.coreutils}/bin/chown -R postgres:postgres "${postgresDataDir}"
      ${pkgs.coreutils}/bin/chmod 0700 "${postgresDataDir}"
    '';
  };

  systemd.services.redis-data-permissions = {
    description = "Ensure Redis data directory ownership";
    before = [ "redis.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail

      ${pkgs.coreutils}/bin/install -d -m 0750 -o redis -g redis "${redisDataDir}"
      ${pkgs.coreutils}/bin/chown -R redis:redis "${redisDataDir}"
      ${pkgs.coreutils}/bin/chmod 0750 "${redisDataDir}"
      ${pkgs.coreutils}/bin/install -m 0600 -o redis -g redis /dev/null "${redisDataDir}/redis.conf"
      printf 'include "/run/redis/nixos.conf"\n' > "${redisDataDir}/redis.conf"
    '';
  };

  systemd.services.ollama-data-permissions = {
    description = "Ensure Ollama data directory ownership";
    before = [ "ollama.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail

      data_dir="$(${pkgs.coreutils}/bin/readlink -f "${ollamaDataDir}")"
      ${pkgs.coreutils}/bin/install -d -m 0750 -o ollama -g ollama "$data_dir"
      ${pkgs.coreutils}/bin/install -d -m 0750 -o ollama -g ollama "$data_dir/models"
      ${pkgs.coreutils}/bin/chown -R ollama:ollama "$data_dir"
      ${pkgs.coreutils}/bin/chmod 0750 "$data_dir"
      ${pkgs.coreutils}/bin/chmod 0750 "$data_dir/models"
    '';
  };

  systemd.services.gitlab-runner-env = {
    description = "Render gitlab-runner auth env file";
    before = [ "gitlab-runner.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail

      if [ ! -r "${gitlabRunnerTokenFile}" ]; then
        echo "Missing GitLab runner token at ${gitlabRunnerTokenFile}" >&2
        exit 1
      fi

      token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${gitlabRunnerTokenFile}")"
      if [ -z "$token" ]; then
        echo "GitLab runner token at ${gitlabRunnerTokenFile} is empty" >&2
        exit 1
      fi

      if [ ! -r "${gitlabRunnerSecondaryTokenFile}" ]; then
        echo "Missing GitLab runner token at ${gitlabRunnerSecondaryTokenFile}" >&2
        exit 1
      fi

      secondary_token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${gitlabRunnerSecondaryTokenFile}")"
      if [ -z "$secondary_token" ]; then
        echo "GitLab runner token at ${gitlabRunnerSecondaryTokenFile} is empty" >&2
        exit 1
      fi

      ${pkgs.coreutils}/bin/install -d -m 0750 -o gitlab-runner -g gitlab-runner /var/lib/gitlab-runner
      ${pkgs.coreutils}/bin/install -m 600 -o gitlab-runner -g gitlab-runner /dev/null "${gitlabRunnerEnvFile}"
      ${pkgs.coreutils}/bin/install -m 600 -o gitlab-runner -g gitlab-runner /dev/null "${gitlabRunnerSecondaryEnvFile}"
      printf 'CI_SERVER_URL=%s\n' "https://gitlab.com/" > "${gitlabRunnerEnvFile}"
      printf 'CI_SERVER_TOKEN=%s\n' "$token" >> "${gitlabRunnerEnvFile}"
      printf 'CI_SERVER_URL=%s\n' "https://gitlab.com/" > "${gitlabRunnerSecondaryEnvFile}"
      printf 'CI_SERVER_TOKEN=%s\n' "$secondary_token" >> "${gitlabRunnerSecondaryEnvFile}"
    '';
  };

  systemd.services.gitlab-runner-docker-auth = {
    description = "Render gitlab-runner Docker auth config";
    before = [ "gitlab-runner.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail
      config_dir="$(${pkgs.coreutils}/bin/dirname "${gitlabRunnerDockerConfig}")"

      ${pkgs.coreutils}/bin/install -d -m 0700 -o gitlab-runner -g gitlab-runner "$config_dir"
      ${pkgs.coreutils}/bin/install -m 0600 -o gitlab-runner -g gitlab-runner /dev/null "${gitlabRunnerDockerConfig}"
      printf '{}' > "${gitlabRunnerDockerConfig}"
    '';
  };

  systemd.services.postgresql-password = {
    description = "Ensure postgres password";
    after = [ "postgresql.service" ];
    requires = [ "postgresql.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [
      pkgs.coreutils
      pkgs.util-linux
    ];
    script = ''
      set -euo pipefail

      if [ ! -r "${postgresPasswordFile}" ]; then
        echo "Missing Postgres password at ${postgresPasswordFile}" >&2
        exit 1
      fi

      password="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${postgresPasswordFile}")"
      if [ -z "$password" ]; then
        echo "Postgres password at ${postgresPasswordFile} is empty" >&2
        exit 1
      fi

      for _ in $(seq 1 60); do
        if ${pkgs.util-linux}/bin/runuser -u postgres -- ${config.services.postgresql.package}/bin/pg_isready -d "${postgresDb}" >/dev/null 2>&1; then
          break
        fi
        sleep 2
      done

      ${pkgs.util-linux}/bin/runuser -u postgres -- \
        ${config.services.postgresql.package}/bin/psql -d "${postgresDb}" -v ON_ERROR_STOP=1 \
        -c "ALTER USER ${postgresUser} WITH PASSWORD '$password';"
    '';
  };

  systemd.services.neo4j-env = {
    description = "Render neo4j env file";
    before = [ "podman-neo4j.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail

      env_dir="$(${pkgs.coreutils}/bin/dirname "${neo4jEnvFile}")"
      ${pkgs.coreutils}/bin/mkdir -p "$env_dir"

      if [ ! -r "${neo4jPasswordFile}" ]; then
        echo "Missing Neo4j password at ${neo4jPasswordFile}" >&2
        exit 1
      fi

      password="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${neo4jPasswordFile}")"
      if [ -z "$password" ]; then
        echo "Neo4j password at ${neo4jPasswordFile} is empty" >&2
        exit 1
      fi

      ${pkgs.coreutils}/bin/install -m 600 /dev/null "${neo4jEnvFile}"
      printf 'NEO4J_AUTH=%s/%s\n' "${neo4jUser}" "$password" > "${neo4jEnvFile}"
    '';
  };

  systemd.services.minio-env = {
    description = "Render MinIO env file";
    before = [ "podman-minio.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [
      pkgs.coreutils
      pkgs.gnugrep
    ];
    script = ''
      set -euo pipefail

      env_dir="$(${pkgs.coreutils}/bin/dirname "${minioEnvFile}")"
      ${pkgs.coreutils}/bin/mkdir -p "$env_dir"

      if [ ! -r "${minioCredentialsFile}" ]; then
        echo "Missing MinIO credentials at ${minioCredentialsFile}" >&2
        exit 1
      fi

      ${pkgs.coreutils}/bin/install -m 600 /dev/null "${minioEnvFile}"

      if ${pkgs.gnugrep}/bin/grep -q '=' "${minioCredentialsFile}"; then
        ${pkgs.coreutils}/bin/cat "${minioCredentialsFile}" > "${minioEnvFile}"
        exit 0
      fi

      if ${pkgs.gnugrep}/bin/grep -q ':' "${minioCredentialsFile}"; then
        creds_line="$(${pkgs.coreutils}/bin/head -n 1 "${minioCredentialsFile}")"
        minio_user="''${creds_line%%:*}"
        minio_password="''${creds_line#*:}"

        if [ -z "$minio_user" ] || [ -z "$minio_password" ]; then
          echo "MinIO credentials file is missing user or password" >&2
          exit 1
        fi

        printf 'MINIO_ROOT_USER=%s\n' "$minio_user" > "${minioEnvFile}"
        printf 'MINIO_ROOT_PASSWORD=%s\n' "$minio_password" >> "${minioEnvFile}"
        exit 0
      fi

      echo "MinIO credentials file must contain either MINIO_ROOT_* env vars or user:password" >&2
      exit 1
    '';
  };

  systemd.services.jarvis-ghost-cleanup = {
    description = "Remove stale Jarvis Tailscale podman overrides";
    before = [
      "podman-litellm.service"
      "podman-qdrant.service"
      "podman-neo4j.service"
    ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [
      pkgs.coreutils
      pkgs.systemd
    ];
    script = ''
      set -euo pipefail

      for unit in podman-litellm podman-qdrant podman-neo4j; do
        dropin_dir="/run/systemd/system/''${unit}.service.d"
        if [ -d "$dropin_dir" ]; then
          ${pkgs.coreutils}/bin/rm -f "$dropin_dir"/tailscale-*.conf
          ${pkgs.coreutils}/bin/rmdir --ignore-fail-on-non-empty "$dropin_dir" 2>/dev/null || true
        fi
      done

      ${pkgs.coreutils}/bin/rm -f /var/lib/jarvis-ghost/podman-*-start-tailscale.sh

      ${pkgs.systemd}/bin/systemctl daemon-reload
    '';
  };

  systemd.services.tailscale-serve-ghost = {
    description = "Expose local services via Tailscale serve";
    after = [
      "network-online.target"
      "tailscaled.service"
    ];
    wants = [
      "network-online.target"
      "tailscaled.service"
    ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [ pkgs.tailscale ];
    script = ''
      set -euo pipefail

      ${pkgs.tailscale}/bin/tailscale status >/dev/null
      ${pkgs.tailscale}/bin/tailscale serve reset


      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp 3005 127.0.0.1:3005
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString pepsApiPort} 127.0.0.1:${toString pepsApiPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString wellnessApiPort} 127.0.0.1:${toString wellnessApiPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString litellmPort} 127.0.0.1:${toString litellmPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString neo4jHttpPort} 127.0.0.1:${toString neo4jHttpPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString neo4jBoltPort} 127.0.0.1:${toString neo4jBoltPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString postgresPort} 127.0.0.1:${toString postgresPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString qdrantHttpPort} 127.0.0.1:${toString qdrantHttpPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString qdrantGrpcPort} 127.0.0.1:${toString qdrantGrpcPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString redisPort} 127.0.0.1:${toString redisPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString minioApiPort} 127.0.0.1:${toString minioApiPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString minioConsolePort} 127.0.0.1:${toString minioConsolePort}
    '';
  };

  systemd.services.podman-litellm = {
    requires = [
      "litellm-env.service"
      "litellm-config.service"
      "jarvis-ghost-cleanup.service"
    ];
    after = [
      "litellm-env.service"
      "litellm-config.service"
      "jarvis-ghost-cleanup.service"
    ];
  };

  systemd.services.podman-qdrant = {
    requires = [
      "qdrant-env.service"
      "jarvis-ghost-cleanup.service"
    ];
    after = [
      "qdrant-env.service"
      "jarvis-ghost-cleanup.service"
    ];
  };

  systemd.services.podman-neo4j = {
    requires = [
      "neo4j-env.service"
      "jarvis-ghost-cleanup.service"
    ];
    after = [
      "neo4j-env.service"
      "jarvis-ghost-cleanup.service"
    ];
  };

  systemd.services.podman-minio = {
    requires = [ "minio-env.service" ];
    after = [ "minio-env.service" ];
  };

  systemd.services.happier-server = {
    requires = [ "happier-env-bootstrap.service" ];
    after = [ "happier-env-bootstrap.service" ];
    serviceConfig = {
      DynamicUser = lib.mkForce false;
      User = "happier-server";
      Group = "happier-server";
      Environment = [
        "HAPPIER_SERVER_HOST=127.0.0.1"
        "METRICS_ENABLED=false"
      ];
    };
  };

  systemd.services.happier-server-migrate.serviceConfig = {
    DynamicUser = lib.mkForce false;
    User = "happier-server";
    Group = "happier-server";
  };

  systemd.services.happier-server-sqlite-wal.serviceConfig = {
    DynamicUser = lib.mkForce false;
    User = "happier-server";
    Group = "happier-server";
  };

  systemd.services.ollama.serviceConfig.DynamicUser = lib.mkForce false;

  systemd.services.peps-sync = {
    description = "Sync peps repo from GitHub";
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

      if [ ! -r "${githubTokenFile}" ]; then
        echo "Missing GitHub token at ${githubTokenFile} for peps clone auth" >&2
        exit 1
      fi

      github_token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${githubTokenFile}")"
      if [ -z "$github_token" ]; then
        echo "GitHub token at ${githubTokenFile} is empty" >&2
        exit 1
      fi

      auth_header="$(${pkgs.coreutils}/bin/printf 'x-access-token:%s' "$github_token" | ${pkgs.coreutils}/bin/base64 | ${pkgs.coreutils}/bin/tr -d '\n')"
      git_auth=(
        -c
        "http.extraHeader=Authorization: Basic $auth_header"
      )

      if [ ! -d "${pepsRepoDir}/.git" ]; then
        rm -rf "${pepsRepoDir}"
        git "''${git_auth[@]}" clone --branch "${pepsGitBranch}" "${pepsGitRemote}" "${pepsRepoDir}"
      fi

      cd "${pepsRepoDir}"
      git reset --hard HEAD
      git clean -fd
      git remote set-url origin "${pepsGitRemote}"
      git "''${git_auth[@]}" fetch --prune origin "${pepsGitBranch}"
      git checkout -B "${pepsGitBranch}" "origin/${pepsGitBranch}"
      git reset --hard "origin/${pepsGitBranch}"

      peps_server_file="${pepsRepoDir}/src/api/server.ts"
      if [ ! -f "$peps_server_file" ]; then
        echo "peps-api: server file not found at $peps_server_file" >&2
        exit 1
      fi

      ${pkgs.perl}/bin/perl -0pi -e "s/app\.listen\(PORT, \(\) => \{/app.listen(PORT, process.env.API_BIND_HOST || '127.0.0.1', () => {/" "$peps_server_file"

      if ! ${pkgs.gnugrep}/bin/grep -Fq "process.env.API_BIND_HOST || '127.0.0.1'" "$peps_server_file"; then
        echo "peps-api: failed to patch loopback bind in $peps_server_file" >&2
        exit 1
      fi
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

  systemd.services.wellness-sync = {
    description = "Sync wellness repo from GitHub";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type = "oneshot";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = wellnessRuntimeDir;
    };
    path = [
      pkgs.coreutils
      pkgs.git
      pkgs.openssh
    ];
    script = ''
      set -euo pipefail

      if [ ! -r "${githubTokenFile}" ]; then
        echo "Missing GitHub token at ${githubTokenFile} for wellness clone auth" >&2
        exit 1
      fi

      github_token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${githubTokenFile}")"
      if [ -z "$github_token" ]; then
        echo "GitHub token at ${githubTokenFile} is empty" >&2
        exit 1
      fi

      auth_header="$(${pkgs.coreutils}/bin/printf 'x-access-token:%s' "$github_token" | ${pkgs.coreutils}/bin/base64 | ${pkgs.coreutils}/bin/tr -d '\n')"
      git_auth=(
        -c
        "http.extraHeader=Authorization: Basic $auth_header"
      )

      if [ ! -d "${wellnessRepoDir}/.git" ]; then
        rm -rf "${wellnessRepoDir}"
        git "''${git_auth[@]}" clone --branch "${wellnessGitBranch}" "${wellnessGitRemote}" "${wellnessRepoDir}"
      fi

      cd "${wellnessRepoDir}"
      git remote set-url origin "${wellnessGitRemote}"
      git "''${git_auth[@]}" fetch --prune origin "${wellnessGitBranch}"
      git checkout -B "${wellnessGitBranch}" "origin/${wellnessGitBranch}"
      git reset --hard "origin/${wellnessGitBranch}"
    '';
  };

  systemd.timers.wellness-sync = {
    description = "Periodic wellness repo sync";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "3m";
      OnUnitActiveSec = "10m";
      Unit = "wellness-sync.service";
    };
  };

  systemd.services.peps-runtime-env = {
    description = "Generate peps runtime env";
    before = [ "peps-api.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
    ];
    serviceConfig = {
      Type = "oneshot";
      UMask = "0077";
    };
    script = ''
      set -euo pipefail

      env_file="${pepsEnvFile}"
      legacy_env_file="${pepsRepoDir}/deploy/backend/.env"
      tmp_env="$(${pkgs.coreutils}/bin/mktemp "${pepsRuntimeDir}/backend.env.XXXXXX")"

      cleanup() {
        ${pkgs.coreutils}/bin/rm -f "$tmp_env"
      }
      trap cleanup EXIT

      write_var() {
        printf '%s=%s\n' "$1" "$2" >> "$tmp_env"
      }

      legacy_value() {
        key="$1"
        if [ -r "$legacy_env_file" ]; then
          while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in
              "$key="*)
                printf '%s' "''${line#*=}"
                return 0
                ;;
            esac
          done < "$legacy_env_file"
        fi
        return 1
      }

      write_var API_PORT "${toString pepsApiPort}"
      write_var API_BIND_HOST "127.0.0.1"
      write_var AUTH_REQUIRED "true"
      write_var AUTH_ADMIN_EMAILS "${pepsAdminEmails}"
      write_var SUPABASE_URL "${wellnessSupabaseUrl}"
      write_var NEXT_PUBLIC_SUPABASE_URL "${wellnessSupabaseUrl}"
      write_var VITE_SUPABASE_URL "${wellnessSupabaseUrl}"
      write_var PEPS_STATE_PROVIDER "supabase"
      write_var PEPS_STATE_TABLE "peps_app_state"
      write_var PEPS_STATE_ROW_ID "global"
      write_var PEPS_STATE_META_ROW_ID "_meta"
      write_var PEPS_STATE_FILE_PATH "${pepsStateFilePath}"
      write_var PEPS_DOSE_TABLE "peps_dose_checkins"
      write_var PEPS_PROGRESS_PHOTOS_TABLE "peps_progress_photos"
      write_var PEPS_PROGRESS_PHOTOS_BUCKET "peps-progress-photos"

      if [ -r "${wellnessSupabasePublishableKeyFile}" ]; then
        supabase_publishable_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${wellnessSupabasePublishableKeyFile}")"
        if [ -n "$supabase_publishable_key" ]; then
          write_var SUPABASE_PUBLISHABLE_KEY "$supabase_publishable_key"
          write_var SUPABASE_ANON_KEY "$supabase_publishable_key"
          write_var NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY "$supabase_publishable_key"
          write_var VITE_SUPABASE_PUBLISHABLE_KEY "$supabase_publishable_key"
          write_var EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY "$supabase_publishable_key"
          write_var EXPO_PUBLIC_SUPABASE_ANON_KEY "$supabase_publishable_key"
        fi
      fi

      if [ -r "${wellnessSupabaseSecretKeyFile}" ]; then
        supabase_secret_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${wellnessSupabaseSecretKeyFile}")"
        if [ -n "$supabase_secret_key" ] && [ "$supabase_secret_key" != "REPLACE_WITH_SB_SECRET_KEY" ]; then
          write_var SUPABASE_SECRET_KEY "$supabase_secret_key"
          write_var SUPABASE_SERVICE_ROLE_KEY "$supabase_secret_key"
        fi
      fi

      if [ -r "${geminiKeyFile}" ]; then
        gemini_api_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${geminiKeyFile}")"
        if [ -n "$gemini_api_key" ]; then
          write_var GEMINI_API_KEY "$gemini_api_key"
          write_var GOOGLE_API_KEY "$gemini_api_key"
        fi
      fi

      health_import_token=""
      if [ -r "${pepsHealthImportTokenFile}" ]; then
        health_import_token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${pepsHealthImportTokenFile}")"
      else
        health_import_token="$(legacy_value HEALTH_IMPORT_TOKEN || true)"
      fi
      if [ -n "$health_import_token" ]; then
        write_var HEALTH_IMPORT_TOKEN "$health_import_token"
      fi

      for key in \
        GEMINI_MODEL \
        GEMINI_API_BASE_URL \
        USDA_FOODDATA_CENTRAL_API_KEY \
        FOODDATA_CENTRAL_API_KEY \
        WEB_PUSH_VAPID_SUBJECT \
        WEB_PUSH_VAPID_PUBLIC_KEY \
        WEB_PUSH_VAPID_PRIVATE_KEY
      do
        value="$(legacy_value "$key" || true)"
        if [ -n "$value" ]; then
          write_var "$key" "$value"
        fi
      done

      ${pkgs.coreutils}/bin/chmod 0400 "$tmp_env"
      ${pkgs.coreutils}/bin/mv -f "$tmp_env" "$env_file"
    '';
  };

  systemd.services.peps-api = {
    description = "Peps API/web runtime";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "tailscaled.service"
      "peps-sync.service"
      "peps-runtime-env.service"
    ];
    wants = [
      "network-online.target"
      "tailscaled.service"
    ];
    requires = [
      "peps-sync.service"
      "peps-runtime-env.service"
    ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.nodejs_24
    ];
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = pepsRepoDir;
      Restart = "always";
      RestartSec = "10s";
      TimeoutStartSec = "20min";
      EnvironmentFile = [
        pepsEnvFile
        "-${pepsEnvLocalFile}"
      ];
      Environment = [
        "HOME=/home/cdenneen"
      ];
    };
    script = ''
      set -euo pipefail

      if [ ! -f package.json ]; then
        echo "peps-api: repository not found at ${pepsRepoDir}" >&2
        exit 1
      fi

      if [ ! -x node_modules/.bin/tsx ]; then
        npm install --include=dev --no-audit --no-fund
      fi

      npm run web:build
      exec npm run api:start
    '';
  };

  systemd.services.wellness-api = {
    description = "Wellness Tracker API";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "wellness-sync.service"
    ];
    wants = [
      "network-online.target"
      "wellness-sync.service"
    ];
    requires = [ "wellness-sync.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.nodejs_24
    ];
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = wellnessRepoDir;
      Restart = "always";
      RestartSec = "10s";
      TimeoutStartSec = "15min";
      Environment = [
        "HOME=/home/cdenneen"
        "EXPO_PUBLIC_API_BASE_URL=https://${wellnessApiHost}"
        "EXPO_PUBLIC_PEPS_API_BASE_URL=https://${pepsApiHost}"
        "EXPO_PUBLIC_SUPABASE_URL=${wellnessSupabaseUrl}"
        "API_BIND_HOST=127.0.0.1"
        "API_PORT=${toString wellnessApiPort}"
        "CORS_ALLOW_ORIGINS=*"
        "SUPABASE_URL=${wellnessSupabaseUrl}"
        "ENCRYPTED_STATE_TABLE=wellness_encrypted_state"
        "ENCRYPTED_STATE_FILE_PATH=/home/cdenneen/.local/state/wellness-api/encrypted-state.json"
        "AI_MODEL=gemini-3.5-flash"
      ];
    };
    script = ''
      set -euo pipefail

      if [ -r "${openAiKeyFile}" ]; then
        export OPENAI_API_KEY="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${openAiKeyFile}")"
      else
        echo "wellness-api: OpenAI key file missing at ${openAiKeyFile}" >&2
      fi

      if [ -r "${geminiKeyFile}" ]; then
        gemini_api_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${geminiKeyFile}")"
        if [ -n "$gemini_api_key" ]; then
          export GEMINI_API_KEY="$gemini_api_key"
          export GOOGLE_API_KEY="$gemini_api_key"
        else
          echo "wellness-api: Gemini key is empty in ${geminiKeyFile}" >&2
        fi
      else
        echo "wellness-api: Gemini key file missing at ${geminiKeyFile}" >&2
      fi

      if [ ! -r "${wellnessSupabasePublishableKeyFile}" ]; then
        echo "wellness-api: Supabase publishable key file missing at ${wellnessSupabasePublishableKeyFile}" >&2
        exit 1
      fi

      supabase_publishable_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${wellnessSupabasePublishableKeyFile}")"
      if [ -z "$supabase_publishable_key" ]; then
        echo "wellness-api: Supabase publishable key is empty" >&2
        exit 1
      fi
      export EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY="$supabase_publishable_key"
      export EXPO_PUBLIC_SUPABASE_ANON_KEY="$supabase_publishable_key"
      export SUPABASE_PUBLISHABLE_KEY="$supabase_publishable_key"
      export SUPABASE_ANON_KEY="$supabase_publishable_key"

      if [ -r "${wellnessSupabaseSecretKeyFile}" ]; then
        supabase_secret_key="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${wellnessSupabaseSecretKeyFile}")"
        if [ -n "$supabase_secret_key" ] && [ "$supabase_secret_key" != "REPLACE_WITH_SB_SECRET_KEY" ]; then
          export SUPABASE_SECRET_KEY="$supabase_secret_key"
          export SUPABASE_SERVICE_ROLE_KEY="$supabase_secret_key"
        else
          echo "wellness-api: Supabase secret key is unset in ${wellnessSupabaseSecretKeyFile}" >&2
        fi
      else
        echo "wellness-api: Supabase secret key file missing at ${wellnessSupabaseSecretKeyFile} (account deletion will be limited)" >&2
      fi

      if [ -r "${wellnessSupabaseDbUrlFile}" ]; then
        supabase_db_url="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${wellnessSupabaseDbUrlFile}")"
        if [ -n "$supabase_db_url" ] && [ "$supabase_db_url" != "REPLACE_WITH_SUPABASE_DB_URL" ]; then
          export SUPABASE_DB_URL="$supabase_db_url"
        else
          echo "wellness-api: Supabase DB URL is unset in ${wellnessSupabaseDbUrlFile}" >&2
        fi
      else
        echo "wellness-api: Supabase DB URL file missing at ${wellnessSupabaseDbUrlFile} (deploy migrations will require manual DB URL)" >&2
      fi

      if [ -r "${supabaseAccessTokenFile}" ]; then
        supabase_access_token="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${supabaseAccessTokenFile}")"
        if [ -n "$supabase_access_token" ] && [ "$supabase_access_token" != "REPLACE_WITH_SUPABASE_ACCESS_TOKEN" ]; then
          export SUPABASE_ACCESS_TOKEN="$supabase_access_token"
        fi
      fi

      if [ ! -x node_modules/.bin/tsx ]; then
        npm ci --include=dev --no-audit --no-fund
      fi

      exec npm run api:start
    '';
  };

  systemd.services.cloudflared-credentials-ghost =
    let
      script = pkgs.writeShellScript "cloudflared-credentials-ghost" ''
        set -euo pipefail

        token_file="${config.sops.secrets.ghost_cloudflare_tunnel_token.path}"
        cred_dir="/var/lib/cloudflared"
        cred_file="${ghostCloudflareCredFile}"

        if [ ! -r "$token_file" ]; then
          echo "cloudflared-credentials-ghost: token file not readable" >&2
          exit 1
        fi

        token_json="$(${pkgs.coreutils}/bin/cat "$token_file" | ${pkgs.coreutils}/bin/tr -d '\n\r' | ${pkgs.coreutils}/bin/base64 -d)"
        account_tag="$(${pkgs.jq}/bin/jq -r '.a // empty' <<<"$token_json")"
        tunnel_id="$(${pkgs.jq}/bin/jq -r '.t // empty' <<<"$token_json")"
        tunnel_secret="$(${pkgs.jq}/bin/jq -r '.s // empty' <<<"$token_json")"

        if [ -z "$account_tag" ] || [ -z "$tunnel_id" ] || [ -z "$tunnel_secret" ]; then
          echo "cloudflared-credentials-ghost: invalid token contents" >&2
          exit 1
        fi

        if [ "$tunnel_id" != "${ghostTunnelId}" ]; then
          echo "cloudflared-credentials-ghost: token tunnel ID $tunnel_id does not match ${ghostTunnelId}" >&2
          exit 1
        fi

        ${pkgs.coreutils}/bin/mkdir -p "$cred_dir"
        ${pkgs.jq}/bin/jq -n \
          --arg account_tag "$account_tag" \
          --arg tunnel_id "$tunnel_id" \
          --arg tunnel_secret "$tunnel_secret" \
          --arg tunnel_name "ghost" \
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
      description = "Generate Cloudflared credentials from ghost token";
      before = [ "cloudflared-tunnel-${ghostTunnelId}.service" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = script;
        RemainAfterExit = true;
      };
    };

  systemd.services."cloudflared-tunnel-${ghostTunnelId}" = {
    requires = [ "cloudflared-credentials-ghost.service" ];
    after = [ "cloudflared-credentials-ghost.service" ];
  };
}
