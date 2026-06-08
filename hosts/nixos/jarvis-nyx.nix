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
  jarvisEnvFile = "${jarvisRuntimeDir}/work-runner.env";
  jarvisDevEnvFile = "${jarvisRuntimeDir}/dev.env";
  jarvisWorkContainerImage = "localhost/jarvis-work-runner:latest";
  jarvisWorkPort = 8091;
  jarvisGhostApiEndpoint = "http://100.114.242.29:8080";
  jarvisGhostHarnessEndpoint = "http://100.114.242.29:8079";
  jarvisNyxPublicEndpoint = "http://100.80.58.4:8091";
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
    pkgs.podman
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
    before = [
      "jarvis-work-runner.service"
      "jarvis-work-runner-container.service"
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
      write_var JARVIS_WORK_STATUS_CALLBACK_URL "${jarvisGhostApiEndpoint}/api/tasks/worker-update"
      write_var JARVIS_HARNESS_URL "${jarvisGhostHarnessEndpoint}"
      write_var JARVIS_WORKER_PUBLIC_ENDPOINT "${jarvisNyxPublicEndpoint}"
      write_var JARVIS_WORKER_REALM "work"

      if [ -r "${config.sops.secrets.jarvis_work_shared_token.path}" ]; then
        shared_token="$(read_secret "${config.sops.secrets.jarvis_work_shared_token.path}")"
        write_var JARVIS_SHARED_TOKEN "$shared_token"
        write_var JARVIS_WORKER_REGISTRATION_TOKEN "$shared_token"
      fi

      ${pkgs.coreutils}/bin/chown cdenneen:users "$tmp_env"
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

      image="''${JARVIS_WORK_RUNNER_CONTAINER_IMAGE:-${jarvisWorkContainerImage}}"
      ${pkgs.podman}/bin/podman rm -f jarvis-work-runner >/dev/null 2>&1 || true

      env_args=(--env-file "${jarvisEnvFile}")
      if [ -r "${jarvisDevEnvFile}" ]; then
        env_args+=(--env-file "${jarvisDevEnvFile}")
      fi

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-work-runner --network host \
        -v "${jarvisRuntimeDir}:${jarvisRuntimeDir}" \
        "''${env_args[@]}" \
        "$image" \
        --host 0.0.0.0 \
        --port ${toString jarvisWorkPort} \
        --shared-token "''${JARVIS_SHARED_TOKEN:-}" \
        --callback-url "''${JARVIS_WORK_STATUS_CALLBACK_URL:-}" \
        --callback-token "''${JARVIS_SHARED_TOKEN:-}" \
        --worker-id "''${JARVIS_WORKER_ID:-${jarvisWorkerId}}" \
        --capabilities "''${JARVIS_WORKER_CAPABILITIES:-${jarvisWorkerCapabilities}}" \
        --harness-url "''${JARVIS_HARNESS_URL:-}" \
        --registration-token "''${JARVIS_WORKER_REGISTRATION_TOKEN:-}" \
        --public-endpoint "''${JARVIS_WORKER_PUBLIC_ENDPOINT:-}" \
        --worker-realm "''${JARVIS_WORKER_REALM:-work}" \
        --state-file "${jarvisDataDir}/work-runner-state.json" \
        --repo-dir "${jarvisRepoDir}"
    '';
  };
}
