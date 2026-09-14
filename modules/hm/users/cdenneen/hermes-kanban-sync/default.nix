{
  agentPkgs ? null,
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.hermesKanbanSync;
  packageAvailable = pkgs.stdenv.hostPlatform.isLinux && agentPkgs != null && agentPkgs ? hermes;
  configPath = "${config.home.homeDirectory}/.config/hermes-kanban-sync/config.json";
  stateDirectory = "${config.home.homeDirectory}/.local/state/hermes-kanban-sync";
  syncPackage = pkgs.writeShellApplication {
    name = "hermes-gitlab-sync";
    runtimeInputs = [
      agentPkgs.hermes
      pkgs.openssh
    ];
    text = ''
      export HERMES_BIN=${agentPkgs.hermes}/bin/hermes
      exec ${agentPkgs.hermes.hermesVenv}/bin/python3 ${./backlog_sync.py} "$@"
    '';
  };
in
{
  options.profiles.hermesKanbanSync = {
    installCollector = lib.mkEnableOption "the GitLab backlog collector used by a remote reconciler";

    enable = lib.mkEnableOption "hourly GitLab to Hermes Kanban reconciliation";

    outboundEnabled = lib.mkEnableOption "outbound Hermes status reconciliation to GitLab";

    settings = lib.mkOption {
      type = lib.types.attrs;
      default = { };
      description = "Non-secret logical project and GitLab source inventory.";
    };
  };

  config = lib.mkIf (cfg.enable || cfg.installCollector) {
    assertions = [
      {
        assertion = packageAvailable;
        message = "profiles.hermesKanbanSync requires Linux and agentPkgs.hermes";
      }
      {
        assertion = !cfg.enable || cfg.settings != { };
        message = "profiles.hermesKanbanSync.settings must define explicit project scope";
      }
    ];

    home.packages = [ syncPackage ];

    home.file.".config/hermes-kanban-sync/config.json" = lib.mkIf cfg.enable {
      text = builtins.toJSON cfg.settings;
    };

    systemd.user.services.hermes-kanban-sync = lib.mkIf cfg.enable {
      Unit = {
        Description = "Reconcile authoritative GitLab backlogs into Hermes Kanban";
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
        Environment = [ "HERMES_HOME=%h/.hermes" ];
        ExecStart = "${pkgs.util-linux}/bin/flock -n %t/hermes-kanban-sync.lock ${syncPackage}/bin/hermes-gitlab-sync reconcile --config ${configPath} --state-dir ${stateDirectory}${lib.optionalString cfg.outboundEnabled " --allow-outbound"}";
      };
    };

    systemd.user.timers.hermes-kanban-sync = lib.mkIf cfg.enable {
      Unit.Description = "Hourly Hermes Kanban backlog reconciliation";
      Timer = {
        OnBootSec = "5m";
        OnUnitActiveSec = "1h";
        Persistent = true;
        RandomizedDelaySec = "5m";
      };
      Install.WantedBy = [ "timers.target" ];
    };
  };
}
