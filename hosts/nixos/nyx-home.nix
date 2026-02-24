{
  config,
  lib,
  ...
}:
{
  programs.telegram-bridge = {
    enable = true;
    systemdMode = "user";
    enableLinger = true;

    telegram.botTokenFile = config.sops.secrets.telegram_bot_token.path;
    telegram.ownerChatIdFile = config.sops.secrets.telegram_chat_id.path;
    telegram.updatesMode = "webhook";
    telegram.webhook.publicUrl = "https://nyx.denneen.net";

    opencode.workspaceRoot = "/home/cdenneen/src/workspace";
    opencode.useSharedServer = true;
    opencode.serverUrl = "http://127.0.0.1:4097";
    opencode.serverUsername = "";
    opencode.serverPasswordFile = null;

    web = {
      enable = true;
      baseUrl = "http://127.0.0.1:4097";
      publicUrl = "https://chat.denneen.net";
      username = "";
      passwordFile = null;
      syncIntervalSec = 10;
      forwardUserPrompts = true;
      forwardAgentSteps = true;
    };

    cloudflared = {
      enable = true;
      tokenFile = "/run/secrets/cloudflare_tunnel_token";
      configText = ''
        ingress:
          - hostname: nyx.denneen.net
            path: /telegram
            service: http://127.0.0.1:18080
          - hostname: chat.denneen.net
            service: http://127.0.0.1:4096
          - service: http_status:404
      '';
    };

    chat.allowedGithubUsers = [ "cdenneen" ];
    chat.announceStartup = true;
    chat.announceMessage = "Bridge connected.";
  };

  sops.secrets = {
    telegram_bot_token = { };
    telegram_chat_id = { };
  };

  programs.starship.settings.palette = lib.mkForce "nyx";
}
