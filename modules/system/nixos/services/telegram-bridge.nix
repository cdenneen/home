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
in
{
  options.services.opencode-telegram-bridge = {
    enable = lib.mkEnableOption "OpenCode Telegram bridge system integration";

    user = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "User account that owns the bridge configuration and runs system services.";
    };

    systemdMode = lib.mkOption {
      type = lib.types.enum [
        "user"
        "system"
      ];
      default = "user";
      description = "Install systemd units as user or system services.";
    };

    enableLinger = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Enable user linger so user services start at boot.";
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

    users.users.${cfg.user}.linger = cfg.systemdMode == "user" && cfg.enableLinger;

    home-manager.users.${cfg.user}.programs.telegram-bridge = {
      systemdMode = lib.mkDefault cfg.systemdMode;
      enableLinger = lib.mkDefault cfg.enableLinger;
    };

    systemd.services.opencode-telegram-bridge = lib.mkIf (cfg.systemdMode == "system") {
      description = "OpenCode <-> Telegram bridge";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];

      serviceConfig = {
        Type = "simple";
        User = cfg.user;
        Group = "users";
        ExecStart = "${tb.package}/bin/opencode-telegram-bridge";
        Restart = "always";
        RestartSec = 2;
        UMask = "0077";
        Environment = [
          "OPENCODE_TELEGRAM_CONFIG=${configPath}"
          "OPENCODE_TELEGRAM_CONFIG_USER=${userOverridePath}"
        ];
        WorkingDirectory = "/home/${cfg.user}";
      };
    };

    systemd.services.cloudflared-telegram-bridge =
      lib.mkIf (cfg.systemdMode == "system" && tb.cloudflared.enable)
        (
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
            description = "Cloudflare Tunnel (Telegram bridge + chat)";
            after = [
              "network-online.target"
              "opencode-telegram-bridge.service"
            ];
            wants = [
              "network-online.target"
              "opencode-telegram-bridge.service"
            ];
            wantedBy = [ "multi-user.target" ];

            serviceConfig = {
              ExecStart = run;
              Restart = "always";
              RestartSec = 2;
            };
          }
        );
  };
}
