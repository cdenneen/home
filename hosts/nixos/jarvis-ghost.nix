{
  config,
  lib,
  pkgs,
  ...
}:
let
  jarvisRepoDir = "/var/lib/jarvis/repo";
  jarvisLegacyRepoDir = "/opt/jarvis";
  jarvisRuntimeDir = "/var/lib/jarvis";
  jarvisDataDir = "${jarvisRuntimeDir}/data";
  jarvisSecretsDir = "${jarvisDataDir}/secrets";
  jarvisSecretsFile = "${jarvisSecretsDir}/jarvis.yaml";
  jarvisEnvFile = "${jarvisRuntimeDir}/jarvis.env";
  jarvisDevEnvFile = "${jarvisRuntimeDir}/dev.env";
  jarvisHarnessPort = 8079;
  jarvisApiPort = 8080;
  jarvisSlackPort = 8081;
  jarvisWebPort = 3000;
  jarvisLiteLLMPort = 4000;
  jarvisContainerNetwork = "jarvis-net";
  jarvisWorkEndpoint = "http://100.80.58.4:8091";
  jarvisMacEndpoint = "http://100.90.97.48:8091";
  jarvisVoiceEdgeEndpoint = "http://127.0.0.1:8092";
  jarvisLiteLLMEndpoint = "http://127.0.0.1:${toString jarvisLiteLLMPort}/v1";
  jarvisLiteLLMConfig = "${jarvisRuntimeDir}/litellm-proxy.yaml";
  jarvisLiteLLMImage = "docker.litellm.ai/berriai/litellm:main-latest";
  jarvisApiContainerImage = "localhost/jarvis-api:latest";
  jarvisHarnessContainerImage = "localhost/jarvis-harness:latest";
  jarvisSlackContainerImage = "localhost/jarvis-slack-gateway:latest";
  jarvisWebContainerImage = "localhost/jarvis-web:latest";
  jarvisSupabaseProjectRef = "ysxipmxwfupqzywhevji";
  jarvisSupabaseApiHost = "${jarvisSupabaseProjectRef}.supabase.co";
  jarvisSupabasePoolerHost = "aws-1-us-east-2.pooler.supabase.com";
  jarvisSupabasePoolerPort = 6543;
  jarvisSupabasePoolerUser = "postgres.${jarvisSupabaseProjectRef}";
  jarvisUsageDb = "${jarvisDataDir}/usage.db";
  jarvisWebDir = "${jarvisRepoDir}/web";
  openAiSecretPath = lib.attrByPath [ "sops" "secrets" "openai_api_key" "path" ] "" config;
  geminiSecretPath = lib.attrByPath [ "sops" "secrets" "gemini_api_key" "path" ] "" config;
  openRouterSecretPath = lib.attrByPath [ "sops" "secrets" "openrouter_api_key" "path" ] "" config;
  jarvisPython = pkgs.python3.withPackages (
    ps: with ps; [
      fastapi
      httpx
      psycopg
      pyyaml
      uvicorn
      websockets
    ]
  );
in
{
  users.groups.jarvis = { };
  users.users.jarvis = {
    isSystemUser = true;
    group = "jarvis";
    home = "/var/lib/jarvis";
    createHome = true;
    extraGroups = [ "podman" ];
    subUidRanges = [ { startUid = 200000; count = 65536; } ];
    subGidRanges = [ { startGid = 200000; count = 65536; } ];
  };

  services.ollama.enable = true;

  networking.firewall.interfaces.tailscale0.allowedTCPPorts = [
    jarvisApiPort
    jarvisHarnessPort
  ];

  environment.systemPackages = lib.mkAfter [
    jarvisPython
  ];

  sops.secrets.jarvis_slack_bot_token = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_signing_secret = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_app_id = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_client_id = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_client_secret = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_channel_ops = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_channel_approvals = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_channel_audit = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_channel_dev = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_channel_incidents = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_work_shared_token = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_dashboard_password = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_supbabase_db_password = {
    sopsFile = ../../secrets/jarvis.yaml;
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };

  systemd.tmpfiles.rules = [
    "L+ ${jarvisRepoDir} - - - - ${jarvisLegacyRepoDir}"
    "d ${jarvisLegacyRepoDir} 0755 cdenneen users -"
    "d ${jarvisRuntimeDir} 0750 jarvis jarvis -"
    "d ${jarvisDataDir} 0750 jarvis jarvis -"
    "d ${jarvisSecretsDir} 0750 jarvis jarvis -"
  ];

  systemd.services.jarvis-runtime-sanitize = {
    description = "Normalize Jarvis runtime ownership and clear stale listeners";
    wantedBy = [ "multi-user.target" ];
    before = [
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
      "jarvis-api.service"
      "jarvis-slack-gateway.service"
      "jarvis-web.service"
    ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.lsof
      pkgs.procps
      pkgs.podman
      pkgs.sudo
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      Group = "root";
    };
    script = ''
      set -euo pipefail

      ${pkgs.coreutils}/bin/install -d -m 0750 -o jarvis -g jarvis "${jarvisRuntimeDir}" "${jarvisDataDir}" "${jarvisSecretsDir}"
      ${pkgs.coreutils}/bin/chown -R jarvis:jarvis "${jarvisDataDir}"
      for dir in "${jarvisRuntimeDir}/.config" "${jarvisRuntimeDir}/.cache" "${jarvisRuntimeDir}/.local"; do
        ${pkgs.coreutils}/bin/install -d -m 0700 -o jarvis -g jarvis "$dir"
        ${pkgs.coreutils}/bin/chown -R jarvis:jarvis "$dir"
      done

      home_repo="/home/cdenneen/src/workspace/nix/home"
      if [ -d "$home_repo" ]; then
        status_out="$(${pkgs.git}/bin/git -c safe.directory="$home_repo" -C "$home_repo" status --porcelain 2>&1 || true)"
        if [ -n "$status_out" ]; then
          ${pkgs.coreutils}/bin/echo "jarvis-runtime-sanitize: refusing to continue with dirty or unreadable home repo at $home_repo" >&2
          ${pkgs.coreutils}/bin/echo "$status_out" >&2
          exit 1
        fi
      fi

      jarvis_uid="$(${pkgs.coreutils}/bin/id -u jarvis)"
      ${pkgs.coreutils}/bin/install -d -m 0700 -o jarvis -g jarvis "/run/user/$jarvis_uid"
      podman_as_jarvis() {
        ${pkgs.sudo}/bin/sudo -n -u jarvis env HOME="${jarvisRuntimeDir}" XDG_RUNTIME_DIR="/run/user/$jarvis_uid" ${pkgs.podman}/bin/podman "$@"
      }

      required_images=(
        "${jarvisApiContainerImage}"
        "${jarvisHarnessContainerImage}"
        "${jarvisSlackContainerImage}"
        "${jarvisWebContainerImage}"
        "${jarvisLiteLLMImage}"
      )
      missing=0
      for image in "''${required_images[@]}"; do
        if ! podman_as_jarvis image exists "$image"; then
          ${pkgs.coreutils}/bin/echo "jarvis-runtime-sanitize: missing required image: $image" >&2
          missing=1
        fi
      done
      if [ "$missing" -ne 0 ]; then
        exit 1
      fi

      for port in ${toString jarvisWebPort}; do
        pids="$(${pkgs.lsof}/bin/lsof -tiTCP:$port -sTCP:LISTEN 2>/dev/null || true)"
        if [ -n "$pids" ]; then
          ${pkgs.coreutils}/bin/echo "jarvis-runtime-sanitize: killing stale listener(s) on :$port -> $pids"
          ${pkgs.procps}/bin/kill -9 $pids || true
        fi
      done
    '';
  };

  systemd.services.jarvis-ghost-env = {
    description = "Generate Jarvis ghost runtime env";
    before = [
      "jarvis-harness.service"
      "jarvis-api.service"
      "jarvis-slack-gateway.service"
      "jarvis-web.service"
    ];
    after = [ "jarvis-runtime-sanitize.service" ];
    wants = [ "jarvis-runtime-sanitize.service" ];
    requires = [ "jarvis-runtime-sanitize.service" ];
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

      tmp_env="$(${pkgs.coreutils}/bin/mktemp "${jarvisRuntimeDir}/jarvis.env.XXXXXX")"

      cleanup() {
        ${pkgs.coreutils}/bin/rm -f "$tmp_env"
      }
      trap cleanup EXIT

      write_var() {
        printf '%s=%s\n' "$1" "$2" >> "$tmp_env"
      }

      read_secret() {
        ${pkgs.coreutils}/bin/tr -d '\n\r' < "$1"
      }

      write_var JARVIS_REPO_DIR "${jarvisRepoDir}"
      write_var JARVIS_DATA_DIR "${jarvisDataDir}"
      write_var JARVIS_SECRETS_FILE "${jarvisSecretsFile}"
      write_var JARVIS_REGISTRY_PATH "${jarvisRepoDir}/config/agent_registry.yaml"
      write_var JARVIS_DELEGATION_PATH "${jarvisRepoDir}/config/delegation_policy.yaml"
      write_var JARVIS_MODEL_PROFILES_PATH "${jarvisRepoDir}/config/model_profiles.yaml"
      write_var JARVIS_OLLAMA_MODELS_FILE "${jarvisRepoDir}/config/ollama_models.yaml"
      write_var JARVIS_REALMS_PATH "${jarvisRepoDir}/config/realms.yaml"
      write_var JARVIS_LOCKS_PATH "${jarvisDataDir}/realm_locks.json"
      write_var JARVIS_ROUTING_OUTPUT "${jarvisDataDir}/routing_events.jsonl"
      write_var JARVIS_USAGE_DB "${jarvisUsageDb}"
      write_var JARVIS_POSTGRES_DB_URL "postgresql://jarvis@127.0.0.1:5432/jarvis?sslmode=disable"
      write_var JARVIS_FACTORY_DB_URL "postgresql://jarvis@127.0.0.1:5432/jarvis?sslmode=disable"
      factory_sync_target="none"
      write_var JARVIS_BRAIN_MANIFEST "${jarvisDataDir}/context_manifest.neuronet.jsonl"
      write_var JARVIS_BRAIN_CANDIDATES "${jarvisDataDir}/memory_import_candidates.neuronet.jsonl"
      write_var JARVIS_BRAIN_IMPORT_READY "${jarvisDataDir}/recallium_import_ready.neuronet.jsonl"
      write_var JARVIS_BRAIN_IMPORT_STATE "${jarvisDataDir}/context_import_state.neuronet.json"
      write_var JARVIS_BRAIN_STATE_FILE "${jarvisDataDir}/brain_sync_state.json"
      write_var JARVIS_REMEDIATOR_STATE_FILE "${jarvisDataDir}/autopilot_remediator_state.json"
      write_var JARVIS_REMEDIATOR_POLICY_FILE "${jarvisRepoDir}/config/autopilot_policy.yaml"
      write_var JARVIS_BRAIN_REMOTE_HOST "nyx"
      write_var JARVIS_HARNESS_URL "http://jarvis-harness:${toString jarvisHarnessPort}"
      write_var JARVIS_API_URL "http://jarvis-api:${toString jarvisApiPort}"
      write_var JARVIS_OLLAMA_ENDPOINT "http://127.0.0.1:11434"
      write_var JARVIS_OLLAMA_SYNC_TIMEOUT "3600"
      write_var JARVIS_WORK_ENDPOINT "${jarvisWorkEndpoint}"
      write_var JARVIS_MAC_ENDPOINT "${jarvisMacEndpoint}"
      write_var JARVIS_VOICE_EDGE_ENDPOINT "http://127.0.0.1:8080/voice-edge"
      write_var JARVIS_LLM_GATEWAY_URL "http://jarvis-litellm:${toString jarvisLiteLLMPort}/v1"
      write_var JARVIS_LLM_GATEWAY_API_KEY "jarvis-local-gateway"
      write_var JARVIS_LLM_GATEWAY_CHAIN "jarvis-openrouter,jarvis-gemini,jarvis-openai"
      write_var JARVIS_API_CONTAINER_IMAGE "${jarvisApiContainerImage}"
      write_var JARVIS_HARNESS_CONTAINER_IMAGE "${jarvisHarnessContainerImage}"
      write_var JARVIS_SLACK_CONTAINER_IMAGE "${jarvisSlackContainerImage}"
      write_var JARVIS_WEB_CONTAINER_IMAGE "${jarvisWebContainerImage}"
      write_var JARVIS_VOICE_WS_URL "wss://ai.denneen.net/ws/voice"
      write_var JARVIS_WAKE_PHRASE "Let's get to work Jarvis"
      write_var JARVIS_TTS_MODE "remote_text_local_tts"
      write_var JARVIS_TTS_VOICE_PROFILE "british-ai-assistant"
      write_var SLACK_WORKSPACE_DOMAIN "denneen.slack.com"
      write_var SLACK_BOT_TOKEN "$(read_secret "${config.sops.secrets.jarvis_slack_bot_token.path}")"
      write_var SLACK_SIGNING_SECRET "$(read_secret "${config.sops.secrets.jarvis_slack_signing_secret.path}")"
      write_var SLACK_APP_ID "$(read_secret "${config.sops.secrets.jarvis_slack_app_id.path}")"
      write_var SLACK_CLIENT_ID "$(read_secret "${config.sops.secrets.jarvis_slack_client_id.path}")"
      write_var SLACK_CLIENT_SECRET "$(read_secret "${config.sops.secrets.jarvis_slack_client_secret.path}")"
      write_var SLACK_CHANNEL_OPS "$(read_secret "${config.sops.secrets.jarvis_slack_channel_ops.path}")"
      write_var SLACK_CHANNEL_APPROVALS "$(read_secret "${config.sops.secrets.jarvis_slack_channel_approvals.path}")"
      write_var SLACK_CHANNEL_AUDIT "$(read_secret "${config.sops.secrets.jarvis_slack_channel_audit.path}")"
      write_var SLACK_CHANNEL_DEV "$(read_secret "${config.sops.secrets.jarvis_slack_channel_dev.path}")"
      write_var SLACK_CHANNEL_INCIDENTS "$(read_secret "${config.sops.secrets.jarvis_slack_channel_incidents.path}")"

      if [ -r "${config.sops.secrets.jarvis_work_shared_token.path}" ]; then
        write_var JARVIS_SHARED_TOKEN "$(read_secret "${config.sops.secrets.jarvis_work_shared_token.path}")"
      fi

      if [ -n "${openAiSecretPath}" ] && [ -r "${openAiSecretPath}" ]; then
        write_var OPENAI_API_KEY "$(read_secret "${openAiSecretPath}")"
      fi

      if [ -n "${geminiSecretPath}" ] && [ -r "${geminiSecretPath}" ]; then
        write_var GEMINI_API_KEY "$(read_secret "${geminiSecretPath}")"
      fi

      if [ -n "${openRouterSecretPath}" ] && [ -r "${openRouterSecretPath}" ]; then
        write_var OPENROUTER_API_KEY "$(read_secret "${openRouterSecretPath}")"
      fi

      if [ -r "${config.sops.secrets.jarvis_supbabase_db_password.path}" ]; then
        jarvis_supabase_db_password="$(read_secret "${config.sops.secrets.jarvis_supbabase_db_password.path}")"
        if [ -n "$jarvis_supabase_db_password" ]; then
          write_var JARVIS_SUPABASE_URL "https://${jarvisSupabaseApiHost}"
          write_var JARVIS_SUPABASE_DB_URL "postgresql://${jarvisSupabasePoolerUser}:$jarvis_supabase_db_password@${jarvisSupabasePoolerHost}:${toString jarvisSupabasePoolerPort}/postgres?sslmode=require"
          factory_sync_target="postgresql://${jarvisSupabasePoolerUser}@${jarvisSupabasePoolerHost}:${toString jarvisSupabasePoolerPort}/postgres?sslmode=require"
        fi
      fi

      write_var JARVIS_FACTORY_SYNC_TARGET "$factory_sync_target"

      tmp_litellm="$(${pkgs.coreutils}/bin/mktemp "${jarvisRuntimeDir}/litellm-proxy.yaml.XXXXXX")"
      ${pkgs.coreutils}/bin/cat > "$tmp_litellm" <<'LITELLMCFG'
model_list:
  - model_name: jarvis-ollama
    litellm_params:
      model: ollama/jarvis:fast
      api_base: http://127.0.0.1:11434

  - model_name: jarvis-openrouter
    litellm_params:
      model: openrouter/google/gemma-3-27b-it:free
      api_key: os.environ/OPENROUTER_API_KEY

  - model_name: jarvis-gemini
    litellm_params:
      model: gemini/gemini-3.5-flash
      api_key: os.environ/GEMINI_API_KEY

  - model_name: jarvis-openai
    litellm_params:
      model: openai/gpt-5.4-mini
      api_key: os.environ/OPENAI_API_KEY

general_settings:
  master_key: "jarvis-local-gateway"
LITELLMCFG
      ${pkgs.coreutils}/bin/chown jarvis:jarvis "$tmp_litellm"
      ${pkgs.coreutils}/bin/chmod 0440 "$tmp_litellm"
      ${pkgs.coreutils}/bin/mv -f "$tmp_litellm" "${jarvisLiteLLMConfig}"

      tmp_jarvis_secrets="$(${pkgs.coreutils}/bin/mktemp "${jarvisSecretsDir}/jarvis.yaml.XXXXXX")"
      printf 'JARVIS_DASHBOARD_PASSWORD: %s\n' "$(read_secret "${config.sops.secrets.jarvis_dashboard_password.path}")" > "$tmp_jarvis_secrets"
      ${pkgs.coreutils}/bin/chown jarvis:jarvis "$tmp_jarvis_secrets"
      ${pkgs.coreutils}/bin/chmod 0400 "$tmp_jarvis_secrets"
      ${pkgs.coreutils}/bin/mv -f "$tmp_jarvis_secrets" "${jarvisSecretsFile}"

      ${pkgs.coreutils}/bin/chown jarvis:jarvis "$tmp_env"
      ${pkgs.coreutils}/bin/chmod 0400 "$tmp_env"
      ${pkgs.coreutils}/bin/mv -f "$tmp_env" "${jarvisEnvFile}"
    '';
  };

  systemd.services.jarvis-harness = {
    description = "Jarvis harness service";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "jarvis-ghost-env.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-ghost-env.service"
    ];
    requires = [ "jarvis-ghost-env.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.podman
    ];
    serviceConfig = {
      Type = "simple";
      User = "jarvis";
      Group = "jarvis";
      WorkingDirectory = jarvisRepoDir;
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [
        "HOME=/var/lib/jarvis"
      ];
    };
    script = ''
      set -euo pipefail

      image="''${JARVIS_HARNESS_CONTAINER_IMAGE:-${jarvisHarnessContainerImage}}"
      ${pkgs.podman}/bin/podman network exists "${jarvisContainerNetwork}" >/dev/null 2>&1 || \
        ${pkgs.podman}/bin/podman network create "${jarvisContainerNetwork}" >/dev/null
      ${pkgs.podman}/bin/podman rm -f jarvis-harness >/dev/null 2>&1 || true

      env_args=(--env-file "${jarvisEnvFile}")
      if [ -r "${jarvisDevEnvFile}" ]; then
        env_args+=(--env-file "${jarvisDevEnvFile}")
      fi

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-harness \
        --network host \
        --add-host jarvis-harness:127.0.0.1 \
        --add-host jarvis-api:127.0.0.1 \
        --add-host jarvis-slack-gateway:127.0.0.1 \
        --add-host jarvis-litellm:127.0.0.1 \
        -p 127.0.0.1:${toString jarvisHarnessPort}:${toString jarvisHarnessPort} \
        -v "${jarvisRuntimeDir}:${jarvisRuntimeDir}" \
        "''${env_args[@]}" \
        --env PYTHONPATH=/app/src \
        "$image" \
        --host 127.0.0.1 \
        --port ${toString jarvisHarnessPort} \
        --repo-dir "/app" \
        --registry "/app/config/agent_registry.yaml" \
        --delegation "/app/config/delegation_policy.yaml" \
        --model-profiles "/app/config/model_profiles.yaml" \
        --realms "/app/config/realms.yaml" \
        --admin-token "''${JARVIS_SHARED_TOKEN:-}" \
        --worker-token-store "${jarvisDataDir}/worker-registration-tokens.json" \
        --locks "''${JARVIS_LOCKS_PATH:-${jarvisDataDir}/realm_locks.json}" \
        --routing-output "''${JARVIS_ROUTING_OUTPUT:-${jarvisDataDir}/routing_events.jsonl}"
    '';
  };

  systemd.services.jarvis-api = {
    description = "Jarvis API";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-litellm.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-litellm.service"
    ];
    requires = [
      "jarvis-ghost-env.service"
    ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.podman
    ];
    serviceConfig = {
      Type = "simple";
      User = "jarvis";
      Group = "jarvis";
      WorkingDirectory = jarvisRepoDir;
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [
        "HOME=/var/lib/jarvis"
      ];
    };
    script = ''
      set -euo pipefail

      image="''${JARVIS_API_CONTAINER_IMAGE:-${jarvisApiContainerImage}}"
      ${pkgs.podman}/bin/podman network exists "${jarvisContainerNetwork}" >/dev/null 2>&1 || \
        ${pkgs.podman}/bin/podman network create "${jarvisContainerNetwork}" >/dev/null
      ${pkgs.podman}/bin/podman rm -f jarvis-api >/dev/null 2>&1 || true

      env_args=(--env-file "${jarvisEnvFile}")
      if [ -r "${jarvisDevEnvFile}" ]; then
        env_args+=(--env-file "${jarvisDevEnvFile}")
      fi

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-api \
        --network host \
        --add-host jarvis-harness:127.0.0.1 \
        --add-host jarvis-api:127.0.0.1 \
        --add-host jarvis-slack-gateway:127.0.0.1 \
        --add-host jarvis-litellm:127.0.0.1 \
        -p 127.0.0.1:${toString jarvisApiPort}:${toString jarvisApiPort} \
        -v "${jarvisRuntimeDir}:${jarvisRuntimeDir}" \
        "''${env_args[@]}" \
        "$image" \
        --host 0.0.0.0 \
        --port ${toString jarvisApiPort} \
        --harness-url "''${JARVIS_HARNESS_URL:-http://127.0.0.1:${toString jarvisHarnessPort}}" \
        --work-endpoint "''${JARVIS_WORK_ENDPOINT:-}" \
        --work-shared-token "''${JARVIS_SHARED_TOKEN:-}" \
        --mac-endpoint "''${JARVIS_MAC_ENDPOINT:-}" \
        --voice-edge-endpoint "''${JARVIS_VOICE_EDGE_ENDPOINT:-}" \
        --mac-shared-token "''${JARVIS_SHARED_TOKEN:-}" \
        --usage-db "''${JARVIS_USAGE_DB:-${jarvisUsageDb}}" \
        --usage-cost-db "''${JARVIS_USAGE_COST_DB:-${jarvisDataDir}/usage_cost.db}" \
        --factory-db "''${JARVIS_FACTORY_DB_URL:-''${JARVIS_POSTGRES_DB_URL:-}}" \
        --routing-events-file "''${JARVIS_ROUTING_OUTPUT:-${jarvisDataDir}/routing_events.jsonl}" \
        --project-map-file "/app/data/project_overlap_map.neuronet.json" \
        --remediator-state-file "''${JARVIS_REMEDIATOR_STATE_FILE:-${jarvisDataDir}/autopilot_remediator_state.json}" \
        --remediator-policy-file "/app/config/autopilot_policy.yaml" \
        --slack-endpoint "http://jarvis-slack-gateway:${toString jarvisSlackPort}" \
        --ollama-endpoint "''${JARVIS_OLLAMA_ENDPOINT:-http://127.0.0.1:11434}" \
        --supabase-url "''${JARVIS_SUPABASE_URL:-}" \
        --supabase-key "''${JARVIS_SUPABASE_KEY:-}"
    '';
  };

  systemd.services.jarvis-litellm = {
    description = "Jarvis LiteLLM gateway";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "ollama.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "ollama.service"
    ];
    requires = [ "jarvis-ghost-env.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.podman
    ];
    serviceConfig = {
      Type = "simple";
      User = "jarvis";
      Group = "jarvis";
      WorkingDirectory = jarvisRepoDir;
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [
        "HOME=/var/lib/jarvis"
      ];
    };
    script = ''
      set -euo pipefail

      if [ ! -f "${jarvisLiteLLMConfig}" ]; then
        echo "jarvis-litellm: missing config at ${jarvisLiteLLMConfig}" >&2
        exit 1
      fi

      ${pkgs.podman}/bin/podman network exists "${jarvisContainerNetwork}" >/dev/null 2>&1 || \
        ${pkgs.podman}/bin/podman network create "${jarvisContainerNetwork}" >/dev/null
      ${pkgs.podman}/bin/podman rm -f jarvis-litellm >/dev/null 2>&1 || true

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-litellm \
        --network host \
        -v "${jarvisLiteLLMConfig}:/app/config.yaml:ro" \
        --env OPENROUTER_API_KEY \
        --env OPENAI_API_KEY \
        --env GEMINI_API_KEY \
        ${jarvisLiteLLMImage} \
        --config /app/config.yaml --port ${toString jarvisLiteLLMPort}
    '';
  };

  systemd.services.jarvis-slack-gateway = {
    description = "Jarvis Slack gateway";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
      "jarvis-api.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
      "jarvis-api.service"
    ];
    requires = [
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
      "jarvis-api.service"
    ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.podman
    ];
    serviceConfig = {
      Type = "simple";
      User = "jarvis";
      Group = "jarvis";
      WorkingDirectory = jarvisRepoDir;
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [
        "HOME=/var/lib/jarvis"
      ];
    };
    script = ''
      set -euo pipefail

      image="''${JARVIS_SLACK_CONTAINER_IMAGE:-${jarvisSlackContainerImage}}"
      ${pkgs.podman}/bin/podman network exists "${jarvisContainerNetwork}" >/dev/null 2>&1 || \
        ${pkgs.podman}/bin/podman network create "${jarvisContainerNetwork}" >/dev/null
      ${pkgs.podman}/bin/podman rm -f jarvis-slack-gateway >/dev/null 2>&1 || true

      env_args=(--env-file "${jarvisEnvFile}")
      if [ -r "${jarvisDevEnvFile}" ]; then
        env_args+=(--env-file "${jarvisDevEnvFile}")
      fi

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-slack-gateway \
        --network host \
        --add-host jarvis-harness:127.0.0.1 \
        --add-host jarvis-api:127.0.0.1 \
        --add-host jarvis-slack-gateway:127.0.0.1 \
        --add-host jarvis-litellm:127.0.0.1 \
        -p 127.0.0.1:${toString jarvisSlackPort}:${toString jarvisSlackPort} \
        -v "${jarvisRuntimeDir}:${jarvisRuntimeDir}" \
        "''${env_args[@]}" \
        --env PYTHONPATH=/app/src \
        "$image" \
        --host 0.0.0.0 \
        --port ${toString jarvisSlackPort} \
        --registry "/app/config/agent_registry.yaml" \
        --delegation "/app/config/delegation_policy.yaml" \
        --routing-output "''${JARVIS_ROUTING_OUTPUT:-${jarvisDataDir}/routing_events.jsonl}" \
        --realms "/app/config/realms.yaml" \
        --locks "''${JARVIS_LOCKS_PATH:-${jarvisDataDir}/realm_locks.json}" \
        --api-url "http://jarvis-api:${toString jarvisApiPort}"
    '';
  };

  systemd.services.jarvis-factory-sync = {
    description = "Jarvis Factory sync to remote target";
    after = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-api.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-api.service"
    ];
    requires = [ "jarvis-ghost-env.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.podman
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "jarvis";
      Group = "jarvis";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/var/lib/jarvis" ];
    };
    script = ''
      set -euo pipefail

      sync_target="''${JARVIS_FACTORY_SYNC_TARGET:-none}"
      sync_dsn="''${JARVIS_SUPABASE_DB_URL:-}"
      if [ "$sync_target" = "none" ] || [ -z "$sync_dsn" ]; then
        exit 0
      fi

      image="''${JARVIS_API_CONTAINER_IMAGE:-${jarvisApiContainerImage}}"
      ${pkgs.podman}/bin/podman rm -f jarvis-factory-sync >/dev/null 2>&1 || true

      env_args=(--env-file "${jarvisEnvFile}")
      if [ -r "${jarvisDevEnvFile}" ]; then
        env_args+=(--env-file "${jarvisDevEnvFile}")
      fi

      if ! ${pkgs.podman}/bin/podman run --rm --name jarvis-factory-sync \
        --network host \
        -v "${jarvisRuntimeDir}:${jarvisRuntimeDir}" \
        "''${env_args[@]}" \
        --env PYTHONPATH=/app/src \
        --entrypoint python \
        "$image" \
        /app/scripts/factory-sync \
        --source-dsn "''${JARVIS_FACTORY_DB_URL:-''${JARVIS_POSTGRES_DB_URL:-}}" \
        --target-dsn "$sync_dsn"; then
        echo "jarvis-factory-sync: warning: sync failed (best effort mode)" >&2
      fi
    '';
  };

  systemd.timers.jarvis-factory-sync = {
    description = "Run Jarvis Factory sync every 5 minutes";
    wantedBy = [ "timers.target" ];
    partOf = [ "jarvis-factory-sync.service" ];
    timerConfig = {
      OnBootSec = "2m";
      OnUnitActiveSec = "5m";
      RandomizedDelaySec = "20s";
      Unit = "jarvis-factory-sync.service";
    };
  };

  systemd.services.jarvis-api-image-prune = {
    description = "Prune unused jarvis-api images older than 4h";
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.podman
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "jarvis";
      Group = "jarvis";
      Environment = [ "HOME=/var/lib/jarvis" ];
    };
    script = ''
      set -euo pipefail

      cutoff_epoch="$(${pkgs.coreutils}/bin/date -u -d '4 hours ago' +%s)"
      ids="$(${pkgs.podman}/bin/podman image ls --filter reference='*jarvis-api*' -q | ${pkgs.coreutils}/bin/sort -u)"
      if [ -z "$ids" ]; then
        exit 0
      fi

      for id in $ids; do
        if [ -z "$id" ]; then
          continue
        fi

        in_use="$(${pkgs.podman}/bin/podman ps -a --filter ancestor="$id" -q | ${pkgs.coreutils}/bin/wc -l | ${pkgs.coreutils}/bin/tr -d '[:space:]')"
        if [ "$in_use" != "0" ]; then
          continue
        fi

        created="$(${pkgs.podman}/bin/podman image inspect "$id" --format '{{.Created}}' 2>/dev/null || true)"
        if [ -z "$created" ]; then
          continue
        fi
        created_epoch="$(${pkgs.coreutils}/bin/date -u -d "$created" +%s 2>/dev/null || echo 0)"
        if [ "$created_epoch" -eq 0 ]; then
          continue
        fi

        if [ "$created_epoch" -lt "$cutoff_epoch" ]; then
          ${pkgs.podman}/bin/podman image rm "$id" >/dev/null 2>&1 || true
        fi
      done
    '';
  };

  systemd.timers.jarvis-api-image-prune = {
    description = "Run jarvis-api image prune every hour";
    wantedBy = [ "timers.target" ];
    partOf = [ "jarvis-api-image-prune.service" ];
    timerConfig = {
      OnBootSec = "10m";
      OnUnitActiveSec = "1h";
      RandomizedDelaySec = "5m";
      Unit = "jarvis-api-image-prune.service";
    };
  };

  systemd.services.jarvis-web = {
    description = "Jarvis web service";
    wantedBy = [ "multi-user.target" ];
    after = [ "jarvis-ghost-env.service" ];
    wants = [ "jarvis-ghost-env.service" ];
    requires = [ "jarvis-ghost-env.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.podman
    ];
    serviceConfig = {
      Type = "simple";
      User = "jarvis";
      Group = "jarvis";
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [
        "HOME=/var/lib/jarvis"
      ];
    };
    script = ''
      set -euo pipefail

      image="''${JARVIS_WEB_CONTAINER_IMAGE:-${jarvisWebContainerImage}}"
      ${pkgs.podman}/bin/podman network exists "${jarvisContainerNetwork}" >/dev/null 2>&1 || \
        ${pkgs.podman}/bin/podman network create "${jarvisContainerNetwork}" >/dev/null
      ${pkgs.podman}/bin/podman rm -f jarvis-web >/dev/null 2>&1 || true

      env_args=(--env-file "${jarvisEnvFile}")
      if [ -r "${jarvisDevEnvFile}" ]; then
        env_args+=(--env-file "${jarvisDevEnvFile}")
      fi

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-web \
        --network host \
        --add-host jarvis-harness:127.0.0.1 \
        --add-host jarvis-api:127.0.0.1 \
        --add-host jarvis-slack-gateway:127.0.0.1 \
        --add-host jarvis-litellm:127.0.0.1 \
        -p 127.0.0.1:${toString jarvisWebPort}:${toString jarvisWebPort} \
        "''${env_args[@]}" \
        "$image"
    '';
  };

  systemd.services.jarvis-ollama-model-sync = {
    description = "Jarvis Ollama tier model sync";
    after = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "ollama.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "ollama.service"
    ];
    requires = [ "jarvis-ghost-env.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.podman
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "jarvis";
      Group = "jarvis";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/var/lib/jarvis" ];
    };
    script = ''
      set -euo pipefail

      image="''${JARVIS_API_CONTAINER_IMAGE:-${jarvisApiContainerImage}}"
      ${pkgs.podman}/bin/podman rm -f jarvis-ollama-model-sync >/dev/null 2>&1 || true

      env_args=(--env-file "${jarvisEnvFile}")
      if [ -r "${jarvisDevEnvFile}" ]; then
        env_args+=(--env-file "${jarvisDevEnvFile}")
      fi

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-ollama-model-sync \
        --network host \
        -v "${jarvisRuntimeDir}:${jarvisRuntimeDir}" \
        "''${env_args[@]}" \
        --env PYTHONPATH=/app/src \
        --entrypoint python \
        "$image" \
        /app/services/jarvis-ollama-model-sync.py \
        --ollama-endpoint "''${JARVIS_OLLAMA_ENDPOINT:-http://127.0.0.1:11434}" \
        --models-file "/app/config/ollama_models.yaml" \
        --state-file "${jarvisDataDir}/ollama_model_sync_state.json" \
        --timeout "''${JARVIS_OLLAMA_SYNC_TIMEOUT:-3600}"
    '';
  };

  systemd.timers.jarvis-ollama-model-sync = {
    description = "Periodic Jarvis Ollama model sync";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2m";
      OnUnitActiveSec = "6h";
      Unit = "jarvis-ollama-model-sync.service";
      Persistent = true;
    };
  };

  systemd.services.jarvis-objective-cycle = {
    description = "Jarvis objective planning and execution cycle";
    after = [
      "network-online.target"
      "jarvis-api.service"
      "jarvis-ghost-env.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-api.service"
      "jarvis-ghost-env.service"
    ];
    requires = [ "jarvis-ghost-env.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.podman
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "jarvis";
      Group = "jarvis";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/var/lib/jarvis" ];
    };
    script = ''
      set -euo pipefail

      image="''${JARVIS_API_CONTAINER_IMAGE:-${jarvisApiContainerImage}}"
      ${pkgs.podman}/bin/podman rm -f jarvis-objective-cycle >/dev/null 2>&1 || true

      env_args=(--env-file "${jarvisEnvFile}")
      if [ -r "${jarvisDevEnvFile}" ]; then
        env_args+=(--env-file "${jarvisDevEnvFile}")
      fi

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-objective-cycle \
        --network host \
        -v "${jarvisRuntimeDir}:${jarvisRuntimeDir}" \
        "''${env_args[@]}" \
        --env PYTHONPATH=/app/src \
        --entrypoint python \
        "$image" \
        /app/services/jarvis-objective-cycle.py \
        --api-url "''${JARVIS_API_URL:-http://127.0.0.1:${toString jarvisApiPort}}" \
        --state-file "${jarvisDataDir}/objective_cycle_state.json"
    '';
  };

  systemd.timers.jarvis-objective-cycle = {
    description = "Periodic Jarvis objective execution cycle";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "3m";
      OnUnitActiveSec = "30m";
      Unit = "jarvis-objective-cycle.service";
      Persistent = true;
    };
  };

  systemd.services.jarvis-brain-sync = {
    description = "Jarvis neuronet brain sync";
    after = [
      "network-online.target"
      "jarvis-ghost-env.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-ghost-env.service"
    ];
    requires = [ "jarvis-ghost-env.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.openssh
      pkgs.tmux
      jarvisPython
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "jarvis";
      Group = "jarvis";
      WorkingDirectory = jarvisRepoDir;
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/var/lib/jarvis" ];
    };
    script = ''
      set -euo pipefail
      export PYTHONPATH="${jarvisRepoDir}/src"
      exec ${jarvisPython}/bin/python ${jarvisRepoDir}/src/jarvis/brain_sync.py \
        --repo-dir "${jarvisRepoDir}" \
        --remote-host "''${JARVIS_BRAIN_REMOTE_HOST:-nyx}" \
        --manifest "''${JARVIS_BRAIN_MANIFEST}" \
        --candidates "''${JARVIS_BRAIN_CANDIDATES}" \
        --import-ready "''${JARVIS_BRAIN_IMPORT_READY}" \
        --import-state "''${JARVIS_BRAIN_IMPORT_STATE}" \
        --state-file "''${JARVIS_BRAIN_STATE_FILE}" \
        --limit 300
    '';
  };

  systemd.services.jarvis-autopilot-remediator = {
    description = "Jarvis autopilot remediator";
    after = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-api.service"
      "jarvis-slack-gateway.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-api.service"
      "jarvis-slack-gateway.service"
    ];
    requires = [
      "jarvis-ghost-env.service"
      "jarvis-api.service"
      "jarvis-slack-gateway.service"
    ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.podman
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "jarvis";
      Group = "jarvis";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/var/lib/jarvis" ];
    };
    script = ''
      set -euo pipefail

      image="''${JARVIS_API_CONTAINER_IMAGE:-${jarvisApiContainerImage}}"
      ${pkgs.podman}/bin/podman rm -f jarvis-autopilot-remediator >/dev/null 2>&1 || true

      env_args=(--env-file "${jarvisEnvFile}")
      if [ -r "${jarvisDevEnvFile}" ]; then
        env_args+=(--env-file "${jarvisDevEnvFile}")
      fi

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-autopilot-remediator \
        --network host \
        -v "${jarvisRuntimeDir}:${jarvisRuntimeDir}" \
        "''${env_args[@]}" \
        --env PYTHONPATH=/app/src \
        --entrypoint python \
        "$image" \
        /app/src/jarvis/autopilot_remediator.py \
        --api-url "''${JARVIS_API_URL}" \
        --routing-file "''${JARVIS_ROUTING_OUTPUT}" \
        --state-file "''${JARVIS_REMEDIATOR_STATE_FILE}" \
        --policy-file "/app/config/autopilot_policy.yaml" \
        --stale-after-seconds "''${JARVIS_REMEDIATOR_STALE_SECONDS:-900}" \
        --cooldown-seconds "''${JARVIS_REMEDIATOR_COOLDOWN_SECONDS:-420}" \
        --max-actions "''${JARVIS_REMEDIATOR_MAX_ACTIONS:-3}"
    '';
  };

  systemd.timers.jarvis-brain-sync = {
    description = "Run Jarvis neuronet brain sync every 20 minutes";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "5m";
      OnUnitActiveSec = "20m";
      RandomizedDelaySec = "90s";
      Persistent = true;
      Unit = "jarvis-brain-sync.service";
    };
  };

  systemd.timers.jarvis-autopilot-remediator = {
    description = "Run Jarvis autopilot remediator every 3 minutes";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "3m";
      OnUnitActiveSec = "3m";
      RandomizedDelaySec = "30s";
      Persistent = true;
      Unit = "jarvis-autopilot-remediator.service";
    };
  };
}
