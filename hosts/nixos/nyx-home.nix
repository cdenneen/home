{
  config,
  lib,
  pkgs,
  ...
}:
let
  roleNames = [
    "coder"
    "tester"
    "reviewer"
    "ops"
  ];
  peerUrls = {
    ghost.url = "http://100.114.242.29:8642";
    nyx.url = "http://100.80.58.4:8642";
  };
  opencodePasswordInit = ''
    if [ -z "''${OPENCODE_SERVER_PASSWORD:-}" ] && [ -r /run/secrets/opencode_server_password ]; then
      export OPENCODE_SERVER_PASSWORD="$(${pkgs.coreutils}/bin/tr -d '\n\r' </run/secrets/opencode_server_password)"
    fi
  '';
  mkSecretsCommand =
    slackEnvPath:
    pkgs.writeShellScript "hermes-mesh-secrets" ''
      set -euo pipefail

      emit_secret() {
        name="$1"
        path="$2"
        value="$(${pkgs.coreutils}/bin/tr -d '\r\n' < "$path")"
        [ -n "$value" ]
        ${pkgs.coreutils}/bin/printf '%s=%s\n' "$name" "$value"
      }

      emit_secret EROS_HERMES_AGENTS_KEY ${lib.escapeShellArg config.sops.secrets.eros_litellm_key_hermes_agents.path}
      emit_secret API_SERVER_KEY ${lib.escapeShellArg config.sops.secrets.hermes_mesh_api_key_nyx.path}
      emit_secret HERMES_PEER_GHOST_KEY ${lib.escapeShellArg config.sops.secrets.hermes_mesh_api_key_ghost.path}
      emit_secret HERMES_PEER_NYX_KEY ${lib.escapeShellArg config.sops.secrets.hermes_mesh_api_key_nyx.path}
      ${lib.optionalString (slackEnvPath != null) ''
        ${pkgs.coreutils}/bin/cat ${lib.escapeShellArg slackEnvPath}
      ''}
    '';
  baseSecretsCommand = mkSecretsCommand null;
  coderSecretsCommand = mkSecretsCommand config.sops.secrets.hermes_slack_env_nyx_coder.path;
  opsSecretsCommand = mkSecretsCommand config.sops.secrets.hermes_slack_env_nyx_ops.path;
  localMcp = port: {
    url = "http://127.0.0.1:${toString port}/mcp";
    connect_timeout = 30;
    timeout = 180;
  };
  mkMeshProfile = model: secretsCommand: extraOverrides: {
    createIfMissing = true;
    modelOverrides = {
      "model.default" = model;
      "model.provider" = "custom";
      "model.base_url" = "http://eros.tail0e55.ts.net:4000/v1";
      "model.api_key" = "$" + "{EROS_HERMES_AGENTS_KEY}";
      "model.api_mode" = "chat_completions";
      "secrets.command.enabled" = true;
      "secrets.command.command" = toString secretsCommand;
      "auxiliary.compression.model" = "nova-2-lite";
      "auxiliary.compression.provider" = "main";
      "auxiliary.title_generation.model" = "nova-2-lite";
      "auxiliary.title_generation.provider" = "main";
      bot_peers = peerUrls;
    }
    // extraOverrides;
  };
  mkNamedMeshProfile =
    name: model: secretsCommand: extraOverrides:
    (mkMeshProfile model secretsCommand (
      {
        "platforms.api_server.enabled" = false;
      }
      // extraOverrides
    ))
    // {
      configHomeRelativePath = ".hermes/profiles/${name}/config.yaml";
    };
  gatewayProfile =
    (mkMeshProfile "claude-haiku-4-5" baseSecretsCommand {
      "gateway.multiplex_profiles" = true;
      "gateway.multiplex_profile_allowlist" = roleNames;
      "gateway.max_concurrent_sessions" = 4;
      "platforms.api_server.enabled" = true;
      "platforms.api_server.extra.host" = "100.80.58.4";
      "platforms.api_server.extra.port" = 8642;
      "platforms.api_server.extra.model_name" = "nyx-mesh";
      "plugins.enabled" = [ "platforms/slack" ];
    })
    // {
      configHomeRelativePath = ".hermes/config.yaml";
    };
  commonSoul = ''
    ## Mesh contract

    Your canonical identity is the profile role plus host, for example `coder@nyx`.
    You operate only in the work trust domain on Nyx. Corporate source, credentials, GitLab MCP access, Kubernetes configuration, and AWS access must remain on Nyx. You cannot write the central Ghost Kanban directly. Send a short status handoff to `chief-of-staff@ghost` when work starts, blocks, enters review, or completes so Chief of Staff can update it. Treat any local Nyx Kanban as non-authoritative unless Chief of Staff explicitly delegates otherwise. Never merge, deploy, change infrastructure, or mutate an authoritative external backlog without explicit approval from Chris or delegated approval from Chief of Staff.
  '';
in
{
  sops.secrets = { };

  profiles.hermesGateway.enable = false;
  profiles.hermesGatewaySecondary.enable = false;
  profiles.hermesSupervisor.enable = false;
  profiles.hermesWatchdog.enable = false;
  profiles.gitlabMcpProxy.enable = false;

  profiles.hermesMesh = {
    enable = true;
    workingDirectory = "/home/cdenneen/src/workspace";
    souls = {
      coder = commonSoul + ''
        # Role: Work Coder

        Implement approved enterprise work under `/home/cdenneen/src/workspace`. Use feature branches, focused tests, semantic commits with task references, and GitLab merge requests. Hand changes to Work Tester and Work Reviewer. Never merge your own work without explicit approval.
      '';
      tester = commonSoul + ''
        # Role: Work Tester

        Independently validate enterprise branches and merge requests using the narrowest useful tests first, then integration or regression checks. Report exact evidence and residual risk. Do not merge or deploy.
      '';
      reviewer = commonSoul + ''
        # Role: Work Review Manager

        Review correctness, security, company-boundary compliance, test evidence, and rollback readiness. Approval is explicit and scoped. Never merge unless Chris or Chief of Staff has granted approval authority for that item.
      '';
      ops = commonSoul + ''
        # Role: Work Ops

        Perform work-domain operations on Nyx. Prefer read-only diagnostics, plans, and declarative changes. Infrastructure apply, deployment, service impact, secret rotation, and destructive cleanup require explicit approval and rollback evidence.
      '';
    };
  };

  profiles.hermesKanbanSync.installCollector = true;

  home.activation.retireLegacyHermes = lib.hm.dag.entryBefore [ "writeBoundary" ] ''
    if [ -z "''${DRY_RUN_CMD:-}" ]; then
      ${pkgs.systemd}/bin/systemctl --user disable --now \
        axis-development-watchdog-backup.timer \
        gitlab-mcp-proxy.service \
        hermes-gateway-secondary.service \
        hermes-gateway.service \
        hermes-policy-endpoint-nyx-eks.service \
        hermes-policy-endpoint-nyx-gitlab.service \
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
      elif [ -e "$HOME/.hermes/profiles/nyx-gitlab" ]; then
        echo "Legacy Hermes state reappeared after retirement; refusing activation" >&2
        exit 1
      fi
    fi
  '';

  programs.starship.settings.palette = lib.mkForce "nyx";
  programs.zsh.initContent = lib.mkAfter opencodePasswordInit;
  programs.bash.initExtra = lib.mkAfter opencodePasswordInit;

  profiles.hermesProfileModel.profiles = {
    gateway-router = gatewayProfile;
    coder = mkNamedMeshProfile "coder" "qwen3-coder-next" coderSecretsCommand {
      "platforms.slack.enabled" = true;
      mcp_servers = {
        gitlab_corp = localMcp 18101;
        context7 = localMcp 18106;
      };
    };
    tester = mkNamedMeshProfile "tester" "deepseek-v3.2" baseSecretsCommand {
      mcp_servers = {
        gitlab_corp = localMcp 18101;
        playwright = localMcp 18107;
      };
    };
    reviewer = mkNamedMeshProfile "reviewer" "claude-sonnet-5" baseSecretsCommand {
      "mcp_servers.gitlab_corp" = localMcp 18101;
    };
    ops = mkNamedMeshProfile "ops" "claude-sonnet-4-6" opsSecretsCommand {
      "platforms.slack.enabled" = true;
      mcp_servers = {
        gitlab_corp = localMcp 18101;
        kubernetes = localMcp 18102;
        aws = localMcp 18103;
        terraform = localMcp 18104;
      };
    };
  };
}
