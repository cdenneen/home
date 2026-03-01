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
}
