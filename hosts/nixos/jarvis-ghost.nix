{ config, lib, pkgs, ... }:
let
  jarvisRepoDir = "/opt/jarvis";
  jarvisRuntimeDir = "/var/lib/jarvis";
  jarvisDataDir = "${jarvisRuntimeDir}/data";
  jarvisEnvFile = "${jarvisRuntimeDir}/ghost.env";
  jarvisHarnessPort = 8079;
  jarvisApiPort = 8080;
  jarvisSlackPort = 8081;
  jarvisWebPort = 3000;
  jarvisWorkEndpoint = "http://nyx.tail0e55.ts.net:8090";
  jarvisMacEndpoint = "http://vnjtecmbcd.tail0e55.ts.net:8091";
  jarvisPython = pkgs.python3.withPackages (
    ps: with ps; [
      fastapi
      httpx
      pyyaml
      uvicorn
    ]
  );
  jarvisWebRoot = pkgs.writeTextDir "jarvis-web/index.html" ''
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <title>Jarvis Control Plane</title>
      </head>
      <body>
        <h1>Jarvis control plane</h1>
        <p>Public ingress is live on ghost.</p>
      </body>
    </html>
  '';
in
{
  environment.systemPackages = lib.mkAfter [
    jarvisPython
  ];

  sops.secrets.jarvis_slack_bot_token = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_signing_secret = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_app_id = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_client_id = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_client_secret = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_channel_ops = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_channel_approvals = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_channel_audit = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_channel_dev = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_slack_channel_incidents = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };
  sops.secrets.jarvis_work_shared_token = {
    owner = "cdenneen";
    group = "users";
    mode = "0400";
  };

  systemd.tmpfiles.rules = [
    "d ${jarvisRepoDir} 0755 cdenneen users -"
    "d ${jarvisRuntimeDir} 0750 cdenneen users -"
    "d ${jarvisDataDir} 0750 cdenneen users -"
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

      tmp_env="$(${pkgs.coreutils}/bin/mktemp "${jarvisRuntimeDir}/ghost.env.XXXXXX")"

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
      write_var JARVIS_REGISTRY_PATH "${jarvisRepoDir}/config/agent_registry.yaml"
      write_var JARVIS_REALMS_PATH "${jarvisRepoDir}/config/realms.yaml"
      write_var JARVIS_LOCKS_PATH "${jarvisDataDir}/realm_locks.json"
      write_var JARVIS_ROUTING_OUTPUT "${jarvisDataDir}/routing_events.jsonl"
      write_var JARVIS_HARNESS_URL "http://127.0.0.1:${toString jarvisHarnessPort}"
      write_var JARVIS_WORK_ENDPOINT "${jarvisWorkEndpoint}"
      write_var JARVIS_MAC_ENDPOINT "${jarvisMacEndpoint}"
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
        write_var JARVIS_WORK_SHARED_TOKEN "$(read_secret "${config.sops.secrets.jarvis_work_shared_token.path}")"
      fi

      if [ -r "${config.sops.secrets.jarvis_work_shared_token.path}" ]; then
        write_var JARVIS_MAC_SHARED_TOKEN "$(read_secret "${config.sops.secrets.jarvis_work_shared_token.path}")"
      fi

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

      exec ${jarvisPython}/bin/python ${../../modules/shared/files/jarvis-harness-service.py} \
        --host 127.0.0.1 \
        --port ${toString jarvisHarnessPort} \
        --repo-dir "$JARVIS_REPO_DIR" \
        --registry "$JARVIS_REGISTRY_PATH" \
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

      exec ${jarvisPython}/bin/python ${../../modules/shared/files/jarvis-api-service.py} \
        --host 127.0.0.1 \
        --port ${toString jarvisApiPort} \
        --harness-url "$JARVIS_HARNESS_URL" \
        --work-endpoint "$JARVIS_WORK_ENDPOINT" \
        --work-shared-token "''${JARVIS_WORK_SHARED_TOKEN:-}" \
        --mac-endpoint "''${JARVIS_MAC_ENDPOINT:-}" \
        --mac-shared-token "''${JARVIS_MAC_SHARED_TOKEN:-}"
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
        --routing-output "$JARVIS_ROUTING_OUTPUT" \
        --realms "$JARVIS_REALMS_PATH" \
        --locks "$JARVIS_LOCKS_PATH"
    '';
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

      exec ${jarvisPython}/bin/python -m http.server ${toString jarvisWebPort} \
        --bind 127.0.0.1 \
        --directory ${jarvisWebRoot}/jarvis-web
    '';
  };
}
