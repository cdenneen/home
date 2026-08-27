{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.profiles.gitlabMcpProxy;
  # Both Hermes gateway profiles talk to the same GitLab instance MCP server,
  # so one shared proxy instance is enough - see hermes-supervisor/default.nix
  # for the two profiles (hermesGateway / hermesGatewaySecondary) this feeds.
  proxyRoot = "${config.home.homeDirectory}/.hermes/gitlab_mcp_proxy";
  proxyScript = ./proxy.py;
  reauthScript = ./reauth.py;
  # Matches secrets.nix's isDarwin/isLinux sops-nix secrets dir split - avoid
  # runtime-dir paths (/run/user/$UID) that may be missing on a headless host
  # with no active login session.
  sopsSecretsDir =
    if pkgs.stdenv.isDarwin then
      "${config.home.homeDirectory}/.config/sops-nix/secrets"
    else
      "${config.home.homeDirectory}/.local/share/sops-nix/secrets";
  gitlabMcpReauth = pkgs.writeShellScriptBin "gitlab-mcp-proxy-reauth" ''
    set -euo pipefail
    export GITLAB_MCP_CLIENT_ID_FILE="${config.sops.secrets.gitlab_mcp_client_id.path}"
    export GITLAB_MCP_CLIENT_SECRET_FILE="${config.sops.secrets.gitlab_mcp_client_secret.path}"
    exec ${pkgs.python3}/bin/python3 ${reauthScript} "$@"
  '';
in
{
  options.profiles.gitlabMcpProxy.enable = lib.mkEnableOption ''
    a local OAuth-refreshing reverse proxy in front of GitLab's native MCP
    server (https://docs.gitlab.com/user/model_context_protocol/mcp_server/).
    Hermes's native MCP client only supports static per-server headers with
    no refresh logic, but GitLab MCP access tokens expire every 2 hours -
    this proxy holds the OAuth client_id/secret/refresh_token, refreshes the
    access token transparently before each forwarded request, and exposes an
    unauthenticated local endpoint both Hermes gateway profiles point at.
  '';

  options.profiles.gitlabMcpProxy.port = lib.mkOption {
    type = lib.types.port;
    default = 8899;
    description = "Local port the proxy listens on (127.0.0.1 only).";
  };

  config = lib.mkIf cfg.enable {
    sops.secrets.gitlab_mcp_client_id = {
      mode = "0400";
      path = "${sopsSecretsDir}/gitlab_mcp_client_id";
    };
    sops.secrets.gitlab_mcp_client_secret = {
      mode = "0400";
      path = "${sopsSecretsDir}/gitlab_mcp_client_secret";
    };

    home.packages = [ gitlabMcpReauth ];

    home.activation.gitlabMcpProxyRoot = lib.hm.dag.entryBefore [ "writeBoundary" ] ''
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/mkdir -p "${proxyRoot}"
      $DRY_RUN_CMD ${pkgs.coreutils}/bin/chmod 700 "${proxyRoot}"
    '';

    # creds.json's access_token/refresh_token/expires_at are deliberately NOT
    # managed here - they're mutable runtime state (GitLab rotates the
    # refresh_token on every use) and real bearer credentials, so they don't
    # belong in the world-readable Nix store. client_id/client_secret (which
    # ARE durable and belong in the OAuth app, not the token) come from sops
    # above precisely so a fresh host only needs one manual step:
    #
    #   gitlab-mcp-proxy-reauth
    #
    # which reads client_id/secret from the sops secrets declared above,
    # walks you through the one-time browser authorization, and writes
    # creds.json (mode 0600) - see reauth.py. Re-run the same command any
    # time the refresh_token stops working (OAuth app deleted/recreated,
    # token revoked). No manual `curl`/token-exchange bookkeeping needed on
    # a new host or after a revocation - this script is the one path for both.
    home.activation.gitlabMcpProxyBootstrapHint = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      if [ ! -f "${proxyRoot}/creds.json" ] && [ -z "''${DRY_RUN_CMD:-}" ]; then
        echo "gitlab-mcp-proxy: no ${proxyRoot}/creds.json yet - run 'gitlab-mcp-proxy-reauth' to bootstrap OAuth credentials." >&2
      fi
    '';

    systemd.user.services.gitlab-mcp-proxy = {
      Unit = {
        Description = "GitLab MCP OAuth-refreshing reverse proxy (for Hermes mcp_servers.gitlab)";
        After = [ "network-online.target" ];
        Wants = [ "network-online.target" ];
      };
      Service = {
        Type = "simple";
        ExecStart = "${pkgs.python3}/bin/python3 ${proxyScript} --port ${toString cfg.port}";
        WorkingDirectory = proxyRoot;
        Restart = "on-failure";
        RestartSec = 5;
      };
      Install.WantedBy = [ "default.target" ];
    };

    # Wire mcp_servers.gitlab into BOTH Hermes profiles' config.yaml via the
    # same yq-based activation pattern hermes-supervisor uses for its own
    # config keys - never hand-edit config.yaml (see hermes-agent skill's
    # hard invariant), and idempotent so repeat activations no-op cleanly.
    home.activation.gitlabMcpProxyHermesConfig = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      configure_mcp() {
        local hermes_config="$1"
        if [ -f "$hermes_config" ] && [ -z "''${DRY_RUN_CMD:-}" ]; then
          local config_tmp
          config_tmp="$(${pkgs.coreutils}/bin/mktemp --tmpdir hermes-mcp-config.XXXXXX)"
          ${pkgs.yq-go}/bin/yq '
            .mcp_servers.gitlab.url = "http://127.0.0.1:${toString cfg.port}/mcp"
            | .mcp_servers.gitlab.timeout = 180
            | .mcp_servers.gitlab.connect_timeout = 30
          ' "$hermes_config" > "$config_tmp"
          if ! ${pkgs.diffutils}/bin/cmp -s "$config_tmp" "$hermes_config" \
            || [ "$(${pkgs.coreutils}/bin/stat -c %a "$hermes_config")" != 600 ]; then
            ${pkgs.coreutils}/bin/install -m 600 -T "$config_tmp" "$hermes_config"
          fi
          ${pkgs.coreutils}/bin/rm -f "$config_tmp"
        fi
      }
      configure_mcp "$HOME/.hermes/config.yaml"
      ${lib.optionalString config.profiles.hermesGatewaySecondary.enable ''
        configure_mcp "$HOME/.hermes/profiles/${config.profiles.hermesGatewaySecondary.profileName}/config.yaml"
      ''}
    '';
  };
}
