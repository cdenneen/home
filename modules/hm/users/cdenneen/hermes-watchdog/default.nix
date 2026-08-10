{
  agentPkgs ? null,
  config,
  lib,
  pkgs,
  self,
  ...
}:
let
  packageAvailable = pkgs.stdenv.isLinux && agentPkgs != null && agentPkgs ? hermes;
  watchdogEnabled = config.profiles.hermesWatchdog.enable && packageAvailable;
  runtimeRoot = "${config.home.homeDirectory}/.hermes/supervisor/axis-development-watchdog";
  watchdogPython = pkgs.python3;
  supervisorPython = pkgs.python3.withPackages (pythonPackages: [ pythonPackages.jsonschema ]);
  hermesRevision = "f5be9236e00ddf2f2a412697f267078fc4ee068e";
  sourceRevision =
    if self ? rev then
      self.rev
    else if self ? dirtyRev then
      self.dirtyRev
    else
      "unknown";
  watchdogLauncher = pkgs.replaceVars ./scripts/watchdog_launcher.py.in {
    inherit watchdogPython watchdogCanonicalProjector watchdogCutoverReconcile;
  };
  watchdog = pkgs.writeShellScriptBin "axis-development-watchdog" ''
    set -euo pipefail
    exec "$HOME/.hermes/scripts/axis-development-watchdog.py" "$@"
  '';
  watchdogCronCtl = pkgs.writeShellScriptBin "axis-development-watchdog-cronctl" ''
    set -euo pipefail
    exec ${watchdogPython}/bin/python "$HOME/.hermes/scripts/axis-development-watchdog-cronctl.py" "$@"
  '';
  watchdogCutoverCtl = pkgs.writeShellScriptBin "axis-development-watchdog-cutoverctl" ''
    set -euo pipefail
    export AXIS_WATCHDOG_CUTOVER_RECONCILE_COMMAND=${watchdogCutoverReconcile}/bin/axis-development-watchdog-cutover-reconcile
    exec ${watchdogPython}/bin/python "$HOME/.hermes/scripts/axis-development-watchdog-cutoverctl.py" "$@"
  '';
  watchdogMonitor = pkgs.writeShellScriptBin "axis-development-watchdog-monitor" ''
    set -euo pipefail
    export AXIS_WATCHDOG_SYSTEMCTL=${pkgs.systemd}/bin/systemctl
    exec ${watchdogPython}/bin/python "$HOME/.hermes/scripts/axis-development-watchdog-monitor.py"
  '';
  watchdogDiagnose = pkgs.writeShellScriptBin "axis-development-watchdog-diagnose" ''
    set -euo pipefail
    export AXIS_WATCHDOG_HERMES_REVISION=${hermesRevision}
    exec ${agentPkgs.hermes.hermesVenv}/bin/python3 "$HOME/.hermes/scripts/axis-development-watchdog-diagnostic.py"
  '';
  watchdogCanonicalProjector = pkgs.writeShellScriptBin "axis-development-watchdog-canonical-projector" ''
    set -euo pipefail
    exec ${supervisorPython}/bin/python "$HOME/.hermes/scripts/axis-development-supervisor-slack.py" "$@"
  '';
  watchdogCutoverReconcile = pkgs.writeShellScriptBin "axis-development-watchdog-cutover-reconcile" ''
    set -euo pipefail
    export AXIS_SUPERVISOR_MUTATION_SOURCE=home-manager
    exec ${config.home.profileDirectory}/bin/axis-development-supervisor-cronctl install --hermes ${agentPkgs.hermes}/bin/hermes
  '';
  watchdogSelfRepair = pkgs.writeShellScriptBin "axis-development-watchdog-self-repair" ''
    set -euo pipefail
    exec ${watchdogCronCtl}/bin/axis-development-watchdog-cronctl install --hermes ${agentPkgs.hermes}/bin/hermes
  '';
  watchdogRuntimeRepair = pkgs.writeShellScriptBin "axis-development-watchdog-runtime-repair" ''
    set -euo pipefail
    ${pkgs.systemd}/bin/systemctl --user reset-failed axis-development-watchdog-backup.service
    exec ${pkgs.systemd}/bin/systemctl --user restart axis-development-watchdog-backup.timer
  '';
in
{
  options.profiles.hermesWatchdog.enable = lib.mkEnableOption "independent AXIS Development Watchdog";

  config = lib.mkMerge [
    {
      profiles.hermesWatchdog.enable = lib.mkDefault config.profiles.hermesSupervisor.enable;
    }
    (lib.mkIf watchdogEnabled {
      home.packages = [
        watchdog
        watchdogCronCtl
        watchdogCutoverCtl
        watchdogMonitor
        watchdogDiagnose
        watchdogCanonicalProjector
        watchdogCutoverReconcile
        watchdogSelfRepair
        watchdogRuntimeRepair
      ];

      home.file = {
        ".hermes/supervisor/axis-development-watchdog/docs" = {
          source = ./docs;
          recursive = true;
        };
        ".hermes/supervisor/axis-development-watchdog/schemas" = {
          source = ./schemas;
          recursive = true;
        };
        ".hermes/supervisor/axis-development-watchdog/VERSION".source = ./VERSION;
        ".hermes/supervisor/axis-development-watchdog/deployed-source-revision.json".text =
          builtins.toJSON
            {
              revision = sourceRevision;
              dirty = !(self ? rev);
              rollback = "home-manager generations";
            };
      };

      systemd.user.services.hermes-watchdog-cron = {
        Unit = {
          Description = "Provision independent AXIS Development Watchdog cron";
          After = [
            "hermes-gateway.service"
            "hermes-supervisor-cron.service"
          ];
          Requires = [ "hermes-gateway.service" ];
        };
        Service = {
          Type = "oneshot";
          ExecStart = "${watchdogCronCtl}/bin/axis-development-watchdog-cronctl install --hermes ${agentPkgs.hermes}/bin/hermes";
          RemainAfterExit = true;
        };
        Install.WantedBy = [ "default.target" ];
      };

      systemd.user.services.hermes-watchdog-cutover = {
        Unit = {
          Description = "Reconcile staged AXIS Slack projection cutover";
          After = [
            "hermes-supervisor-cron.service"
            "hermes-watchdog-cron.service"
          ];
          Wants = [
            "hermes-supervisor-cron.service"
            "hermes-watchdog-cron.service"
          ];
        };
        Service = {
          Type = "oneshot";
          ExecStart = "${watchdogCutoverCtl}/bin/axis-development-watchdog-cutoverctl reconcile";
          RemainAfterExit = true;
        };
        Install.WantedBy = [ "default.target" ];
      };

      systemd.user.services.axis-development-watchdog-backup = {
        Unit = {
          Description = "Independent backup execution for AXIS Development Watchdog";
          After = [ "hermes-gateway.service" ];
          Wants = [ "hermes-gateway.service" ];
        };
        Service = {
          Type = "oneshot";
          ExecStart = "${watchdog}/bin/axis-development-watchdog";
        };
      };

      systemd.user.services.axis-development-watchdog-monitor = {
        Unit.Description = "Externally monitor and start AXIS Development Watchdog";
        Service = {
          Type = "oneshot";
          ExecStart = "${watchdogMonitor}/bin/axis-development-watchdog-monitor";
        };
      };

      systemd.user.timers.axis-development-watchdog-backup = {
        Unit.Description = "Backstop AXIS Development Watchdog heartbeat";
        Timer = {
          OnBootSec = "12m";
          OnUnitActiveSec = "15m";
          Persistent = true;
          Unit = "axis-development-watchdog-monitor.service";
        };
        Install.WantedBy = [ "timers.target" ];
      };

      home.activation.hermesWatchdogState = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/mkdir -p \
          "$HOME/.hermes/scripts" \
          "${runtimeRoot}" \
          "${runtimeRoot}/recovery-transactions"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/watchdog.py}" "$HOME/.hermes/scripts/axis-development-watchdog-impl.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${watchdogLauncher}" "$HOME/.hermes/scripts/axis-development-watchdog.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/cronctl.py}" "$HOME/.hermes/scripts/axis-development-watchdog-cronctl.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/cutoverctl.py}" "$HOME/.hermes/scripts/axis-development-watchdog-cutoverctl.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/monitor.py}" "$HOME/.hermes/scripts/axis-development-watchdog-monitor.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/diagnostic_stdin.py}" "$HOME/.hermes/scripts/axis-development-watchdog-diagnostic.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -rf "$HOME/.hermes/scripts/axis_watchdog"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/cp -R \
          "${./scripts/axis_watchdog}" "$HOME/.hermes/scripts/axis_watchdog"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/chmod -R u=rwX,go= \
          "$HOME/.hermes/scripts/axis_watchdog"
        if [ ! -f "${runtimeRoot}/control.json" ]; then
          $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T \
            "${./control.defaults.json}" "${runtimeRoot}/control.json"
        fi
      '';
    })
  ];
}
