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

  # Wire the local Mem0 REST API into each Hermes gateway profile.
  # All agents on nyx share user_id="nyx" (one pool, cross-agent recall).
  # MEM0_AGENT_ID tags memories by profile so you can filter per-profile
  # without siloing the pool.  Claude Code / OpenCode connect via the
  # OpenMemory MCP server on port 8889 instead (see containerPresets.mem0).
  systemd.user.services.hermes-gateway.environment = {
    MEM0_BASE_URL = "http://127.0.0.1:8888";
    MEM0_USER_ID = "nyx";
    MEM0_AGENT_ID = "hermes-eks";
  };

  systemd.user.services.hermes-gateway-secondary.environment = {
    MEM0_BASE_URL = "http://127.0.0.1:8888";
    MEM0_USER_ID = "nyx";
    MEM0_AGENT_ID = "hermes-gitlab";
  };

  programs.starship.settings.palette = lib.mkForce "nyx";

  programs.zsh.initContent = lib.mkAfter opencodePasswordInit;

  programs.bash.initExtra = lib.mkAfter opencodePasswordInit;

}
