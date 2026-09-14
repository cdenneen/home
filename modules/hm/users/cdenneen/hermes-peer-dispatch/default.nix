{
  agentPkgs ? null,
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.hermesPeerDispatch;
  packageAvailable = pkgs.stdenv.hostPlatform.isLinux && agentPkgs != null && agentPkgs ? hermes;
  configPath = "${config.home.homeDirectory}/.config/hermes-peer-dispatch/config.json";
  stateDirectory = "${config.home.homeDirectory}/.local/state/hermes-peer-dispatch";
  dispatchPackage = pkgs.writeShellApplication {
    name = "hermes-peer-dispatch";
    runtimeInputs = [ agentPkgs.hermes ];
    text = ''
      exec ${agentPkgs.hermes.hermesVenv}/bin/python3 ${./peer_dispatch.py} \
        --config ${lib.escapeShellArg configPath} \
        --state-dir ${lib.escapeShellArg stateDirectory} \
        "$@"
    '';
  };
in
{
  options.profiles.hermesPeerDispatch = {
    enable = lib.mkEnableOption "durable isolated Hermes peer-run dispatch and completion tracking";

    peers = lib.mkOption {
      type = lib.types.attrsOf (
        lib.types.submodule {
          options = {
            url = lib.mkOption {
              type = lib.types.str;
              description = "Hermes peer gateway URL.";
            };
            keyFile = lib.mkOption {
              type = lib.types.str;
              description = "Path to the peer API key file.";
            };
          };
        }
      );
      default = { };
      description = "Non-secret peer endpoints and runtime secret file paths.";
    };

    kanbanProfile = lib.mkOption {
      type = lib.types.str;
      default = "chief-of-staff";
      description = "Hermes profile that owns the central Kanban.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = packageAvailable;
        message = "profiles.hermesPeerDispatch requires Linux and agentPkgs.hermes";
      }
      {
        assertion = cfg.peers != { };
        message = "profiles.hermesPeerDispatch.peers must not be empty";
      }
    ];

    home.packages = [ dispatchPackage ];

    home.file.".config/hermes-peer-dispatch/config.json".text = builtins.toJSON {
      hermes_bin = "${agentPkgs.hermes}/bin/hermes";
      kanban_profile = cfg.kanbanProfile;
      peers = lib.mapAttrs (_: value: {
        inherit (value) url;
        key_file = value.keyFile;
      }) cfg.peers;
    };

    systemd.user.services.hermes-peer-dispatch = {
      Unit = {
        Description = "Reconcile durable Hermes peer-run completions";
        After = [
          "hermes-mesh-gateway.service"
          "network-online.target"
          "sops-nix.service"
        ];
        Wants = [
          "network-online.target"
          "sops-nix.service"
        ];
      };
      Service = {
        Type = "oneshot";
        UMask = "0077";
        ExecStart = "${dispatchPackage}/bin/hermes-peer-dispatch reconcile";
      };
    };

    systemd.user.timers.hermes-peer-dispatch = {
      Unit.Description = "Minute-level Hermes peer-run completion reconciliation";
      Timer = {
        OnBootSec = "1m";
        OnUnitActiveSec = "1m";
        Persistent = true;
      };
      Install.WantedBy = [ "timers.target" ];
    };
  };
}
