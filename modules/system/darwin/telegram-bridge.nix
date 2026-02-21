{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.opencode-telegram-bridge;
  hmUser = if cfg.user != null then config.home-manager.users.${cfg.user} or null else null;
  tb = if hmUser != null then hmUser.programs.telegram-bridge or null else null;
  configPath = if tb != null then "${tb.configDir}/config.json" else "";
  userOverridePath = if tb != null then tb.userOverridePath else "";
  homeDir = if cfg.user != null then "/Users/${cfg.user}" else "/Users";
in
{
  options.services.opencode-telegram-bridge = {
    enable = lib.mkEnableOption "OpenCode Telegram bridge launchd integration";

    user = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "User account that owns the bridge configuration and runs launchd agents.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.user != null;
        message = "services.opencode-telegram-bridge.user must be set when enabled.";
      }
      {
        assertion = tb != null;
        message = "home-manager.users.${toString cfg.user}.programs.telegram-bridge must be configured.";
      }
    ];

    launchd.agents.opencode-telegram-bridge = {
      enable = true;
      config = {
        ProgramArguments = [
          "${tb.package}/bin/opencode-telegram-bridge"
        ];
        EnvironmentVariables = {
          OPENCODE_TELEGRAM_CONFIG = configPath;
          OPENCODE_TELEGRAM_CONFIG_USER = userOverridePath;
        };
        WorkingDirectory = homeDir;
        KeepAlive = true;
        RunAtLoad = true;
      };
    };

    launchd.agents.cloudflared-telegram-bridge = lib.mkIf tb.cloudflared.enable (
      let
        cfgFile = pkgs.writeText "cloudflared-telegram-bridge.yml" tb.cloudflared.configText;
        run = pkgs.writeShellScript "cloudflared-telegram-bridge" ''
          set -euo pipefail
          token_file="${tb.cloudflared.tokenFile}"
          if [ -z "$token_file" ]; then
            echo "cloudflared-telegram-bridge: token file not configured" >&2
            exit 1
          fi
          if [ ! -r "$token_file" ]; then
            echo "cloudflared-telegram-bridge: token file not readable" >&2
            exit 1
          fi
          exec ${pkgs.cloudflared}/bin/cloudflared \
            --config "${cfgFile}" \
            tunnel run \
            --token "$(${pkgs.coreutils}/bin/cat "$token_file")"
        '';
      in
      {
        enable = true;
        config = {
          ProgramArguments = [ run ];
          WorkingDirectory = homeDir;
          KeepAlive = true;
          RunAtLoad = true;
        };
      }
    );
  };
}
