{
  config,
  lib,
  pkgs,
  happier,
  ...
}:
{
  home.sessionVariables = {
    OPENCLAW_GATEWAY_URL = "ws://127.0.0.1:18789";
    OPENCLAW_GATEWAY_TOKEN_FILE = "${config.home.homeDirectory}/.config/openclaw/gateway.token";
    OPENCLAW_BUNDLED_PLUGINS_DIR = "${config.home.homeDirectory}/.openclaw/extensions";
  };

  sops.secrets = { };

  programs.starship.settings.palette = lib.mkForce "nyx";

  systemd.user.services.happier-daemon =
    let
      happierCli = happier.packages.${pkgs.stdenv.hostPlatform.system}.happier-cli;
    in
    {
      Unit = {
        Description = "Happier CLI daemon (nyx)";
      };
      Service = {
        Type = "simple";
        ExecStart = "${pkgs.nodejs_22}/bin/node ${happierCli}/lib/happier-cli/apps/cli/dist/index.mjs daemon start-sync";
        WorkingDirectory = "%h";
        Environment = [
          "PATH=${
            lib.makeBinPath [
              pkgs.nodejs_22
              pkgs.difftastic
              pkgs.ripgrep
            ]
          }:/run/wrappers/bin:/etc/profiles/per-user/cdenneen/bin:/run/current-system/sw/bin:/home/cdenneen/.local/bin:/usr/bin:/bin"
          "HAPPIER_HOME_DIR=/home/cdenneen/.happier"
          "HAPPIER_SERVER_URL=https://nyx.tail0e55.ts.net"
          "HAPPIER_WEBAPP_URL=https://nyx.tail0e55.ts.net"
          "HAPPIER_PUBLIC_SERVER_URL=https://nyx.tail0e55.ts.net"
          "HAPPIER_NO_BROWSER_OPEN=1"
          "HAPPIER_DAEMON_WAIT_FOR_AUTH=1"
          "HAPPIER_DAEMON_WAIT_FOR_AUTH_TIMEOUT_MS=0"
        ];
        Restart = "on-failure";
      };
      Install = {
        WantedBy = [ "default.target" ];
      };
    };

  programs.openclaw = {
    enable = true;
    systemd.enable = true;
    exposePluginPackages = false;
    package = pkgs.openclaw-gateway;
    bundledPlugins.summarize.enable = true;
    skills = [
      {
        name = "cwd";
        description = "Return ACP command(s) to switch cwd for this topic.";
        mode = "inline";
        body = ''
          When a user runs /cwd <path>, respond with ACP command(s) that switch the session cwd.

          Rules:
          - If the user includes a session key in the same message (contains "agent:"), return only:
            /acp cwd <path> <session-key>
          - Else, if a session key appears in the last few messages in this thread, use it:
            /acp cwd <path> <session-key>
          - Else return a spawn command that binds this thread:
            /acp spawn codex --mode persistent --thread here --cwd <path>

          Always return exactly one command in a single fenced code block, no extra text.
        '';
      }
    ];
    config = {
      agents = {
        defaults = {
          model = "openai/gpt-5.2-codex";
        };
      };
      acp = {
        dispatch = {
          enabled = true;
        };
      };
      plugins = {
        entries = {
          "happier-session-control" = {
            config = {
              happierServerUrl = "https://nyx.tail0e55.ts.net";
              happierWebappUrl = "https://nyx.tail0e55.ts.net";
              happierHomeDir = "${config.home.homeDirectory}/.config/happier";
            };
          };
        };
        allow = [
          "acpx"
          "acp-dispatch"
          "happier-session-control"
          "memory-core"
          "telegram"
        ];
        load = {
          paths = [
            "${config.home.homeDirectory}/.openclaw/extensions/acpx"
            "${config.home.homeDirectory}/.openclaw/extensions/acp-dispatch"
            "${config.home.homeDirectory}/.openclaw/extensions/happier-session-control"
            "${config.home.homeDirectory}/.openclaw/extensions/memory-core"
            "${config.home.homeDirectory}/.openclaw/extensions/telegram"
          ];
        };
      };
      gateway = {
        trustedProxies = [
          "127.0.0.1/32"
          "::1/128"
        ];
        mode = "local";
        bind = "loopback";
        auth = {
          mode = "token";
        };
        controlUi = {
          enabled = true;
          basePath = "/ui";
          root = "${config.home.homeDirectory}/.openclaw/control-ui";
          allowedOrigins = [ "https://clawd.denneen.net" ];
          dangerouslyAllowHostHeaderOriginFallback = true;
          allowInsecureAuth = true;
          dangerouslyDisableDeviceAuth = true;
        };
      };
      channels.telegram = {
        enabled = true;
        tokenFile = config.sops.secrets.telegram_bot_token.path;
        streaming = "partial";
        groupPolicy = "open";
        allowFrom = [
          8215848239
          8568027099
        ];
        groups = {
          "*" = {
            requireMention = true;
          };
          "-1003440564815" = {
            requireMention = false;
          };
        };
      };
      session = {
        dmScope = "per-channel-peer";
      };
    };
  };

  home.file.".openclaw/openclaw.json".force = true;

  systemd.user.services.openclaw-gateway =
    let
      run = pkgs.writeShellScript "openclaw-gateway-wrapper" ''
        set -euo pipefail

        token_file="${config.sops.secrets.openclaw_gateway_token.path}"
        openai_file="${config.sops.secrets.openai_api_key.path}"

        if [ ! -r "$token_file" ]; then
          echo "openclaw-gateway: token file not readable" >&2
          exit 1
        fi

        if [ ! -r "$openai_file" ]; then
          echo "openclaw-gateway: openai api key file not readable" >&2
          exit 1
        fi

        export OPENCLAW_GATEWAY_TOKEN="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$token_file")"
        export OPENAI_API_KEY="$(${pkgs.coreutils}/bin/tr -d '\n\r' <"$openai_file")"
        export OPENCLAW_BUNDLED_PLUGINS_DIR="${config.home.homeDirectory}/.openclaw/extensions"
        export PATH="${pkgs.nodejs_22}/bin:$PATH"

        exec ${pkgs.openclaw}/bin/openclaw gateway --port 18789
      '';
    in
    {
      Service.ExecStart = lib.mkForce run;
      Service.Environment = [
        "PATH=${pkgs.nodejs_22}/bin:/run/current-system/sw/bin:/usr/bin:/bin"
        "OPENCLAW_BUNDLED_PLUGINS_DIR=${config.home.homeDirectory}/.openclaw/extensions"
      ];
      Install = {
        WantedBy = [ "default.target" ];
      };
    };

  home.activation.openclawControlUiAssets = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    $DRY_RUN_CMD rm -rf "$HOME/.openclaw/control-ui"
    $DRY_RUN_CMD mkdir -p "$HOME/.openclaw/control-ui"
    $DRY_RUN_CMD cp -R "${pkgs.openclaw-gateway}/lib/openclaw/dist/control-ui/." "$HOME/.openclaw/control-ui/"
    $DRY_RUN_CMD chmod -R u+rwX,go+rX "$HOME/.openclaw/control-ui"
  '';

  home.activation.openclawExtensions = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    $DRY_RUN_CMD rm -rf "$HOME/.openclaw/extensions"
    $DRY_RUN_CMD mkdir -p "$HOME/.openclaw/extensions/acpx" "$HOME/.openclaw/extensions/acp-dispatch" "$HOME/.openclaw/extensions/happier-session-control" "$HOME/.openclaw/extensions/memory-core" "$HOME/.openclaw/extensions/telegram"
    $DRY_RUN_CMD cp -R "${pkgs.openclaw-gateway}/lib/openclaw/extensions/acpx/." "$HOME/.openclaw/extensions/acpx/"
    $DRY_RUN_CMD cp -R "${../../modules/hm/users/cdenneen/opencode/extensions/acp-dispatch}/." "$HOME/.openclaw/extensions/acp-dispatch/"
    $DRY_RUN_CMD cp -R "${../../modules/hm/users/cdenneen/opencode/extensions/happier-session-control}/." "$HOME/.openclaw/extensions/happier-session-control/"
    $DRY_RUN_CMD cp -R "${pkgs.openclaw-gateway}/lib/openclaw/extensions/memory-core/." "$HOME/.openclaw/extensions/memory-core/"
    $DRY_RUN_CMD cp -R "${pkgs.openclaw-gateway}/lib/openclaw/extensions/telegram/." "$HOME/.openclaw/extensions/telegram/"
    $DRY_RUN_CMD chmod -R u+rwX,go+rX "$HOME/.openclaw/extensions/acpx" "$HOME/.openclaw/extensions/acp-dispatch" "$HOME/.openclaw/extensions/happier-session-control" "$HOME/.openclaw/extensions/memory-core" "$HOME/.openclaw/extensions/telegram"
  '';

}
