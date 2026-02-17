{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.telegram-bridge;
  configDir = cfg.configDir;
  configPath = "${configDir}/config.json";
  userOverridePath = cfg.userOverridePath;
in
{
  options.programs.telegram-bridge = {
    enable = lib.mkEnableOption "OpenCode Telegram bridge";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../../../pkgs/opencode-telegram-bridge.nix { };
      description = "Package providing opencode-telegram-bridge.";
    };

    configDir = lib.mkOption {
      type = lib.types.str;
      default = "${config.xdg.configHome}/opencode-telegram-bridge";
      description = "Directory for generated config.json.";
    };

    userOverridePath = lib.mkOption {
      type = lib.types.str;
      default = "${config.xdg.configHome}/telegram_bridge/config.user.json";
      description = "Optional user override JSON file merged on startup.";
    };

    telegram = {
      botTokenFile = lib.mkOption {
        type = lib.types.str;
        description = "Path to the Telegram bot token file (sops-nix secret).";
      };

      ownerChatIdFile = lib.mkOption {
        type = lib.types.str;
        description = "Path to the Telegram owner chat ID file (sops-nix secret).";
      };

      allowedChatIds = lib.mkOption {
        type = lib.types.listOf lib.types.int;
        default = [ ];
        description = "Allowed chat IDs (overrides DB if non-empty).";
      };

      allowedChatIdsFile = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Optional file with comma-separated allowed chat IDs.";
      };

      updatesMode = lib.mkOption {
        type = lib.types.enum [
          "polling"
          "webhook"
        ];
        default = "polling";
        description = "How the bridge receives Telegram updates.";
      };

      pollTimeoutSec = lib.mkOption {
        type = lib.types.int;
        default = 30;
        description = "Polling timeout for getUpdates.";
      };

      dbRetentionDays = lib.mkOption {
        type = lib.types.int;
        default = 30;
        description = "Prune topic rows older than this many days (0 disables).";
      };

      dbMaxTopics = lib.mkOption {
        type = lib.types.int;
        default = 500;
        description = "Max topics to retain in the DB (0 disables).";
      };

      webhook = {
        listenHost = lib.mkOption {
          type = lib.types.str;
          default = "127.0.0.1";
        };

        listenPort = lib.mkOption {
          type = lib.types.int;
          default = 18080;
        };

        path = lib.mkOption {
          type = lib.types.str;
          default = "/telegram";
        };

        publicUrl = lib.mkOption {
          type = lib.types.nullOr lib.types.str;
          default = null;
          description = "Public base URL for webhook (scheme+host).";
        };

        fallbackSec = lib.mkOption {
          type = lib.types.int;
          default = 0;
          description = "Fallback to polling if webhook idle (0 disables).";
        };
      };
    };

    opencode = {
      workspaceRoot = lib.mkOption {
        type = lib.types.str;
        default = "${config.home.homeDirectory}/src";
        description = "Workspace root for /map <name>.";
      };

      bin = lib.mkOption {
        type = lib.types.str;
        default = "${pkgs.opencode}/bin/opencode";
        description = "Path to opencode executable.";
      };

      maxSessions = lib.mkOption {
        type = lib.types.int;
        default = 5;
      };

      idleTimeoutSec = lib.mkOption {
        type = lib.types.int;
        default = 3600;
      };

      defaultModel = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
      };

      defaultAgent = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
      };

      defaultProvider = lib.mkOption {
        type = lib.types.str;
        default = "openai";
        description = "Provider prefix used when a model lacks provider (e.g. openai).";
      };

      useSharedServer = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Use a single shared opencode server instead of per-workspace servers.";
      };

      serverUrl = lib.mkOption {
        type = lib.types.str;
        default = "http://127.0.0.1:4096";
        description = "Base URL for the shared opencode server.";
      };

      serverUsername = lib.mkOption {
        type = lib.types.str;
        default = "opencode";
        description = "Username for opencode server basic auth.";
      };

      serverPasswordFile = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Path to opencode server password file (sops secret).";
      };
    };

    chat = {
      allowedGithubUsers = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ ];
        description = "GitHub users allowed via Cloudflare Access (documented for policy).";
      };

      announceStartup = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Send a startup notice to all mapped topics on bridge boot.";
      };

      announceMessage = lib.mkOption {
        type = lib.types.str;
        default = "Bridge connected.";
        description = "Message sent when announceStartup is enabled.";
      };
    };

    web = {
      enable = lib.mkEnableOption "sync OpenCode web responses to Telegram";

      baseUrl = lib.mkOption {
        type = lib.types.str;
        default = "http://127.0.0.1:4096";
        description = "Base URL for opencode web server (no trailing slash).";
      };

      username = lib.mkOption {
        type = lib.types.str;
        default = "opencode";
        description = "Username for opencode web basic auth.";
      };

      passwordFile = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Path to opencode web password file (sops secret).";
      };

      syncIntervalSec = lib.mkOption {
        type = lib.types.int;
        default = 10;
        description = "Polling interval for web->Telegram sync.";
      };

      forwardUserPrompts = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Forward user prompts from web sessions to Telegram topics.";
      };

      forwardAgentSteps = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Forward assistant step/tool parts (if available) to Telegram topics.";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [
      cfg.package
      pkgs.opencode
      pkgs.sqlite
    ];

    home.activation.telegramBridgeConfig =
      lib.hm.dag.entryAfter
        [
          "writeBoundary"
          "sops-nix"
        ]
        ''
          set -euo pipefail

          cfg_dir="${configDir}"
          cfg_file="${configPath}"
          bot_token_file="${cfg.telegram.botTokenFile}"
          owner_chat_file="${cfg.telegram.ownerChatIdFile}"
          allowed_file="${
            lib.optionalString (cfg.telegram.allowedChatIdsFile != null) cfg.telegram.allowedChatIdsFile
          }"

          $DRY_RUN_CMD mkdir -p "$cfg_dir"

          if [ ! -r "$bot_token_file" ]; then
            echo "telegram-bridge: missing bot token secret" >&2
            exit 0
          fi

          bot_token="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$bot_token_file")"
          owner_chat_id="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$owner_chat_file" || true)"

          if [ -z "$bot_token" ]; then
            echo "telegram-bridge: bot token is empty" >&2
            exit 1
          fi

          allowed_chat_ids="${lib.concatStringsSep "," (map toString cfg.telegram.allowedChatIds)}"

          if [ -z "$allowed_chat_ids" ] && [ -n "${
            lib.optionalString (cfg.telegram.allowedChatIdsFile != null) "1"
          }" ] && [ -r "$allowed_file" ]; then
            allowed_chat_ids="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$allowed_file")"
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

          $DRY_RUN_CMD ${pkgs.coreutils}/bin/install -m 600 /dev/null "$cfg_file"
          $DRY_RUN_CMD ${pkgs.coreutils}/bin/chmod 600 "$cfg_file"

          export BOT_TOKEN="$bot_token"
          export OWNER_CHAT_ID="$owner_chat_id"
          export ALLOWED_CHAT_IDS="$allowed_chat_ids"
          export WEBHOOK_PUBLIC_URL="${
            if cfg.telegram.webhook.publicUrl == null then "" else cfg.telegram.webhook.publicUrl
          }"
          export WEBHOOK_LISTEN_HOST="${cfg.telegram.webhook.listenHost}"
          export WEBHOOK_LISTEN_PORT="${toString cfg.telegram.webhook.listenPort}"
          export WEBHOOK_PATH="${cfg.telegram.webhook.path}"
          export WEBHOOK_SECRET="$webhook_secret"
          export WEBHOOK_FALLBACK_SEC="${toString cfg.telegram.webhook.fallbackSec}"
          export UPDATES_MODE="${cfg.telegram.updatesMode}"
          export POLL_TIMEOUT_SEC="${toString cfg.telegram.pollTimeoutSec}"
          export DB_RETENTION_DAYS="${toString cfg.telegram.dbRetentionDays}"
          export DB_MAX_TOPICS="${toString cfg.telegram.dbMaxTopics}"
          export OPENCODE_WORKSPACE_ROOT="${cfg.opencode.workspaceRoot}"
          export OPENCODE_BIN="${cfg.opencode.bin}"
          export OPENCODE_MAX_SESSIONS="${toString cfg.opencode.maxSessions}"
          export OPENCODE_IDLE_TIMEOUT_SEC="${toString cfg.opencode.idleTimeoutSec}"
          export OPENCODE_DEFAULT_MODEL="${
            lib.optionalString (cfg.opencode.defaultModel != null) cfg.opencode.defaultModel
          }"
          export OPENCODE_DEFAULT_AGENT="${
            lib.optionalString (cfg.opencode.defaultAgent != null) cfg.opencode.defaultAgent
          }"
          export OPENCODE_DEFAULT_PROVIDER="${cfg.opencode.defaultProvider}"
          export OPENCODE_USE_SHARED_SERVER="${if cfg.opencode.useSharedServer then "1" else "0"}"
          export OPENCODE_SERVER_URL="${cfg.opencode.serverUrl}"
          export OPENCODE_SERVER_USERNAME="${cfg.opencode.serverUsername}"
          export OPENCODE_SERVER_PASSWORD_FILE="${
            lib.optionalString (cfg.opencode.serverPasswordFile != null) cfg.opencode.serverPasswordFile
          }"
          export CHAT_ALLOWED_GITHUB_USERS="${lib.concatStringsSep "," cfg.chat.allowedGithubUsers}"
          export CHAT_ANNOUNCE_STARTUP="${if cfg.chat.announceStartup then "1" else "0"}"
          export CHAT_ANNOUNCE_MESSAGE="${cfg.chat.announceMessage}"
          export OPENCODE_WEB_ENABLE="${if cfg.web.enable then "1" else "0"}"
          export OPENCODE_WEB_BASE_URL="${cfg.web.baseUrl}"
          export OPENCODE_WEB_USERNAME="${cfg.web.username}"
          export OPENCODE_WEB_PASSWORD_FILE="${
            lib.optionalString (cfg.web.passwordFile != null) cfg.web.passwordFile
          }"
          export OPENCODE_WEB_SYNC_INTERVAL_SEC="${toString cfg.web.syncIntervalSec}"
          export OPENCODE_WEB_FORWARD_USER_PROMPTS="${if cfg.web.forwardUserPrompts then "1" else "0"}"
          export OPENCODE_WEB_FORWARD_AGENT_STEPS="${if cfg.web.forwardAgentSteps then "1" else "0"}"
          export CONFIG_FILE="$cfg_file"

          ${pkgs.python3}/bin/python - <<'PY'
          import json
          import os
          from pathlib import Path

          bot_token = os.environ.get("BOT_TOKEN", "")
          owner_chat_id = os.environ.get("OWNER_CHAT_ID", "")
          allowed_csv = os.environ.get("ALLOWED_CHAT_IDS", "")
          webhook_public = os.environ.get("WEBHOOK_PUBLIC_URL", "")

          def parse_csv(value):
              out = []
              for item in value.split(","):
                  item = item.strip()
                  if item:
                      out.append(int(item))
              return out

          cfg = {
              "telegram": {
                  "bot_token": bot_token,
                  "updates_mode": os.environ.get("UPDATES_MODE", "polling"),
                  "poll_timeout_sec": int(os.environ.get("POLL_TIMEOUT_SEC", "30")),
                  "db_retention_days": int(os.environ.get("DB_RETENTION_DAYS", "30")),
                  "db_max_topics": int(os.environ.get("DB_MAX_TOPICS", "500")),
                  "webhook": {
                      "listen_host": os.environ.get("WEBHOOK_LISTEN_HOST", "127.0.0.1"),
                      "listen_port": int(os.environ.get("WEBHOOK_LISTEN_PORT", "18080")),
                      "path": os.environ.get("WEBHOOK_PATH", "/telegram"),
                      "secret": os.environ.get("WEBHOOK_SECRET", ""),
                      "fallback_sec": int(os.environ.get("WEBHOOK_FALLBACK_SEC", "0")),
                  },
              },
              "opencode": {
                  "workspace_root": os.environ.get("OPENCODE_WORKSPACE_ROOT", ""),
                  "bin": os.environ.get("OPENCODE_BIN", "opencode"),
                  "max_sessions": int(os.environ.get("OPENCODE_MAX_SESSIONS", "5")),
                  "idle_timeout_sec": int(os.environ.get("OPENCODE_IDLE_TIMEOUT_SEC", "3600")),
              },
          }

          if owner_chat_id:
              cfg["telegram"]["owner_chat_id"] = int(owner_chat_id)
          if allowed_csv:
              cfg["telegram"]["allowed_chat_ids"] = parse_csv(allowed_csv)
          if webhook_public:
              cfg["telegram"]["webhook"]["public_url"] = webhook_public

          default_model = os.environ.get("OPENCODE_DEFAULT_MODEL")
          if default_model:
              cfg["opencode"]["default_model"] = default_model
          default_agent = os.environ.get("OPENCODE_DEFAULT_AGENT")
          if default_agent:
              cfg["opencode"]["default_agent"] = default_agent

          default_provider = os.environ.get("OPENCODE_DEFAULT_PROVIDER")
          if default_provider:
              cfg["opencode"]["default_provider"] = default_provider

          use_shared = os.environ.get("OPENCODE_USE_SHARED_SERVER", "")
          if use_shared and use_shared.lower() not in ("0", "false", "no", "off"):
              cfg["opencode"]["use_shared_server"] = True
              cfg["opencode"]["server_url"] = os.environ.get("OPENCODE_SERVER_URL", "")
              cfg["opencode"]["server_username"] = os.environ.get("OPENCODE_SERVER_USERNAME", "")
              cfg["opencode"]["server_password_file"] = os.environ.get("OPENCODE_SERVER_PASSWORD_FILE", "")

          web_enable = os.environ.get("OPENCODE_WEB_ENABLE", "")
          if web_enable and web_enable.lower() not in ("0", "false", "no", "off"):
              cfg["web"] = {
                  "enable": True,
                  "base_url": os.environ.get("OPENCODE_WEB_BASE_URL", ""),
                  "username": os.environ.get("OPENCODE_WEB_USERNAME", ""),
                  "password_file": os.environ.get("OPENCODE_WEB_PASSWORD_FILE", ""),
                  "sync_interval_sec": int(os.environ.get("OPENCODE_WEB_SYNC_INTERVAL_SEC", "10")),
                  "forward_user_prompts": os.environ.get("OPENCODE_WEB_FORWARD_USER_PROMPTS", "")
                    .lower() not in ("", "0", "false", "no", "off"),
                  "forward_agent_steps": os.environ.get("OPENCODE_WEB_FORWARD_AGENT_STEPS", "")
                    .lower() not in ("", "0", "false", "no", "off"),
              }

          allowed_github = os.environ.get("CHAT_ALLOWED_GITHUB_USERS", "")
          if allowed_github:
              cfg["chat"] = {
                  "allowed_github_users": [
                      user.strip() for user in allowed_github.split(",") if user.strip()
                  ]
              }

          path = Path(os.environ.get("CONFIG_FILE", ""))
          path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
          PY
        '';

    systemd.user.services.opencode-telegram-bridge = {
      Unit = {
        Description = "OpenCode <-> Telegram bridge";
        After = [ "network-online.target" ];
        ConditionPathExists = configPath;
      };

      Service = {
        Type = "simple";
        ExecStart = "${cfg.package}/bin/opencode-telegram-bridge";
        Restart = "always";
        RestartSec = 2;
        Environment = [
          "OPENCODE_TELEGRAM_CONFIG=${configPath}"
          "OPENCODE_TELEGRAM_CONFIG_USER=${userOverridePath}"
        ];
        UMask = "0077";
      };

      Install = {
        WantedBy = [ "default.target" ];
      };
    };
  };
}
