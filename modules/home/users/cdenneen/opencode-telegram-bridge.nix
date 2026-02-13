{
  config,
  lib,
  pkgs,
  osConfig ? null,
  ...
}:
let
  hostName = if osConfig != null then (osConfig.networking.hostName or "") else "";
  isNyx = hostName == "nyx";

  cfg = config.opencodeTelegramBridge;

  python = pkgs.python3.withPackages (ps: [
    ps.aiohttp
    ps.httpx
  ]);

  bridgePy = pkgs.writeText "opencode-telegram-bridge.py" (
    builtins.readFile ./files/opencode-telegram-bridge/bridge.py
  );

  bridgeBin = pkgs.writeShellScriptBin "opencode-telegram-bridge" ''
    set -euo pipefail
    exec ${python}/bin/python ${bridgePy} "$@"
  '';
in
{
  options.opencodeTelegramBridge = {
    workspaceRoot = lib.mkOption {
      type = lib.types.str;
      default = "/home/cdenneen/src/workspace";
      description = "Workspace root directory used for /map <name>.";
    };

    updatesMode = lib.mkOption {
      type = lib.types.enum [
        "polling"
        "webhook"
      ];
      default = "polling";
      description = "How the bridge receives Telegram updates.";
    };

    webhookPublicUrl = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Public base URL (scheme+host) for Telegram webhook, e.g. https://tg.example.com";
    };
  };

  config = lib.mkIf isNyx {
    home.packages = [
      bridgeBin
      pkgs.opencode
      pkgs.sqlite
    ];

    home.activation.opencodeTelegramBridgeEnvFile = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
            set -euo pipefail

            cfg_dir="$HOME/.config/opencode-telegram-bridge"
            env_file="$cfg_dir/env"
            notify_src="${config.sops.secrets.opencode_telegram_notify_ts.path}"
            webhook_public_url='${if cfg.webhookPublicUrl == null then "" else cfg.webhookPublicUrl}'

            $DRY_RUN_CMD mkdir -p "$cfg_dir"

            extracted="$(${python}/bin/python - "$notify_src" <<'PY'
      import re
      import sys

      src = sys.argv[1]
      txt = open(src, 'r', encoding='utf-8', errors='ignore').read()

      def grab(name: str) -> str | None:
          m = re.search(rf"\\b{name}\\b\\s*=\\s*(['\"])([^'\"]+)\\1", txt)
          if m:
              return m.group(2).strip()
          m = re.search(rf"\\b{name}\\b\\s*:\\s*(['\"])([^'\"]+)\\1", txt)
          if m:
              return m.group(2).strip()
          return None

      token = grab('BOT_TOKEN') or grab('TELEGRAM_BOT_TOKEN')
      chat_id = grab('CHAT_ID') or grab('TELEGRAM_CHAT_ID')

      if not token:
          sys.stderr.write('Failed to extract BOT_TOKEN from opencode_telegram_notify_ts\n')
          sys.exit(2)

      print('TOKEN=' + token)
      if chat_id:
          print('CHAT_ID=' + chat_id)
      PY
            )"

            bot_token="$(printf '%s\n' "$extracted" | ${pkgs.gnugrep}/bin/grep '^TOKEN=' | ${pkgs.coreutils}/bin/cut -d= -f2-)"
            owner_chat_id="$(printf '%s\n' "$extracted" | ${pkgs.gnugrep}/bin/grep '^CHAT_ID=' | ${pkgs.coreutils}/bin/cut -d= -f2- || true)"

            allowed_chat_ids="$owner_chat_id"
            case "$owner_chat_id" in
              -*) owner_chat_id="" ;;
            esac

            webhook_secret="$(${pkgs.coreutils}/bin/printf '%s' "$bot_token" | ${pkgs.openssl}/bin/openssl dgst -sha256 | ${pkgs.gnused}/bin/sed 's/^.*= //')"

            $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 /dev/null "$env_file"
            $DRY_RUN_CMD ${pkgs.coreutils}/bin/chmod 600 "$env_file"
            $DRY_RUN_CMD ${pkgs.coreutils}/bin/printf '%s\n' \
              "TELEGRAM_BOT_TOKEN=$bot_token" \
              "TELEGRAM_ALLOWED_CHAT_IDS=$allowed_chat_ids" \
              "TELEGRAM_OWNER_CHAT_ID=$owner_chat_id" \
              "OPENCODE_WORKSPACE_ROOT=${cfg.workspaceRoot}" \
              "OPENCODE_MAX_SESSIONS=5" \
              "OPENCODE_IDLE_TIMEOUT_SEC=3600" \
              "TELEGRAM_POLL_TIMEOUT_SEC=30" \
              "TELEGRAM_UPDATES_MODE=${cfg.updatesMode}" \
              "TELEGRAM_WEBHOOK_LISTEN_HOST=127.0.0.1" \
              "TELEGRAM_WEBHOOK_LISTEN_PORT=18080" \
              "TELEGRAM_WEBHOOK_PATH=/telegram" \
              "TELEGRAM_WEBHOOK_SECRET=$webhook_secret" \
              "TELEGRAM_WEBHOOK_PUBLIC_URL=$webhook_public_url" \
              >"$env_file"
    '';

    systemd.user.services.opencode-telegram-bridge = {
      Unit = {
        Description = "OpenCode <-> Telegram bridge";
        After = [ "network-online.target" ];
      };

      Service = {
        Type = "simple";
        ExecStart = "${bridgeBin}/bin/opencode-telegram-bridge";
        Restart = "always";
        RestartSec = 2;
        EnvironmentFile = "%h/.config/opencode-telegram-bridge/env";
        UMask = "0077";
      };

      Install = {
        WantedBy = [ "default.target" ];
      };
    };
  };
}
