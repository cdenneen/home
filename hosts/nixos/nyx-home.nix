{
  config,
  lib,
  pkgs,
  happier,
  ...
}:
{
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
    config = {
      gateway = {
        mode = "local";
        bind = "tailnet";
        auth = {
          mode = "token";
        };
        controlUi = {
          allowedOrigins = [ "https://clawd.denneen.net" ];
        };
      };
      channels.telegram = {
        enabled = true;
        tokenFile = config.sops.secrets.telegram_bot_token.path;
        streaming = "partial";
      };
      session = {
        dmScope = "per-channel-peer";
      };
    };
  };

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

        exec ${pkgs.openclaw}/bin/openclaw gateway --port 18789
      '';
    in
    {
      Service.ExecStart = lib.mkForce run;
    };

}
