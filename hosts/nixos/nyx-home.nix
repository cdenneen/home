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
  #
  # nyx-eks: primary instance, pinned to the EKS workspace so every Slack/chat
  # message there gets that project's AGENTS.md/.ai/ context auto-injected
  # (previously it ran from ~/.hermes with no project context at all).
  profiles.hermesGateway = {
    enable = true;
    workingDirectory = "/home/cdenneen/src/workspace/eks";
  };

  # nyx-gitlab: secondary instance, its own Hermes profile (own config.yaml,
  # .env, skills, kanban board - created via `hermes profile create
  # nyx-gitlab --clone`) and its own Slack app, pinned to the gitlab
  # workspace. Deliberately a separate systemd unit + profile rather than a
  # second channel on the primary instance, so GitLab work never shares
  # state or context with the EKS instance.
  profiles.hermesGatewaySecondary = {
    enable = true;
    profileName = "nyx-gitlab";
    workingDirectory = "/home/cdenneen/src/workspace/gitlab";
  };

  # Shared OAuth-refreshing proxy so both Hermes gateway instances above can
  # use GitLab's native MCP server without hitting Hermes's lack of built-in
  # OAuth-refresh support for MCP servers - see modules/hm/users/cdenneen/
  # gitlab-mcp-proxy for details and the bootstrap steps for creds.json.
  profiles.gitlabMcpProxy.enable = true;

  programs.starship.settings.palette = lib.mkForce "nyx";

  programs.zsh.initContent = lib.mkAfter opencodePasswordInit;

  programs.bash.initExtra = lib.mkAfter opencodePasswordInit;

  # Bootstrap-tier host-local policy endpoints (deployed, healthy, NOT yet
  # referenced by any Hermes profile's model.base_url - see
  # modules/hm/users/cdenneen/hermes-policy-endpoint and
  # bootstrap-gate-evidence.md). Wiring a profile to actually use one is a
  # separate Phase 2 canary action requiring its own explicit approval.
  profiles.hermesPolicyEndpoint.instances = {
    nyx-eks = {
      port = 8601;
      priority = "P1";
      continuityClass = "human-present";
      erosBaseUrl = "http://eros.tail0e55.ts.net:4000";
      erosTailscaleIp = "100.117.68.38";
      erosApiKeySecret = "eros_litellm_key_nyx_eks";
    };
    nyx-gitlab = {
      port = 8602;
      priority = "P1";
      continuityClass = "manual-break-glass";
      erosBaseUrl = "http://eros.tail0e55.ts.net:4000";
      erosTailscaleIp = "100.117.68.38";
      erosApiKeySecret = "eros_litellm_key_nyx_gitlab";
    };
  };
}
