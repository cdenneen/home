{
  config,
  lib,
  pkgs,
  ...
}:
let
  erosKeyEnvironment = pkgs.writeShellScript "hermes-eros-key-environment" ''
    set -eu
    key="$(${pkgs.coreutils}/bin/tr -d '\r\n' < ${lib.escapeShellArg config.sops.secrets.eros_litellm_key_hermes_agents.path})"
    [ -n "$key" ]
    ${pkgs.coreutils}/bin/printf 'EROS_HERMES_AGENTS_KEY=%s\n' "$key"
  '';
  mkMeshProfile = model: {
    createIfMissing = true;
    modelOverrides = {
      "model.default" = model;
      "model.provider" = "custom";
      "model.base_url" = "http://eros.tail0e55.ts.net:4000/v1";
      "model.api_key" = "$" + "{EROS_HERMES_AGENTS_KEY}";
      "model.api_mode" = "chat_completions";
      "secrets.command.enabled" = true;
      "secrets.command.command" = toString erosKeyEnvironment;
      "auxiliary.compression.model" = "nova-2-lite";
      "auxiliary.compression.provider" = "main";
      "auxiliary.title_generation.model" = "nova-2-lite";
      "auxiliary.title_generation.provider" = "main";
    };
  };
  mkNamedMeshProfile =
    name: model:
    (mkMeshProfile model)
    // {
      configHomeRelativePath = ".hermes/profiles/${name}/config.yaml";
    };
in
{
  profiles.hermesAxisControlGateway.enable = false;
  profiles.hermesGateway.enable = false;
  profiles.hermesGatewaySecondary.enable = false;
  profiles.hermesSupervisor.enable = false;
  profiles.hermesWatchdog.enable = false;
  profiles.gitlabMcpProxy.enable = false;
  services.axis-control-observer.enable = false;

  home.activation.retireLegacyHermes = lib.hm.dag.entryBefore [ "writeBoundary" ] ''
    if [ -z "''${DRY_RUN_CMD:-}" ]; then
      ${pkgs.systemd}/bin/systemctl --user disable --now \
        alpha0-gitlab-nyx-relay.service \
        axis-control-observe.service \
        axis-control-observe.timer \
        axis-control-watchdog.service \
        axis-control-watchdog.timer \
        axis-development-watchdog-backup.service \
        axis-development-watchdog-backup.timer \
        axis-development-watchdog-monitor.service \
        hermes-alpha0-gateway.service \
        hermes-axis-control-scheduler-watchdog.service \
        hermes-axis-control-scheduler-watchdog.timer \
        hermes-axis-control-gateway.service \
        hermes-gateway-secondary.service \
        hermes-gateway.service \
        hermes-policy-endpoint-ghost-alpha0.service \
        hermes-policy-endpoint-ghost-axis-control.service \
        hermes-policy-endpoint-ghost-default.service \
        hermes-stuck-cron-watchdog.service \
        hermes-stuck-cron-watchdog.timer \
        hermes-supervisor-cron.service \
        hermes-watchdog-cron.service \
        hermes-watchdog-cutover.service \
        2>/dev/null || true

      archive_root="$HOME/.hermes-retired/pre-mesh-20260912"
      archive_path="$archive_root/hermes-home"
      retirement_marker="$archive_root/.retired"
      ${pkgs.coreutils}/bin/install -d -m 700 "$archive_root"
      if [ ! -e "$retirement_marker" ]; then
        if [ -e "$archive_path" ]; then
          echo "Refusing unverified pre-existing Hermes archive: $archive_path" >&2
          exit 1
        fi
        if [ -e "$HOME/.hermes" ]; then
          ${pkgs.coreutils}/bin/mv -T "$HOME/.hermes" "$archive_path"
        fi
        ${pkgs.coreutils}/bin/touch "$retirement_marker"
      elif [ -e "$HOME/.hermes/config.yaml" ] \
        || [ -e "$HOME/.hermes/profiles/alpha0" ] \
        || [ -e "$HOME/.hermes/profiles/axis-control" ]; then
        echo "Legacy Hermes state reappeared after retirement; refusing activation" >&2
        exit 1
      fi
    fi
  '';

  profiles.hermesProfileModel.profiles = {
    chief-of-staff = mkNamedMeshProfile "chief-of-staff" "claude-sonnet-5";
    researcher = mkNamedMeshProfile "researcher" "kimi-k2.5";
    architect = mkNamedMeshProfile "architect" "claude-opus-5";
    coder = mkNamedMeshProfile "coder" "qwen3-coder-next";
    tester = mkNamedMeshProfile "tester" "deepseek-v3.2";
    reviewer = mkNamedMeshProfile "reviewer" "claude-sonnet-5";
    ops = mkNamedMeshProfile "ops" "claude-sonnet-4-6";
  };
}
