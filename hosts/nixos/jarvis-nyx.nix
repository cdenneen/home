{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.jarvis;
  jarvisRepoDir = "/opt/jarvis";
  jarvisRuntimeDir = "/var/lib/jarvis";
  jarvisDataDir = "${jarvisRuntimeDir}/data";
  jarvisEnvFile = "${jarvisRuntimeDir}/jarvis-node.env";
  jarvisDevEnvFile = "${jarvisRuntimeDir}/dev.env";
  jarvisNodeContainerImage = "registry.gitlab.com/cdenneen/my-jarvis/jarvis-node:latest";
  jarvisNodePort = 8091;
  jarvisGhostApiEndpoint = "http://100.114.242.29:8080";
  jarvisGhostHarnessEndpoint = "http://100.114.242.29:8079";
  jarvisNyxPublicEndpoint = "http://100.80.58.4:8091";
  jarvisNodeId = "nyx-node-1";
  jarvisNodeCapabilities = "code,triage,documentation,investigation";
  jarvisPython = pkgs.python3.withPackages (
    ps: with ps; [
      fastapi
      uvicorn
    ]
  );
in
lib.mkIf (cfg.enable && cfg.role == "node") {
  networking.firewall.interfaces.tailscale0.allowedTCPPorts = [ jarvisNodePort ];

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

  systemd.services.jarvis-node-env = {
    description = "Generate Jarvis node env";
    before = [
      "jarvis-node.service"
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

      tmp_env="$(${pkgs.coreutils}/bin/mktemp "${jarvisRuntimeDir}/jarvis-node.env.XXXXXX")"

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
      write_var JARVIS_WORK_BIND "0.0.0.0:${toString jarvisNodePort}"
      write_var JARVIS_WORKER_ID "${jarvisNodeId}"
      write_var JARVIS_WORKER_CAPABILITIES "${jarvisNodeCapabilities}"
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

  systemd.services.jarvis-node = {
    description = "Jarvis node";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "tailscaled.service"
      "jarvis-node-env.service"
    ];
    wants = [
      "network-online.target"
      "tailscaled.service"
      "jarvis-node-env.service"
    ];
    requires = [ "jarvis-node-env.service" ];
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

      image="''${JARVIS_NODE_CONTAINER_IMAGE:-${jarvisNodeContainerImage}}"
      ${pkgs.podman}/bin/podman rm -f jarvis-node >/dev/null 2>&1 || true

      env_args=(--env-file "${jarvisEnvFile}")
      if [ -r "${jarvisDevEnvFile}" ]; then
        env_args+=(--env-file "${jarvisDevEnvFile}")
      fi

      exec ${pkgs.podman}/bin/podman run --rm --name jarvis-node --network host \
        -v "${jarvisRuntimeDir}:${jarvisRuntimeDir}" \
        "''${env_args[@]}" \
        "$image" \
        --host 0.0.0.0 \
        --port ${toString jarvisNodePort} \
        --shared-token "''${JARVIS_SHARED_TOKEN:-}" \
        --callback-url "''${JARVIS_WORK_STATUS_CALLBACK_URL:-}" \
        --callback-token "''${JARVIS_SHARED_TOKEN:-}" \
        --worker-id "''${JARVIS_WORKER_ID:-${jarvisNodeId}}" \
        --capabilities "''${JARVIS_WORKER_CAPABILITIES:-${jarvisNodeCapabilities}}" \
        --harness-url "''${JARVIS_HARNESS_URL:-}" \
        --registration-token "''${JARVIS_WORKER_REGISTRATION_TOKEN:-}" \
        --public-endpoint "''${JARVIS_WORKER_PUBLIC_ENDPOINT:-}" \
        --worker-realm "''${JARVIS_WORKER_REALM:-work}" \
        --state-file "${jarvisDataDir}/jarvis-node-state.json" \
        --repo-dir "${jarvisRepoDir}"
    '';
  };
}
