{
  agentPkgs ? null,
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.hermesAlpha0Gateway;
  packageAvailable = pkgs.stdenv.isLinux && agentPkgs != null && agentPkgs ? hermes;
  hermesHome = "${config.home.homeDirectory}/.local/share/alpha0/hermes";
  rootConfig = "${hermesHome}/config.yaml";
  profileConfig = "${hermesHome}/profiles/alpha0/config.yaml";
  defaultSecrets = "/run/secrets/rendered/alpha0-hermes-default.env";
  profileSecrets = "/run/secrets/rendered/alpha0-hermes-profile-alpha0.env";
  defaultSecretsCommand = "/run/current-system/sw/bin/cat ${defaultSecrets}";
  profileSecretsCommand = "/run/current-system/sw/bin/cat ${profileSecrets}";
  servicePath = lib.makeBinPath [
    pkgs.bash
    pkgs.coreutils
    pkgs.git
  ];
  preflight = pkgs.writeShellScript "hermes-alpha0-gateway-preflight" ''
    set -euo pipefail

    fail() {
      echo "hermes-alpha0-gateway: $1" >&2
      exit 78
    }

    [ -d "${hermesHome}" ] || fail "dedicated HERMES_HOME is missing"
    [ -r "${rootConfig}" ] || fail "root config is unreadable"
    [ -r "${profileConfig}" ] || fail "alpha0 profile config is unreadable"

    for secrets_file in "${defaultSecrets}" "${profileSecrets}"; do
      [ -f "$secrets_file" ] || fail "runtime secret map is missing"
      [ -r "$secrets_file" ] || fail "runtime secret map is unreadable"
      [ "$(${pkgs.coreutils}/bin/stat -Lc '%a:%U:%G' "$secrets_file")" = "400:cdenneen:users" ] \
        || fail "runtime secret map ownership or mode is unsafe"
    done

    ${pkgs.gnugrep}/bin/grep -Eq '^SLACK_BOT_TOKEN=xoxb-.+$' "${defaultSecrets}" \
      || fail "Slack bot token is unavailable"
    ${pkgs.gnugrep}/bin/grep -Eq '^SLACK_APP_TOKEN=xapp-.+$' "${defaultSecrets}" \
      || fail "Slack app token is unavailable"
    ${pkgs.gnugrep}/bin/grep -Eq '^SLACK_ALLOWED_USERS=U[A-Z0-9]+$' "${defaultSecrets}" \
      || fail "Slack allowlist is unavailable"
    ${pkgs.gnugrep}/bin/grep -Eq '^API_SERVER_KEY=.{64}$' "${defaultSecrets}" \
      || fail "API server key is unavailable"
    ${pkgs.gnugrep}/bin/grep -Eq '^SLACK_ALLOW_ALL_USERS=false$' "${defaultSecrets}" \
      || fail "Slack deny-by-default control is missing"
    ${pkgs.gnugrep}/bin/grep -Eq '^GATEWAY_ALLOW_ALL_USERS=false$' "${defaultSecrets}" \
      || fail "gateway deny-by-default control is missing"
    ${pkgs.gnugrep}/bin/grep -Eq '^OPENAI_API_KEY=.+$' "${profileSecrets}" \
      || fail "alpha0 provider key is unavailable"

    ${pkgs.yq-go}/bin/yq -e '
      .kanban.dispatch_in_gateway == true and
      .kanban.auto_decompose == false and
      .kanban.max_in_progress_per_profile == 1 and
      .gateway.multiplex_profiles == true and
      (.gateway.profile_routes | length) == 1 and
      .gateway.profile_routes[0].name == "alpha0-slack" and
      .gateway.profile_routes[0].platform == "slack" and
      .gateway.profile_routes[0].profile == "alpha0" and
      .secrets.command.enabled == true and
      .secrets.command.command == "${defaultSecretsCommand}"
    ' "${rootConfig}" >/dev/null || fail "root routing controls do not match"

    ${pkgs.yq-go}/bin/yq -e '
      .kanban.dispatch_in_gateway == false and
      .kanban.auto_decompose == false and
      .kanban.max_in_progress_per_profile == 1 and
      .platforms.api_server.enabled == false and
      .secrets.command.enabled == true and
      .secrets.command.command == "${profileSecretsCommand}"
    ' "${profileConfig}" >/dev/null || fail "alpha0 profile controls do not match"
  '';
in
{
  options.profiles.hermesAlpha0Gateway.enable = lib.mkEnableOption "dedicated Alpha0 Hermes Slack gateway";

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = packageAvailable;
        message = "profiles.hermesAlpha0Gateway requires Linux and agentPkgs.hermes";
      }
    ];

    home.packages = [ agentPkgs.hermes ];

    home.activation.hermesAlpha0GatewayConfig = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      root_config="${rootConfig}"
      profile_config="${profileConfig}"

      if [ ! -f "$root_config" ] || [ ! -f "$profile_config" ]; then
        echo "hermes-alpha0-gateway: activate the dedicated alpha0 profile first" >&2
        exit 1
      fi

      if [ -z "''${DRY_RUN_CMD:-}" ]; then
        root_tmp="$(${pkgs.coreutils}/bin/mktemp "$root_config.XXXXXX")"
        profile_tmp="$(${pkgs.coreutils}/bin/mktemp "$profile_config.XXXXXX")"
        trap '${pkgs.coreutils}/bin/rm -f "$root_tmp" "$profile_tmp"' EXIT

        ${pkgs.yq-go}/bin/yq '
          .gateway.multiplex_profiles = true |
          .gateway.profile_routes = [{"name": "alpha0-slack", "platform": "slack", "profile": "alpha0"}] |
          .secrets.command.enabled = true |
          .secrets.command.command = "${defaultSecretsCommand}"
        ' "$root_config" > "$root_tmp"
        ${pkgs.yq-go}/bin/yq '
          .platforms.api_server.enabled = false |
          .secrets.command.enabled = true |
          .secrets.command.command = "${profileSecretsCommand}"
        ' "$profile_config" > "$profile_tmp"

        ${pkgs.coreutils}/bin/install -m 0600 -T "$root_tmp" "$root_config"
        ${pkgs.coreutils}/bin/install -m 0600 -T "$profile_tmp" "$profile_config"
      fi
    '';

    systemd.user.services.hermes-alpha0-gateway = {
      Unit = {
        Description = "Hermes Agent Gateway - Alpha0 Slack";
        After = [ "network-online.target" ];
        Wants = [ "network-online.target" ];
        StartLimitIntervalSec = 60;
        StartLimitBurst = 5;
      };
      Service = {
        Type = "simple";
        WorkingDirectory = hermesHome;
        ExecStartPre = preflight;
        ExecStart = ''
          ${pkgs.coreutils}/bin/env -i \
            HOME=%h \
            USER=cdenneen \
            LOGNAME=cdenneen \
            LANG=C.UTF-8 \
            PATH=${servicePath} \
            XDG_RUNTIME_DIR=%t \
            HERMES_HOME=${hermesHome} \
            API_SERVER_HOST=127.0.0.1 \
            API_SERVER_PORT=8643 \
            ${agentPkgs.hermes}/bin/hermes gateway run --external-supervisor
        '';
        Restart = "on-failure";
        RestartSec = 5;
        RestartPreventExitStatus = 78;
        TimeoutStopSec = 180;
        KillMode = "mixed";
        UMask = "0077";
        NoNewPrivileges = true;
        PrivateTmp = true;
      };
      Install.WantedBy = [ "default.target" ];
    };
  };
}
