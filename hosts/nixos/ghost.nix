{
  axis,
  config,
  lib,
  pkgs,
  ...
}:
let
  alpha0SecretsFile = ../../secrets/alpha0.yaml;
  hasAlpha0NodeIdentity = lib.hasInfix "alpha0_node_nyx_identity:" (
    builtins.readFile alpha0SecretsFile
  );
  hasAlpha0GitlabRelayIdentity = lib.hasInfix "alpha0_gitlab_nyx_relay_identity:" (
    builtins.readFile alpha0SecretsFile
  );
  ghostTunnelId = "1481e71c-a53f-4fe0-8983-468a3e0fffdf";
  ghostCloudflareCredFile = "/var/lib/cloudflared/ghost.json";
  axisWebPackage = axis.packages.${pkgs.system}.axis-web;
  axisWebDashboardPasswordFile = config.sops.secrets.axis_web_dashboard_password.path;
  axisWebSessionSecretFile = config.sops.secrets.axis_web_session_secret.path;
  axisWebTokenFile = "/run/axis-web/token";
  axisApiBearerTokenFile = config.sops.secrets.axis_remote_client_token.path;
  axisSlackBotTokenFile = config.sops.secrets.jarvis_slack_bot_token.path;
  axisSlackSigningSecretFile = config.sops.secrets.jarvis_slack_signing_secret.path;
  axisErosApiKeyFile = config.sops.secrets.axis_eros_api_key.path;
  axisErosBaseUrlFile = config.sops.secrets.axis_eros_base_url.path;
  axisSlackTeamId = "T0B7QDWFLJ3";
  axisSlackProductOwnerId = "U0B7ZGP6M43";
  axisSlackIdentitySecretName = "provider.slack.identity.${
    builtins.substring 0 32 (
      builtins.hashString "sha256" "${axisSlackTeamId}${builtins.fromJSON ''"\u001f"''}${axisSlackProductOwnerId}"
    )
  }";
  axisSlackCapabilitySetup = pkgs.writeShellScript "axis-slack-capability-setup" ''
    set -euo pipefail

    contexts_file="/var/lib/axis/client/contexts.json"
    if [ ! -r "$contexts_file" ]; then
      echo "axis Slack identity binding: client contexts are not readable" >&2
      exit 1
    fi

    binding="$(${pkgs.jq}/bin/jq -cer '
      .active_context as $active
      | select(($active | type) == "string" and ($active | length) > 0)
      | .contexts[$active]
      | select(type == "object")
      | {
          active: true,
          principal_id: (.principal_id | select(type == "string" and length > 0)),
          access_token: (.auth_token | select(type == "string" and length > 0))
        }
    ' "$contexts_file")" || {
      echo "axis Slack identity binding: active context principal or token is missing" >&2
      exit 1
    }

    principal_id="$(${pkgs.jq}/bin/jq -er '.principal_id' <<<"$binding")"
    profile_db="/var/lib/axis/profile.db"
    if [ ! -r "$profile_db" ]; then
      echo "axis Slack identity binding: canonical profile database is not readable" >&2
      exit 1
    fi
    profile_payload="$(${pkgs.sqlite}/bin/sqlite3 -readonly "$profile_db" \
      'SELECT payload FROM profile_state WHERE singleton = 1;')" || {
      echo "axis Slack identity binding: canonical profile payload is unavailable" >&2
      exit 1
    }
    if ! ${pkgs.coreutils}/bin/printf '%s' "$profile_payload" | ${pkgs.jq}/bin/jq -e \
      --arg principal_id "$principal_id" '
        .principals
        | select(type == "array")
        | any(
            .[];
            type == "object"
            and .principal_id == $principal_id
            and .relationship == "owner"
            and (.role_ids | if type == "array" then index("deployment-owner") != null else false end)
          )
      ' > /dev/null; then
      echo "axis Slack identity binding: active principal is not the deployment owner" >&2
      exit 1
    fi

    for secret_file in "${axisSlackBotTokenFile}" "${axisSlackSigningSecretFile}"; do
      if [ ! -s "$secret_file" ]; then
        echo "axis Slack capability setup: required secret file is missing or empty" >&2
        exit 1
      fi
    done

    ${pkgs.coreutils}/bin/cat "${axisSlackBotTokenFile}" \
      | ${axis.packages.${pkgs.system}.axis}/bin/axis --data-root /var/lib/axis capability authorize \
        --capability-id provider.slack.bot-token \
        --secret-name provider.slack.bot_token \
        --scope axis_vault \
        --display-name "Slack bot token" \
        --secret-stdin \
        > /dev/null
    ${pkgs.coreutils}/bin/cat "${axisSlackSigningSecretFile}" \
      | ${axis.packages.${pkgs.system}.axis}/bin/axis --data-root /var/lib/axis capability authorize \
        --capability-id provider.slack.signing-secret \
        --secret-name provider.slack.signing_secret \
        --scope axis_vault \
        --display-name "Slack signing secret" \
        --secret-stdin \
        > /dev/null
    ${pkgs.coreutils}/bin/printf '%s' "$binding" \
      | ${axis.packages.${pkgs.system}.axis}/bin/axis --data-root /var/lib/axis capability authorize \
        --capability-id provider.slack.identity.product-owner \
        --secret-name ${axisSlackIdentitySecretName} \
        --scope axis_vault \
        --display-name "Slack Product Owner identity" \
        --secret-stdin \
        > /dev/null
  '';
  axisErosCapabilitySetup = pkgs.writeShellScript "axis-eros-capability-setup" ''
    set -euo pipefail

    for secret_file in "${axisErosApiKeyFile}" "${axisErosBaseUrlFile}"; do
      if [ ! -s "$secret_file" ]; then
        echo "axis Eros capability setup: required secret file is missing or empty" >&2
        exit 1
      fi
    done

    ${pkgs.coreutils}/bin/cat "${axisErosApiKeyFile}" \
      | ${axis.packages.${pkgs.system}.axis}/bin/axis --data-root /var/lib/axis capability authorize \
        --capability-id provider.openai-compatible.eros.api-key \
        --secret-name provider.openai-compatible.eros.api_key \
        --scope axis_vault \
        --display-name "AXIS Eros OpenAI-compatible API key" \
        --secret-stdin \
        > /dev/null
    ${pkgs.coreutils}/bin/cat "${axisErosBaseUrlFile}" \
      | ${axis.packages.${pkgs.system}.axis}/bin/axis --data-root /var/lib/axis capability authorize \
        --capability-id provider.openai-compatible.eros.base-url \
        --secret-name provider.openai-compatible.eros.base_url \
        --scope axis_vault \
        --display-name "AXIS Eros OpenAI-compatible base URL" \
        --secret-stdin \
        > /dev/null
  '';
  axisRevision = axis.rev or "unknown";
  supervisorRevision =
    if config.system.configurationRevision != null then
      config.system.configurationRevision
    else
      "unknown";
  axisWebTokenSetup = pkgs.writeShellScript "axis-web-token-setup" ''
    set -euo pipefail

    contexts_file="/var/lib/axis/client/contexts.json"
    token_tmp="${axisWebTokenFile}.tmp"

    if [ ! -r "$contexts_file" ]; then
      echo "axis-web: client contexts are not readable" >&2
      exit 1
    fi

    trap '${pkgs.coreutils}/bin/rm -f "$token_tmp"' EXIT
    ${pkgs.coreutils}/bin/install -m 0600 /dev/null "$token_tmp"
    ${pkgs.jq}/bin/jq -er '
      .active_context as $active
      | select(($active | type) == "string" and ($active | length) > 0)
      | .contexts[$active].auth_token
      | select(type == "string" and length > 0)
    ' "$contexts_file" > "$token_tmp"
    ${pkgs.coreutils}/bin/chmod 0600 "$token_tmp"
    ${pkgs.coreutils}/bin/mv -f "$token_tmp" "${axisWebTokenFile}"
  '';
  axisApiProxy = pkgs.writeText "axis-api-auth-proxy.py" ''
    import hmac
    import http.client
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    TOKEN_FILE = ${builtins.toJSON axisApiBearerTokenFile}
    SLACK_HOST = "slack.denneen.net"
    SLACK_CALLBACK_PATH = "/callbacks/slack"
    MAX_SLACK_CALLBACK_BYTES = 1_048_576

    class Handler(BaseHTTPRequestHandler):
        def _read_slack_body(self):
            if self.headers.get("Transfer-Encoding"):
                self.send_error(400)
                return None
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                self.send_error(400)
                return None
            if length < 0:
                self.send_error(400)
                return None
            if length > MAX_SLACK_CALLBACK_BYTES:
                self.send_error(413)
                return None
            return self.rfile.read(length) if length else b""

        def _forward(self, body, headers):
            upstream = http.client.HTTPConnection("127.0.0.1", 8780, timeout=30)
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            payload = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in {"transfer-encoding", "connection"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            upstream.close()

        def _slack_callback(self):
            body = self._read_slack_body()
            if body is None:
                return
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in {"host", "connection", "transfer-encoding"}
            }
            self._forward(body, headers)

        def _authenticated_proxy(self):
            with open(TOKEN_FILE, "r", encoding="utf-8") as handle:
                expected = handle.read().strip()
            supplied = self.headers.get("Authorization", "")
            if not expected or not hmac.compare_digest(supplied, f"Bearer {expected}"):
                self.send_response(401)
                self.send_header("WWW-Authenticate", "Bearer")
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else None
            headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in {"host", "content-length", "connection"}
            }
            upstream_path = self.path[4:] if self.path.startswith("/api") else self.path
            original_path, self.path = self.path, upstream_path or "/"
            try:
                self._forward(body, headers)
            finally:
                self.path = original_path

        def _proxy(self):
            if self.headers.get("Host", "").split(":", 1)[0].lower() == SLACK_HOST:
                if self.path != SLACK_CALLBACK_PATH:
                    self.send_error(404)
                elif self.command != "POST":
                    self.send_response(405)
                    self.send_header("Allow", "POST")
                    self.end_headers()
                else:
                    self._slack_callback()
                return
            self._authenticated_proxy()

        do_GET = _proxy
        do_POST = _proxy
        do_PUT = _proxy
        do_PATCH = _proxy
        do_DELETE = _proxy

        def __getattr__(self, name):
            if name.startswith("do_"):
                return self._proxy
            raise AttributeError(name)

        def log_message(self, format, *args):
            return

    ThreadingHTTPServer(("127.0.0.1", 8001), Handler).serve_forever()
  '';
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
  gitlabRunnerTokenFile = config.sops.secrets.gitlab_com_runner_token.path;
  gitlabRunnerSecondaryTokenFile = config.sops.secrets.gitlab_com_runner_token_2.path;
  qdrantApiKeyFile = config.sops.secrets.local_qdrant_api_key.path;
  litellmMasterKeyFile = config.sops.secrets.local_litellm_master_key.path;
  litellmSaltKeyFile = config.sops.secrets.local_litellm_salt_key.path;
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
  neo4jHttpPort = 7474;
  neo4jBoltPort = 7687;
  redisPort = 6379;
  minioApiPort = 9000;
  minioConsolePort = 9001;
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
  neo4jDataDir = "/var/lib/neo4j/data";
  neo4jLogsDir = "/var/lib/neo4j/logs";
  redisDataDir = "/var/lib/redis";
  minioCredentialsFile = config.sops.secrets.minio-credentials.path;
in
{
  imports = [
    ./ghost-base.nix
    axis.nixosModules.default
  ];

  profiles.hmIntegrated.enable = lib.mkForce true;

  profiles.aiTools.enable = true;

  containerPresets = {
    podman.enable = true;
    open-webui = {
      enable = true;
      dataDir = "/var/lib/open-webui-fresh";
      ollamaBaseUrl = "http://127.0.0.1:${toString ollamaPort}";
    };
  };
  virtualisation.docker.enable = lib.mkForce false;
  virtualisation.podman.autoPrune = {
    enable = true;
    dates = "weekly";
    flags = [ "--all" ];
  };

  boot.tmp.cleanOnBoot = true;
  systemd.tmpfiles.settings."00-ghost-tmp"."/tmp".q = {
    mode = "1777";
    user = "root";
    group = "root";
    age = "3d";
  };

  # Oracle Cloud's free-tier VM has 4 OCPUs and 24 GiB RAM. Keep enough
  # headroom for remote access and core services when agent workloads spike.
  zramSwap = {
    enable = true;
    algorithm = "zstd";
    memoryPercent = 25;
    priority = 100;
  };

  nix = {
    gc = {
      dates = lib.mkForce "daily";
      options = lib.mkForce "--delete-older-than 3d";
    };
    settings = {
      cores = lib.mkForce 2;
      max-jobs = lib.mkForce 2;
      min-free = 5 * 1024 * 1024 * 1024;
      max-free = 15 * 1024 * 1024 * 1024;
    };
  };

  systemd.slices."user-1000" = {
    description = "Resource limits for cdenneen user workloads";
    sliceConfig = {
      MemoryHigh = "16G";
      MemoryMax = "18G";
      MemorySwapMax = "4G";
      TasksMax = 2048;
    };
  };

  # If the user slice reaches its hard limit, preserve the user manager so
  # the kernel selects a large worker instead of tearing down tmux wholesale.
  systemd.services."user@".serviceConfig.OOMScoreAdjust = lib.mkForce (-500);

  networking = {
    firewall.trustedInterfaces = lib.mkAfter [ "podman0" ];
    nftables.tables.shared-container-nat = {
      family = "ip";
      content = ''
        chain prerouting {
          type nat hook prerouting priority dstnat;
          ip saddr 10.88.0.0/16 ip daddr 127.0.0.1 tcp dport 6379 dnat ip to 127.0.0.1:6379
          ip saddr 10.88.0.0/16 ip daddr 127.0.0.1 tcp dport 4000 dnat ip to 127.0.0.1:4000
        }
      '';
    };
  };

  users.users.cdenneen.extraGroups = lib.mkAfter [ "tailscale" ];
  users.groups.gitlab-runner = { };
  users.users.gitlab-runner = {
    isSystemUser = true;
    group = "gitlab-runner";
    home = "/var/lib/gitlab-runner";
    extraGroups = [ "podman" ];
  };

  systemd.user.services.herdr = {
    description = "Herdr persistent terminal workspace server";
    wantedBy = [ "default.target" ];
    serviceConfig = {
      Type = "simple";
      ExecStart = "${config.users.users.cdenneen.home}/.local/bin/herdr server";
      Restart = "on-failure";
      RestartSec = "5s";
    };
  };

  systemd.user.services.omniroute = {
    description = "OmniRoute local AI gateway";
    wantedBy = [ "default.target" ];
    serviceConfig = {
      Type = "simple";
      ExecStart = "${config.users.users.cdenneen.home}/.local/bin/omniroute --no-open";
      Environment = [
        "HOSTNAME=127.0.0.1"
        "PORT=20128"
        "DATA_DIR=${config.users.users.cdenneen.home}/.omniroute"
        "PATH=${
          lib.makeBinPath [ pkgs.nodejs_24 ]
        }:/run/current-system/sw/bin:/etc/profiles/per-user/cdenneen/bin"
      ];
      Restart = "on-failure";
      RestartSec = "5s";
    };
  };

  environment.systemPackages = lib.mkAfter [
    pkgs.caddy
    pkgs.cloudflared
    pkgs.crane
    pkgs.nodejs_24
    pkgs.pnpm
  ];

  environment.etc."ssh/alpha0-node-nyx-known-hosts" = {
    mode = "0444";
    text = ''
      nyx,nyx.tail0e55.ts.net,100.80.58.4 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK3PCrjUkoqJkZ1Ibi+s702ub7zrqvh44pxVFii5C/FG
    '';
  };

  services = {
    axis = {
      enable = true;
      dataRoot = "/var/lib/axis";
      host = "127.0.0.1";
      port = 8780;
    };

    tailscale = {
      enable = true;
      openFirewall = true;
      useRoutingFeatures = "client";
      extraSetFlags = [ "--accept-dns=true" ];
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
          dockerImage = "alpine:3.24.1@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b";
          requestConcurrency = 2;
        };
        "ghost-2" = {
          authenticationTokenConfigFile = gitlabRunnerSecondaryEnvFile;
          executor = "docker";
          dockerImage = "alpine:3.24.1@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b";
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
          "chat.denneen.net" = "http://127.0.0.1:8080";
          "${pepsApiHost}" = "http://127.0.0.1:${toString pepsApiPort}";
          "${pepsWebHost}" = "http://127.0.0.1:${toString pepsApiPort}";
          "${wellnessApiHost}" = "http://127.0.0.1:${toString wellnessApiPort}";
          "ai-dev.denneen.net" = "http://127.0.0.1:3000";
          "ai.denneen.net" = "http://127.0.0.1:3001";
          "slack.denneen.net" = "http://127.0.0.1:8001";
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
      image = "qdrant/qdrant:v1.18.3@sha256:0bd98fa7977f1e75694779359ca4e212822e5a71334e28421182f72f209d5286";
      ports = [
        "127.0.0.1:${toString qdrantHttpPort}:6333"
        "127.0.0.1:${toString qdrantGrpcPort}:6334"
      ];
      volumes = [ "${qdrantDataDir}:/qdrant/storage:U" ];
      extraOptions = [ "--env-file=${qdrantEnvFile}" ];
      autoStart = true;
    };

    litellm = {
      image = "ghcr.io/berriai/litellm:v1.94.0@sha256:65d84a2282137b4dc73bbe184650a7c807177c533e4223b3bfbc87963fe3fabe";
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
      image = "neo4j:5.26.28@sha256:362542416de6c09a971484d1893878016cc3b5cdec166e54b1c824a220ecd6b9";
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
  sops.secrets.axis_web_dashboard_password = {
    sopsFile = ../../secrets/axis.yaml;
    owner = "axis";
    group = "axis";
    mode = "0400";
    restartUnits = [ "axis-web.service" ];
  };
  sops.secrets.axis_web_session_secret = {
    sopsFile = ../../secrets/axis.yaml;
    owner = "axis";
    group = "axis";
    mode = "0400";
    restartUnits = [ "axis-web.service" ];
  };
  sops.secrets.axis_remote_client_token = {
    sopsFile = ../../secrets/axis.yaml;
    owner = "axis";
    group = "axis";
    mode = "0440";
    restartUnits = [ "axis-api-auth-proxy.service" ];
  };
  sops.secrets.jarvis_slack_bot_token = {
    sopsFile = ../../secrets/axis.yaml;
    owner = "axis";
    group = "axis";
    mode = "0400";
    restartUnits = [ "axis.service" ];
  };
  sops.secrets.jarvis_slack_signing_secret = {
    sopsFile = ../../secrets/axis.yaml;
    owner = "axis";
    group = "axis";
    mode = "0400";
    restartUnits = [ "axis.service" ];
  };
  sops.secrets.axis_eros_api_key = {
    sopsFile = ../../secrets/axis.yaml;
    owner = "axis";
    group = "axis";
    mode = "0400";
    restartUnits = [ "axis.service" ];
  };
  sops.secrets.axis_eros_base_url = {
    sopsFile = ../../secrets/axis.yaml;
    owner = "axis";
    group = "axis";
    mode = "0400";
    restartUnits = [ "axis.service" ];
  };
  sops.secrets."alpha0/audit-key" = {
    sopsFile = alpha0SecretsFile;
    key = "alpha0_audit_key";
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets."alpha0/node-nyx-identity" = lib.mkIf hasAlpha0NodeIdentity {
    sopsFile = alpha0SecretsFile;
    key = "alpha0_node_nyx_identity";
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets."alpha0/gitlab-nyx-relay-identity" = lib.mkIf hasAlpha0GitlabRelayIdentity {
    sopsFile = alpha0SecretsFile;
    key = "alpha0_gitlab_nyx_relay_identity";
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets."alpha0/gitlab-com-token" = {
    sopsFile = ../../secrets/alpha0.yaml;
    key = "alpha0_gitlab_com_token";
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets."alpha0/git-ap-org-token" = {
    sopsFile = ../../secrets/alpha0.yaml;
    key = "alpha0_git_ap_org_token";
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets."alpha0/slack-bot-token" = {
    sopsFile = ../../secrets/alpha0.yaml;
    key = "alpha0_slack_bot_token";
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets."alpha0/slack-app-token" = {
    sopsFile = ../../secrets/alpha0.yaml;
    key = "alpha0_slack_app_token";
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets."alpha0/slack-member-id" = {
    sopsFile = ../../secrets/alpha0.yaml;
    key = "slack_member_id";
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets."alpha0/api-server-key" = {
    sopsFile = ../../secrets/alpha0.yaml;
    key = "alpha0_api_server_key";
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.templates."alpha0-hermes-default.env" = {
    content = ''
      SLACK_BOT_TOKEN=${config.sops.placeholder."alpha0/slack-bot-token"}
      SLACK_APP_TOKEN=${config.sops.placeholder."alpha0/slack-app-token"}
      SLACK_ALLOWED_USERS=${config.sops.placeholder."alpha0/slack-member-id"}
      API_SERVER_KEY=${config.sops.placeholder."alpha0/api-server-key"}
      SLACK_ALLOW_ALL_USERS=false
      GATEWAY_ALLOW_ALL_USERS=false
    '';
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.templates."alpha0-hermes-profile-alpha0.env" = {
    content = ''
      OPENAI_API_KEY=${config.sops.placeholder.openai_api_key}
    '';
    owner = "cdenneen";
    group = "users";
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
    "d ${pepsRuntimeDir} 0750 cdenneen users -"
    "d ${pepsRepoDir} 0750 cdenneen users -"
    "d ${wellnessRuntimeDir} 0750 cdenneen users -"
    "d ${wellnessRepoDir} 0750 cdenneen users -"
    "d ${ollamaDataDir} 0750 ollama ollama -"
    "d ${qdrantDataDir} 0750 root root -"
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

  systemd.services.axis-api-auth-proxy = {
    description = "Authenticated AXIS API proxy";
    after = [ "axis.service" ];
    requires = [ "axis.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      User = "axis";
      Group = "axis";
      ExecStart = "${pkgs.python3}/bin/python ${axisApiProxy}";
      Restart = "on-failure";
      RestartSec = 2;
      NoNewPrivileges = true;
      PrivateTmp = true;
    };
  };

  systemd.services.axis = {
    unitConfig.RequiresMountsFor = [
      axisSlackBotTokenFile
      axisSlackSigningSecretFile
      axisErosApiKeyFile
      axisErosBaseUrlFile
    ];
    preStart = lib.mkBefore ''
      ${axisSlackCapabilitySetup}
      ${axisErosCapabilitySetup}
    '';
  };

  systemd.services.axis-deployment-identity = {
    description = "Record Ghost AXIS deployment identity";
    after = [ "axis.service" ];
    requires = [ "axis.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      install -d -m 0750 -o axis -g axis /var/lib/axis
      deployed_at="$(${pkgs.coreutils}/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
      cat > /var/lib/axis/deployment-identity.json <<EOF
      {"runtime":"ghost","ring":0,"runtime_revision":"${axisRevision}","supervisor_revision":"${supervisorRevision}","deployment_time":"$deployed_at","verification_status":"deployment-recorded","health":"pending-runtime-verification"}
      EOF
      chown axis:axis /var/lib/axis/deployment-identity.json
      chmod 0644 /var/lib/axis/deployment-identity.json
      cp /var/lib/axis/deployment-identity.json /run/axis-deployment-identity.json
      chmod 0644 /run/axis-deployment-identity.json
    '';
  };

  # Daily DR backup: axis's own export (identity/memory/knowledge_documents/
  # vault), a neo4j dump (brief clean stop/start -- ~3s downtime observed,
  # neo4j Community has no online-backup, dump requires the db stopped),
  # and a qdrant snapshot of every collection (queried dynamically -- ghost
  # has grown to 12 collections across axis/jarvis/litellm, don't hardcode
  # names). Pushed to savage over tailscale via a dedicated `restrict`-only
  # key (see savage.nix) since ghost is the only real copy of any of this
  # right now. Not real-time -- daily is the agreed bound on acceptable
  # data loss (see cloud-architecture-plan.md's DR section).
  sops.secrets.ghost_axis_dr_backup_key = {
    owner = "root";
    mode = "0400";
  };
  sops.secrets.ghost_axis_dr_export_passphrase = {
    owner = "axis";
    mode = "0400";
  };

  systemd.services.axis-dr-backup = {
    description = "Daily AXIS DR backup (axis export + neo4j dump + qdrant snapshots) to savage";
    unitConfig.RequiresMountsFor = [
      config.sops.secrets.ghost_axis_dr_backup_key.path
      config.sops.secrets.ghost_axis_dr_export_passphrase.path
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "root";
    };
    script = ''
      set -euo pipefail
      ts="$(${pkgs.coreutils}/bin/date -u +%Y%m%dT%H%M%SZ)"
      stage="/var/lib/axis-dr-backup/$ts"
      install -d -m 0755 "$stage"

      echo "[axis-dr-backup] exporting axis core state..."
      install -d -m 0750 -o axis -g axis "$stage/axis-owned"
      ${pkgs.util-linux}/bin/runuser -u axis -- ${pkgs.bash}/bin/bash -c \
        "${pkgs.coreutils}/bin/cat ${config.sops.secrets.ghost_axis_dr_export_passphrase.path} | ${
          axis.packages.${pkgs.system}.axis
        }/bin/axis --data-root /var/lib/axis export --output '$stage/axis-owned/axis-export.tar' --passphrase-stdin"

      echo "[axis-dr-backup] dumping neo4j (brief clean stop/start)..."
      ${pkgs.systemd}/bin/systemctl stop podman-neo4j.service
      ${pkgs.coreutils}/bin/sleep 3
      install -d -m 0755 "$stage/neo4j"
      ${pkgs.podman}/bin/podman run --rm \
        -v /var/lib/neo4j/data:/data:U \
        -v "$stage/neo4j":/dumps:U \
        --entrypoint neo4j-admin \
        docker.io/library/neo4j@sha256:362542416de6c09a971484d1893878016cc3b5cdec166e54b1c824a220ecd6b9 \
        database dump neo4j --to-path=/dumps
      ${pkgs.systemd}/bin/systemctl start podman-neo4j.service

      echo "[axis-dr-backup] snapshotting qdrant collections..."
      qkey="$(${pkgs.coreutils}/bin/cut -d= -f2 /run/ghost-services/qdrant/env)"
      install -d -m 0755 "$stage/qdrant"
      for coll in $(${pkgs.curl}/bin/curl -sf -H "api-key: $qkey" http://127.0.0.1:6333/collections | ${pkgs.jq}/bin/jq -r '.result.collections[].name'); do
        snap="$(${pkgs.curl}/bin/curl -sf -X POST -H "api-key: $qkey" "http://127.0.0.1:6333/collections/$coll/snapshots" | ${pkgs.jq}/bin/jq -r '.result.name')"
        ${pkgs.curl}/bin/curl -sf -H "api-key: $qkey" -o "$stage/qdrant/$coll--$snap" "http://127.0.0.1:6333/collections/$coll/snapshots/$snap"
      done

      echo "[axis-dr-backup] pushing to savage..."
      ${pkgs.rsync}/bin/rsync -az \
        -e "${pkgs.openssh}/bin/ssh -i ${config.sops.secrets.ghost_axis_dr_backup_key.path} -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/var/lib/axis-dr-backup/known_hosts" \
        "$stage/" "cdenneen@100.76.222.17:/var/backups/ghost-dr/$ts/"

      echo "[axis-dr-backup] pruning local staging older than 3 days..."
      ${pkgs.findutils}/bin/find /var/lib/axis-dr-backup -maxdepth 1 -mindepth 1 -mtime +3 -exec rm -rf {} +

      echo "[axis-dr-backup] done: $ts"
    '';
  };

  systemd.timers.axis-dr-backup = {
    description = "Daily AXIS DR backup timer";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "03:17";
      Persistent = true;
      RandomizedDelaySec = "10m";
    };
  };

  systemd.services.axis-web = {
    description = "AXIS Web embodiment";
    wantedBy = [ "multi-user.target" ];
    after = [
      "axis.service"
      "network-online.target"
    ];
    requires = [
      "axis.service"
      "network-online.target"
    ];
    unitConfig.RequiresMountsFor = [
      axisWebDashboardPasswordFile
      axisWebSessionSecretFile
    ];
    environment = {
      HOSTNAME = "127.0.0.1";
      PORT = "3001";
      AXIS_WEB_DASHBOARD_PASSWORD_FILE = axisWebDashboardPasswordFile;
      AXIS_WEB_SESSION_SECRET_FILE = axisWebSessionSecretFile;
      AXIS_WEB_SERVICE_URL = "http://127.0.0.1:8780";
      AXIS_WEB_TOKEN_FILE = axisWebTokenFile;
    };
    serviceConfig = {
      Type = "simple";
      User = "axis";
      Group = "axis";
      ExecStartPre = axisWebTokenSetup;
      ExecStart = "${axisWebPackage}/bin/axis-web";
      Restart = "on-failure";
      RestartSec = "5s";
      RuntimeDirectory = "axis-web";
      RuntimeDirectoryMode = "0700";
      UMask = "0077";

      AmbientCapabilities = "";
      CapabilityBoundingSet = "";
      LockPersonality = true;
      NoNewPrivileges = true;
      PrivateDevices = true;
      PrivateTmp = true;
      ProcSubset = "pid";
      ProtectClock = true;
      ProtectControlGroups = true;
      ProtectHome = true;
      ProtectHostname = true;
      ProtectKernelLogs = true;
      ProtectKernelModules = true;
      ProtectKernelTunables = true;
      ProtectProc = "invisible";
      ProtectSystem = "strict";
      RemoveIPC = true;
      RestrictAddressFamilies = [
        "AF_INET"
        "AF_INET6"
        "AF_UNIX"
      ];
      RestrictNamespaces = true;
      RestrictRealtime = true;
      RestrictSUIDSGID = true;
      SystemCallArchitectures = "native";
    };
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
      master_key="$(read_secret "${litellmMasterKeyFile}" "LiteLLM master key")"
      salt_key="$(read_secret "${litellmSaltKeyFile}" "LiteLLM salt key")"
      qdrant_api_key="$(read_secret "${qdrantApiKeyFile}" "Qdrant API key")"

      ${pkgs.coreutils}/bin/install -m 600 /dev/null "${litellmEnvFile}"
      {
        printf 'OPENAI_API_KEY=%s\n' "$openai_key"
        printf 'OPENROUTER_API_KEY=%s\n' "$openrouter_key"
        printf 'GEMINI_API_KEY=%s\n' "$gemini_key"
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
        num_retries: 2
        timeout: 90

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


      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString pepsApiPort} 127.0.0.1:${toString pepsApiPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString wellnessApiPort} 127.0.0.1:${toString wellnessApiPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString litellmPort} 127.0.0.1:${toString litellmPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString neo4jHttpPort} 127.0.0.1:${toString neo4jHttpPort}
      ${pkgs.tailscale}/bin/tailscale serve --bg --yes --tcp ${toString neo4jBoltPort} 127.0.0.1:${toString neo4jBoltPort}
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
    ];
    after = [
      "litellm-env.service"
      "litellm-config.service"
    ];
  };

  systemd.services.podman-qdrant = {
    requires = [ "qdrant-env.service" ];
    after = [ "qdrant-env.service" ];
  };

  systemd.services.podman-neo4j = {
    requires = [ "neo4j-env.service" ];
    after = [ "neo4j-env.service" ];
  };

  systemd.services.podman-minio = {
    requires = [ "minio-env.service" ];
    after = [ "minio-env.service" ];
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
    requires = [
      "axis-api-auth-proxy.service"
      "cloudflared-credentials-ghost.service"
    ];
    after = [
      "axis-api-auth-proxy.service"
      "axis-web.service"
      "cloudflared-credentials-ghost.service"
    ];
  };
}
