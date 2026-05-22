{
  lib,
  pkgs,
  happier,
  ...
}:
let
  happierCli = happier.packages.${pkgs.stdenv.hostPlatform.system}.happier-cli;
in
{
  sops.secrets = { };

  programs.starship.settings.palette = lib.mkForce "nyx";

  home.activation.happierNyxCleanupLegacyService = lib.hm.dag.entryAfter [ "linkGeneration" ] ''
    legacy_default="$HOME/.config/systemd/user/happier-daemon.service"
    legacy_unit="$HOME/.config/systemd/user/happier-daemon.nyx.service"
    if [ -e "$legacy_default" ]; then
      ${pkgs.systemd}/bin/systemctl --user disable --now happier-daemon.service 2>/dev/null || true
      $DRY_RUN_CMD rm -f "$legacy_default"
    fi
    if [ -e "$legacy_unit" ]; then
      ${pkgs.systemd}/bin/systemctl --user disable --now happier-daemon.nyx.service 2>/dev/null || true
      $DRY_RUN_CMD rm -f "$legacy_unit"
    fi
    ${pkgs.systemd}/bin/systemctl --user daemon-reload 2>/dev/null || true
  '';

  home.activation.happierNyxDaemonLinux = lib.hm.dag.entryAfter [ "reloadSystemd" ] ''
    if [ "$(uname -s)" = "Linux" ]; then
      happier_bin="${happierCli}/bin/happier"
      if [ -x "$happier_bin" ]; then
        export HAPPIER_HOME_DIR="$HOME/.happier"
        export HAPPIER_NO_BROWSER_OPEN=1
        export HAPPIER_DAEMON_WAIT_FOR_AUTH=1
        export HAPPIER_DAEMON_WAIT_FOR_AUTH_TIMEOUT_MS=0

        $DRY_RUN_CMD "$happier_bin" \
          --server-url "https://nyx.tail0e55.ts.net" \
          --webapp-url "https://nyx.tail0e55.ts.net" \
          service install --yes --replace-existing=all --json >/dev/null

        $DRY_RUN_CMD "$happier_bin" \
          --server-url "https://nyx.tail0e55.ts.net" \
          --webapp-url "https://nyx.tail0e55.ts.net" \
          service start --json >/dev/null
      fi
    fi
  '';

}
