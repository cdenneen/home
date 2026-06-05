{ config, lib, pkgs, ... }:
let
  jarvisRepoDir = "/opt/jarvis";
  jarvisRuntimeDir = "/var/lib/jarvis";
  jarvisDataDir = "${jarvisRuntimeDir}/data";
  jarvisEnvFile = "${jarvisRuntimeDir}/work-runner.env";
  jarvisWorkPort = 8090;
  jarvisWorkerId = "nyx-worker-1";
  jarvisWorkerCapabilities = "code,triage,documentation,investigation";
  jarvisPython = pkgs.python3.withPackages (
    ps: with ps; [
      fastapi
      uvicorn
    ]
  );
in
{
  networking.firewall.interfaces.tailscale0.allowedTCPPorts = [ jarvisWorkPort ];

  environment.systemPackages = lib.mkAfter [
    jarvisPython
  ];

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

  systemd.services.jarvis-work-env = {
    description = "Generate Jarvis work runner env";
    before = [ "jarvis-work-runner.service" ];
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

      tmp_env="$(${pkgs.coreutils}/bin/mktemp "${jarvisRuntimeDir}/work-runner.env.XXXXXX")"

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
      write_var JARVIS_WORK_BIND "0.0.0.0:${toString jarvisWorkPort}"
      write_var JARVIS_WORKER_ID "${jarvisWorkerId}"
      write_var JARVIS_WORKER_CAPABILITIES "${jarvisWorkerCapabilities}"
      write_var JARVIS_WORK_STATUS_CALLBACK_URL "https://ai.denneen.net/api/tasks/worker-update"

      if [ -r "${config.sops.secrets.jarvis_work_shared_token.path}" ]; then
        write_var JARVIS_WORK_SHARED_TOKEN "$(read_secret "${config.sops.secrets.jarvis_work_shared_token.path}")"
      fi

      ${pkgs.coreutils}/bin/chmod 0400 "$tmp_env"
      ${pkgs.coreutils}/bin/mv -f "$tmp_env" "${jarvisEnvFile}"
    '';
  };

  systemd.services.jarvis-work-runner = {
    description = "Jarvis work runner";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "tailscaled.service"
      "jarvis-work-env.service"
    ];
    wants = [
      "network-online.target"
      "tailscaled.service"
      "jarvis-work-env.service"
    ];
    requires = [ "jarvis-work-env.service" ];
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

      exec ${jarvisPython}/bin/python ${../../modules/shared/files/jarvis-work-runner-service.py} \
        --host 0.0.0.0 \
        --port ${toString jarvisWorkPort} \
        --shared-token "''${JARVIS_WORK_SHARED_TOKEN:-}" \
        --callback-url "''${JARVIS_WORK_STATUS_CALLBACK_URL:-}" \
        --callback-token "''${JARVIS_WORK_SHARED_TOKEN:-}" \
        --worker-id "''${JARVIS_WORKER_ID:-${jarvisWorkerId}}" \
        --capabilities "''${JARVIS_WORKER_CAPABILITIES:-${jarvisWorkerCapabilities}}" \
        --state-file "${jarvisDataDir}/work-runner-state.json" \
        --repo-dir "${jarvisRepoDir}"
    '';
  };
}
