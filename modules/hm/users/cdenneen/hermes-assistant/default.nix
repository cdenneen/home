{
  agentPkgs ? null,
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.hermesAssistant;
  profileHome = "${config.home.homeDirectory}/.hermes/profiles/assistant";
  sopsNixProgram = config.systemd.user.services.sops-nix.Service.ExecStart;
  assistantPython = pkgs.python3.withPackages (pythonPackages: [
    pythonPackages.msal
    pythonPackages.requests
  ]);
  googleWorkspace = pkgs.writeShellScriptBin "hermes-google-workspace" ''
    export HERMES_HOME=${lib.escapeShellArg profileHome}
    exec ${agentPkgs.hermes.hermesVenv}/bin/python3 \
      ${agentPkgs.hermes}/share/hermes-agent/skills/productivity/google-workspace/scripts/google_api.py "$@"
  '';
  microsoftGraph = pkgs.writeShellScriptBin "hermes-msgraph" ''
    export HERMES_MSGRAPH_TOKEN_CACHE=${lib.escapeShellArg "${profileHome}/msgraph_token_cache.json"}
    exec ${assistantPython}/bin/python3 ${./msgraph.py} "$@"
  '';
  assistantAutomation = pkgs.writeShellScriptBin "hermes-assistant-automation" ''
    exec ${pkgs.python3}/bin/python3 ${./assistant_automation.py} "$@"
  '';
  assistantKind = if cfg.personal.enable then "personal" else "work";
in
{
  options.profiles.hermesAssistant = {
    personal.enable = lib.mkEnableOption "the personal Google assistant profile on Ghost";
    work.enable = lib.mkEnableOption "the work Microsoft Graph assistant profile on Nyx";

    automation = {
      enable = lib.mkEnableOption "hourly assistant health and scheduled read-only briefings";
      slackEnvFile = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "Runtime SOPS environment file containing the approved Slack bot token.";
      };
      slackChannel = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "Approved Slack channel or direct-message ID for assistant reports.";
      };
      briefCalendar = lib.mkOption {
        type = lib.types.str;
        default = "Mon..Fri *-*-* 08:00:00 America/New_York";
        description = "Systemd calendar expression for the daily briefing.";
      };
    };
  };

  config = lib.mkIf (cfg.personal.enable || cfg.work.enable) {
    assertions = [
      {
        assertion = pkgs.stdenv.hostPlatform.isLinux && agentPkgs != null && agentPkgs ? hermes;
        message = "profiles.hermesAssistant requires Linux and agentPkgs.hermes";
      }
      {
        assertion = !(cfg.personal.enable && cfg.work.enable);
        message = "A Hermes assistant host cannot hold both personal and work OAuth credentials";
      }
      {
        assertion =
          !cfg.automation.enable || (cfg.automation.slackEnvFile != "" && cfg.automation.slackChannel != "");
        message = "Hermes assistant automation requires an approved Slack environment file and channel";
      }
    ];

    home.packages = [
      assistantAutomation
    ]
    ++ lib.optionals cfg.personal.enable [ googleWorkspace ]
    ++ lib.optionals cfg.work.enable [ microsoftGraph ];

    home.file = lib.mkMerge [
      (lib.mkIf cfg.personal.enable {
        ".hermes/profiles/assistant/skills/productivity/personal-google-assistant/SKILL.md".source =
          ./personal-google-assistant.md;
      })
      (lib.mkIf cfg.work.enable {
        ".hermes/profiles/assistant/skills/productivity/work-microsoft-assistant/SKILL.md".source =
          ./work-microsoft-assistant.md;
      })
    ];

    home.activation.hermesAssistantOauth =
      lib.hm.dag.entryAfter
        [
          "hermesProfileModelConfig"
          "materializeLinuxSopsSecrets"
        ]
        ''
              set -euo pipefail

          seed_oauth() {
            source_path="$1"
            target_path="$2"

            if [ ! -r "$source_path" ]; then
              $DRY_RUN_CMD ${lib.escapeShellArg sopsNixProgram}
            fi
            if [ ! -r "$source_path" ]; then
                  echo "error: Hermes assistant OAuth seed is missing: $source_path" >&2
                  exit 1
                fi

                $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -d -m 700 "$(${pkgs.coreutils}/bin/dirname "$target_path")"
                if [ ! -e "$target_path" ]; then
                  $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 -T "$source_path" "$target_path"
                elif [ ! -f "$target_path" ]; then
                  echo "error: Hermes assistant OAuth target is not a regular file: $target_path" >&2
                  exit 1
                else
                  $DRY_RUN_CMD ${pkgs.coreutils}/bin/chmod 600 "$target_path"
                fi
              }

              ${lib.optionalString cfg.personal.enable ''
                seed_oauth \
                  ${lib.escapeShellArg config.sops.secrets.hermes_assistant_google_oauth_ghost.path} \
                  ${lib.escapeShellArg "${profileHome}/google_token.json"}
              ''}
              ${lib.optionalString cfg.work.enable ''
                seed_oauth \
                  ${lib.escapeShellArg config.sops.secrets.hermes_assistant_msgraph_oauth_nyx.path} \
                  ${lib.escapeShellArg "${profileHome}/msgraph_token_cache.json"}
              ''}
        '';

    systemd.user = lib.mkIf cfg.automation.enable {
      services = {
        hermes-assistant-health = {
          Unit = {
            Description = "Hourly read-only Hermes assistant health and soak check";
            After = [
              "hermes-mesh-gateway.service"
              "network-online.target"
              "sops-nix.service"
            ];
            Wants = [
              "hermes-mesh-gateway.service"
              "network-online.target"
              "sops-nix.service"
            ];
          };
          Service = {
            Type = "oneshot";
            ExecStart = "${assistantAutomation}/bin/hermes-assistant-automation health";
            EnvironmentFile = cfg.automation.slackEnvFile;
            Environment = [
              "HERMES_ASSISTANT_KIND=${assistantKind}"
              "HERMES_ASSISTANT_SLACK_CHANNEL=${cfg.automation.slackChannel}"
            ];
            TimeoutStartSec = 360;
          };
        };

        hermes-assistant-brief = {
          Unit = {
            Description = "Scheduled read-only Hermes assistant daily briefing";
            After = [
              "hermes-mesh-gateway.service"
              "network-online.target"
              "sops-nix.service"
            ];
            Wants = [
              "hermes-mesh-gateway.service"
              "network-online.target"
              "sops-nix.service"
            ];
          };
          Service = {
            Type = "oneshot";
            ExecStart = "${assistantAutomation}/bin/hermes-assistant-automation brief";
            EnvironmentFile = cfg.automation.slackEnvFile;
            Environment = [
              "HERMES_ASSISTANT_KIND=${assistantKind}"
              "HERMES_ASSISTANT_SLACK_CHANNEL=${cfg.automation.slackChannel}"
            ];
            TimeoutStartSec = 420;
          };
        };
      };

      timers = {
        hermes-assistant-health = {
          Unit.Description = "Hourly Hermes assistant health timer";
          Timer = {
            OnBootSec = "5m";
            OnUnitActiveSec = "1h";
            Persistent = true;
            RandomizedDelaySec = "5m";
          };
          Install.WantedBy = [ "timers.target" ];
        };

        hermes-assistant-brief = {
          Unit.Description = "Daily Hermes assistant briefing timer";
          Timer = {
            OnCalendar = cfg.automation.briefCalendar;
            Persistent = true;
            RandomizedDelaySec = "5m";
          };
          Install.WantedBy = [ "timers.target" ];
        };
      };
    };
  };
}
