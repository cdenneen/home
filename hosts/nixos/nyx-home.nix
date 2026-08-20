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

  programs.starship.settings.palette = lib.mkForce "nyx";

  programs.zsh.initContent = lib.mkAfter opencodePasswordInit;

  programs.bash.initExtra = lib.mkAfter opencodePasswordInit;

}
