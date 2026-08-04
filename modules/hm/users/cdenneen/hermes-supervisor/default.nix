{
  agentPkgs ? null,
  config,
  lib,
  pkgs,
  ...
}:
let
  packageAvailable = pkgs.stdenv.isLinux && agentPkgs != null && agentPkgs ? hermes;
  gatewayEnabled = config.profiles.hermesGateway.enable && packageAvailable;
  supervisorEnabled = config.profiles.hermesSupervisor.enable && packageAvailable;
  runtimeRoot = "${config.home.homeDirectory}/.hermes/supervisor/axis-development-supervisor";
  supervisorCtl = pkgs.writeShellScriptBin "axis-development-supervisorctl" ''
    set -euo pipefail
    exec ${pkgs.python3}/bin/python "$HOME/.hermes/scripts/axis-development-supervisorctl.py" "$@"
  '';
  supervisorHealth = pkgs.writeShellScriptBin "axis-development-supervisor-health" ''
    set -euo pipefail
    exec ${pkgs.python3}/bin/python "$HOME/.hermes/scripts/axis-development-supervisor-health.py" "$@"
  '';
  supervisorCronCtl = pkgs.writeShellScriptBin "axis-development-supervisor-cronctl" ''
    set -euo pipefail
    exec ${pkgs.python3}/bin/python "$HOME/.hermes/scripts/axis-development-supervisor-cronctl.py" "$@"
  '';
  supervisorCycle = pkgs.writeShellScriptBin "axis-development-supervisor-cycle" ''
    set -euo pipefail
    exec ${pkgs.python3}/bin/python "$HOME/.hermes/scripts/axis_supervisor/cycle.py" "$@"
  '';
  supervisorCommand = pkgs.writeShellScriptBin "axis-development-supervisor-command" ''
    set -euo pipefail
    exec ${pkgs.python3}/bin/python "$HOME/.hermes/scripts/axis-development-supervisor-command.py" "$@"
  '';
in
{
  options.profiles.hermesGateway.enable = lib.mkEnableOption "managed Hermes messaging gateway";
  options.profiles.hermesSupervisor.enable = lib.mkEnableOption "temporary Hermes Development Supervisor";

  config = lib.mkIf (gatewayEnabled || supervisorEnabled) {
    home.packages =
      lib.optionals gatewayEnabled [ agentPkgs.hermes ]
      ++ lib.optionals supervisorEnabled [
        supervisorCtl
        supervisorHealth
        supervisorCronCtl
        supervisorCycle
        supervisorCommand
      ];
    home.file = lib.mkIf supervisorEnabled {
      ".hermes/supervisor/axis-development-supervisor/worker-prompt.txt".source = ./worker-prompt.txt;
      ".hermes/supervisor/axis-development-supervisor/docs" = {
        source = ./docs;
        recursive = true;
      };
      ".hermes/supervisor/axis-development-supervisor/schemas" = {
        source = ./schemas;
        recursive = true;
      };
      ".hermes/supervisor/axis-development-supervisor/VERSION".source = ./VERSION;
    };

    systemd.user.services = lib.mkMerge [
      (lib.mkIf gatewayEnabled {
        hermes-gateway = {
          Unit = {
            Description = "Hermes Agent Gateway - Messaging Platform Integration";
            After = [ "network-online.target" ];
            Wants = [ "network-online.target" ];
          };
          Service = {
            Type = "simple";
            ExecStart = "${agentPkgs.hermes}/bin/hermes gateway run";
            WorkingDirectory = "%h/.hermes";
            Environment = [ "HERMES_HOME=%h/.hermes" ];
            Restart = "on-failure";
            RestartSec = 5;
            TimeoutStopSec = 180;
            KillMode = "mixed";
            RestartPreventExitStatus = 78;
          };
          Install.WantedBy = [ "default.target" ];
        };
      })
      (lib.mkIf supervisorEnabled {
        hermes-supervisor-cron = {
          Unit = {
            Description = "Provision Hermes Development Supervisor cron jobs";
            After = [ "hermes-gateway.service" ];
            Requires = [ "hermes-gateway.service" ];
          };
          Service = {
            Type = "oneshot";
            ExecStart = "${pkgs.python3}/bin/python ${./scripts/cronctl.py} install --hermes ${agentPkgs.hermes}/bin/hermes";
            RemainAfterExit = true;
          };
          Install.WantedBy = [ "default.target" ];
        };
      })
    ];

    home.activation.hermesSupervisorLegacyCleanup = lib.mkIf gatewayEnabled (
      lib.hm.dag.entryBefore [ "checkLinkTargets" ] ''
        migration_backup="$HOME/.hermes/supervisor/axis-development-supervisor/migration-backup-1.0.0"
        for relative in \
          ".hermes/skills/axis-development-supervisor/SKILL.md" \
          ".hermes/skills/axis-supervisor-operations/SKILL.md" \
          ".hermes/scripts/axis-development-supervisor-preflight.py" \
          ".hermes/scripts/axis-development-supervisor-reconcile.py" \
          ".hermes/scripts/axis-development-supervisor-report.py" \
          ".hermes/scripts/axis-development-supervisorctl.py" \
          ".hermes/scripts/axis-development-supervisor-health.py" \
          ".hermes/scripts/axis-development-supervisor-cronctl.py" \
          ".hermes/scripts/axis-development-supervisor-slack.py" \
          ".hermes/scripts/axis-development-supervisor-command.py" \
          ".hermes/scripts/axis_supervisor" \
          ".hermes/supervisor/axis-development-supervisor/worker-prompt.txt" \
          ".hermes/supervisor/axis-development-supervisor/docs" \
          ".hermes/supervisor/axis-development-supervisor/schemas" \
          ".hermes/supervisor/axis-development-supervisor/VERSION"
        do
          target="$HOME/$relative"
          if [ -e "$target" ] && [ ! -L "$target" ]; then
            backup="$migration_backup/$relative"
            $DRY_RUN_CMD ${pkgs.coreutils}/bin/mkdir -p "$(${pkgs.coreutils}/bin/dirname "$backup")"
            $DRY_RUN_CMD ${pkgs.coreutils}/bin/mv -f "$target" "$backup"
          fi
        done
        if [ -f "$HOME/.config/systemd/user/hermes-gateway.service" ] \
          && [ ! -L "$HOME/.config/systemd/user/hermes-gateway.service" ]; then
          $DRY_RUN_CMD mv -f \
            "$HOME/.config/systemd/user/hermes-gateway.service" \
            "$HOME/.config/systemd/user/hermes-gateway.service.pre-home-manager"
        fi
        if [ -f "$HOME/.config/systemd/user/hermes-gateway.service.d/override.conf" ]; then
          $DRY_RUN_CMD mv -f \
            "$HOME/.config/systemd/user/hermes-gateway.service.d/override.conf" \
            "$HOME/.config/systemd/user/hermes-gateway.service.d/override.conf.pre-home-manager"
        fi
        $DRY_RUN_CMD rmdir --ignore-fail-on-non-empty "$HOME/.config/systemd/user/hermes-gateway.service.d" 2>/dev/null || true
      ''
    );

    home.activation.hermesSupervisorState = lib.mkIf supervisorEnabled (
      lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/mkdir -p \
          "$HOME/.hermes/skills/axis-development-supervisor" \
          "$HOME/.hermes/skills/axis-supervisor-operations" \
          "$HOME/.hermes/scripts"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 644 -T \
          "${./skill/SKILL.md}" "$HOME/.hermes/skills/axis-development-supervisor/SKILL.md"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 644 -T \
          "${./slack-skill/SKILL.md}" "$HOME/.hermes/skills/axis-supervisor-operations/SKILL.md"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/preflight.py}" "$HOME/.hermes/scripts/axis-development-supervisor-preflight.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/reconcile.py}" "$HOME/.hermes/scripts/axis-development-supervisor-reconcile.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/report.py}" "$HOME/.hermes/scripts/axis-development-supervisor-report.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/supervisorctl.py}" "$HOME/.hermes/scripts/axis-development-supervisorctl.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/health.py}" "$HOME/.hermes/scripts/axis-development-supervisor-health.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/cronctl.py}" "$HOME/.hermes/scripts/axis-development-supervisor-cronctl.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/slack_projection.py}" "$HOME/.hermes/scripts/axis-development-supervisor-slack.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/commands.py}" "$HOME/.hermes/scripts/axis-development-supervisor-command.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -rf "$HOME/.hermes/scripts/axis_supervisor"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/cp -R \
          "${./scripts/axis_supervisor}" "$HOME/.hermes/scripts/axis_supervisor"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/chmod -R u=rwX,go= "$HOME/.hermes/scripts/axis_supervisor"
        $DRY_RUN_CMD mkdir -p \
          "${runtimeRoot}/assignments" \
          "${runtimeRoot}/leases" \
          "${runtimeRoot}/reports" \
          "${runtimeRoot}/runs" \
          "${runtimeRoot}/worktrees"
        if [ ! -f "${runtimeRoot}/control.json" ]; then
          $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T \
            "${./control.defaults.json}" "${runtimeRoot}/control.json"
        elif [ -z "''${DRY_RUN_CMD:-}" ]; then
          control_tmp="$(${pkgs.coreutils}/bin/mktemp "${runtimeRoot}/control.json.XXXXXX")"
          ${pkgs.jq}/bin/jq -s '
            .[1] as $existing
            | (.[0] * $existing)
            | if (($existing.schema // "") != "axis.external-development-supervisor.control"
                  or ($existing.version // 0) < 2)
              then .mode = "observing"
                   | .allow_repository_mutation = false
                   | .version = 2
                   | del(.proof_assignment_id)
              else .
              end
            | del(.daily_model_call_limit)
          ' \
            "${./control.defaults.json}" "${runtimeRoot}/control.json" > "$control_tmp"
          ${pkgs.coreutils}/bin/install -m 600 -T "$control_tmp" "${runtimeRoot}/control.json"
          ${pkgs.coreutils}/bin/rm -f "$control_tmp"
        fi
        if [ ! -f "${runtimeRoot}/baseline.json" ]; then
          $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T \
            "${./baseline.defaults.json}" "${runtimeRoot}/baseline.json"
        elif [ -z "''${DRY_RUN_CMD:-}" ]; then
          baseline_tmp="$(${pkgs.coreutils}/bin/mktemp "${runtimeRoot}/baseline.json.XXXXXX")"
          ${pkgs.jq}/bin/jq -s '.[0] * .[1]' \
            "${./baseline.defaults.json}" "${runtimeRoot}/baseline.json" > "$baseline_tmp"
          ${pkgs.coreutils}/bin/install -m 600 -T "$baseline_tmp" "${runtimeRoot}/baseline.json"
          ${pkgs.coreutils}/bin/rm -f "$baseline_tmp"
        fi
      ''
    );

    home.activation.hermesSupervisorGatewayConfig = lib.mkIf gatewayEnabled (
      lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        hermes_config="$HOME/.hermes/config.yaml"
        if [ -f "$hermes_config" ] && [ -z "''${DRY_RUN_CMD:-}" ]; then
          config_tmp="$(${pkgs.coreutils}/bin/mktemp "$HOME/.hermes/config.yaml.XXXXXX")"
          ${pkgs.yq-go}/bin/yq '
            .agent.restart_drain_timeout = 120
            | .agent.reasoning_overrides."gpt-5.4" = "medium"
            | .agent.reasoning_overrides."gpt-5.3-codex" = "medium"
          ' "$hermes_config" > "$config_tmp"
          ${pkgs.coreutils}/bin/install -m 600 -T "$config_tmp" "$hermes_config"
          ${pkgs.coreutils}/bin/rm -f "$config_tmp"
        fi
      ''
    );
  };
}
