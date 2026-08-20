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
  hermesGatewaySitecustomize = pkgs.writeTextDir "sitecustomize.py" ''
    try:
        import hermes_cli.commands as commands
        commands.should_bypass_active_session = commands.is_gateway_known_command
    except Exception:
        pass
  '';
  hermesGatewayBypassCheck = pkgs.writeShellScript "hermes-gateway-plugin-bypass-check" ''
    set -euo pipefail
    exec ${agentPkgs.hermes.hermesVenv}/bin/python3 -c \
      'import hermes_cli.commands as commands; assert commands.should_bypass_active_session is commands.is_gateway_known_command'
  '';
  gatewayEnabled = config.profiles.hermesGateway.enable && packageAvailable;
  secondaryGatewayEnabled = config.profiles.hermesGatewaySecondary.enable && packageAvailable;
  supervisorEnabled = config.profiles.hermesSupervisor.enable && packageAvailable;
  anyGatewayEnabled = gatewayEnabled || secondaryGatewayEnabled;
  # Mitigates a known upstream Hermes bug (NousResearch/hermes-agent#78068,
  # #80471): certain exceptions mid-turn (a gateway restart, a bad path, a
  # transient upstream-provider error - confirmed 2026-08-17 with a Bedrock
  # internalServerException) corrupt nemo_relay's scope stack; end_turn's own
  # cleanup then throws a second, unhandled RuntimeError and the session is
  # stranded with no timeout, retry, or surfaced error. Track those issues
  # for an upstream fix; this is the local workaround until one lands.
  hermesStuckCronWatchdog = pkgs.writeShellScript "hermes-stuck-cron-watchdog" ''
    set -euo pipefail
    threshold_min="''${HERMES_WATCHDOG_THRESHOLD_MIN:-10}"
    grace_min="''${HERMES_WATCHDOG_GRACE_MIN:-3}"
    now=$(${pkgs.coreutils}/bin/date +%s)

    # A restart here kills every session on the gateway, not just the stuck
    # one - including a live conversation someone is actively watching.
    # Confirmed 2026-08-17: restarted mid-conversation while a human was
    # actively answering a clarify prompt in a different session on the
    # same instance. Refuse to restart if ANY session on this instance has
    # had activity within grace_min - that's real evidence the process is
    # responsive, not hung, even if one unrelated run is stuck/orphaned.
    gateway_has_recent_activity() {
      local hermes_home="$1"
      local rows
      rows=$(HERMES_HOME="$hermes_home" ${agentPkgs.hermes}/bin/hermes sessions list --limit 20 2>/dev/null || true)
      if echo "$rows" | ${pkgs.gnugrep}/bin/grep -qE '\bjust now\b'; then
        return 0
      fi
      if echo "$rows" | ${pkgs.gnugrep}/bin/grep -qE '[0-9]+s ago'; then
        return 0
      fi
      local m
      m=$(echo "$rows" | ${pkgs.gnugrep}/bin/grep -oE '[0-9]+m ago' | ${pkgs.gnugrep}/bin/grep -oE '[0-9]+' | ${pkgs.coreutils}/bin/sort -n | ${pkgs.coreutils}/bin/head -1)
      [ -n "$m" ] && [ "$m" -lt "$grace_min" ]
    }

    check_instance() {
      local hermes_home="$1" service="$2"
      HERMES_HOME="$hermes_home" ${agentPkgs.hermes}/bin/hermes cron runs --limit 50 2>/dev/null \
        | ${pkgs.gawk}/bin/awk '$2=="running"{print}' \
        | while read -r _id _status _job _src ts; do
            started=$(${pkgs.coreutils}/bin/date -d "$ts" +%s 2>/dev/null || echo 0)
            [ "$started" -eq 0 ] && continue
            age_min=$(( (now - started) / 60 ))
            if [ "$age_min" -ge "$threshold_min" ]; then
              if gateway_has_recent_activity "$hermes_home"; then
                echo "$(${pkgs.coreutils}/bin/date -Is) stuck cron run in $hermes_home (age ''${age_min}m) but another session there is active within ''${grace_min}m - not restarting $service"
              else
                echo "$(${pkgs.coreutils}/bin/date -Is) stuck cron run in $hermes_home (age ''${age_min}m), no recent activity anywhere on this instance - restarting $service"
                ${pkgs.systemd}/bin/systemctl --user restart "$service"
              fi
              break
            fi
          done
    }

    check_instance "$HOME/.hermes" "hermes-gateway.service"
    ${lib.optionalString secondaryGatewayEnabled ''
      check_instance "$HOME/.hermes/profiles/${config.profiles.hermesGatewaySecondary.profileName}" "hermes-gateway-secondary.service"
    ''}
  '';
  runtimeRoot = "${config.home.homeDirectory}/.hermes/supervisor/axis-development-supervisor";
  sourceRevision =
    if self ? rev then
      self.rev
    else if self ? dirtyRev then
      self.dirtyRev
    else
      "unknown";
  supervisorPython = pkgs.python3.withPackages (pythonPackages: [ pythonPackages.jsonschema ]);
  supervisorCtl = pkgs.writeShellScriptBin "axis-development-supervisorctl" ''
    set -euo pipefail
    exec ${supervisorPython}/bin/python "$HOME/.hermes/scripts/axis-development-supervisorctl.py" "$@"
  '';
  supervisorHealth = pkgs.writeShellScriptBin "axis-development-supervisor-health" ''
    set -euo pipefail
    exec ${supervisorPython}/bin/python "$HOME/.hermes/scripts/axis-development-supervisor-health.py" "$@"
  '';
  supervisorCronCtl = pkgs.writeShellScriptBin "axis-development-supervisor-cronctl" ''
    set -euo pipefail
    exec ${supervisorPython}/bin/python "$HOME/.hermes/scripts/axis-development-supervisor-cronctl.py" "$@"
  '';
  supervisorCycle = pkgs.writeShellScriptBin "axis-development-supervisor-cycle" ''
    set -euo pipefail
    exec ${supervisorPython}/bin/python "$HOME/.hermes/scripts/axis_supervisor/cycle.py" "$@"
  '';
  supervisorCommand = pkgs.writeShellScriptBin "axis-development-supervisor-command" ''
    set -euo pipefail
    exec ${supervisorPython}/bin/python "$HOME/.hermes/scripts/axis-development-supervisor-command.py" "$@"
  '';
  supervisorCanaryCtl = pkgs.writeShellScriptBin "axis-development-supervisor-canaryctl" ''
    set -euo pipefail
    exec ${supervisorPython}/bin/python "$HOME/.hermes/scripts/axis-development-supervisor-canaryctl.py" "$@"
  '';
  supervisorReview = pkgs.writeShellScriptBin "axis-development-supervisor-review" ''
    set -euo pipefail
    export PYTHONPATH="$HOME/.hermes/scripts"
    exec ${supervisorPython}/bin/python -m axis_supervisor.review_settling "$@"
  '';
  supervisorPreflightLauncher = pkgs.writeText "axis-development-supervisor-preflight.py" ''
    #!${supervisorPython}/bin/python
    import os
    import sys

    python = "${supervisorPython}/bin/python"
    script = os.path.expanduser("~/.hermes/scripts/axis-development-supervisor-preflight-impl.py")
    os.execv(python, [python, script, *sys.argv[1:]])
  '';
  supervisorSlackLauncher = pkgs.writeText "axis-development-supervisor-slack.py" ''
    #!${supervisorPython}/bin/python
    import os
    import sys

    python = "${supervisorPython}/bin/python"
    script = os.path.expanduser("~/.hermes/scripts/axis-development-supervisor-slack-impl.py")
    os.execv(python, [python, script, *sys.argv[1:]])
  '';
in
{
  options.profiles.hermesGateway.enable = lib.mkEnableOption "managed Hermes messaging gateway";
  options.profiles.hermesGateway.restartOnActivation = lib.mkEnableOption "restart the primary Hermes gateway during Home Manager activation";
  options.profiles.hermesGateway.workingDirectory = lib.mkOption {
    type = lib.types.str;
    default = "%h/.hermes";
    description = ''
      cwd the primary gateway process runs from. Hermes auto-injects
      AGENTS.md/SOUL.md/CLAUDE.md/.cursorrules found here into every chat
      session's system prompt (see agent/agent_init.py's skip_context_files
      handling) - point this at a project worktree to give Slack/chat
      sessions the same ambient project context cron's --workdir gives.
      Independent of HERMES_HOME (profile state - kanban/skills/sessions);
      changing this does not move or affect profile data.
    '';
  };

  options.profiles.hermesGatewaySecondary.enable = lib.mkEnableOption ''
    a second, independently-profiled Hermes gateway instance - for running a
    separate Slack app / project against its own Hermes profile (own
    config.yaml, .env, skills, kanban board) without it sharing or polluting
    the primary instance's state
  '';
  options.profiles.hermesGatewaySecondary.profileName = lib.mkOption {
    type = lib.types.str;
    description = "Hermes CLI profile name. HERMES_HOME resolves to %h/.hermes/profiles/<profileName> (see hermes profile create).";
  };
  options.profiles.hermesGatewaySecondary.workingDirectory = lib.mkOption {
    type = lib.types.str;
    description = "cwd this instance runs from - see profiles.hermesGateway.workingDirectory for what this controls.";
  };

  options.profiles.hermesSupervisor.enable = lib.mkEnableOption "temporary Hermes Development Supervisor";
  options.profiles.hermesSupervisor.decommissionLegacyCron = lib.mkEnableOption ''
    strictly remove owned legacy AXIS supervisor/watchdog Hermes cron records on an authorized Home activation
  '';

  config = lib.mkIf (gatewayEnabled || secondaryGatewayEnabled || supervisorEnabled) {
    home.packages =
      lib.optionals (gatewayEnabled || secondaryGatewayEnabled) [ agentPkgs.hermes ]
      ++ lib.optionals supervisorEnabled [
        pkgs.bubblewrap
        supervisorCanaryCtl
        supervisorCtl
        supervisorHealth
        supervisorCronCtl
        supervisorCycle
        supervisorCommand
        supervisorReview
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
      ".hermes/supervisor/axis-development-supervisor/capability-runtime-matrix.json".source =
        ./capability-runtime-matrix.json;
      ".hermes/supervisor/axis-development-supervisor/deployed-source-revision.json".text =
        builtins.toJSON
          {
            revision = sourceRevision;
            dirty = !(self ? rev);
            rollback = "home-manager generations";
          };
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
            ExecStartPre = hermesGatewayBypassCheck;
            ExecStart = "${agentPkgs.hermes}/bin/hermes gateway run";
            WorkingDirectory = config.profiles.hermesGateway.workingDirectory;
            Environment = [
              "HERMES_HOME=%h/.hermes"
              "PYTHONPATH=${hermesGatewaySitecustomize}"
            ];
            Restart = "on-failure";
            RestartSec = 5;
            TimeoutStopSec = 180;
            KillMode = "mixed";
            RestartPreventExitStatus = 78;
          };
          Install.WantedBy = [ "default.target" ];
        };
      })
      (lib.mkIf secondaryGatewayEnabled {
        hermes-gateway-secondary = {
          Unit = {
            Description = "Hermes Agent Gateway (secondary instance, profile: ${config.profiles.hermesGatewaySecondary.profileName}) - Messaging Platform Integration";
            After = [
              "network-online.target"
              "hermes-gateway.service"
            ];
            Wants = [ "network-online.target" ];
          };
          Service = {
            Type = "simple";
            ExecStartPre = hermesGatewayBypassCheck;
            ExecStart = "${agentPkgs.hermes}/bin/hermes gateway run";
            WorkingDirectory = config.profiles.hermesGatewaySecondary.workingDirectory;
            Environment = [
              "HERMES_HOME=%h/.hermes/profiles/${config.profiles.hermesGatewaySecondary.profileName}"
              "PYTHONPATH=${hermesGatewaySitecustomize}"
            ];
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
            ExecStart = "${supervisorCronCtl}/bin/axis-development-supervisor-cronctl install --hermes ${agentPkgs.hermes}/bin/hermes";
            Environment = [ "AXIS_SUPERVISOR_MUTATION_SOURCE=home-manager" ];
            RemainAfterExit = true;
          };
          Install.WantedBy = [ "default.target" ];
        };
      })
      (lib.mkIf anyGatewayEnabled {
        hermes-stuck-cron-watchdog = {
          Unit.Description = "Restart a Hermes gateway instance if a cron run has been stuck (workaround for NousResearch/hermes-agent#78068, #80471)";
          Service = {
            Type = "oneshot";
            ExecStart = "${hermesStuckCronWatchdog}";
          };
        };
      })
    ];

    systemd.user.timers = lib.mkIf anyGatewayEnabled {
      hermes-stuck-cron-watchdog = {
        Unit.Description = "Periodic check for stuck Hermes cron runs";
        Timer = {
          OnStartupSec = "2m";
          OnUnitActiveSec = "5m";
        };
        Install.WantedBy = [ "timers.target" ];
      };
    };

    home.activation.hermesSupervisorLegacyCleanup = lib.mkIf gatewayEnabled (
      lib.hm.dag.entryBefore [ "checkLinkTargets" ] ''
        migration_backup="$HOME/.hermes/supervisor/axis-development-supervisor/migration-backup-1.0.0"
        if [ ! -d "$migration_backup" ]; then
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
          ".hermes/supervisor/axis-development-supervisor/VERSION" \
          ".hermes/supervisor/axis-development-supervisor/deployed-source-revision.json"
          do
            target="$HOME/$relative"
            if [ -e "$target" ] && [ ! -L "$target" ]; then
              backup="$migration_backup/$relative"
              $DRY_RUN_CMD ${pkgs.coreutils}/bin/mkdir -p "$(${pkgs.coreutils}/bin/dirname "$backup")"
              $DRY_RUN_CMD ${pkgs.coreutils}/bin/mv -f "$target" "$backup"
            fi
          done
        fi
        revision_target="$HOME/.hermes/supervisor/axis-development-supervisor/deployed-source-revision.json"
        if [ -e "$revision_target" ] && [ ! -L "$revision_target" ]; then
          revision_backup="$HOME/.hermes/supervisor/axis-development-supervisor/migration-backup-1.0.0/.hermes/supervisor/axis-development-supervisor/deployed-source-revision.json"
          $DRY_RUN_CMD ${pkgs.coreutils}/bin/mkdir -p "$(${pkgs.coreutils}/bin/dirname "$revision_backup")"
          $DRY_RUN_CMD ${pkgs.coreutils}/bin/mv -f "$revision_target" "$revision_backup"
        fi
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
          "$HOME/.hermes/plugins" \
          "$HOME/.hermes/scripts"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 644 -T \
          "${./skill/SKILL.md}" "$HOME/.hermes/skills/axis-development-supervisor/SKILL.md"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 644 -T \
          "${./slack-skill/SKILL.md}" "$HOME/.hermes/skills/axis-supervisor-operations/SKILL.md"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/preflight.py}" "$HOME/.hermes/scripts/axis-development-supervisor-preflight-impl.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${supervisorPreflightLauncher}" "$HOME/.hermes/scripts/axis-development-supervisor-preflight.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/reconcile.py}" "$HOME/.hermes/scripts/axis-development-supervisor-reconcile.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -f \
          "$HOME/.hermes/scripts/axis-development-supervisor-report.py" \
          "${runtimeRoot}/report-delivery-pending.json" \
          "${runtimeRoot}/report-delivery-state.json" \
          "${runtimeRoot}/report-state.json" \
          "${runtimeRoot}/schemas/baseline.schema.json" \
          "${runtimeRoot}/schemas/report.schema.json"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/supervisorctl.py}" "$HOME/.hermes/scripts/axis-development-supervisorctl.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/health.py}" "$HOME/.hermes/scripts/axis-development-supervisor-health.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/cronctl.py}" "$HOME/.hermes/scripts/axis-development-supervisor-cronctl.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/slack_projection.py}" "$HOME/.hermes/scripts/axis-development-supervisor-slack-impl.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${supervisorSlackLauncher}" "$HOME/.hermes/scripts/axis-development-supervisor-slack.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/commands.py}" "$HOME/.hermes/scripts/axis-development-supervisor-command.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 700 -T \
          "${./scripts/canaryctl.py}" "$HOME/.hermes/scripts/axis-development-supervisor-canaryctl.py"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -rf "$HOME/.hermes/scripts/axis_supervisor"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/cp -R \
          "${./scripts/axis_supervisor}" "$HOME/.hermes/scripts/axis_supervisor"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/chmod -R u=rwX,go= "$HOME/.hermes/scripts/axis_supervisor"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -rf "$HOME/.hermes/plugins/axis-supervisor-commands"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/cp -R \
          "${./plugin/axis-supervisor-commands}" "$HOME/.hermes/plugins/axis-supervisor-commands"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/chmod -R u=rwX,go= "$HOME/.hermes/plugins/axis-supervisor-commands"
        $DRY_RUN_CMD mkdir -p \
          "${runtimeRoot}/assignments" \
          "${runtimeRoot}/accounting" \
          "${runtimeRoot}/leases" \
          "${runtimeRoot}/runs" \
          "${runtimeRoot}/worktrees"
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -rf "${runtimeRoot}/reports"
        if [ ! -f "${runtimeRoot}/control.json" ]; then
          $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T \
            "${./control.defaults.json}" "${runtimeRoot}/control.json"
        elif [ -z "''${DRY_RUN_CMD:-}" ]; then
          control_tmp="$(${pkgs.coreutils}/bin/mktemp "${runtimeRoot}/control.json.XXXXXX")"
          ${pkgs.jq}/bin/jq -s '
            .[1] as $existing
            | (.[0] * $existing)
            | if (($existing.schema // "") != "axis.external-development-supervisor.control"
                  or ($existing.version // 0) < 4)
              then .mode = "observing"
                   | .allow_repository_mutation = false
                    | .version = 4
                    | .max_active_assignments = 2
                   | .overview_freshness_minutes = ($existing.overview_freshness_minutes // $existing.report_heartbeat_minutes // 90)
                   | del(.proof_assignment_id, .max_delegated_assignments, .report_heartbeat_minutes,
                         .gitlab_host, .gitlab_group, .slack_delivery, .operator,
                         .continue_unattended_after_proof, .repository_roots, .updated_at)
              else .
              end
          ' \
            "${./control.defaults.json}" "${runtimeRoot}/control.json" > "$control_tmp"
          ${pkgs.coreutils}/bin/install -m 600 -T "$control_tmp" "${runtimeRoot}/control.json"
          ${pkgs.coreutils}/bin/rm -f "$control_tmp"
        fi
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -f "${runtimeRoot}/baseline.json"
      ''
    );

    home.activation.hermesSupervisorGatewayConfig = lib.mkIf gatewayEnabled (
      lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        hermes_config="$HOME/.hermes/config.yaml"
        if [ -f "$hermes_config" ] && [ -z "''${DRY_RUN_CMD:-}" ]; then
          config_tmp="$(${pkgs.coreutils}/bin/mktemp --tmpdir hermes-gateway-config.XXXXXX)"
          ${pkgs.yq-go}/bin/yq '
            .agent.restart_drain_timeout = 120
            | .agent.reasoning_overrides."gpt-5.4" = "medium"
            | .agent.reasoning_overrides."gpt-5.3-codex" = "medium"
            ${lib.optionalString supervisorEnabled ''
              | .plugins.enabled = (((.plugins.enabled // []) + ["axis-supervisor-commands"]) | unique)
            ''}
            ${lib.optionalString (!supervisorEnabled) ''
              | .plugins.enabled = ((.plugins.enabled // []) | map(select(. != "axis-supervisor-commands")))
            ''}
          ' "$hermes_config" > "$config_tmp"
          if ! ${pkgs.diffutils}/bin/cmp -s "$config_tmp" "$hermes_config" \
            || [ "$(${pkgs.coreutils}/bin/stat -c %a "$hermes_config")" != 600 ]; then
            ${pkgs.coreutils}/bin/install -m 600 -T "$config_tmp" "$hermes_config"
          fi
          ${pkgs.coreutils}/bin/rm -f "$config_tmp"
        fi
      ''
    );

    home.activation.hermesLegacyAxisCronDecommission =
      lib.mkIf
        (gatewayEnabled && !supervisorEnabled && config.profiles.hermesSupervisor.decommissionLegacyCron)
        (
          lib.hm.dag.entryAfter [ "writeBoundary" ] ''
            if [ -n "''${DRY_RUN_CMD:-}" ]; then
              ${supervisorPython}/bin/python ${./scripts/decommission_cron.py} check
            else
              ${supervisorPython}/bin/python ${./scripts/decommission_cron.py} apply \
                --hermes ${agentPkgs.hermes}/bin/hermes
            fi
          ''
        );

    home.activation.hermesSupervisorPluginCleanup = lib.mkIf (gatewayEnabled && !supervisorEnabled) (
      lib.hm.dag.entryAfter [ "writeBoundary" ] ''
        $DRY_RUN_CMD ${pkgs.coreutils}/bin/rm -rf "$HOME/.hermes/plugins/axis-supervisor-commands"
      ''
    );

    home.activation.hermesSupervisorGatewayRestart =
      lib.mkIf (gatewayEnabled && config.profiles.hermesGateway.restartOnActivation)
        (
          lib.hm.dag.entryAfter
            (
              [
                "reloadSystemd"
                "hermesSupervisorGatewayConfig"
              ]
              ++ lib.optional supervisorEnabled "hermesSupervisorState"
              ++ lib.optional (!supervisorEnabled) "hermesSupervisorPluginCleanup"
            )
            ''
              $DRY_RUN_CMD ${pkgs.systemd}/bin/systemctl --user try-restart hermes-gateway.service
            ''
        );
  };
}
