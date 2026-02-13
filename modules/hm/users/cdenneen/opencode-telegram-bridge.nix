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

    # Needs decrypted sops-nix secrets to exist.
    home.activation.opencodeTelegramBridgeEnvFile =
      lib.hm.dag.entryAfter
        [
          "writeBoundary"
          "sops-nix"
        ]
        ''
          set -euo pipefail

          cfg_dir="$HOME/.config/opencode-telegram-bridge"
          env_file="$cfg_dir/env"
          bot_token_file="${config.sops.secrets.telegram_bot_token.path}"
          chat_id_file="${config.sops.secrets.telegram_chat_id.path}"
          allowed_ids_file=""
          webhook_public_url='${if cfg.webhookPublicUrl == null then "" else cfg.webhookPublicUrl}'

          $DRY_RUN_CMD mkdir -p "$cfg_dir"

          if [ ! -r "$bot_token_file" ]; then
            echo "opencode-telegram-bridge: missing telegram_bot_token secret; skipping env setup" >&2
            exit 0
          fi

          bot_token="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$bot_token_file")"
          owner_chat_id="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$chat_id_file" || true)"

          if [ -z "$bot_token" ]; then
            echo "opencode-telegram-bridge: telegram_bot_token is empty" >&2
            exit 1
          fi

          allowed_chat_ids=""

          if [ -n "$allowed_ids_file" ] && [ -r "$allowed_ids_file" ]; then
            allowed_chat_ids="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$allowed_ids_file")"
          fi

          if [ -z "$allowed_chat_ids" ]; then
            db_file="$HOME/.local/share/opencode-telegram-bridge/state.sqlite"
            if [ -r "$db_file" ]; then
              allowed_chat_ids="$(${pkgs.sqlite}/bin/sqlite3 "$db_file" "select value from kv where key='telegram.allowed_chat_ids' limit 1;")"
            fi
          fi

          if [ -z "$allowed_chat_ids" ]; then
            allowed_chat_ids="$owner_chat_id"
          fi
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
                  "TELEGRAM_WEBHOOK_FALLBACK_SEC=300" \
                  >"$env_file"
        '';

    systemd.user.services.opencode-telegram-bridge = {
      Unit = {
        Description = "OpenCode <-> Telegram bridge";
        After = [ "network-online.target" ];
        ConditionPathExists = "%h/.config/opencode-telegram-bridge/env";
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
