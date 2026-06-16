{
  config,
  lib,
  pkgs,
  happier,
  jarvis,
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
  geminiKeyFile = config.sops.secrets.gemini_api_key.path;
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
  postgresUser = "postgres";
  postgresDb = "postgres";
  neo4jUser = "neo4j";
  ollamaDataDir = "/var/lib/ollama";
  qdrantDataDir = "/var/lib/qdrant";
  litellmConfigFile = "/etc/litellm/config.yaml";
  litellmEnvFile = "/run/litellm/env";
  qdrantEnvFile = "/run/qdrant/env";
  postgresEnvFile = "/run/postgres/env";
  neo4jEnvFile = "/run/neo4j/env";
  redisConfigFile = "/run/redis/redis.conf";
  postgresDataDir = "/var/lib/postgres";
  neo4jDataDir = "/var/lib/neo4j/data";
  neo4jLogsDir = "/var/lib/neo4j/logs";
  redisDataDir = "/var/lib/redis";
in
{
  imports = [
    ./ghost-base.nix
    happier.nixosModules.happier-server
    jarvis.nixosModules.jarvis
  ];

  profiles.hmIntegrated.enable = lib.mkForce true;

  containerPresets = {
    podman.enable = true;
  };
  networking = {
    firewall.trustedInterfaces = lib.mkAfter [ "tailscale0" ];
  };

  users.users.cdenneen.extraGroups = lib.mkAfter [ "tailscale" ];

  environment.systemPackages = lib.mkAfter [
    pkgs.caddy
    pkgs.cloudflared
    pkgs.nodejs_24
  ];

  services = {
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

    jarvis = {
      enable = true;
      mode = "oci";
      image = "registry.gitlab.com/cdenneen/my-jarvis/jarvis";
      imageTag = "0.1.0a3";
    };

    cloudflared = {
      enable = true;
      tunnels."${ghostTunnelId}" = {
        credentialsFile = ghostCloudflareCredFile;
        ingress = {
          "${pepsApiHost}" = "http://127.0.0.1:${toString pepsApiPort}";
          "${pepsWebHost}" = "http://127.0.0.1:${toString pepsApiPort}";
          "${wellnessApiHost}" = "http://127.0.0.1:${toString wellnessApiPort}";
        };
        default = "http_status:404";
        originRequest = {
          connectTimeout = "30s";
          noTLSVerify = false;
        };
      };
    };
  };

  environment.etc."litellm/config.yaml".text = ''
    model_list:
      - model_name: gpt-4o-mini
        litellm_params:
          model: gpt-4o-mini
          api_key: os.environ/OPENAI_API_KEY
  '';

  virtualisation.oci-containers.backend = "podman";
  virtualisation.oci-containers.containers = {
    ollama = {
      image = "ollama/ollama:latest";
      ports = [ "127.0.0.1:${toString ollamaPort}:11434" ];
      volumes = [ "${ollamaDataDir}:/root/.ollama:U" ];
      autoStart = true;
    };

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
      ports = [ "127.0.0.1:${toString litellmPort}:4000" ];
      volumes = [ "${litellmConfigFile}:/app/config.yaml:ro" ];
      extraOptions = [ "--env-file=${litellmEnvFile}" ];
      cmd = [
        "--config"
        "/app/config.yaml"
        "--port"
        "${toString litellmPort}"
      ];
      environment = {
        LITELLM_CONFIG = "/app/config.yaml";
        LITELLM_PORT = toString litellmPort;
      };
      autoStart = true;
    };

    postgres = {
      image = "postgres:16";
      ports = [ "127.0.0.1:${toString postgresPort}:5432" ];
      volumes = [ "${postgresDataDir}:/var/lib/postgresql/data:U" ];
      extraOptions = [ "--env-file=${postgresEnvFile}" ];
      environment = {
        POSTGRES_USER = postgresUser;
        POSTGRES_DB = postgresDb;
        POSTGRES_INITDB_ARGS = "--auth-host=scram-sha-256 --auth-local=scram-sha-256";
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

    redis = {
      image = "redis:7";
      ports = [ "127.0.0.1:${toString redisPort}:6379" ];
      volumes = [
        "${redisDataDir}:/data:U"
        "${redisConfigFile}:/usr/local/etc/redis/redis.conf:ro"
      ];
      cmd = [
        "redis-server"
        "/usr/local/etc/redis/redis.conf"
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
    owner = "root";
    group = "root";
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

  systemd.tmpfiles.rules = [
    "d /var/lib/cloudflared 0700 root root -"
    "d /var/lib/happier-server 0700 root root -"
    "d ${pepsRuntimeDir} 0750 cdenneen users -"
    "d ${pepsRepoDir} 0750 cdenneen users -"
    "d ${wellnessRuntimeDir} 0750 cdenneen users -"
    "d ${wellnessRepoDir} 0750 cdenneen users -"
    "d ${ollamaDataDir} 0750 root root -"
    "d ${qdrantDataDir} 0750 root root -"
    "d /var/lib/postgres 0750 root root -"
    "d /var/lib/neo4j 0750 root root -"
    "d ${neo4jDataDir} 0750 root root -"
    "d ${neo4jLogsDir} 0750 root root -"
    "d ${redisDataDir} 0750 root root -"
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
      master_key="$(read_secret "${litellmMasterKeyFile}" "LiteLLM master key")"
      salt_key="$(read_secret "${litellmSaltKeyFile}" "LiteLLM salt key")"

      ${pkgs.coreutils}/bin/install -m 600 /dev/null "${litellmEnvFile}"
      {
        printf 'OPENAI_API_KEY=%s\n' "$openai_key"
        printf 'LITELLM_MASTER_KEY=%s\n' "$master_key"
        printf 'LITELLM_SALT_KEY=%s\n' "$salt_key"
      } > "${litellmEnvFile}"
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

  systemd.services.postgres-env = {
    description = "Render postgres env file";
    before = [ "podman-postgres.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail

      env_dir="$(${pkgs.coreutils}/bin/dirname "${postgresEnvFile}")"
      ${pkgs.coreutils}/bin/mkdir -p "$env_dir"

      if [ ! -r "${postgresPasswordFile}" ]; then
        echo "Missing Postgres password at ${postgresPasswordFile}" >&2
        exit 1
      fi

      password="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${postgresPasswordFile}")"
      if [ -z "$password" ]; then
        echo "Postgres password at ${postgresPasswordFile} is empty" >&2
        exit 1
      fi

      ${pkgs.coreutils}/bin/install -m 600 /dev/null "${postgresEnvFile}"
      printf 'POSTGRES_PASSWORD=%s\n' "$password" > "${postgresEnvFile}"
    '';
  };

  systemd.services.postgres-bootstrap = {
    description = "Ensure postgres password auth";
    after = [ "podman-postgres.service" ];
    requires = [ "podman-postgres.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    path = [
      pkgs.coreutils
      pkgs.gnugrep
      pkgs.gnused
      pkgs.podman
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

      pg_hba="${postgresDataDir}/pg_hba.conf"
      for _ in $(seq 1 60); do
        if [ -s "$pg_hba" ] && ${pkgs.podman}/bin/podman exec postgres pg_isready -U "${postgresUser}" >/dev/null 2>&1; then
          break
        fi
        sleep 2
      done

      if [ ! -s "$pg_hba" ]; then
        echo "postgres-bootstrap: pg_hba.conf not found at $pg_hba" >&2
        exit 1
      fi

      ${pkgs.podman}/bin/podman exec \
        -e PGPASSWORD="$password" \
        postgres \
        psql -U "${postgresUser}" -d "${postgresDb}" \
        -c "ALTER USER ${postgresUser} WITH PASSWORD '$password';"

      if ${pkgs.gnugrep}/bin/grep -q '^local\s\+all\s\+all\s\+' "$pg_hba"; then
        ${pkgs.gnused}/bin/sed -i 's/^local\s\+all\s\+all\s\+.*/local all all scram-sha-256/' "$pg_hba"
      else
        printf 'local all all scram-sha-256\n' >> "$pg_hba"
      fi

      if ${pkgs.gnugrep}/bin/grep -q '^host\s\+all\s\+all\s\+all\s\+' "$pg_hba"; then
        ${pkgs.gnused}/bin/sed -i 's/^host\s\+all\s\+all\s\+all\s\+.*/host all all all scram-sha-256/' "$pg_hba"
      else
        printf 'host all all all scram-sha-256\n' >> "$pg_hba"
      fi

      ${pkgs.podman}/bin/podman exec \
        -e PGPASSWORD="$password" \
        postgres \
        psql -U "${postgresUser}" -d "${postgresDb}" \
        -c "SELECT pg_reload_conf();"
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

  systemd.services.redis-config = {
    description = "Render redis config";
    before = [ "podman-redis.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      UMask = "0077";
    };
    path = [ pkgs.coreutils ];
    script = ''
      set -euo pipefail

      config_dir="$(${pkgs.coreutils}/bin/dirname "${redisConfigFile}")"
      ${pkgs.coreutils}/bin/mkdir -p "$config_dir"

      if [ ! -r "${redisPasswordFile}" ]; then
        echo "Missing Redis password at ${redisPasswordFile}" >&2
        exit 1
      fi

      password="$(${pkgs.coreutils}/bin/tr -d '\n\r' < "${redisPasswordFile}")"
      if [ -z "$password" ]; then
        echo "Redis password at ${redisPasswordFile} is empty" >&2
        exit 1
      fi

      ${pkgs.coreutils}/bin/install -m 640 /dev/null "${redisConfigFile}"
      {
        printf 'bind 0.0.0.0\n'
        printf 'protected-mode yes\n'
        printf 'port %s\n' "${toString redisPort}"
        printf 'requirepass %s\n' "$password"
      } > "${redisConfigFile}"
      ${pkgs.coreutils}/bin/chown 999:999 "${redisConfigFile}"
    '';
  };

  systemd.services.podman-litellm = {
    requires = [ "litellm-env.service" ];
    after = [ "litellm-env.service" ];
  };

  systemd.services.podman-qdrant = {
    requires = [ "qdrant-env.service" ];
    after = [ "qdrant-env.service" ];
  };

  systemd.services.podman-postgres = {
    requires = [ "postgres-env.service" ];
    after = [ "postgres-env.service" ];
  };

  systemd.services.podman-neo4j = {
    requires = [ "neo4j-env.service" ];
    after = [ "neo4j-env.service" ];
  };

  systemd.services.podman-redis = {
    requires = [ "redis-config.service" ];
    after = [ "redis-config.service" ];
  };

  systemd.services.happier-server = {
    requires = [ "happier-env-bootstrap.service" ];
    after = [ "happier-env-bootstrap.service" ];
  };

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
