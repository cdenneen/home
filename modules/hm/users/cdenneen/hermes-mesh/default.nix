{
  agentPkgs ? null,
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.hermesMesh;
  packageAvailable = pkgs.stdenv.hostPlatform.isLinux && agentPkgs != null && agentPkgs ? hermes;
  singleWriterLock = import ../hermes-single-writer-lock.nix { inherit pkgs; };
  workloadMetadata = import ../hermes-workload-metadata {
    inherit pkgs agentPkgs;
  };
  workloadMetadataSitecustomize = workloadMetadata.mkCombinedSitecustomize "";
  hermesHome = "${config.home.homeDirectory}/.hermes";
  gatewayChecks = pkgs.writeShellScript "hermes-mesh-gateway-checks" ''
    set -euo pipefail
    test -r ${lib.escapeShellArg "${hermesHome}/config.yaml"}
    test -d ${lib.escapeShellArg cfg.workingDirectory}
  '';
in
{
  options.profiles.hermesMesh = {
    enable = lib.mkEnableOption "the persistent multiplexed Hermes peer mesh gateway";

    workingDirectory = lib.mkOption {
      type = lib.types.str;
      default = "${config.home.homeDirectory}/src/workspace";
      description = "Default working directory for the Hermes mesh gateway.";
    };

    consumer = lib.mkOption {
      type = lib.types.str;
      description = "Stable Eros spend-attribution consumer name.";
    };

    trustDomain = lib.mkOption {
      type = lib.types.enum [
        "personal"
        "work"
      ];
      description = "Fixed Eros trust domain for this Hermes mesh.";
    };

    souls = lib.mkOption {
      type = lib.types.attrsOf lib.types.lines;
      default = { };
      description = "Normative SOUL.md contents keyed by Hermes profile name.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = packageAvailable;
        message = "profiles.hermesMesh requires Linux and agentPkgs.hermes";
      }
      {
        assertion = cfg.souls != { };
        message = "profiles.hermesMesh requires at least one declared profile SOUL.md";
      }
    ];

    home.packages = [ agentPkgs.hermes ];

    home.file = lib.mapAttrs' (
      name: soul:
      lib.nameValuePair ".hermes/profiles/${name}/SOUL.md" {
        text = soul;
      }
    ) cfg.souls;

    profiles.hermesSingleWriterRegistry.entries = [
      {
        name = "hermes-mesh-gateway";
        inherit hermesHome;
      }
    ];

    systemd.user.services.hermes-mesh-gateway = {
      Unit = {
        Description = "Hermes multiplexed agent mesh gateway";
        After = [
          "network-online.target"
          "sops-nix.service"
        ];
        Wants = [
          "network-online.target"
          "sops-nix.service"
        ];
        StartLimitIntervalSec = 60;
        StartLimitBurst = 5;
      };
      Service = {
        Type = "simple";
        ExecStartPre = [
          gatewayChecks
          workloadMetadata.selftestCheck
        ];
        ExecStart = singleWriterLock.wrapExecStart {
          lockPath = "${hermesHome}/.single-writer.lock";
          execStart = "${agentPkgs.hermes}/bin/hermes gateway run --replace --external-supervisor";
        };
        WorkingDirectory = cfg.workingDirectory;
        Environment = [
          "HERMES_HOME=%h/.hermes"
          "PYTHONPATH=${workloadMetadataSitecustomize}"
          "EROS_CONSUMER=${cfg.consumer}"
          "EROS_TRUST_DOMAIN=${cfg.trustDomain}"
        ];
        Restart = "on-failure";
        RestartSec = 5;
        RestartPreventExitStatus = 78;
        TimeoutStopSec = 180;
        KillMode = "mixed";
      };
      Install.WantedBy = [ "default.target" ];
    };
  };
}
