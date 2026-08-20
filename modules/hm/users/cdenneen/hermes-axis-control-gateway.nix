{
  agentPkgs ? null,
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.hermesAxisControlGateway;
  packageAvailable = pkgs.stdenv.isLinux && agentPkgs != null && agentPkgs ? hermes;
  axisControlRoot = "${config.home.homeDirectory}/src/workspace/work/axis-control";
  hermesHome = "${axisControlRoot}/.hermes";
  hermesGatewaySitecustomize = pkgs.writeTextDir "sitecustomize.py" ''
    try:
        import hermes_cli.commands as commands
        commands.should_bypass_active_session = commands.is_gateway_known_command
    except Exception:
        pass
  '';
  hermesGatewayBypassCheck = pkgs.writeShellScript "hermes-axis-control-plugin-bypass-check" ''
    set -euo pipefail
    exec ${agentPkgs.hermes.hermesVenv}/bin/python3 -c \
      'import hermes_cli.commands as commands; assert commands.should_bypass_active_session is commands.is_gateway_known_command'
  '';
  servicePath = builtins.concatStringsSep ":" [
    "${axisControlRoot}/.venv/bin"
    "${config.home.profileDirectory}/bin"
    "/etc/profiles/per-user/${config.home.username}/bin"
    "/run/wrappers/bin"
    "${config.home.homeDirectory}/.local/bin"
    "/run/current-system/sw/bin"
    "/usr/bin"
    "/bin"
  ];
in
{
  options.profiles.hermesAxisControlGateway.enable = lib.mkEnableOption "dedicated Axis-control Hermes gateway";

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = packageAvailable;
        message = "profiles.hermesAxisControlGateway requires Linux and agentPkgs.hermes";
      }
    ];

    home.packages = [ agentPkgs.hermes ];

    home.activation.hermesAxisControlGatewayLegacyCleanup =
      lib.hm.dag.entryBefore [ "checkLinkTargets" ]
        ''
          unit="$HOME/.config/systemd/user/hermes-axis-control-gateway.service"
          if [ -f "$unit" ] && [ ! -L "$unit" ]; then
            $DRY_RUN_CMD ${pkgs.coreutils}/bin/mv -f "$unit" "$unit.pre-home-manager"
          fi
          if [ -f "$unit.d/override.conf" ]; then
            $DRY_RUN_CMD ${pkgs.coreutils}/bin/mv -f \
              "$unit.d/override.conf" "$unit.d/override.conf.pre-home-manager"
          fi
          $DRY_RUN_CMD ${pkgs.coreutils}/bin/rmdir --ignore-fail-on-non-empty "$unit.d" 2>/dev/null || true
        '';

    systemd.user.services.hermes-axis-control-gateway = {
      Unit = {
        Description = "Hermes Axis-Control Gateway / Cron Scheduler";
        After = [ "network-online.target" ];
        Wants = [ "network-online.target" ];
      };
      Service = {
        Type = "simple";
        ExecStartPre = hermesGatewayBypassCheck;
        ExecStart = "${agentPkgs.hermes}/bin/hermes --profile axis-control gateway run";
        WorkingDirectory = axisControlRoot;
        Environment = [
          "HERMES_HOME=${hermesHome}"
          "AWS_PROFILE=sso-apss"
          "AWS_DEFAULT_PROFILE=sso-apss"
          "AWS_REGION=us-east-1"
          "AWS_CONFIG_FILE=${config.home.homeDirectory}/.aws/config"
          "CLAUDE_CODE_USE_BEDROCK=1"
          "ANTHROPIC_DEFAULT_SONNET_MODEL=us.anthropic.claude-sonnet-4-6"
          "ANTHROPIC_DEFAULT_HAIKU_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0"
          "PATH=${servicePath}"
          "PYTHONPATH=${hermesGatewaySitecustomize}"
        ];
        Restart = "on-failure";
        RestartPreventExitStatus = 78;
        RestartSec = 5;
        TimeoutStopSec = 180;
        KillMode = "mixed";
      };
      Install.WantedBy = [ "default.target" ];
    };
  };
}
