{
  config,
  lib,
  pkgs,
  ...
}:
let
  jarvisRepoDir = "/opt/jarvis";
  jarvisRuntimeDir = "/var/lib/jarvis";
  jarvisDataDir = "${jarvisRuntimeDir}/data";
  jarvisSecretsDir = "${jarvisDataDir}/secrets";
  jarvisSecretsFile = "${jarvisSecretsDir}/jarvis.yaml";
  jarvisEnvFile = "${jarvisRuntimeDir}/jarvis.env";
  jarvisHarnessPort = 8079;
  jarvisApiPort = 8080;
  jarvisSlackPort = 8081;
  jarvisWebPort = 3000;
  jarvisLiteLLMPort = 4000;
  jarvisWorkEndpoint = "http://100.80.58.4:8090";
  jarvisMacEndpoint = "http://100.90.97.48:8091";
  jarvisVoiceEdgeEndpoint = "http://127.0.0.1:8091";
  jarvisLiteLLMEndpoint = "http://127.0.0.1:${toString jarvisLiteLLMPort}/v1";
  jarvisLiteLLMConfig = "${jarvisRepoDir}/config/litellm-proxy.yaml";
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
  services.ollama.enable = true;

  networking.firewall.interfaces.tailscale0.allowedTCPPorts = [ jarvisApiPort ];

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
    "d ${jarvisRepoDir} 0755 cdenneen users -"
    "d ${jarvisRuntimeDir} 0750 cdenneen users -"
    "d ${jarvisDataDir} 0750 cdenneen users -"
    "d ${jarvisSecretsDir} 0750 cdenneen users -"
  ];

  systemd.services.jarvis-ghost-env = {
    description = "Generate Jarvis ghost runtime env";
    before = [
      "jarvis-harness.service"
      "jarvis-api.service"
      "jarvis-slack-gateway.service"
      "jarvis-web.service"
    ];
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
      factory_sync_target="none"
      write_var JARVIS_BRAIN_MANIFEST "${jarvisDataDir}/context_manifest.neuronet.jsonl"
      write_var JARVIS_BRAIN_CANDIDATES "${jarvisDataDir}/memory_import_candidates.neuronet.jsonl"
      write_var JARVIS_BRAIN_IMPORT_READY "${jarvisDataDir}/recallium_import_ready.neuronet.jsonl"
      write_var JARVIS_BRAIN_IMPORT_STATE "${jarvisDataDir}/context_import_state.neuronet.json"
      write_var JARVIS_BRAIN_STATE_FILE "${jarvisDataDir}/brain_sync_state.json"
      write_var JARVIS_REMEDIATOR_STATE_FILE "${jarvisDataDir}/autopilot_remediator_state.json"
      write_var JARVIS_REMEDIATOR_POLICY_FILE "${jarvisRepoDir}/config/autopilot_policy.yaml"
      write_var JARVIS_BRAIN_REMOTE_HOST "nyx"
      write_var JARVIS_HARNESS_URL "http://127.0.0.1:${toString jarvisHarnessPort}"
      write_var JARVIS_API_URL "http://127.0.0.1:${toString jarvisApiPort}"
      write_var JARVIS_OLLAMA_ENDPOINT "http://127.0.0.1:11434"
      write_var JARVIS_OLLAMA_SYNC_TIMEOUT "3600"
      write_var JARVIS_WORK_ENDPOINT "${jarvisWorkEndpoint}"
      write_var JARVIS_MAC_ENDPOINT "${jarvisMacEndpoint}"
      write_var JARVIS_VOICE_EDGE_ENDPOINT "${jarvisVoiceEdgeEndpoint}"
      write_var JARVIS_LLM_GATEWAY_URL "${jarvisLiteLLMEndpoint}"
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

      tmp_jarvis_secrets="$(${pkgs.coreutils}/bin/mktemp "${jarvisSecretsDir}/jarvis.yaml.XXXXXX")"
      printf 'JARVIS_DASHBOARD_PASSWORD: %s\n' "$(read_secret "${config.sops.secrets.jarvis_dashboard_password.path}")" > "$tmp_jarvis_secrets"
      ${pkgs.coreutils}/bin/chown cdenneen:users "$tmp_jarvis_secrets"
      ${pkgs.coreutils}/bin/chmod 0400 "$tmp_jarvis_secrets"
      ${pkgs.coreutils}/bin/mv -f "$tmp_jarvis_secrets" "${jarvisSecretsFile}"

      ${pkgs.coreutils}/bin/chown cdenneen:users "$tmp_env"
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
      jarvisPython
    ];
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = jarvisRepoDir;
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail

      if [ ! -d "${jarvisRepoDir}/src/jarvis" ]; then
        echo "jarvis-harness: repo checkout missing at ${jarvisRepoDir}" >&2
        exit 1
      fi

      export PYTHONPATH="${jarvisRepoDir}/src"

      exec ${jarvisPython}/bin/python ${jarvisRepoDir}/services/jarvis-harness-service.py \
        --host 127.0.0.1 \
        --port ${toString jarvisHarnessPort} \
        --repo-dir "$JARVIS_REPO_DIR" \
        --registry "$JARVIS_REGISTRY_PATH" \
        --delegation "$JARVIS_DELEGATION_PATH" \
        --model-profiles "$JARVIS_MODEL_PROFILES_PATH" \
        --realms "$JARVIS_REALMS_PATH" \
        --locks "$JARVIS_LOCKS_PATH" \
        --routing-output "$JARVIS_ROUTING_OUTPUT"
    '';
  };

  systemd.services.jarvis-api = {
    description = "Jarvis API";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
      "jarvis-litellm.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
      "jarvis-litellm.service"
    ];
    requires = [
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
    ];
    path = [
      pkgs.bash
      pkgs.coreutils
      jarvisPython
    ];
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = jarvisRepoDir;
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail

      exec ${jarvisPython}/bin/python ${jarvisRepoDir}/services/jarvis-api-service.py \
        --host 0.0.0.0 \
        --port ${toString jarvisApiPort} \
        --harness-url "$JARVIS_HARNESS_URL" \
        --work-endpoint "$JARVIS_WORK_ENDPOINT" \
        --work-shared-token "''${JARVIS_WORK_SHARED_TOKEN:-''${JARVIS_SHARED_TOKEN:-}}" \
        --mac-endpoint "''${JARVIS_MAC_ENDPOINT:-}" \
        --voice-edge-endpoint "''${JARVIS_VOICE_EDGE_ENDPOINT:-}" \
        --mac-shared-token "''${JARVIS_MAC_SHARED_TOKEN:-''${JARVIS_SHARED_TOKEN:-}}" \
        --usage-db "''${JARVIS_USAGE_DB:-${jarvisUsageDb}}" \
        --usage-cost-db "''${JARVIS_USAGE_COST_DB:-${jarvisDataDir}/usage_cost.db}" \
        --factory-db "''${JARVIS_FACTORY_DB_URL:-''${JARVIS_POSTGRES_DB_URL:-}}" \
        --routing-events-file "''${JARVIS_ROUTING_OUTPUT:-${jarvisDataDir}/routing_events.jsonl}" \
        --project-map-file "${jarvisRepoDir}/data/project_overlap_map.neuronet.json" \
        --remediator-state-file "''${JARVIS_REMEDIATOR_STATE_FILE:-${jarvisDataDir}/autopilot_remediator_state.json}" \
        --remediator-policy-file "''${JARVIS_REMEDIATOR_POLICY_FILE:-${jarvisRepoDir}/config/autopilot_policy.yaml}" \
        --slack-endpoint "http://127.0.0.1:${toString jarvisSlackPort}" \
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
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = jarvisRepoDir;
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail

      if [ ! -f "${jarvisLiteLLMConfig}" ]; then
        echo "jarvis-litellm: missing config at ${jarvisLiteLLMConfig}" >&2
        exit 1
      fi

      ${pkgs.podman}/bin/podman rm -f jarvis-litellm >/dev/null 2>&1 || true

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-litellm -p 127.0.0.1:${toString jarvisLiteLLMPort}:${toString jarvisLiteLLMPort} \
        -v "${jarvisLiteLLMConfig}:/app/config.yaml:ro" \
        --env OPENROUTER_API_KEY \
        --env OPENAI_API_KEY \
        --env GEMINI_API_KEY \
        docker.litellm.ai/berriai/litellm:main-latest \
        --config /app/config.yaml --port ${toString jarvisLiteLLMPort}
    '';
  };

  # Staging scaffold for future containerized API rollout.
  # Intentionally not enabled by default while python-based jarvis-api.service remains primary.
  systemd.services.jarvis-api-container = {
    description = "Jarvis API container (staged)";
    conflicts = [ "jarvis-api.service" ];
    after = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
      "jarvis-litellm.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
      "jarvis-litellm.service"
    ];
    requires = [
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
    ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.podman
    ];
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = jarvisRepoDir;
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail

      image="''${JARVIS_API_CONTAINER_IMAGE:-${jarvisApiContainerImage}}"
      ${pkgs.podman}/bin/podman rm -f jarvis-api >/dev/null 2>&1 || true

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-api --network host \
        -v "${jarvisRepoDir}:${jarvisRepoDir}:ro" \
        -v "${jarvisRuntimeDir}:${jarvisRuntimeDir}" \
        --env-file "${jarvisEnvFile}" \
        "$image" \
        --host 0.0.0.0 \
        --port ${toString jarvisApiPort} \
        --harness-url "''${JARVIS_HARNESS_URL:-http://127.0.0.1:${toString jarvisHarnessPort}}" \
        --work-endpoint "''${JARVIS_WORK_ENDPOINT:-}" \
        --work-shared-token "''${JARVIS_WORK_SHARED_TOKEN:-''${JARVIS_SHARED_TOKEN:-}}" \
        --mac-endpoint "''${JARVIS_MAC_ENDPOINT:-}" \
        --voice-edge-endpoint "''${JARVIS_VOICE_EDGE_ENDPOINT:-}" \
        --mac-shared-token "''${JARVIS_MAC_SHARED_TOKEN:-''${JARVIS_SHARED_TOKEN:-}}" \
        --usage-db "''${JARVIS_USAGE_DB:-${jarvisUsageDb}}" \
        --usage-cost-db "''${JARVIS_USAGE_COST_DB:-${jarvisDataDir}/usage_cost.db}" \
        --factory-db "''${JARVIS_FACTORY_DB_URL:-''${JARVIS_POSTGRES_DB_URL:-}}" \
        --routing-events-file "''${JARVIS_ROUTING_OUTPUT:-${jarvisDataDir}/routing_events.jsonl}" \
        --project-map-file "${jarvisRepoDir}/data/project_overlap_map.neuronet.json" \
        --remediator-state-file "''${JARVIS_REMEDIATOR_STATE_FILE:-${jarvisDataDir}/autopilot_remediator_state.json}" \
        --remediator-policy-file "''${JARVIS_REMEDIATOR_POLICY_FILE:-${jarvisRepoDir}/config/autopilot_policy.yaml}" \
        --slack-endpoint "http://127.0.0.1:${toString jarvisSlackPort}" \
        --ollama-endpoint "''${JARVIS_OLLAMA_ENDPOINT:-http://127.0.0.1:11434}" \
        --supabase-url "''${JARVIS_SUPABASE_URL:-}" \
        --supabase-key "''${JARVIS_SUPABASE_KEY:-}"
    '';
  };

  systemd.services.jarvis-harness-container = {
    description = "Jarvis harness container (staged)";
    conflicts = [ "jarvis-harness.service" ];
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
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = jarvisRepoDir;
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail

      image="''${JARVIS_HARNESS_CONTAINER_IMAGE:-${jarvisHarnessContainerImage}}"
      ${pkgs.podman}/bin/podman rm -f jarvis-harness >/dev/null 2>&1 || true

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-harness --network host \
        -v "${jarvisRepoDir}:${jarvisRepoDir}:ro" \
        -v "${jarvisRuntimeDir}:${jarvisRuntimeDir}" \
        --env-file "${jarvisEnvFile}" \
        --env PYTHONPATH=/app/src \
        "$image" \
        --host 127.0.0.1 \
        --port ${toString jarvisHarnessPort} \
        --repo-dir "''${JARVIS_REPO_DIR:-${jarvisRepoDir}}" \
        --registry "''${JARVIS_REGISTRY_PATH:-${jarvisRepoDir}/config/agent_registry.yaml}" \
        --delegation "''${JARVIS_DELEGATION_PATH:-${jarvisRepoDir}/config/delegation_policy.yaml}" \
        --model-profiles "''${JARVIS_MODEL_PROFILES_PATH:-${jarvisRepoDir}/config/model_profiles.yaml}" \
        --realms "''${JARVIS_REALMS_PATH:-${jarvisRepoDir}/config/realm_policy.yaml}" \
        --locks "''${JARVIS_LOCKS_PATH:-${jarvisDataDir}/realm_locks.json}" \
        --routing-output "''${JARVIS_ROUTING_OUTPUT:-${jarvisDataDir}/routing_events.jsonl}"
    '';
  };

  systemd.services.jarvis-slack-gateway-container = {
    description = "Jarvis Slack gateway container (staged)";
    conflicts = [ "jarvis-slack-gateway.service" ];
    after = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-harness-container.service"
      "jarvis-api-container.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-harness-container.service"
      "jarvis-api-container.service"
    ];
    requires = [ "jarvis-ghost-env.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
      pkgs.podman
    ];
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = jarvisRepoDir;
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail

      image="''${JARVIS_SLACK_CONTAINER_IMAGE:-${jarvisSlackContainerImage}}"
      ${pkgs.podman}/bin/podman rm -f jarvis-slack-gateway >/dev/null 2>&1 || true

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-slack-gateway --network host \
        -v "${jarvisRepoDir}:${jarvisRepoDir}:ro" \
        -v "${jarvisRuntimeDir}:${jarvisRuntimeDir}" \
        --env-file "${jarvisEnvFile}" \
        --env PYTHONPATH=/app/src \
        "$image" \
        --host 127.0.0.1 \
        --port ${toString jarvisSlackPort} \
        --registry "''${JARVIS_REGISTRY_PATH:-${jarvisRepoDir}/config/agent_registry.yaml}" \
        --delegation "''${JARVIS_DELEGATION_PATH:-${jarvisRepoDir}/config/delegation_policy.yaml}" \
        --routing-output "''${JARVIS_ROUTING_OUTPUT:-${jarvisDataDir}/routing_events.jsonl}" \
        --realms "''${JARVIS_REALMS_PATH:-${jarvisRepoDir}/config/realm_policy.yaml}" \
        --locks "''${JARVIS_LOCKS_PATH:-${jarvisDataDir}/realm_locks.json}" \
        --api-url "http://127.0.0.1:${toString jarvisApiPort}"
    '';
  };

  systemd.services.jarvis-web-container = {
    description = "Jarvis web container (staged)";
    conflicts = [ "jarvis-web.service" ];
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
      User = "cdenneen";
      Group = "users";
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail

      image="''${JARVIS_WEB_CONTAINER_IMAGE:-${jarvisWebContainerImage}}"
      ${pkgs.podman}/bin/podman rm -f jarvis-web >/dev/null 2>&1 || true

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-web --network host \
        -v "${jarvisRepoDir}:${jarvisRepoDir}:ro" \
        "$image"
    '';
  };

  systemd.services.jarvis-slack-gateway = {
    description = "Jarvis Slack gateway";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
    ];
    wants = [
      "network-online.target"
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
    ];
    requires = [
      "jarvis-ghost-env.service"
      "jarvis-harness.service"
    ];
    path = [
      pkgs.bash
      pkgs.coreutils
      jarvisPython
    ];
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = jarvisRepoDir;
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail

      if [ ! -f "${jarvisRepoDir}/src/jarvis/slack_gateway.py" ]; then
        echo "jarvis-slack-gateway: slack gateway entrypoint missing at ${jarvisRepoDir}" >&2
        exit 1
      fi

      export PYTHONPATH="${jarvisRepoDir}/src"

      exec ${jarvisPython}/bin/python ${jarvisRepoDir}/src/jarvis/slack_gateway.py \
        --host 127.0.0.1 \
        --port ${toString jarvisSlackPort} \
        --registry "$JARVIS_REGISTRY_PATH" \
        --delegation "$JARVIS_DELEGATION_PATH" \
        --routing-output "$JARVIS_ROUTING_OUTPUT" \
        --realms "$JARVIS_REALMS_PATH" \
        --locks "$JARVIS_LOCKS_PATH" \
        --api-url "http://127.0.0.1:${toString jarvisApiPort}"
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
      jarvisPython
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = jarvisRepoDir;
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail

      sync_target="''${JARVIS_FACTORY_SYNC_TARGET:-none}"
      sync_dsn="''${JARVIS_SUPABASE_DB_URL:-}"
      if [ "$sync_target" = "none" ] || [ -z "$sync_dsn" ]; then
        exit 0
      fi

      if ! ${jarvisPython}/bin/python ${jarvisRepoDir}/scripts/factory-sync \
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
      User = "cdenneen";
      Group = "users";
      Environment = [ "HOME=/home/cdenneen" ];
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
    description = "Jarvis web placeholder";
    wantedBy = [ "multi-user.target" ];
    after = [ "jarvis-ghost-env.service" ];
    wants = [ "jarvis-ghost-env.service" ];
    requires = [ "jarvis-ghost-env.service" ];
    path = [
      pkgs.bash
      pkgs.coreutils
      jarvisPython
    ];
    serviceConfig = {
      Type = "simple";
      User = "cdenneen";
      Group = "users";
      Restart = "always";
      RestartSec = "10s";
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail

      if [ ! -f "${jarvisWebDir}/index.html" ]; then
        echo "jarvis-web: missing portal index at ${jarvisWebDir}/index.html" >&2
        exit 1
      fi

      exec ${jarvisPython}/bin/python -m http.server ${toString jarvisWebPort} \
        --bind 127.0.0.1 \
        --directory ${jarvisWebDir}
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
      jarvisPython
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = jarvisRepoDir;
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail
      exec ${jarvisPython}/bin/python ${jarvisRepoDir}/services/jarvis-ollama-model-sync.py \
        --ollama-endpoint "''${JARVIS_OLLAMA_ENDPOINT:-http://127.0.0.1:11434}" \
        --models-file "''${JARVIS_OLLAMA_MODELS_FILE:-${jarvisRepoDir}/config/ollama_models.yaml}" \
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
      jarvisPython
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = jarvisRepoDir;
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail
      exec ${jarvisPython}/bin/python ${jarvisRepoDir}/services/jarvis-objective-cycle.py \
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
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = jarvisRepoDir;
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
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
      jarvisPython
    ];
    serviceConfig = {
      Type = "oneshot";
      User = "cdenneen";
      Group = "users";
      WorkingDirectory = jarvisRepoDir;
      EnvironmentFile = [ jarvisEnvFile ];
      Environment = [ "HOME=/home/cdenneen" ];
    };
    script = ''
      set -euo pipefail
      export PYTHONPATH="${jarvisRepoDir}/src"
      exec ${jarvisPython}/bin/python ${jarvisRepoDir}/src/jarvis/autopilot_remediator.py \
        --api-url "''${JARVIS_API_URL}" \
        --routing-file "''${JARVIS_ROUTING_OUTPUT}" \
        --state-file "''${JARVIS_REMEDIATOR_STATE_FILE}" \
        --policy-file "''${JARVIS_REMEDIATOR_POLICY_FILE}" \
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
