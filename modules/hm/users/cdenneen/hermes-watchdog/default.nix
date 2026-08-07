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
  sourceRevision =
    if self ? rev then
      self.rev
    else if self ? dirtyRev then
      self.dirtyRev
    else
      "unknown";
  watchdog = pkgs.writeShellScriptBin "axis-development-watchdog" ''
    set -euo pipefail
    exec ${watchdogPython}/bin/python "$HOME/.hermes/scripts/axis-development-watchdog.py" "$@"
  '';
  watchdogCronCtl = pkgs.writeShellScriptBin "axis-development-watchdog-cronctl" ''
    set -euo pipefail
    exec ${watchdogPython}/bin/python "$HOME/.hermes/scripts/axis-development-watchdog-cronctl.py" "$@"
  '';
  watchdogDiagnose = pkgs.writeShellScriptBin "axis-development-watchdog-diagnose" ''
    set -euo pipefail
    exec ${agentPkgs.hermes.hermesVenv}/bin/python3 "$HOME/.hermes/scripts/axis-development-watchdog-diagnostic.py"
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
        watchdogDiagnose
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
          After = [ "hermes-gateway.service" ];
          Requires = [ "hermes-gateway.service" ];
        };
        Service = {
          Type = "oneshot";
          ExecStart = "${watchdogCronCtl}/bin/axis-development-watchdog-cronctl install --hermes ${agentPkgs.hermes}/bin/hermes";
          RemainAfterExit = true;
        };
        Install.WantedBy = [ "default.target" ];
      };

      home.activation.hermesWatchdogState = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/mkdir -p \
          "$HOME/.hermes/scripts" \
          "${runtimeRoot}"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/watchdog.py}" "$HOME/.hermes/scripts/axis-development-watchdog.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/cronctl.py}" "$HOME/.hermes/scripts/axis-development-watchdog-cronctl.py"
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
