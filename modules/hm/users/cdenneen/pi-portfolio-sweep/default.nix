{
  agentPkgs ? null,
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.piPortfolioSweep;
  piSweepScript = pkgs.writeShellScriptBin "pi-portfolio-sweep" ''
    exec ${pkgs.python3}/bin/python3 ${./pi_portfolio_sweep.py} "$@"
  '';
in
{
  options.profiles.piPortfolioSweep = {
    enable = lib.mkEnableOption "pi morning/evening portfolio sweep timers";

    slackEnvFile = lib.mkOption {
      type = lib.types.str;
      default = "";
      description = "Runtime SOPS environment file containing the approved Slack bot token.";
    };

    slackChannel = lib.mkOption {
      type = lib.types.str;
      default = "C0BHLUXQ4EB";
      description = "Slack channel ID for portfolio sweep delivery.";
    };

    morningCalendar = lib.mkOption {
      type = lib.types.str;
      default = "Mon..Fri *-*-* 07:00:00 America/New_York";
      description = "Systemd calendar expression for the morning sweep (default: weekday mornings at 07:00 ET).";
    };

    eveningCalendar = lib.mkOption {
      type = lib.types.str;
      default = "Mon..Fri *-*-* 18:00:00 America/New_York";
      description = "Systemd calendar expression for the evening sweep (default: weekday evenings at 18:00 ET).";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = pkgs.stdenv.hostPlatform.isLinux;
        message = "profiles.piPortfolioSweep requires Linux (systemd user timers).";
      }
      {
        assertion = cfg.slackEnvFile != "";
        message = "profiles.piPortfolioSweep requires slackEnvFile to be set.";
      }
    ];

    home.packages = [ piSweepScript ];

    systemd.user = {
      services = {
        pi-portfolio-sweep-morning = {
          Unit = {
            Description = "Pi morning personal portfolio sweep";
            After = [
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
            ExecStart = "${piSweepScript}/bin/pi-portfolio-sweep morning";
            EnvironmentFile = cfg.slackEnvFile;
            Environment = [
              "PI_SWEEP_SLACK_CHANNEL=${cfg.slackChannel}"
              "PI_TELEMETRY=0"
            ];
            TimeoutStartSec = 420;
            # Give pi a real HOME so ~/.pi/agent/* is accessible
            PassEnvironment = [ "HOME" "PATH" "XDG_RUNTIME_DIR" ];
          };
        };

        pi-portfolio-sweep-evening = {
          Unit = {
            Description = "Pi evening personal portfolio sweep";
            After = [
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
            ExecStart = "${piSweepScript}/bin/pi-portfolio-sweep evening";
            EnvironmentFile = cfg.slackEnvFile;
            Environment = [
              "PI_SWEEP_SLACK_CHANNEL=${cfg.slackChannel}"
              "PI_TELEMETRY=0"
            ];
            TimeoutStartSec = 420;
            PassEnvironment = [ "HOME" "PATH" "XDG_RUNTIME_DIR" ];
          };
        };
      };

      timers = {
        pi-portfolio-sweep-morning = {
          Unit.Description = "Pi morning portfolio sweep timer";
          Timer = {
            OnCalendar = cfg.morningCalendar;
            Persistent = true;
            RandomizedDelaySec = "5m";
          };
          Install.WantedBy = [ "timers.target" ];
        };

        pi-portfolio-sweep-evening = {
          Unit.Description = "Pi evening portfolio sweep timer";
          Timer = {
            OnCalendar = cfg.eveningCalendar;
            Persistent = true;
            RandomizedDelaySec = "5m";
          };
          Install.WantedBy = [ "timers.target" ];
        };
      };
    };
  };
}
