{
  lib,
  pkgs,
  ...
}:
let
  opencodePasswordInit = ''
    if [ -z "''${OPENCODE_SERVER_PASSWORD:-}" ] && [ -r /run/secrets/opencode_server_password ]; then
      export OPENCODE_SERVER_PASSWORD="$(${pkgs.coreutils}/bin/tr -d '\n\r' </run/secrets/opencode_server_password)"
    fi
  '';
in
{
  sops.secrets = { };

  # Declarative default-profile Hermes gateway (Slack, cron, etc.) instead of
  # imperative `hermes gateway install` - see modules/hm/users/cdenneen/hermes-supervisor.
  # That module's ExecStart already goes through the `hermes` launcher wrapper
  # (not the raw venv python), which is required for bundled plugins like
  # slack-platform to register at all.
  profiles.hermesGateway.enable = true;

  programs.starship.settings.palette = lib.mkForce "nyx";

  programs.zsh.initContent = lib.mkAfter opencodePasswordInit;

  programs.bash.initExtra = lib.mkAfter opencodePasswordInit;

}
