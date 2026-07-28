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

  programs.starship.settings.palette = lib.mkForce "nyx";

  programs.zsh.initContent = lib.mkAfter opencodePasswordInit;

  programs.bash.initExtra = lib.mkAfter opencodePasswordInit;

}
